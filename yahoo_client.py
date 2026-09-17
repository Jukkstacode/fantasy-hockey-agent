"""Yahoo Fantasy API client wrapping yfpy.

Reads roster, league settings, available players and ownership data.
Write access (lineup PUT, add/drop POST) needs the fspt-w scope, which
Yahoo currently grants only to whitelisted apps; this app has Read only,
so the agent stays advisory: it recommends, you click.
"""

import logging
import os
import sys
from datetime import datetime
from typing import Optional

from yfpy.query import YahooFantasySportsQuery
from yfpy.models import Player

import config
from scouts.base import PlayerInfo

logger = logging.getLogger(__name__)

YAHOO_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
PAGE = 25   # Yahoo's hard cap per players request


def _headless() -> bool:
    """True when there's no browser to open (SSH session, Docker, cron)."""
    flag = os.getenv("YAHOO_BROWSER_CALLBACK", "").lower()
    if flag in ("1", "true", "yes"):
        return False
    if flag in ("0", "false", "no"):
        return True
    return not os.environ.get("DISPLAY") or not sys.stdin.isatty()


class YahooClient:
    """Wrapper around yfpy for Yahoo Fantasy Hockey."""

    def __init__(self, fresh_auth: bool = False):
        if fresh_auth:
            self._retire_saved_token()
        self.query = YahooFantasySportsQuery(
            league_id=config.YAHOO_LEAGUE_ID,
            game_code=config.YAHOO_GAME_CODE,
            yahoo_consumer_key=config.YAHOO_CONSUMER_KEY,
            yahoo_consumer_secret=config.YAHOO_CONSUMER_SECRET,
            env_file_location=config.AUTH_DIR,
            save_token_data_to_env_file=True,
            browser_callback=not _headless(),
        )
        self._scoring: Optional[dict] = None
        self._league_key: Optional[str] = None

    @staticmethod
    def _retire_saved_token():
        """Move the saved token aside so yfpy runs the full OAuth flow again."""
        token_file = config.AUTH_DIR / ".env"
        if token_file.exists():
            backup = config.AUTH_DIR / f".env.bak-{datetime.now():%Y%m%d-%H%M%S}"
            token_file.rename(backup)
            logger.info("Saved previous Yahoo token to %s", backup.name)
        for var in ("YAHOO_ACCESS_TOKEN", "YAHOO_REFRESH_TOKEN", "YAHOO_GUID",
                    "YAHOO_TOKEN_TIME", "YAHOO_TOKEN_TYPE"):
            os.environ.pop(var, None)

    @property
    def league_key(self) -> str:
        if self._league_key is None:
            self._league_key = self.query.get_league_key()
        return self._league_key

    # ── League info ──────────────────────────────────────────────

    def get_league_settings(self):
        """Fetch league settings (scoring, roster positions, etc.)."""
        settings = self.query.get_league_settings()
        logger.info("Fetched league settings")
        return settings

    def get_league_scoring(self) -> dict:
        """Parsed league settings: stat categories and roster slots.

        Returns:
            {"categories": ["G", "A", ...], "roster_positions": {"C": 2, ...},
             "scoring_type": "head", "uses_faab": bool, ...}
        """
        if self._scoring is not None:
            return self._scoring
        s = self.get_league_settings()
        categories = []
        for st in getattr(getattr(s, "stat_categories", None), "stats", []) or []:
            if getattr(st, "is_only_display_stat", 0) in (1, "1", True):
                continue
            name = getattr(st, "display_name", None) or getattr(st, "abbr", None) or getattr(st, "name", "")
            if name:
                categories.append(str(name))
        positions = {}
        for rp in getattr(s, "roster_positions", []) or []:
            pos = getattr(rp, "position", None)
            if pos:
                positions[str(pos)] = int(getattr(rp, "count", 0) or 0)
        self._scoring = {
            "categories": categories,
            "roster_positions": positions,
            "scoring_type": getattr(s, "scoring_type", ""),
            "uses_faab": bool(getattr(s, "uses_faab", 0)),
            "waiver_rule": getattr(s, "waiver_rule", ""),
            "max_weekly_adds": getattr(s, "max_weekly_adds", None),
        }
        logger.info("League categories: %s | roster: %s", categories, positions)
        return self._scoring

    def get_league_scoreboard(self):
        return self.query.get_league_scoreboard()

    def get_league_standings(self):
        return self.query.get_league_standings()

    def get_recent_transactions(self) -> list:
        """League add/drop/trade transactions (most recent first)."""
        return self.query.get_league_transactions()

    # ── Team / roster ────────────────────────────────────────────

    def get_all_teams(self) -> list:
        return self.query.get_league_teams()

    def get_my_roster(self) -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.get_roster_for_date(today)

    def get_roster_for_date(self, date_str: str) -> list:
        return self.query.get_team_roster_player_info_by_date(config.YAHOO_TEAM_ID, date_str)

    # ── Player data ──────────────────────────────────────────────

    def get_available_players(self, count: int = 300, status: str = "A",
                              sort: str = "AR") -> list[Player]:
        """Players not on any roster in this league, best first.

        Args:
            count: how many to fetch (pages of 25 under the hood)
            status: "A" = all available, "FA" = free agents only, "W" = waivers only
            sort: "AR" actual rank, "OR" overall rank, "PTS" fantasy points
        """
        out: list[Player] = []
        start = 0
        while start < count:
            n = min(PAGE, count - start)
            url = (f"{YAHOO_BASE}/league/{self.league_key}/players;status={status};"
                   f"sort={sort};start={start};count={n};out=ownership,percent_owned")
            try:
                page = self.query.query(url, ["league", "players"], Player)
            except Exception as e:
                logger.warning("Available-players page at %d failed: %s", start, e)
                break
            if not page:
                break
            if not isinstance(page, list):
                page = [page]
            out.extend(page)
            if len(page) < n:
                break
            start += n
        logger.info("Fetched %d available players (status=%s)", len(out), status)
        return out

    def get_free_agents(self, position: str = None, count: int = 25) -> list:
        players = self.get_available_players(count=count)
        if position:
            players = [p for p in players
                       if position in [str(x) for x in (getattr(p, "eligible_positions", []) or [])]]
        return players

    def get_player_stats(self, player_key: str):
        return self.query.get_player_stats_for_season(player_key)

    # ── Player universe for the scouts ───────────────────────────

    def get_player_universe(self, pool: int = None) -> list[PlayerInfo]:
        """My roster plus the top available players, as PlayerInfo records."""
        pool = pool or config.SCOUT_AVAILABLE_POOL
        infos = []
        for p in self.get_my_roster():
            info = player_to_info(p)
            info.on_my_roster = True
            info.ownership_type = "team"
            infos.append(info)
        for p in self.get_available_players(count=pool):
            info = player_to_info(p)
            if not info.ownership_type:
                info.ownership_type = "freeagents"
            infos.append(info)
        return infos


