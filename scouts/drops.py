"""DroppedPlayerScout: another manager dropped someone worth grabbing.

Compares this run's ownership against the previous snapshot: a player who
was on someone else's roster last time and is a free agent (or on waivers)
now was dropped. Valued on projected fantasy points per game; good ones are
"act now" because the whole league sees the same wire.
"""

import logging

import config
from opportunity import Opportunity, DROPPED, URGENCY_NOW, URGENCY_WEEK
from scouts.base import Scout, ScoutContext

logger = logging.getLogger(__name__)


class DroppedPlayerScout(Scout):
    name = "drops"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        if not ctx.prev_players:
            return []
        opps, seen = [], set()
        for info in ctx.players.values():
            if id(info) in seen or not info.available:
                continue
            seen.add(id(info))
            prev = ctx.lookup_prev(info.name)
            if prev is None or prev.ownership_type != "team" or prev.on_my_roster:
                continue
            value = ctx.value_of(info.name, "G" if info.is_goalie else "")
            fppg = value / 10.0
            urgent = fppg >= config.SCOUT_DROP_MIN_FPPG
            status = f" ({info.status_full or info.status})" if info.status else ""
            opps.append(Opportunity(
                player_name=info.name, nhl_team=info.team, signal=DROPPED,
                evidence=(f"Dropped by {prev.owner_team or 'another manager'} since {ctx.prev_snapshot_date}"
                          f"{status}; projects {fppg:.1f} fantasy points per game"),
                confidence=0.9 if urgent else 0.6, urgency=URGENCY_NOW if urgent else URGENCY_WEEK,
                positions=list(info.positions), projected_value=value,
            ))
        return opps
