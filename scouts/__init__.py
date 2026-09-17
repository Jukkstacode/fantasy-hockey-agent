"""Scouts: each one watches for a kind of change that creates fantasy value.

See SCOUTING_PLAN.md section 2. Every scout implements `scan(ctx)` and
returns a list of Opportunity objects.
"""

from scouts.base import Scout, ScoutContext, PlayerInfo
from scouts.injury_return import InjuryReturnScout
from scouts.goalie_injury import GoalieInjuryScout
from scouts.deployment import DeploymentScout
from scouts.regression import RegressionScout
from scouts.ownership import OwnershipScout
from scouts.news import NewsScout
from scouts.dailyfaceoff import DailyFaceoffScout
from scouts.hot_streak import HotStreakScout

ALL_SCOUTS = [
    InjuryReturnScout,
    GoalieInjuryScout,
    DeploymentScout,
    RegressionScout,
    HotStreakScout,
    OwnershipScout,
    DailyFaceoffScout,
    NewsScout,
]

__all__ = ["Scout", "ScoutContext", "PlayerInfo", "ALL_SCOUTS"] + [c.__name__ for c in ALL_SCOUTS]