# ── yfpy Player -> PlayerInfo ────────────────────────────────────

def player_name(player) -> str:
    try:
        return player.full_name
    except AttributeError:
        try:
            return f"{player.name.first} {player.name.last}"
        except AttributeError:
            return str(player)


def eligible_positions(player) -> list[str]:
    positions = getattr(player, "eligible_positions", []) or []
    if not isinstance(positions, list):
        positions = [positions]
    out = []
    for p in positions:
        p = getattr(p, "position", p)
        out.append(str(p))
    return out


def player_to_info(p) -> PlayerInfo:
    pct = getattr(p, "percent_owned", None)
    pct_value = getattr(pct, "value", None) if pct is not None else getattr(p, "percent_owned_value", None)
    pct_delta = getattr(pct, "delta", None) if pct is not None else None
    own = getattr(p, "ownership", None)
    return PlayerInfo(
        name=player_name(p),
        key=getattr(p, "player_key", "") or "",
        team=getattr(p, "editorial_team_abbr", "") or "",
        positions=eligible_positions(p),
        status=str(getattr(p, "status", "") or ""),
        status_full=str(getattr(p, "status_full", "") or ""),
        injury_note=str(getattr(p, "injury_note", "") or ""),
        percent_owned=float(pct_value or 0.0),
        percent_owned_delta=float(pct_delta or 0.0),
        ownership_type=str(getattr(own, "ownership_type", "") or "") if own is not None else "",
        owner_team=str(getattr(own, "owner_team_name", "") or "") if own is not None else "",
    )
