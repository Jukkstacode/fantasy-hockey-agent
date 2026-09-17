"""Merge, score, and sort opportunities against the current roster.

score = gain × confidence × urgency_multiplier × schedule_factor

- gain: projected value minus the weakest compatible roster player's value
- urgency_multiplier: 2.0 now, 1.0 this week, 0.5 watch
- schedule_factor: games next 7 days / 3.5, capped at 1.4
Goalie-backup signals pass even at gain <= 0 because start volume is the
point. Warnings about your own roster are routed to their own section.
"""

import logging
from datetime import date, timedelta

import config
from opportunity import (Opportunity, GOALIE_BACKUP, GOALIE_START_SHARE, INJURY_RETURN,
                         HOT_STREAK, COLD_STREAK, URGENCY_NOW, URGENCY_WEEK, URGENCY_WATCH)
from scouts.base import ScoutContext
from stats_provider import _normalize_name

logger = logging.getLogger(__name__)

URGENCY_MULT = {URGENCY_NOW: 2.0, URGENCY_WEEK: 1.0, URGENCY_WATCH: 0.5}
BENCH_LIKE = {"BN", "IR", "IR+", "IR-LT", "NA", "Util", "UTIL"}


class RankedReport:
    def __init__(self):
        self.act_now: list[dict] = []
        self.rising: list[dict] = []
        self.watchlist: list[dict] = []
        self.roster_alerts: list[dict] = []
        self.still_available: list[dict] = []
        self.notes: list[str] = []

    def is_empty(self) -> bool:
        return not (self.act_now or self.rising or self.watchlist or self.roster_alerts)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("act_now", "rising", "watchlist", "roster_alerts", "still_available", "notes")}


def merge_opportunities(opps: list[Opportunity]) -> list[Opportunity]:
    merged: dict[str, Opportunity] = {}
    for o in opps:
        key = _normalize_name(o.player_name) + ("|warn" if o.is_warning else "")
        if key in merged:
            merged[key].merge(o)
        else:
            merged[key] = o
    return list(merged.values())


def _roster_values(ctx: ScoutContext) -> list[dict]:
    out = []
    for p in ctx.my_roster:
        out.append({
            "name": p.name, "positions": [x for x in p.positions if x not in BENCH_LIKE],
            "is_goalie": p.is_goalie,
            "value": ctx.value_of(p.name, "G" if p.is_goalie else ""),
            "status": p.status,
        })
    return out


def _weakest_compatible(opp: Opportunity, roster: list[dict]) -> dict | None:
    cands = []
    opp_pos = {x for x in opp.positions if x not in BENCH_LIKE}
    for r in roster:
        if r["is_goalie"] != opp.is_goalie:
            continue
        if not opp.is_goalie and opp_pos and not (opp_pos & set(r["positions"])):
            continue
        cands.append(r)
    if not cands:
        return None
    return min(cands, key=lambda r: r["value"])


