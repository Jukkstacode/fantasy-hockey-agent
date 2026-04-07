"""Waiver wire and free agent evaluation using advanced stats.

Uses pyhockey (MoneyPuck/NaturalStatTrick data) for real player valuation
based on points-per-hour, xGoals, and other predictive metrics.

Filters to actual free agents by checking which players are on rosters
across the entire league.
"""

import logging

from nhl_client import NHLClient
from yahoo_client import YahooClient
from stats_provider import StatsProvider
import config

logger = logging.getLogger(__name__)


class WaiverManager:
    """Evaluates and recommends waiver wire / free agent pickups."""

    def __init__(self, yahoo: YahooClient, nhl: NHLClient,
                 stats: StatsProvider = None):
        self.yahoo = yahoo
        self.nhl = nhl
        self.stats = stats or StatsProvider()
        self._rostered_keys: set[str] = set()

    def evaluate_moves(self) -> list[dict]:
        """Evaluate potential waiver/FA moves.

        Returns a small, high-quality list of recommended moves.
        """
        logger.info("Evaluating waiver wire moves...")

        # 1. Pre-load advanced stats
        self.stats.load()

        # 2. Build set of rostered player keys across the entire league
        self._build_rostered_set()

        # 3. Score current roster
        roster = self.yahoo.get_my_roster()
        roster_scored = self._score_roster(roster)
        logger.info("Scored %d roster players", len(roster_scored))

        # 4. Get free agents (filtered to actually-available players)
        free_agents = self._get_free_agents(count=200)
        fa_scored = self._score_free_agents(free_agents)
        logger.info("Scored %d free agents", len(fa_scored))

        # 5. Get weekly schedule density
        schedule = self.nhl.get_weekly_schedule()

        # 6. Find best upgrade per roster slot (not all combinations)
        recommendations = self._find_best_upgrades(
            roster_scored, fa_scored, schedule
        )

        # 7. Cap at max recommendations
        recommendations = recommendations[:config.WAIVER_MAX_ADDS_PER_WEEK]

        if recommendations:
            logger.info("Recommended %d moves", len(recommendations))
        else:
            logger.info("No waiver moves recommended")

        return recommendations

    def _build_rostered_set(self):
        """Collect player keys from every team in the league."""
        logger.info("Collecting rostered players across league...")
        try:
            teams = self.yahoo.get_all_teams()
            for team in teams:
                team_id = getattr(team, 'team_id', None)
                if team_id is None:
                    continue
                try:
                    roster = self.yahoo.get_roster_for_date(
                        __import__('datetime').datetime.now().strftime("%Y-%m-%d")
                    )
                    # Note: get_roster_for_date uses our team. We need a way
                    # to fetch other teams' rosters. For now, just use our own
                    # team to filter ourselves out.
                except Exception:
                    pass

            # Simpler approach: fetch each team's roster directly via the query
            for team in teams:
                team_id = getattr(team, 'team_id', None)
                if team_id is None:
                    continue
                try:
                    players = self.yahoo.query.get_team_roster_player_info_by_date(
                        team_id,
                        __import__('datetime').datetime.now().strftime("%Y-%m-%d")
                    )
                    for p in players:
                        key = getattr(p, 'player_key', None)
                        if key:
                            self._rostered_keys.add(key)
                except Exception as e:
                    logger.debug("Couldn't fetch roster for team %s: %s", team_id, e)

            logger.info("Found %d rostered players league-wide",
                        len(self._rostered_keys))
        except Exception as e:
            logger.warning("Failed to build rostered set: %s", e)
            logger.warning("Free agent filtering will be limited")

    def _get_free_agents(self, count: int = 200) -> list:
        """Fetch league players and filter to actual free agents."""
        all_players = self.yahoo.query.get_league_players(
            player_count_limit=count,
            player_count_start=0,
        )

        free_agents = []
        for p in all_players:
            key = getattr(p, 'player_key', None)
            status = getattr(p, 'status', '') or ''

            # Skip if rostered
            if key and key in self._rostered_keys:
                continue
            # Skip injured/inactive players (NA = Not Active, IR = Injured Reserve)
            if status in ('NA', 'IR', 'IR+', 'O'):
                continue

            free_agents.append(p)

        return free_agents

    def _score_roster(self, roster: list) -> list[dict]:
        """Score each roster player with advanced stats."""
        scored = []
        for player in roster:
            name = self._player_name(player)
            position = self._display_position(player)
            score = self.stats.get_player_value(name, position)
            scored.append({
                "player": player,
                "name": name,
                "player_key": self._player_key(player),
                "positions": self._eligible_positions(player),
                "display_position": position,
                "nhl_team": self._player_team(player),
                "score": score,
                "is_goalie": 'G' in position.upper(),
            })

        # Sort ascending — worst first (most droppable)
        scored.sort(key=lambda x: x["score"])
        return scored

    def _score_free_agents(self, free_agents: list) -> list[dict]:
        """Score free agents with advanced stats."""
        scored = []
        for player in free_agents:
            name = self._player_name(player)
            position = self._display_position(player)
            score = self.stats.get_player_value(name, position)

            # Only include FAs with real stats data
            if score <= 0:
                continue

            scored.append({
                "player": player,
                "name": name,
                "player_key": self._player_key(player),
                "positions": self._eligible_positions(player),
                "display_position": position,
                "nhl_team": self._player_team(player),
                "score": score,
                "is_goalie": 'G' in position.upper(),
            })

        # Best first
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def _find_best_upgrades(self, roster: list[dict], free_agents: list[dict],
                            schedule: dict) -> list[dict]:
        """For each weak roster spot, find the single best upgrade.

        Avoids combinatorial explosion by matching one FA per drop candidate.
        """
        recommendations = []
        used_fa_keys: set[str] = set()
        used_drop_keys: set[str] = set()

        # Iterate roster from weakest to strongest
        for rp in roster:
            if rp["player_key"] in used_drop_keys:
                continue

            # Find the best FA at the same position type
            best_fa = None
            for fa in free_agents:
                if fa["player_key"] in used_fa_keys:
                    continue
                if not self._positions_compatible(fa, rp):
                    continue

                # Schedule density check
                fa_team = fa.get("nhl_team", "")
                fa_games = schedule.get(fa_team, 0)
                if fa_games < config.WAIVER_MIN_GAMES:
                    continue

                # Must actually be an upgrade
                if fa["score"] <= rp["score"]:
                    continue

                # Calculate improvement
                if rp["score"] <= 0:
                    improvement_pct = 100.0
                else:
                    improvement_pct = ((fa["score"] - rp["score"]) / rp["score"]) * 100

                if improvement_pct < config.WAIVER_MIN_IMPROVEMENT_PCT:
                    continue

                best_fa = (fa, fa_games, improvement_pct)
                break  # FAs are sorted best-first, take the first match

            if best_fa:
                fa, fa_games, improvement_pct = best_fa
                recommendations.append({
                    "add_player": fa,
                    "drop_player": rp,
                    "improvement_pct": round(improvement_pct, 1),
                    "fa_score": fa["score"],
                    "drop_score": rp["score"],
                    "fa_games_this_week": fa_games,
                    "reason": self._build_reason(fa, rp, fa_games, improvement_pct),
                })
                used_fa_keys.add(fa["player_key"])
                used_drop_keys.add(rp["player_key"])

        # Sort recommendations by improvement (best first)
        recommendations.sort(key=lambda x: x["improvement_pct"], reverse=True)
        return recommendations

    def _positions_compatible(self, fa: dict, roster_player: dict) -> bool:
        """Check if a FA can replace a roster player (same position type)."""
        # Goalies only swap with goalies
        if fa["is_goalie"] != roster_player["is_goalie"]:
            return False

        # For skaters, check eligible position overlap
        fa_positions = set(p for p in fa["positions"]
                           if p not in ("BN", "IR", "IR+", "Util", "NA"))
        rp_positions = set(p for p in roster_player["positions"]
                           if p not in ("BN", "IR", "IR+", "Util", "NA"))
        return bool(fa_positions & rp_positions)

    def _build_reason(self, fa: dict, roster_player: dict,
                      fa_games: int, improvement_pct: float) -> str:
        """Build a human-readable reason for the move."""
        parts = []
        parts.append(f"value {fa['score']:.1f} vs {roster_player['score']:.1f}")
        parts.append(f"{fa_games} games this week")
        return " · ".join(parts)

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
        return getattr(player, 'player_key', '')

    def _player_team(self, player) -> str:
        return getattr(player, 'editorial_team_abbr', '')

    def _display_position(self, player) -> str:
        return getattr(player, 'display_position', '')

    def _eligible_positions(self, player) -> list[str]:
        positions = getattr(player, 'eligible_positions', [])
        if isinstance(positions, list):
            return [str(p) for p in positions]
        return [str(positions)] if positions else []