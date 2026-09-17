"""The common currency of the scouting system.

Every scout emits Opportunity objects; the ranker merges and scores them;
the briefing renders them. Keeping one shape means a news hit and a
stats-derived signal about the same player can be combined.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

# Signal types. Positive ones are pickup candidates; the *_demotion / hot
# ones are warnings about players you already roster.
INJURY_RETURN = "injury_return"
GOALIE_BACKUP = "goalie_backup"
GOALIE_START_SHARE = "goalie_start_share"
PP_PROMOTION = "pp_promotion"
LINE_PROMOTION = "line_promotion"
REGRESSION_BUY = "regression_buy"
OWNERSHIP_SURGE = "ownership_surge"
NEWS = "news"
# Roster warnings
PP_DEMOTION = "pp_demotion"
LINE_DEMOTION = "line_demotion"
RUNNING_HOT = "running_hot"
INJURY_OUT = "injury_out"
HOT_STREAK = "hot_streak"
COLD_STREAK = "cold_streak"
DROPPED = "dropped"

WARNING_SIGNALS = {PP_DEMOTION, LINE_DEMOTION, RUNNING_HOT, INJURY_OUT, COLD_STREAK}

URGENCY_NOW = "now"
URGENCY_WEEK = "this_week"
URGENCY_WATCH = "watch"
_URGENCY_RANK = {URGENCY_NOW: 3, URGENCY_WEEK: 2, URGENCY_WATCH: 1}


@dataclass
class Opportunity:
    player_name: str
    nhl_team: str
    signal: str
    evidence: str
    confidence: float                       # 0..1
    urgency: str = URGENCY_WEEK
    positions: list[str] = field(default_factory=list)   # Yahoo eligible positions
    yahoo_key: Optional[str] = None
    projected_value: float = 0.0            # same scale as StatsProvider scores
    available: Optional[bool] = None        # None = unknown (Yahoo not reachable)
    rostered_by: Optional[str] = None       # other manager's team name, if owned
    on_my_roster: bool = False
    source_url: Optional[str] = None
    signals: list[str] = field(default_factory=list)      # filled when merged
    evidence_lines: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.signals:
            self.signals = [self.signal]
        if not self.evidence_lines:
            self.evidence_lines = [self.evidence]
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    @property
    def is_warning(self) -> bool:
        return self.signal in WARNING_SIGNALS

    @property
    def is_goalie(self) -> bool:
        return "G" in [p.upper() for p in self.positions]

    def merge(self, other: "Opportunity") -> "Opportunity":
        """Combine two signals about the same player.

        Confidence combines as independent evidence: 1 - (1-a)(1-b).
        Urgency takes the higher of the two.
        """
        self.confidence = 1.0 - (1.0 - self.confidence) * (1.0 - other.confidence)
        if _URGENCY_RANK.get(other.urgency, 0) > _URGENCY_RANK.get(self.urgency, 0):
            self.urgency = other.urgency
        for s in other.signals:
            if s not in self.signals:
                self.signals.append(s)
        for e in other.evidence_lines:
            if e not in self.evidence_lines:
                self.evidence_lines.append(e)
        self.projected_value = max(self.projected_value, other.projected_value)
        self.source_url = self.source_url or other.source_url
        self.yahoo_key = self.yahoo_key or other.yahoo_key
        if not self.positions:
            self.positions = list(other.positions)
        if self.available is None:
            self.available = other.available
        self.rostered_by = self.rostered_by or other.rostered_by
        self.on_my_roster = self.on_my_roster or other.on_my_roster
        return self

    def to_dict(self) -> dict:
        return asdict(self)
