"""OwnershipScout: available players whose Yahoo ownership is surging.

A lagging indicator, but it catches whatever the other scouts missed and
tells you how long the window is.
"""

import logging

import config
from opportunity import Opportunity, OWNERSHIP_SURGE, URGENCY_WEEK
from scouts.base import Scout, ScoutContext

logger = logging.getLogger(__name__)


class OwnershipScout(Scout):
    name = "ownership"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        opps, seen = [], set()
        for info in ctx.players.values():
            if id(info) in seen or not info.available:
                continue
            seen.add(id(info))
            prev = ctx.lookup_prev(info.name)
            run_delta = (info.percent_owned - prev.percent_owned) if prev else 0.0
            delta = max(run_delta, info.percent_owned_delta or 0.0)
            if delta < config.SCOUT_OWNERSHIP_JUMP:
                continue
            since = f"since {ctx.prev_snapshot_date}" if prev and run_delta >= delta else "this week"
            opps.append(Opportunity(
                player_name=info.name, nhl_team=info.team, signal=OWNERSHIP_SURGE,
                evidence=f"{info.percent_owned:.0f}% owned, up {delta:.0f} points {since}",
                confidence=min(0.7, 0.3 + delta / 50.0), urgency=URGENCY_WEEK,
                positions=list(info.positions),
                projected_value=ctx.value_of(info.name, "G" if info.is_goalie else ""),
            ))
        return opps
