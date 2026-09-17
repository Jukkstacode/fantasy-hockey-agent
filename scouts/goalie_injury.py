"""GoalieInjuryScout: when a starter goes down, grab the backup.

Two detectors:
1. Injury: a goalie's Yahoo status flipped to injured since the last run
   (or is injured now and we haven't alerted on it recently). Look up his
   team's other goalies from the NHL roster and report their recent start
   share and the team's schedule for the coming week.
2. Start-share drift: a goalie who has started 3 of his team's last 4 games
   while not previously the primary starter. Catches quiet tandem changes
   and hidden injuries before the transaction wire does.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

import polars as pl

import config
from opportunity import (Opportunity, GOALIE_BACKUP, GOALIE_START_SHARE, INJURY_OUT,
                         URGENCY_NOW, URGENCY_WEEK)
from scouts.base import Scout, ScoutContext, INJURED_STATUSES

logger = logging.getLogger(__name__)

STARTER_MIN_TOI = 30.0   # minutes across situations to count as a start


class GoalieInjuryScout(Scout):
    name = "goalie_injury"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        starts = self._start_log(ctx)
        opps = []
        opps += self._injuries(ctx, starts)
        opps += self._start_share_drift(ctx, starts)
        return opps

    # ── data ─────────────────────────────────────────────────────

    def _start_log(self, ctx: ScoutContext) -> dict[str, list[tuple[int, str, str]]]:
        """team -> [(gameID, gameDate, starter_name)] sorted by date."""
        df = ctx.stats.get_goalie_games(days=35)
        if df.is_empty():
            return {}
        if "situation" in df.columns:
            df = df.filter(pl.col("situation") == "all")   # 'all' already totals ev/pp/pk
        toi = df.group_by(["team", "gameID", "gameDate", "name"]).agg(
            pl.col("iceTime").sum().alias("toi")
        )
        per_game: dict[tuple[str, int], list] = defaultdict(list)
        dates = {}
        for row in toi.iter_rows(named=True):
            per_game[(row["team"], row["gameID"])].append((row["toi"], row["name"]))
            dates[(row["team"], row["gameID"])] = str(row["gameDate"])
        out: dict[str, list] = defaultdict(list)
        for (team, gid), goalies in per_game.items():
            goalies.sort(reverse=True)
            if goalies[0][0] >= STARTER_MIN_TOI:
                out[team].append((gid, dates[(team, gid)], goalies[0][1]))
        for team in out:
            out[team].sort(key=lambda x: (x[1], x[0]))
        return out

    @staticmethod
    def _share(starts: list, name: str, last_n: int) -> tuple[int, int]:
        window = starts[-last_n:] if last_n else starts
        return sum(1 for _, _, g in window if g == name), len(window)

    # ── detectors ────────────────────────────────────────────────

    def _injuries(self, ctx: ScoutContext, starts) -> list[Opportunity]:
        alerts = ctx.store.load("goalie_alerts", {})
        cutoff = (ctx.as_of - timedelta(days=14)).isoformat()
        opps = []
        seen_ids = set()
        for info in ctx.players.values():
            if id(info) in seen_ids or not info.is_goalie or not info.is_injured:
                continue
            seen_ids.add(id(info))
            prev = ctx.lookup_prev(info.name)
            newly = prev is None or not prev.is_injured
            recently_alerted = alerts.get(info.name, "") >= cutoff
            if not newly and recently_alerted:
                continue
            if not newly and not recently_alerted and prev is not None:
                # Long-term injury we already know about; skip quietly.
                continue
            alerts[info.name] = ctx.as_of.isoformat()
            team = info.team
            team_starts = starts.get(team, [])
            hurt_share, n = self._share(team_starts, info.name, 10)
            was_primary = n and hurt_share / n >= 0.5
            if info.on_my_roster:
                opps.append(Opportunity(
                    player_name=info.name, nhl_team=team, signal=INJURY_OUT,
                    evidence=f"{info.status_full or info.status}: {info.injury_note or 'no details'}",
                    confidence=0.9, urgency=URGENCY_NOW, positions=list(info.positions),
                ))
            teammates = [g for g in ctx.nhl.get_team_goalies(team) if g["name"] != info.name]
            games = ctx.games_next_week(team)
            # Rank teammates by recent starts; camp rosters can list five goalies.
            ranked = []
            for g in teammates:
                mate = ctx.lookup(g["name"])
                if mate and mate.is_injured:
                    continue
                share, n2 = self._share(team_starts, g["name"], 10)
                ranked.append((share, g, n2))
            ranked.sort(key=lambda x: -x[0])
            for share, g, n2 in ranked[:2]:
                if share == 0 and ranked[0][0] > 0:
                    conf = 0.35     # third-stringer / call-up; note but don't shout
                else:
                    conf = 0.85 if was_primary else 0.6
                evidence = (f"{info.name} is {info.status_full or info.status}"
                            f"{' (' + info.injury_note + ')' if info.injury_note else ''}; "
                            f"{g['name']} started {share} of {team}'s last {n2} games. "
                            f"{team} plays {games} times in the next 7 days.")
                opps.append(Opportunity(
                    player_name=g["name"], nhl_team=team, signal=GOALIE_BACKUP,
                    evidence=evidence, confidence=conf,
                    urgency=URGENCY_NOW if conf >= 0.6 else URGENCY_WEEK,
                    positions=["G"], projected_value=ctx.stats.get_goalie_value(g["name"]),
                ))
        ctx.store.save("goalie_alerts", alerts)
        return opps

    def _start_share_drift(self, ctx: ScoutContext, starts) -> list[Opportunity]:
        opps = []
        for team, log in starts.items():
            if len(log) < 8:
                continue
            recent, prior = log[-4:], log[-14:-4]
            counts = defaultdict(int)
            for _, _, g in recent:
                counts[g] += 1
            leader, n_recent = max(counts.items(), key=lambda x: x[1])
            if n_recent < 3 or len(prior) < 6:
                continue
            prior_counts = defaultdict(int)
            for _, _, g in prior:
                prior_counts[g] += 1
            prior_leader = max(prior_counts.items(), key=lambda x: x[1])[0]
            prior_share, n_prior = self._share(prior, leader, 0)
            if prior_leader == leader or prior_share / n_prior > 0.45:
                continue    # he was already the main starter; normal tandem noise
            if ctx.on_my_roster(leader):
                continue
            games = ctx.games_next_week(team)
            opps.append(Opportunity(
                player_name=leader, nhl_team=team, signal=GOALIE_START_SHARE,
                evidence=(f"Started {n_recent} of {team}'s last 4 (was {prior_share} of {n_prior} before; "
                          f"{prior_leader} had been the starter). {team} plays {games} times in the next 7 days."),
                confidence=0.6, urgency=URGENCY_WEEK, positions=["G"],
                projected_value=ctx.stats.get_goalie_value(leader),
            ))
        return opps
