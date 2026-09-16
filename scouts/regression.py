"""RegressionScout: shooters running cold (buy) or hot (sell / expect a dip).

Individual expected goals (ixG) measures shot quality. A player whose ixG
is well above his actual goals has been unlucky; points usually follow.
"""

import logging

import config
from opportunity import Opportunity, REGRESSION_BUY, RUNNING_HOT, URGENCY_WATCH
from scouts.base import Scout, ScoutContext

logger = logging.getLogger(__name__)


class RegressionScout(Scout):
    name = "regression"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        opps = []
        for row in ctx.stats.regression_table():
            if row["gp"] < config.SCOUT_REGRESSION_MIN_GP:
                continue
            diff = row["diff"]
            mine = ctx.on_my_roster(row["name"])
            if diff >= config.SCOUT_REGRESSION_XG_DIFF and not mine:
                conf = min(0.8, 0.35 + diff / 10.0)
                opps.append(Opportunity(
                    player_name=row["name"], nhl_team=row["team"], signal=REGRESSION_BUY,
                    evidence=f"{row['goals']} goals on {row['ixg']:.1f} expected ({row['gp']} GP): due for positive regression",
                    confidence=conf, urgency=URGENCY_WATCH,
                    projected_value=ctx.value_of(row["name"], row["position"]),
                ))
            elif diff <= -config.SCOUT_REGRESSION_XG_DIFF and mine:
                opps.append(Opportunity(
                    player_name=row["name"], nhl_team=row["team"], signal=RUNNING_HOT,
                    evidence=f"{row['goals']} goals on {row['ixg']:.1f} expected ({row['gp']} GP): running hot, expect a cooldown",
                    confidence=min(0.8, 0.35 + abs(diff) / 10.0), urgency=URGENCY_WATCH,
                    projected_value=ctx.value_of(row["name"], row["position"]),
                ))
        return opps
