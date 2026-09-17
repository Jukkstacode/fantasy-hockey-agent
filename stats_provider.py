"""Advanced stats provider using pyhockey (MoneyPuck data).

Three jobs:

1. Player valuation. If league scoring categories are supplied (from Yahoo
   league settings) the value is a category-weighted sum of per-game z-scores
   across the player pool, so "would this player beat mine" is measured in
   the stats your league actually counts. Without categories it falls back
   to the original points-per-hour + xGoals formula.

2. Season fallback. pyhockey only knows completed/in-progress seasons. In
   September there is no 2026-27 data yet, and in October a player has three
   games. Values fall back to the previous season until a player has
   MIN_GAMES_CURRENT games.

3. Game logs. Per-game power-play / even-strength ice time and goalie game
   logs for the deployment and goalie scouts.

Data is fetched once per run and cached in memory.
"""

import logging
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

import polars as pl
import pyhockey

import config
import scoring

logger = logging.getLogger(__name__)

MIN_GAMES_CURRENT = 10   # trust current-season values after this many games
MIN_GAMES_POOL = 10      # players with fewer games are excluded from z-score pool

# pyhockey uses different situation codes for season tables vs game logs
SEASON_SITUATION = {"all": "all", "pp": "5on4", "ev": "5on5", "pk": "4on5"}

# Yahoo spells some names differently from MoneyPuck
NAME_ALIASES = {
    "egor chinakhov": "yegor chinakhov",
    "nikolai kovalenko": "nikolay kovalenko",
    "alexander nylander": "alex nylander",
    "mitchell marner": "mitch marner",
    "matthew boldy": "matt boldy",
    "joshua norris": "josh norris",
    "jacob middleton": "jake middleton",
}

# Yahoo stat display names -> how to compute a per-player season total from
# MoneyPuck tables. Each entry: (table, expression). Tables: "all", "ev",
# "pp", "pk" are skater_seasons situations; "games" is aggregated skater_games.
SKATER_CATEGORY_MAP = {
    "G":   ("all", lambda r: r.get("goals", 0)),
    "A":   ("all", lambda r: (r.get("points", 0) or 0) - (r.get("goals", 0) or 0)),
    "P":   ("all", lambda r: r.get("points", 0)),
    "PTS": ("all", lambda r: r.get("points", 0)),
    "PPP": ("pp",  lambda r: r.get("points", 0)),
    "PPG": ("pp",  lambda r: r.get("goals", 0)),
    "PPA": ("pp",  lambda r: (r.get("points", 0) or 0) - (r.get("goals", 0) or 0)),
    "SHP": ("pk",  lambda r: r.get("points", 0)),
    "SHG": ("pk",  lambda r: r.get("goals", 0)),
    "+/-": ("ev",  lambda r: (r.get("goalsFor", 0) or 0) - (r.get("goalsAgainst", 0) or 0)),
    "PIM": ("all", lambda r: 2 * (r.get("penaltiesTaken", 0) or 0)),
    "BLK": ("all", lambda r: r.get("shotsBlocked", 0)),
    "FW":  ("all", lambda r: r.get("faceoffsWon", 0)),
    "SOG": ("games", lambda r: r.get("shots", 0)),
    "HIT": ("games", lambda r: r.get("hits", 0)),
}
NEGATIVE_CATEGORIES = {"PIM"}  # higher is worse (some leagues count PIM positive; flip via config if so)


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKD", name)
    return normalized.encode("ascii", "ignore").decode("ascii").lower().strip()


def name_keys(name: str) -> list[str]:
    """Lookup keys for fuzzy name matching between Yahoo and MoneyPuck.

    'Juraj Slafkovský' -> ['juraj slafkovsky', 'slafkovsky j', 'slafkovsk j']
    The truncated form handles a MoneyPuck quirk where the last character of
    some names is dropped.
    """
    normalized = _normalize_name(name)
    if not normalized:
        return []
    normalized = NAME_ALIASES.get(normalized, normalized)
    keys = [normalized]
    parts = normalized.replace(".", "").split()
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        keys.append(f"{last} {first[0]}")
        if len(last) > 4:
            keys.append(f"{last[:-1]} {first[0]}")
    return keys


