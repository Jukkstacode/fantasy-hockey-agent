"""NHL API client for schedule, injuries, and player stats.

Uses the undocumented but public api-web.nhle.com endpoints.
"""

import logging
from datetime import datetime, timedelta

import requests

import config

logger = logging.getLogger(__name__)

# Cache for expensive lookups within a single run
_cache = {}


class NHLClient:
    """Client for the NHL's public API (api-web.nhle.com)."""

    def __init__(self):
        self.base = config.NHL_API_BASE
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "FantasyHockeyAgent/1.0",
        })

    def _get(self, path: str, params: dict = None) -> dict:
        """Make a GET request to the NHL API."""
        url = f"{self.base}{path}"
        cache_key = f"{url}:{params}"
        if cache_key in _cache:
            return _cache[cache_key]

        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _cache[cache_key] = data
        return data

    # ── Schedule ─────────────────────────────────────────────────

    def get_schedule_for_date(self, date_str: str) -> list[dict]:
        """Get all NHL games for a specific date.

        Args:
            date_str: Date in YYYY-MM-DD format

        Returns:
            List of game dicts with home/away teams, time, etc.
        """
        data = self._get(f"/schedule/{date_str}")
        games = []
        for game_week in data.get("gameWeek", []):
            if game_week.get("date") == date_str:
                for game in game_week.get("games", []):
                    games.append({
                        "game_id": game.get("id"),
                        "home_team": game.get("homeTeam", {}).get("abbrev"),
                        "away_team": game.get("awayTeam", {}).get("abbrev"),
                        "start_time": game.get("startTimeUTC"),
                        "game_state": game.get("gameState"),
                    })
        return games

    def get_weekly_schedule(self, start_date: str = None, days: int = 7) -> dict:
        """Get schedule for a week, returning games-per-team count.

        Returns:
            Dict mapping team abbreviation to number of games that week.
        """
        if start_date is None:
            start_date = datetime.now().strftime("%Y-%m-%d")

        team_games = {}
        start = datetime.strptime(start_date, "%Y-%m-%d")

        for i in range(days):
            date_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            games = self.get_schedule_for_date(date_str)
            for game in games:
                for team in [game["home_team"], game["away_team"]]:
                    if team:
                        team_games[team] = team_games.get(team, 0) + 1

        return team_games

    def get_teams_playing_today(self) -> set[str]:
        """Get set of team abbreviations playing today."""
        today = datetime.now().strftime("%Y-%m-%d")
        games = self.get_schedule_for_date(today)
        teams = set()
        for game in games:
            teams.add(game["home_team"])
            teams.add(game["away_team"])
        return teams

    # ── Team stats (for matchup evaluation) ──────────────────────

    def get_team_stats(self) -> dict:
        """Get league-wide team stats for matchup evaluation.

        Returns:
            Dict mapping team abbrev to stats dict (GAA, save%, etc.)
        """
        data = self._get("/standings/now")
        team_stats = {}
        for record in data.get("standings", []):
            abbrev = record.get("teamAbbrev", {}).get("default", "")
            team_stats[abbrev] = {
                "wins": record.get("wins", 0),
                "losses": record.get("losses", 0),
                "ot_losses": record.get("otLosses", 0),
                "goals_for_per_game": record.get("goalFor", 0) / max(record.get("gamesPlayed", 1), 1),
                "goals_against_per_game": record.get("goalAgainst", 0) / max(record.get("gamesPlayed", 1), 1),
                "points": record.get("points", 0),
            }
        return team_stats

    # ── Player info ──────────────────────────────────────────────

    def get_player_landing(self, player_id: int) -> dict:
        """Get player info from the landing page endpoint.

        This gives current season stats, bio, and recent game log.
        """
        return self._get(f"/player/{player_id}/landing")

    def get_player_game_log(self, player_id: int, season: str = None) -> list:
        """Get a player's game log for the season.

        Args:
            player_id: NHL player ID
            season: Season string like '20252026'. Defaults to current.
        """
        if season is None:
            now = datetime.now()
            year = now.year if now.month >= 9 else now.year - 1
            season = f"{year}{year + 1}"

        data = self._get(f"/player/{player_id}/game-log/{season}/2")
        return data.get("gameLog", [])

    # ── Rosters ──────────────────────────────────────────────────

    def get_team_abbrevs(self) -> list[str]:
        """All current NHL team abbreviations (from the standings feed)."""
        data = self._get("/standings/now")
        teams = []
        for record in data.get("standings", []):
            abbrev = record.get("teamAbbrev", {}).get("default", "")
            if abbrev:
                teams.append(abbrev)
        return sorted(teams)

    def get_team_roster(self, team: str) -> dict[str, list[dict]]:
        """Current active roster for a team.

        Returns:
            {"forwards": [...], "defensemen": [...], "goalies": [...]} where each
            entry is {"id", "name", "position"}. Empty lists on failure.
        """
        try:
            data = self._get(f"/roster/{team}/current")
        except Exception as e:
            logger.warning("Roster fetch failed for %s: %s", team, e)
            return {"forwards": [], "defensemen": [], "goalies": []}

        def simplify(group):
            out = []
            for p in data.get(group, []):
                first = p.get("firstName", {}).get("default", "")
                last = p.get("lastName", {}).get("default", "")
                out.append({
                    "id": p.get("id"),
                    "name": f"{first} {last}".strip(),
                    "position": p.get("positionCode", ""),
                })
            return out

        return {
            "forwards": simplify("forwards"),
            "defensemen": simplify("defensemen"),
            "goalies": simplify("goalies"),
        }

    def get_team_goalies(self, team: str) -> list[dict]:
        """Goalies currently on a team's active roster."""
        return self.get_team_roster(team).get("goalies", [])

    def get_upcoming_games(self, team: str, days: int = 7,
                           start_date: str = None) -> list[str]:
        """Dates (YYYY-MM-DD) a team plays in the next `days` days."""
        if start_date is None:
            start_date = datetime.now().strftime("%Y-%m-%d")
        start = datetime.strptime(start_date, "%Y-%m-%d")
        dates = []
        for i in range(days):
            date_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            for game in self.get_schedule_for_date(date_str):
                if team in (game["home_team"], game["away_team"]):
                    dates.append(date_str)
        return dates

    # ── Injuries ─────────────────────────────────────────────────

    def get_injuries(self) -> list[dict]:
        """Get current injury report across all teams.

        Returns:
            List of dicts with player name, team, injury status, etc.
        """
        # The NHL API doesn't have a clean single injury endpoint,
        # but we can get roster status from the club-schedule endpoint
        # or use the roster endpoint per team. For now, we'll rely on
        # Yahoo's injury data as it's more fantasy-relevant.
        logger.info("Injury data will come from Yahoo roster status flags")
        return []

    # ── Back-to-back detection ───────────────────────────────────

    def get_back_to_backs(self, start_date: str = None, days: int = 7) -> dict:
        """Find teams playing on consecutive days.

        Returns:
            Dict mapping team abbrev to list of back-to-back date pairs.
        """
        if start_date is None:
            start_date = datetime.now().strftime("%Y-%m-%d")

        start = datetime.strptime(start_date, "%Y-%m-%d")
        team_game_dates = {}

        for i in range(days):
            date_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            games = self.get_schedule_for_date(date_str)
            for game in games:
                for team in [game["home_team"], game["away_team"]]:
                    if team:
                        if team not in team_game_dates:
                            team_game_dates[team] = []
                        team_game_dates[team].append(date_str)

        b2b = {}
        for team, dates in team_game_dates.items():
            dates.sort()
            pairs = []
            for i in range(len(dates) - 1):
                d1 = datetime.strptime(dates[i], "%Y-%m-%d")
                d2 = datetime.strptime(dates[i + 1], "%Y-%m-%d")
                if (d2 - d1).days == 1:
                    pairs.append((dates[i], dates[i + 1]))
            if pairs:
                b2b[team] = pairs

        return b2b
