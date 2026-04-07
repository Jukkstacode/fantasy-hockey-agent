"""Advanced stats provider using pyhockey (MoneyPuck/NaturalStatTrick).

Provides real player valuation using xGoals, points-per-hour, and other
advanced metrics — much better than Yahoo's ownership percentage.

The data is queried once per run and cached in memory to avoid hitting
the underlying database for every player lookup.
"""

import logging
import unicodedata
from datetime import datetime
from typing import Optional

import pyhockey

logger = logging.getLogger(__name__)


def _current_season() -> int:
    """Get the current NHL season year.

    pyhockey uses the year of season start (e.g., 2025 = 2025-26 season).
    NHL seasons start in October, so anything before October is the previous
    season's year.
    """
    now = datetime.now()
    return now.year if now.month >= 9 else now.year - 1


class StatsProvider:
    """Wraps pyhockey with caching for fast repeated lookups."""

    def __init__(self, season: Optional[int] = None):
        self.season = season or _current_season()
        self._skater_cache: dict[str, dict] = {}
        self._goalie_cache: dict[str, dict] = {}
        self._loaded = False

    def load(self):
        """Pre-load all skater and goalie data for the current season.

        This makes ONE query to pyhockey instead of one per player.
        """
        if self._loaded:
            return

        logger.info("Loading advanced stats from pyhockey for season %s...", self.season)

        try:
            # Load all skaters across the league
            skaters = pyhockey.skater_seasons(season=self.season, situation='all')
            for row in skaters.iter_rows(named=True):
                # Store under multiple keys for fuzzy matching
                for key in self._name_keys(row['name']):
                    self._skater_cache[key] = row
            unique_skaters = len(set(
                r['name'] for r in self._skater_cache.values()
            ))
            logger.info("  Loaded %d skater records (%d unique names)",
                        len(self._skater_cache), unique_skaters)

            # Load all goalies
            goalies = pyhockey.goalie_seasons(season=self.season)
            for row in goalies.iter_rows(named=True):
                for key in self._name_keys(row['name']):
                    self._goalie_cache[key] = row
            unique_goalies = len(set(
                r['name'] for r in self._goalie_cache.values()
            ))
            logger.info("  Loaded %d goalie records (%d unique names)",
                        len(self._goalie_cache), unique_goalies)

            self._loaded = True
        except Exception as e:
            logger.warning("Failed to load advanced stats: %s", e)
            logger.warning("Falling back to basic player valuation")
            self._loaded = True  # Mark loaded so we don't retry

    def _normalize_name(self, name: str) -> str:
        """Normalize a player name: strip accents, lowercase, trim."""
        if not name:
            return ""
        normalized = unicodedata.normalize('NFKD', name)
        ascii_name = normalized.encode('ascii', 'ignore').decode('ascii')
        return ascii_name.lower().strip()

    def _name_keys(self, name: str) -> list[str]:
        """Generate multiple lookup keys for a name to enable fuzzy matching.

        Returns keys like:
          - 'juraj slafkovsky'  (normalized full)
          - 'slafkovsky j'      (last + first initial)
          - 'slafkovsk j'       (truncated last + first initial — handles MoneyPuck bug)
        """
        if not name:
            return []

        normalized = self._normalize_name(name)
        if not normalized:
            return []

        keys = [normalized]

        parts = normalized.split()
        if len(parts) >= 2:
            first = parts[0]
            last = parts[-1]
            # Last name + first initial
            keys.append(f"{last} {first[0]}")
            # Truncated last name + first initial (handles MoneyPuck name bugs
            # where the final character of a name is sometimes dropped)
            if len(last) > 4:
                keys.append(f"{last[:-1]} {first[0]}")

        return keys

    def _lookup(self, cache: dict, player_name: str) -> Optional[dict]:
        """Try multiple key variations to find a player in the cache."""
        for key in self._name_keys(player_name):
            if key in cache:
                return cache[key]
        return None

    def get_skater_value(self, player_name: str) -> float:
        """Calculate a fantasy value score for a skater.

        Combines points-per-hour (production rate) and xGoalsForPerHour
        (chance creation) for a stable, predictive value.

        Returns 0.0 if the player isn't found.
        """
        self.load()
        stats = self._lookup(self._skater_cache, player_name)
        if not stats:
            return 0.0

        points_per_hour = stats.get('pointsPerHour') or 0.0
        xgoals_per_hour = stats.get('xGoalsForPerHour') or 0.0
        games_played = stats.get('gamesPlayed') or 0

        # Need a minimum sample size to trust the rate stats
        if games_played < 5:
            return 0.0

        # Weight points-per-hour higher (it's the actual production)
        # but boost with xGoals (predictive of future production)
        score = (points_per_hour * 10.0) + (xgoals_per_hour * 2.0)
        return round(score, 2)

    def get_goalie_value(self, player_name: str) -> float:
        """Calculate a fantasy value score for a goalie.

        Uses Goals Saved Above Expected (xGoals - actual goals) per game.
        Positive = above-average goalie.
        """
        self.load()
        stats = self._lookup(self._goalie_cache, player_name)
        if not stats:
            return 0.0

        games_played = stats.get('gamesPlayed') or 0
        if games_played < 3:
            return 0.0

        x_goals_against = stats.get('xGoals') or 0.0
        actual_goals = stats.get('goals') or 0.0

        # Goals Saved Above Expected — positive is good
        gsax = x_goals_against - actual_goals
        gsax_per_game = gsax / games_played

        # Convert to a 0-100ish scale, boost with games played
        # (a workhorse goalie is more valuable than a backup with great rate)
        score = (gsax_per_game * 20.0) + (games_played * 0.5)
        return round(score, 2)

    def get_player_value(self, player_name: str, position: str) -> float:
        """Get a value score for any player based on position."""
        if 'G' in position.upper():
            return self.get_goalie_value(player_name)
        return self.get_skater_value(player_name)

    def get_skater_stats(self, player_name: str) -> Optional[dict]:
        """Get the raw stats dict for a skater (for debugging/display)."""
        self.load()
        return self._lookup(self._skater_cache, player_name)

    def get_goalie_stats(self, player_name: str) -> Optional[dict]:
        """Get the raw stats dict for a goalie."""
        self.load()
        return self._lookup(self._goalie_cache, player_name)