def current_season_year(today: date = None) -> int:
    """MoneyPuck season year: 2025 means 2025-26. Seasons start in October."""
    today = today or date.today()
    return today.year if today.month >= 9 else today.year - 1


class StatsProvider:
    """Wraps pyhockey with caching, season fallback, and league-aware scoring."""

    def __init__(self, season: Optional[int] = None, as_of: Optional[date] = None):
        self.as_of = as_of or date.today()
        self.requested_season = season or current_season_year(self.as_of)
        self.season = self.requested_season
        self.season_is_stale = False     # True when we fell back to an older season
        self._loaded = False
        self._skaters: dict[str, dict] = {}        # name key -> row (situation all)
        self._skaters_by_sit: dict[str, dict[str, dict]] = {}   # sit -> name key -> row
        self._goalies: dict[str, dict] = {}
        self._prev_skaters: Optional[dict[str, dict]] = None
        self._prev_goalies: Optional[dict[str, dict]] = None
        self._game_totals: Optional[dict[str, dict]] = None    # name key -> shots/hits totals
        self._categories: list[str] = []
        self._cat_values: dict[str, float] = {}    # name key -> category-weighted value
        self._points_mode = False
        self._skater_fppg: dict[str, float] = {}   # name key -> season fantasy points per game
        self._goalie_fppg: dict[str, tuple[float, int]] = {}   # name key -> (FP per start, starts)
        self._team_results: Optional[dict] = None  # (team, date) -> (GF, GA)
        self._game_log_cache: dict[tuple, pl.DataFrame] = {}

    # ── Loading ──────────────────────────────────────────────────

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        season = self.requested_season
        for attempt in range(3):
            try:
                self._load_season(season)
                self.season = season
                self.season_is_stale = season != self.requested_season
                if self.season_is_stale:
                    logger.info("No MoneyPuck data for %s yet; using %s", self.requested_season, season)
                return
            except ValueError as e:
                if "for season" in str(e).lower():
                    season -= 1
                    continue
                logger.warning("Failed to load advanced stats: %s", e)
                return
            except Exception as e:
                logger.warning("Failed to load advanced stats: %s", e)
                return

    def _index(self, df: pl.DataFrame) -> dict[str, dict]:
        """Index rows by fuzzy name keys, plus last name alone when unique."""
        out = {}
        by_last: dict[str, list] = {}
        for row in df.iter_rows(named=True):
            for key in name_keys(row["name"]):
                out[key] = row
            parts = _normalize_name(row["name"]).split()
            if parts:
                by_last.setdefault("last:" + parts[-1], []).append(row)
        for key, rows in by_last.items():
            if len(rows) == 1:
                out[key] = rows[0]
        return out

    def _load_season(self, season: int):
        logger.info("Loading advanced stats from pyhockey for season %s...", season)
        for sit, code in SEASON_SITUATION.items():
            df = pyhockey.skater_seasons(season=season, situation=code, quiet=True)
            self._skaters_by_sit[sit] = self._index(df)
        self._skaters = self._skaters_by_sit["all"]
        goalies = pyhockey.goalie_seasons(season=season, quiet=True)
        if "situation" in goalies.columns:
            goalies = goalies.filter(pl.col("situation") == "all")
        self._goalies = self._index(goalies)
        logger.info("  Loaded %d skaters, %d goalies",
                    len({r["name"] for r in self._skaters.values()}),
                    len({r["name"] for r in self._goalies.values()}))

    def _load_previous(self):
        if self._prev_skaters is not None:
            return
        self._prev_skaters, self._prev_goalies = {}, {}
        try:
            prev = self.season - 1
            self._prev_skaters = self._index(pyhockey.skater_seasons(season=prev, situation="all", quiet=True))
            pg = pyhockey.goalie_seasons(season=prev, quiet=True)
            if "situation" in pg.columns:
                pg = pg.filter(pl.col("situation") == "all")
            self._prev_goalies = self._index(pg)
            logger.info("  Loaded previous season (%s) for fallback", prev)
        except Exception as e:
            logger.warning("Previous season load failed: %s", e)

    def _load_game_totals(self):
        """Season shots/hits per player (not in the season table) from game logs."""
        if self._game_totals is not None:
            return
        self._game_totals = {}
        try:
            df = pyhockey.skater_games(season=self.season, situation="all", quiet=True)
            agg = df.group_by("name").agg([
                pl.col("shots").sum().alias("shots"),
                pl.col("hits").sum().alias("hits"),
                pl.col("gameID").n_unique().alias("gp"),
            ])
            self._game_totals = self._index(agg)
        except Exception as e:
            logger.warning("Game totals load failed (SOG/HIT categories unavailable): %s", e)

    # ── Lookup helpers ───────────────────────────────────────────

    def _lookup(self, cache: dict, player_name: str) -> Optional[dict]:
        for key in name_keys(player_name):
            if key in cache:
                return cache[key]
        parts = _normalize_name(player_name).split()
        if parts:
            return cache.get("last:" + parts[-1])
        return None

    def get_skater_stats(self, player_name: str) -> Optional[dict]:
        self.load()
        return self._lookup(self._skaters, player_name)

    def get_goalie_stats(self, player_name: str) -> Optional[dict]:
        self.load()
        return self._lookup(self._goalies, player_name)

    def get_skater_stats_with_fallback(self, player_name: str) -> tuple[Optional[dict], bool]:
        """Current-season row, or previous season if too few games. (row, is_fallback)"""
        self.load()
        row = self._lookup(self._skaters, player_name)
        if row and (row.get("gamesPlayed") or 0) >= MIN_GAMES_CURRENT:
            return row, False
        self._load_previous()
        prev = self._lookup(self._prev_skaters or {}, player_name)
        if prev and (prev.get("gamesPlayed") or 0) >= MIN_GAMES_CURRENT:
            return prev, True
        return row, False

    # ── League-aware scoring ─────────────────────────────────────

    def configure_scoring(self, league: Optional[dict] = None):
        """Pick points or category valuation from league settings / config."""
        scoring_type = (league or {}).get("scoring_type", "") or ""
        use_points = config.VALUATION == "points" or "point" in scoring_type.lower()
        if use_points:
            self.set_points_scoring()
        else:
            cats = (league or {}).get("categories") or config.LEAGUE_CATEGORIES
            self.set_league_categories(cats)

    def set_points_scoring(self):
        """Value = projected fantasy points per game under the league formula (x10)."""
        self.load()
        self._points_mode = True
        self._cat_values = {}
        seen = set()
        for row in self._skaters.values():
            name = row["name"]
            if name in seen:
                continue
            seen.add(name)
            gp = row.get("gamesPlayed") or 0
            if gp <= 0:
                continue
            fp = scoring.skater_points_from_season_rows(
                row, self._lookup(self._skaters_by_sit.get("ev", {}), name),
                self._lookup(self._skaters_by_sit.get("pp", {}), name),
                self._lookup(self._skaters_by_sit.get("pk", {}), name))
            for key in name_keys(name):
                self._skater_fppg[key] = fp / gp
        self._compute_goalie_points()
        logger.info("Points valuation ready: %d skaters, %d goalies", len(seen), len(self._goalie_fppg) // 3 or len(self._goalie_fppg))

    def team_results(self) -> dict:
        """(team, gameDate iso) -> (goalsFor, goalsAgainst) for the season."""
        if self._team_results is None:
            self._team_results = {}
            try:
                tg = pyhockey.team_games(season=self.season, situation="all", quiet=True)
                for r in tg.select(["team", "gameDate", "goalsFor", "goalsAgainst"]).iter_rows(named=True):
                    self._team_results[(r["team"], str(r["gameDate"]))] = (r["goalsFor"] or 0, r["goalsAgainst"] or 0)
            except Exception as e:
                logger.warning("team_games load failed (goalie wins unavailable): %s", e)
        return self._team_results

    def goalie_game_points(self, days: Optional[int] = None, end_date: Optional[date] = None) -> list[dict]:
        """Per-start fantasy points for every goalie: [{name, team, date, fp, ga, sa, toi, win, so}]."""
        gg = self.get_goalie_games(days=days or 400, end_date=end_date)
        if gg.is_empty():
            return []
        if "situation" in gg.columns:
            gg = gg.filter(pl.col("situation") == "all")
        results = self.team_results()
        out = []
        for r in gg.select(["name", "team", "gameDate", "iceTime", "shotsAgainst", "goalsAgainst"]).sort("gameDate").iter_rows(named=True):
            toi = float(r["iceTime"] or 0)
            if toi < 30:
                continue
            gf, ga_team = results.get((r["team"], str(r["gameDate"])), (None, None))
            win = 1 if (gf is not None and gf > ga_team) else 0
            ga = int(r["goalsAgainst"] or 0)
            so = 1 if (ga == 0 and toi >= 58) else 0
            fp = scoring.goalie_points(win, ga, int(r["shotsAgainst"] or 0), so)
            out.append({"name": r["name"], "team": r["team"], "date": str(r["gameDate"]), "fp": fp,
                        "ga": ga, "sa": int(r["shotsAgainst"] or 0), "toi": toi, "win": win, "so": so})
        return out

    def _compute_goalie_points(self):
        totals: dict[str, list] = {}
        for g in self.goalie_game_points():
            t = totals.setdefault(g["name"], [0.0, 0])
            t[0] += g["fp"]
            t[1] += 1
        for name, (fp, n) in totals.items():
            for key in name_keys(name):
                self._goalie_fppg[key] = (fp / n, n)

    def set_league_categories(self, categories: list[str], negative: set[str] = None):
        """Enable category-weighted valuation using the league's stat categories."""
        self._categories = [c for c in categories if c in SKATER_CATEGORY_MAP]
        skipped = [c for c in categories if c not in SKATER_CATEGORY_MAP and c not in GOALIE_KNOWN]
        if skipped:
            logger.info("Categories without a MoneyPuck mapping (ignored): %s", skipped)
        self._negative = set(negative or NEGATIVE_CATEGORIES)
        self._cat_values = {}
        if self._categories:
            self.load()
            self._compute_category_values()

    def _compute_category_values(self):
        needs_games = any(SKATER_CATEGORY_MAP[c][0] == "games" for c in self._categories)
        if needs_games:
            self._load_game_totals()
        # Build per-player per-game rates
        players = {}
        for key, row in self._skaters.items():
            name = row["name"]
            if name in players:
                continue
            gp = row.get("gamesPlayed") or 0
            if gp < MIN_GAMES_POOL:
                continue
            rates = {}
            for cat in self._categories:
                table, fn = SKATER_CATEGORY_MAP[cat]
                if table == "games":
                    src = self._lookup(self._game_totals or {}, name)
                    total = fn(src) if src else None
                else:
                    src = self._lookup(self._skaters_by_sit.get(table, {}), name)
                    total = fn(src) if src else 0
                if total is None:
                    continue
                rates[cat] = (total or 0) / gp
            players[name] = rates
        if not players:
            return
        # z-scores per category
        stats = {}
        for cat in self._categories:
            vals = [r[cat] for r in players.values() if cat in r]
            if len(vals) < 20:
                continue
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            stats[cat] = (mean, var ** 0.5 or 1.0)
        for name, rates in players.items():
            z_total = 0.0
            for cat, (mean, sd) in stats.items():
                if cat not in rates:
                    continue
                z = (rates[cat] - mean) / sd
                if cat in self._negative:
                    z = -z
                z_total += z
            # Scale to roughly the legacy 10-60 range so thresholds stay meaningful
            value = 30.0 + 8.0 * z_total
            for key in name_keys(name):
                self._cat_values[key] = round(max(value, 0.0), 2)
        logger.info("Category-weighted values computed for %d skaters over %s",
                    len(players), list(stats.keys()))

    # ── Values ───────────────────────────────────────────────────

    def get_skater_value(self, player_name: str) -> float:
        self.load()
        if self._points_mode:
            for key in name_keys(player_name):
                if key in self._skater_fppg:
                    return round(self._skater_fppg[key] * 10.0, 2)
            row, _ = self.get_skater_stats_with_fallback(player_name)
            return self._legacy_skater_value(row)
        if self._cat_values:
            for key in name_keys(player_name):
                if key in self._cat_values:
                    return self._cat_values[key]
            # Not in this season's pool: fall back to legacy formula on prior season
        row, _ = self.get_skater_stats_with_fallback(player_name)
        return self._legacy_skater_value(row)

    @staticmethod
    def _legacy_skater_value(stats: Optional[dict]) -> float:
        if not stats or (stats.get("gamesPlayed") or 0) < 5:
            return 0.0
        pph = stats.get("pointsPerHour") or 0.0
        xg = stats.get("xGoalsForPerHour") or 0.0
        return round(pph * 10.0 + xg * 2.0, 2)

    def get_goalie_value(self, player_name: str) -> float:
        """Points mode: fantasy points per start (x10), discounted for tiny samples.
        Otherwise Goals Saved Above Expected per game plus a workload bonus."""
        self.load()
        if self._points_mode:
            for key in name_keys(player_name):
                if key in self._goalie_fppg:
                    fp, n = self._goalie_fppg[key]
                    return round(fp * 10.0 * min(1.0, n / 10.0), 2)
            return 0.0
        stats = self._lookup(self._goalies, player_name)
        if not stats or (stats.get("gamesPlayed") or 0) < 3:
            self._load_previous()
            stats = self._lookup(self._prev_goalies or {}, player_name)
        if not stats:
            return 0.0
        gp = stats.get("gamesPlayed") or 0
        if gp < 3:
            return 0.0
        gsax = (stats.get("xGoals") or 0.0) - (stats.get("goals") or 0.0)
        return round((gsax / gp) * 20.0 + gp * 0.5, 2)

    @property
    def points_mode(self) -> bool:
        return self._points_mode

    def get_player_value(self, player_name: str, position: str) -> float:
        if "G" in (position or "").upper():
            return self.get_goalie_value(player_name)
        return self.get_skater_value(player_name)

    # ── Game logs (for scouts) ───────────────────────────────────

    def get_skater_games(self, situation: str, days: int = 45,
                         end_date: Optional[date] = None) -> pl.DataFrame:
        """Per-game skater rows for the last `days` days ending at as_of."""
        self.load()
        end_date = end_date or self.as_of
        start = end_date - timedelta(days=days)
        key = ("skater", situation, start, end_date)
        if key not in self._game_log_cache:
            try:
                df = pyhockey.skater_games(
                    season=self.season, situation=situation,
                    start_date=start.isoformat(), end_date=end_date.isoformat(), quiet=True,
                )
            except Exception as e:
                logger.warning("skater_games(%s) failed: %s", situation, e)
                df = pl.DataFrame()
            self._game_log_cache[key] = df
        return self._game_log_cache[key]

    def get_goalie_games(self, days: int = 45, end_date: Optional[date] = None) -> pl.DataFrame:
        """Per-game goalie rows (one row per situation) for the window."""
        self.load()
        end_date = end_date or self.as_of
        start = end_date - timedelta(days=days)
        key = ("goalie", start, end_date)
        if key not in self._game_log_cache:
            try:
                df = pyhockey.goalie_games(
                    season=self.season, start_date=start.isoformat(),
                    end_date=end_date.isoformat(), quiet=True,
                )
            except Exception as e:
                logger.warning("goalie_games failed: %s", e)
                df = pl.DataFrame()
            self._game_log_cache[key] = df
        return self._game_log_cache[key]

    def regression_table(self) -> list[dict]:
        """Skaters with ixG vs goals gap (positive diff = shooting cold)."""
        self.load()
        seen, out = set(), []
        for row in self._skaters.values():
            if row["name"] in seen:
                continue
            seen.add(row["name"])
            gp = row.get("gamesPlayed") or 0
            ixg = row.get("individualxGoals") or 0.0
            goals = row.get("goals") or 0
            out.append({
                "name": row["name"], "team": row.get("team", ""),
                "position": row.get("position", ""), "gp": gp,
                "goals": goals, "ixg": round(ixg, 2), "diff": round(ixg - goals, 2),
            })
        return out


GOALIE_KNOWN = {"W", "L", "GA", "GAA", "SV", "SV%", "SHO", "SA", "GS", "MIN"}
