"""Yahoo Fantasy API client wrapping yfpy (read-only).

Yahoo's API only grants read access, so this client fetches
roster, league, and player data. The agent generates recommendations
that you execute manually in the Yahoo Fantasy app.
"""

import logging

from yfpy.query import YahooFantasySportsQuery

import config

logger = logging.getLogger(__name__)


class YahooClient:
    """Read-only wrapper around yfpy for Yahoo Fantasy Hockey."""

    def __init__(self):
        self.query = YahooFantasySportsQuery(
            league_id=config.YAHOO_LEAGUE_ID,
            game_code=config.YAHOO_GAME_CODE,
            yahoo_consumer_key=config.YAHOO_CONSUMER_KEY,
            yahoo_consumer_secret=config.YAHOO_CONSUMER_SECRET,
            env_file_location=config.AUTH_DIR,
            save_token_data_to_env_file=True,
            browser_callback=True,
        )

    # ── League info ──────────────────────────────────────────────

    def get_league_settings(self):
        """Fetch league settings (scoring, roster positions, etc.)."""
        settings = self.query.get_league_settings()
        logger.info("Fetched league settings")
        return settings

    def get_league_scoreboard(self):
        """Get current week's scoreboard."""
        return self.query.get_league_scoreboard()

    def get_league_standings(self):
        """Get current league standings."""
        return self.query.get_league_standings()

    # ── Team / roster ────────────────────────────────────────────

    def get_all_teams(self) -> list:
        """Fetch all teams in the league."""
        return self.query.get_league_teams()

    def get_my_roster(self) -> list:
        """Get the current roster for our team."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        roster = self.query.get_team_roster_player_info_by_date(
            config.YAHOO_TEAM_ID,
            today
        )
        return roster

    def get_roster_for_date(self, date_str: str) -> list:
        """Get roster for a specific date (YYYY-MM-DD)."""
        return self.query.get_team_roster_player_info_by_date(
            config.YAHOO_TEAM_ID,
            date_str
        )

    # ── Player data ──────────────────────────────────────────────

    def get_free_agents(self, position: str = None, count: int = 25) -> list:
        """Fetch league players.

        Args:
            position: Filter by position (C, LW, RW, D, G, or None for all)
            count: Number of players to return
        """
        players = self.query.get_league_players(
            player_count_limit=count,
            player_count_start=0,
        )
        if position:
            players = [
                p for p in players
                if hasattr(p, 'eligible_positions') and position in str(p.eligible_positions)
            ]
        return players

    def get_waivers(self, count: int = 25) -> list:
        """Fetch league players."""
        return self.query.get_league_players(
            player_count_limit=count,
            player_count_start=0,
        )

    def get_player_stats(self, player_key: str):
        """Get stats for a specific player."""
        return self.query.get_player_stats_for_season(player_key)