def rank(opps: list[Opportunity], ctx: ScoutContext) -> RankedReport:
    report = RankedReport()
    roster = _roster_values(ctx)
    shown = ctx.store.load("opportunities_shown", {})
    cutoff = (ctx.as_of - timedelta(days=config.SCOUT_REPEAT_DAYS)).isoformat()
    today = ctx.as_of.isoformat()

    dfo = ctx.extras.get("dfo", {})
    trends = ctx.extras.get("trends")
    for o in merge_opportunities(opps):
        if trends is not None and not any(s in (HOT_STREAK, COLD_STREAK) for s in o.signals):
            t = trends.get(o.player_name, is_goalie=o.is_goalie)
            if t:
                o.evidence_lines.append(f"Trend: {t.label()}")
        unit = dfo.get(_normalize_name(o.player_name))
        if unit:
            from scouts.dailyfaceoff import describe_unit
            desc = describe_unit(unit)
            if desc:
                o.evidence_lines.append(f"DailyFaceoff: {desc}")
            if unit.get("team") and not o.nhl_team:
                o.nhl_team = unit["team"]
        entry = o.to_dict()
        if o.is_warning:
            entry["score"] = round(o.confidence * URGENCY_MULT.get(o.urgency, 1.0), 2)
            report.roster_alerts.append(entry)
            continue
        if o.on_my_roster:
            continue
        drop = _weakest_compatible(o, roster)
        gain = (o.projected_value - drop["value"]) if drop else o.projected_value
        entry["drop_candidate"] = drop["name"] if drop else None
        entry["drop_value"] = round(drop["value"], 1) if drop else None
        entry["gain"] = round(gain, 1)
        games = ctx.games_next_week(o.nhl_team) if o.nhl_team else 0
        entry["games_next_7"] = games
        sched = min(1.4, games / 3.5) if games else 1.0
        score = gain * o.confidence * URGENCY_MULT.get(o.urgency, 1.0) * sched
        volume_signal = bool({GOALIE_BACKUP, GOALIE_START_SHARE} & set(o.signals))
        if gain <= 0 and not volume_signal:
            # Not an upgrade over anyone on the roster. Keep as a watch item only
            # if it's an injury return with unknown current value.
            if INJURY_RETURN in o.signals and o.projected_value == 0:
                entry["score"] = round(o.confidence, 2)
                report.watchlist.append(entry)
            continue
        if volume_signal and gain <= 0:
            if o.confidence < 0.6:
                entry["score"] = round(o.confidence, 2)
                report.watchlist.append(entry)
                continue
            score = max(score, 5.0 * o.confidence * URGENCY_MULT.get(o.urgency, 1.0))
        entry["score"] = round(score, 2)

        # Availability routing
        if o.available is False:
            # Rostered by another manager: trade target
            entry["note"] = f"Rostered by {o.rostered_by}" if o.rostered_by else "Rostered"
            report.watchlist.append(entry)
            continue

        # Repeat suppression: same player, same signals, shown within the last
        # few days and not urgent -> one-line "still available" mention instead
        sig_key = ",".join(sorted(o.signals))
        entry["_sig"] = sig_key
        prev = shown.get(_normalize_name(o.player_name))
        if prev and prev.get("signals") == sig_key and prev.get("date", "") >= cutoff and o.urgency != URGENCY_NOW:
            report.still_available.append(entry)
            continue
        if o.urgency == URGENCY_NOW:
            report.act_now.append(entry)
        elif o.urgency == URGENCY_WEEK:
            report.rising.append(entry)
        else:
            report.watchlist.append(entry)

    for section in (report.act_now, report.rising, report.watchlist, report.roster_alerts, report.still_available):
        section.sort(key=lambda e: -e.get("score", 0))
    report.act_now = report.act_now[:config.SCOUT_MAX_ACT_NOW]
    report.rising = report.rising[:config.SCOUT_MAX_RISING]
    report.watchlist = report.watchlist[:config.SCOUT_MAX_WATCHLIST]
    report.still_available = report.still_available[:6]

    # Only players actually displayed count as "shown" for later suppression
    for section in (report.act_now, report.rising, report.watchlist):
        for e in section:
            shown[_normalize_name(e["player_name"])] = {"signals": e["_sig"], "date": today}
    for e in report.still_available:
        shown[_normalize_name(e["player_name"])]["date"] = today   # keep suppressing while listed
    for section in (report.act_now, report.rising, report.watchlist, report.still_available, report.roster_alerts):
        for e in section:
            e.pop("_sig", None)

    # Prune old entries from the shown-log
    shown = {k: v for k, v in shown.items() if v.get("date", "") >= cutoff}
    ctx.store.save("opportunities_shown", shown)
    if not ctx.yahoo_ok:
        report.notes.append("Yahoo was unreachable: free-agent availability is unknown"
                            + ("." if ctx.my_roster else "; drop candidates need a roster."))
    return report
