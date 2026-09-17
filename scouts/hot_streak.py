"""HotStreakScout: players whose fantasy points per game over the last few
games are well above their prior stretch (pickups), and roster players
going cold (warnings). Purely statistical; complements the news scout.
"""

import logging

import config
from opportunity import Opportunity, HOT_STREAK, COLD_STREAK, URGENCY_WEEK, URGENCY_WATCH
from scouts.base import Scout, ScoutContext

logger = logging.getLogger(__name__)


class HotStreakScout(Scout):
    name = "hot_streak"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        trends = ctx.extras.get("trends")
        if trends is None:
            return []
        opps = []
        for t in trends.hot_skaters():
            if ctx.on_my_roster(t.name):
                continue
            sus = t.sustainable
            conf = 0.45 + min(0.3, (t.ratio - 1.0) * 0.15) + (0.15 if sus else 0.0)
            opps.append(Opportunity(
                player_name=t.name, nhl_team=t.team, signal=HOT_STREAK,
                evidence=f"Heating up: {t.label()}", confidence=min(0.9, conf),
                urgency=URGENCY_WEEK, projected_value=ctx.value_of(t.name, t.position),
            ))
        for t in trends.cold_skaters():
            if not ctx.on_my_roster(t.name):
                continue
            opps.append(Opportunity(
                player_name=t.name, nhl_team=t.team, signal=COLD_STREAK,
                evidence=f"Cooling off: {t.label()}", confidence=0.5, urgency=URGENCY_WATCH,
                projected_value=ctx.value_of(t.name, t.position),
            ))
        return opps
