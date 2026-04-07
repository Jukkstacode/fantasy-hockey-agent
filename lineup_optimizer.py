"""Daily lineup optimization logic.

Decides which players to start vs. bench based on:
- Whether their NHL team plays today
- Injury/DTD status
- Player value from advanced stats (pyhockey)
- Matchup quality (opponent GAA)
"""

import logging
from datetime import datetime
from typing import Optional

from nhl_client import NHLClient
from yahoo_client import YahooClient
from stats_provider import StatsProvider
import config

logger = logging.getLogger(__name__)

# Standard Yahoo Fantasy Hockey positions
SKATER_POSITIONS = {"C", "LW", "RW", "D"}
GOALIE_POSITIONS = {"G"}
BENCH_POSITIONS = {"BN", "IR", "IR+", "NA"}
UTIL_POSITIONS = {"Util"}


class LineupOptimizer:
    """Optimizes daily fantasy hockey lineups."""

    def __init__(self, yahoo: YahooClient, nhl: NHLClient,
                 stats: Optional[StatsProvider] = None):
        self.yahoo = yahoo
        self.nhl = nhl
        self.stats = stats or StatsProvider()

    def optimize_lineup(self, date_str: str = None) -> list[dict]:
        """Generate optimal lineup changes for a given date."""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        logger.info("Optimizing lineup for %s", date_str)

        # Pre-load advanced stats (cached if already loaded)
        self.stats.load()

        # 1. Get current roster
        roster = self.yahoo.get_my_roster()
        logger.info("Roster has %d players", len(roster))

        # 2. Get today's NHL schedule
        teams_playing = self.nhl.get_teams_playing_today()
        logger.info("Teams playing today: %s", teams_playing)

        # 3. Get team stats for matchup quality
        team_stats = self.nhl.get_team_stats()

        # 4. Score and rank each player
        scored_players = []
        for player in roster:
            score = self._score_player(player, teams_playing, team_stats, date_str)
            scored_players.append({
                "player": player,
                "score": score,
                "name": self._player_name(player),
                "player_key": self._player_key(player),
                "eligible_positions": self._eligible_positions(player),
                "current_position": self._current_position(player),
                "nhl_team": self._player_team(player),
                "is_playing_today": self._player_team(player) in teams_playing,
                "display_position": self._display_position(player),
            })

        # 5. Assign optimal positions using greedy allocation
        changes = self._assign_positions(scored_players)

        if changes:
            logger.info("Lineup changes for %s:", date_str)
            for c in changes:
                logger.info("  %s -> %s", c.get("name", c["player_key"]), c["selected_position"])
        else:
            logger.info("No lineup changes needed for %s", date_str)

        return changes

    def _score_player(self, player, teams_playing: set, team_stats: dict,
                      date_str: str) -> float:
        """Score a player for today's lineup decision.

        Higher score = should start. Negative = should bench.

        Combines:
        - Schedule (must be playing today)
        - Injury status
        - Advanced stats value (from pyhockey)
        - Matchup quality
        """
        team = self._player_team(player)
        status = self._injury_status(player)

        # Not playing today = bench
        if team not in teams_playing:
            return -100.0

        # Injured / IR = definitely bench
        if status in ("IR", "IR+", "O", "NA"):
            return -200.0

        # Get the player's intrinsic value from pyhockey
        # This is the big upgrade — real player quality, not a flat 50
        name = self._player_name(player)
        position = self._display_position(player)
        intrinsic_value = self.stats.get_player_value(name, position)

        # Base score: 50 (just for playing) + intrinsic value
        # If we have no stats data (rookie/missing), fall back to 50
        if intrinsic_value > 0:
            score = 50.0 + intrinsic_value
        else:
            score = 50.0

        # DTD penalty (still might play)
        if status == "DTD":
            score -= 20.0

        # Matchup bonus: playing against a team that allows lots of goals
        opponent = self._get_opponent(team, teams_playing, date_str)
        if opponent and opponent in team_stats:
            opp_gaa = team_stats[opponent].get("goals_against_per_game", 3.0)
            if opp_gaa > config.MATCHUP_GAA_THRESHOLD:
                score += (opp_gaa - config.MATCHUP_GAA_THRESHOLD) * 5

        return score

    def _assign_positions(self, scored_players: list[dict]) -> list[dict]:
        """Greedily assign players to roster slots.

        Highest-scored players get active positions first.
        Lower-scored players go to bench.
        """
        # Sort by score descending — best players claim slots first
        scored_players.sort(key=lambda x: x["score"], reverse=True)

        # Standard Yahoo H2H hockey roster
        # TODO: Fetch actual roster positions from league settings
        available_slots = {
            "C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "Util": 1,
            "BN": 4, "IR": 2, "IR+": 2,
        }
        filled_slots = {pos: 0 for pos in available_slots}
        changes = []

        for sp in scored_players:
            eligible = sp["eligible_positions"]

            if sp["score"] < 0:
                # Player should be benched (not playing or injured)
                best_pos = "BN"
                if sp.get("is_ir"):
                    best_pos = "IR+"
            else:
                # Try to assign to best active position
                best_pos = None
                for pos in eligible:
                    if pos in BENCH_POSITIONS:
                        continue
                    if pos in available_slots and filled_slots.get(pos, 0) < available_slots.get(pos, 0):
                        best_pos = pos
                        break

                # Try Util if no specific position available
                if best_pos is None and "Util" in available_slots:
                    if filled_slots.get("Util", 0) < available_slots.get("Util", 0):
                        best_pos = "Util"

                # Fall back to bench
                if best_pos is None:
                    best_pos = "BN"

            filled_slots[best_pos] = filled_slots.get(best_pos, 0) + 1

            # Track if position changed
            if best_pos != sp["current_position"]:
                changes.append({
                    "player_key": sp["player_key"],
                    "selected_position": best_pos,
                    "name": sp["name"],
                    "score": sp["score"],
                })

        return changes

    def _get_opponent(self, team: str, teams_playing: set, date_str: str) -> str:
        """Find the opponent for a team on a given date."""
        games = self.nhl.get_schedule_for_date(date_str)
        for game in games:
            if game["home_team"] == team:
                return game["away_team"]
            if game["away_team"] == team:
                return game["home_team"]
        return None

    # ── Player attribute helpers ─────────────────────────────────

    def _player_name(self, player) -> str:
        try:
            return player.full_name
        except AttributeError:
            try:
                return f"{player.name.first} {player.name.last}"
            except AttributeError:
                return str(player)

    def _player_key(self, player) -> str:
        return getattr(player, "player_key", "")

    def _player_team(self, player) -> str:
        return getattr(player, "editorial_team_abbr", "")

    def _display_position(self, player) -> str:
        return getattr(player, "display_position", "")

    def _eligible_positions(self, player) -> list[str]:
        positions = getattr(player, "eligible_positions", [])
        if isinstance(positions, list):
            return [str(p) for p in positions]
        return [str(positions)] if positions else []

    def _current_position(self, player) -> str:
        try:
            sp = player.selected_position
            # selected_position can be a SelectedPosition object with a 'position' attr
            if hasattr(sp, 'position'):
                return sp.position
            return str(sp) if sp else "BN"
        except AttributeError:
            return "BN"

    def _injury_status(self, player) -> str:
        try:
            return player.status or ""
        except AttributeError:
            return ""