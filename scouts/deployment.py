"""DeploymentScout: power-play and line promotions (and demotions) from
per-game ice time.

Uses MoneyPuck game logs via pyhockey. For each skater, compares mean ice
time over the last RECENT games to the mean over the BASELINE games before
that. A jump plus a top-N rank on his team (PP1 proxy for power play, top-6
F / top-4 D for even strength) is a promotion.
"""

import logging
from collections import defaultdict

import polars as pl

import config
from opportunity import (Opportunity, PP_PROMOTION, PP_DEMOTION, LINE_PROMOTION,
                         LINE_DEMOTION, URGENCY_WEEK, URGENCY_WATCH)
from scouts.base import Scout, ScoutContext

logger = logging.getLogger(__name__)


def _fmt(minutes: float) -> str:
    m = int(minutes)
    s = int(round((minutes - m) * 60))
    return f"{m}:{s:02d}"


class DeploymentScout(Scout):
    name = "deployment"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        opps = []
        opps += self._scan_situation(ctx, "pp")
        opps += self._scan_situation(ctx, "ev")
        return opps

    def _scan_situation(self, ctx: ScoutContext, situation: str) -> list[Opportunity]:
        recent_n = config.SCOUT_RECENT_GAMES
        base_n = config.SCOUT_BASELINE_GAMES
        df = ctx.stats.get_skater_games(situation, days=60)
        if df.is_empty():
            return []
        df = df.sort(["team", "gameDate", "gameID"])

        # Team game lists (to define "the team's last N games")
        team_games: dict[str, list[int]] = {}
        for team, grp in df.group_by("team"):
            team = team[0] if isinstance(team, tuple) else team
            games = grp.select(["gameID", "gameDate"]).unique().sort("gameDate")
            team_games[team] = games["gameID"].to_list()

        # Per-player per-game ice time. On the power play, normalize by the
        # team's PP minutes that game (sum of skater TOI / 5 skaters on ice)
        # so a night with six power plays doesn't look like a promotion.
        toi: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
        pos: dict[tuple[str, str], str] = {}
        team_minutes: dict[tuple[str, int], float] = defaultdict(float)
        rows = df.select(["name", "team", "position", "gameID", "iceTime"]).iter_rows(named=True)
        for row in rows:
            k = (row["name"], row["team"])
            minutes = float(row["iceTime"] or 0.0)
            toi[k][row["gameID"]] = minutes
            pos[k] = row["position"] or ""
            team_minutes[(row["team"], row["gameID"])] += minutes
        if situation == "pp":
            for k, games in toi.items():
                for gid in games:
                    tm = team_minutes.get((k[1], gid), 0.0) / 5.0
                    games[gid] = games[gid] / tm if tm >= 1.0 else 0.0

        # Team rank in the recent window
        recent_means: dict[tuple[str, str], float] = {}
        base_means: dict[tuple[str, str], float] = {}
        for k, games in toi.items():
            tg = team_games.get(k[1], [])
            if len(tg) < recent_n + 5:
                continue
            recent_ids = tg[-recent_n:]
            base_ids = tg[-(recent_n + base_n):-recent_n]
            r = [games.get(g, 0.0) for g in recent_ids if g in games]
            b = [games.get(g, 0.0) for g in base_ids if g in games]
            if len(r) < recent_n or len(b) < 5:
                continue
            recent_means[k] = sum(r) / len(r)
            base_means[k] = sum(b) / len(b)

        rank_recent = self._team_ranks(recent_means, pos, situation)
        rank_base = self._team_ranks(base_means, pos, situation)

        jump_min = config.SCOUT_ES_JUMP_MIN   # even-strength minutes; PP uses share thresholds
        top_n = config.SCOUT_PP_TOP_N
        opps = []
        for k, recent in recent_means.items():
            name, team = k
            base = base_means[k]
            jump = recent - base
            r_now, r_before = rank_recent.get(k, 99), rank_base.get(k, 99)
            is_f = pos.get(k, "") != "D"
            if situation == "pp":
                # values are shares of team PP time (0..1)
                promoted = (jump >= config.SCOUT_PP_SHARE_JUMP and r_now <= top_n
                            and r_before > top_n and recent >= 0.45)
                demoted = jump <= -config.SCOUT_PP_SHARE_JUMP and r_before <= top_n and r_now > top_n
            else:
                limit = 6 if is_f else 4
                promoted = jump >= jump_min and r_now <= limit and r_before > limit
                demoted = jump <= -jump_min and r_before <= limit and r_now > limit
            if not (promoted or demoted):
                continue
            mine = ctx.on_my_roster(name)
            if promoted and mine:
                continue        # good news about my own player; not a pickup
            if demoted and not mine:
                continue        # only warn about my players
            if situation == "pp":
                conf = min(0.95, 0.5 + abs(jump) * 1.2)
                evidence = (f"PP share {base:.0%} → {recent:.0%} of team PP time over last {recent_n} games "
                            f"(team PP rank {r_before} → {r_now})")
            else:
                conf = min(0.9, 0.45 + abs(jump) / 6.0)
                evidence = (f"ES TOI {_fmt(base)} → {_fmt(recent)} over last {recent_n} games "
                            f"({'D' if not is_f else 'F'} rank {r_before} → {r_now})")
            signal = ((PP_PROMOTION if situation == "pp" else LINE_PROMOTION) if promoted
                      else (PP_DEMOTION if situation == "pp" else LINE_DEMOTION))
            opps.append(Opportunity(
                player_name=name, nhl_team=team, signal=signal, evidence=evidence,
                confidence=conf, urgency=URGENCY_WEEK if promoted else URGENCY_WATCH,
                projected_value=ctx.value_of(name, pos.get(k, "")),
            ))
        return opps

    @staticmethod
    def _team_ranks(means: dict, pos: dict, situation: str) -> dict:
        """Rank players within their team (F and D separately at even strength)."""
        buckets = defaultdict(list)
        for k, v in means.items():
            group = (k[1], "D" if (situation == "ev" and pos.get(k) == "D") else "F")
            buckets[group].append((v, k))
        ranks = {}
        for group, items in buckets.items():
            items.sort(reverse=True)
            for i, (_, k) in enumerate(items, 1):
                ranks[k] = i
        return ranks
