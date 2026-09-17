"""Shared context and base class for scouts."""

import logging
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

from opportunity import Opportunity
from stats_provider import StatsProvider, name_keys, _normalize_name
from nhl_client import NHLClient
from state_store import StateStore

logger = logging.getLogger(__name__)

# Yahoo status codes that mean "not playing"
INJURED_STATUSES = {"IR", "IR-LT", "IR-NR", "O", "NA", "SUSP", "IL"}
QUESTIONABLE_STATUSES = {"DTD", "D"}


@dataclass
class PlayerInfo:
    """What we know about a player from Yahoo (one snapshot)."""
    name: str
    key: str = ""
    team: str = ""
    positions: list[str] = field(default_factory=list)
    status: str = ""                 # "", "DTD", "IR", "O", ...
    status_full: str = ""
    injury_note: str = ""
    percent_owned: float = 0.0
    percent_owned_delta: float = 0.0
    ownership_type: str = ""         # "freeagents" | "waivers" | "team"
    owner_team: str = ""
    on_my_roster: bool = False
    nhl_id: str = ""

    @property
    def available(self) -> bool:
        return self.ownership_type in ("freeagents", "waivers")

    @property
    def is_injured(self) -> bool:
        return self.status in INJURED_STATUSES

    @property
    def is_goalie(self) -> bool:
        return "G" in [p.upper() for p in self.positions]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlayerInfo":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ScoutContext:
    """Everything a scout needs. Built once per run by main."""

    def __init__(self, stats: StatsProvider, nhl: NHLClient, store: StateStore,
                 as_of: Optional[date] = None,
                 players: Optional[list[PlayerInfo]] = None,
                 yahoo_ok: bool = False):
        self.stats = stats
        self.nhl = nhl
        self.store = store
        self.as_of = as_of or date.today()
        self.yahoo_ok = yahoo_ok
        self.default_available: Optional[bool] = None   # pre-draft: unrostered players are draftable
        self.players: dict[str, PlayerInfo] = {}       # name key -> info
        self.my_roster: list[PlayerInfo] = []
        self.extras: dict = {}                          # scout-to-scout shared data
        try:
            from trend import TrendProvider
            self.extras["trends"] = TrendProvider(stats, pool=self.trend_pool)
        except Exception as e:
            logger.warning("Trend provider unavailable: %s", e)
        for p in players or []:
            self.add_player(p)
        prev = store.load("players", {})
        self.prev_snapshot_date: str = prev.get("_date", "")
        self.prev_players: dict[str, PlayerInfo] = {
            k: PlayerInfo.from_dict(v) for k, v in prev.items() if not k.startswith("_")
        }

    # ── player universe ──────────────────────────────────────────

    def add_player(self, p: PlayerInfo):
        for key in name_keys(p.name):
            self.players.setdefault(key, p)
        if p.on_my_roster and p not in self.my_roster:
            self.my_roster.append(p)

    def lookup(self, name: str) -> Optional[PlayerInfo]:
        for key in name_keys(name):
            if key in self.players:
                return self.players[key]
        return None

    def lookup_prev(self, name: str) -> Optional[PlayerInfo]:
        return self.prev_players.get(_normalize_name(name))

    def on_my_roster(self, name: str) -> bool:
        p = self.lookup(name)
        return bool(p and p.on_my_roster)

    def snapshot(self) -> dict:
        """Serializable snapshot of the current player universe for next run."""
        out = {"_date": self.as_of.isoformat()}
        seen = set()
        for p in self.players.values():
            if id(p) in seen:
                continue
            seen.add(id(p))
            out[_normalize_name(p.name)] = p.to_dict()
        return out

    def trend_pool(self) -> list[str]:
        """Players worth fetching game logs for: everyone we know about plus the
        top of the league by value (covers hot free agents outside the Yahoo scan)."""
        names, seen = [], set()
        for p in list(self.players.values()):
            if id(p) in seen:
                continue
            seen.add(id(p))
            names.append(p.name)
        names += self.stats.top_skaters(150)
        names += self.stats.top_goalies(30)
        return list(dict.fromkeys(names))

    def decorate(self, opp: Opportunity) -> Opportunity:
        """Fill Yahoo availability/position fields onto an opportunity."""
        info = self.lookup(opp.player_name)
        if info:
            opp.yahoo_key = opp.yahoo_key or info.key
            opp.positions = opp.positions or list(info.positions)
            opp.nhl_team = opp.nhl_team or info.team
            opp.available = info.available
            opp.on_my_roster = info.on_my_roster
            if info.ownership_type == "team" and not info.on_my_roster:
                opp.rostered_by = info.owner_team
        elif self.default_available is not None:
            opp.available = self.default_available
        elif self.yahoo_ok:
            # Yahoo reachable but player not in the scanned pool: probably a
            # deep free agent outside the top-N scan, or a name mismatch.
            opp.available = None
        return opp

    def value_of(self, name: str, position: str = "") -> float:
        return self.stats.get_player_value(name, position)

    def games_next_week(self, team: str) -> int:
        try:
            return len(self.nhl.get_upcoming_games(team, days=7, start_date=self.as_of.isoformat()))
        except Exception:
            return 0


class Scout:
    """Base class. Subclasses set `name` and implement `scan`."""

    name = "scout"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        raise NotImplementedError

    def safe_scan(self, ctx: ScoutContext) -> list[Opportunity]:
        try:
            opps = self.scan(ctx)
            logger.info("%s: %d signals", self.name, len(opps))
            return [ctx.decorate(o) for o in opps]
        except Exception as e:
            logger.exception("%s failed: %s", self.name, e)
            return []
