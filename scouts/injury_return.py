"""InjuryReturnScout: players whose Yahoo status went from injured to active
since the last run. Valued on their last healthy season so a star coming
off long-term IR isn't scored as a zero.
"""

import logging

from opportunity import Opportunity, INJURY_RETURN, URGENCY_NOW, URGENCY_WEEK
from scouts.base import Scout, ScoutContext, INJURED_STATUSES, QUESTIONABLE_STATUSES

logger = logging.getLogger(__name__)


class InjuryReturnScout(Scout):
    name = "injury_return"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        if not ctx.prev_players:
            logger.info("No previous snapshot; injury-return detection starts next run")
            return []
        opps = []
        for norm, prev in ctx.prev_players.items():
            was_out = prev.status in INJURED_STATUSES
            was_dtd = prev.status in QUESTIONABLE_STATUSES
            if not (was_out or was_dtd):
                continue
            cur = ctx.lookup(prev.name)
            if cur is None or cur.is_injured or cur.status in QUESTIONABLE_STATUSES:
                continue
            if cur.on_my_roster:
                continue
            row, fallback = ctx.stats.get_skater_stats_with_fallback(cur.name)
            value = ctx.value_of(cur.name, "G" if cur.is_goalie else "")
            basis = "last season" if fallback else "this season"
            conf = 0.8 if was_out else 0.4
            evidence = (f"Status {prev.status_full or prev.status} on {ctx.prev_snapshot_date} → active now"
                        f"{' (' + prev.injury_note + ')' if prev.injury_note else ''}; "
                        f"value {value:.1f} based on {basis}")
            opps.append(Opportunity(
                player_name=cur.name, nhl_team=cur.team, signal=INJURY_RETURN,
                evidence=evidence, confidence=conf,
                urgency=URGENCY_NOW if was_out else URGENCY_WEEK,
                positions=list(cur.positions), projected_value=value,
            ))
        return opps
