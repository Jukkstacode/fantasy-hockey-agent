"""Fantasy-points-per-game trend lines from MoneyPuck game logs.

For every skater: per-game fantasy points using FANTASY_POINTS_WEIGHTS,
then a recent window vs. a longer baseline, a least-squares slope over the
baseline, and a unicode sparkline for the email. Goalies get a goals-saved-
above-expected per-game trend on the same shape.

A "sustainable" flag compares the shots/ixG trend to the points trend: a
player scoring more on the same shot volume is riding luck; one shooting
more is earning it.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import polars as pl

import config
import scoring
from stats_provider import StatsProvider, _normalize_name

logger = logging.getLogger(__name__)

BARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return BARS[3] * len(values)
    return "".join(BARS[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in values)


@dataclass
class Trend:
    name: str
    team: str
    position: str
    games: int
    per_game: list[float]               # oldest -> newest, baseline window
    recent_avg: float
    base_avg: float
    season_avg: float
    slope: float                        # points per game per game, over baseline
    shots_recent: float = 0.0
    shots_base: float = 0.0
    ixg_recent: float = 0.0
    ixg_base: float = 0.0
    is_goalie: bool = False

    @property
    def ratio(self) -> float:
        return self.recent_avg / self.base_avg if self.base_avg > 0.05 else (2.0 if self.recent_avg > 0 else 1.0)

    @property
    def sustainable(self) -> Optional[bool]:
        """None when unknown; True when volume is up with the results."""
        if self.is_goalie or self.shots_base <= 0:
            return None
        return (self.shots_recent / self.shots_base) >= 1.15 or (
            self.ixg_base > 0 and self.ixg_recent / self.ixg_base >= 1.15)

    @property
    def arrow(self) -> str:
        r = self.ratio
        if r >= config.TREND_HOT_RATIO:
            return "🔥"
        if r >= 1.2:
            return "↗"
        if r <= 1 / config.TREND_HOT_RATIO:
            return "🧊"
        if r <= 0.8:
            return "↘"
        return "→"

    def spark(self) -> str:
        return sparkline(self.per_game[-config.TREND_BASE_GAMES:])

    def label(self) -> str:
        unit = "FP/start" if self.is_goalie else "FPPG"
        n = config.TREND_HOT_GAMES
        s = f"{self.arrow} {self.spark()} last {n}: {self.recent_avg:.1f} {unit} vs {self.base_avg:.1f} prior"
        if self.season_avg:
            s += f", {self.season_avg:.1f} over {self.games} {'starts' if self.is_goalie else 'games'}"
        sus = self.sustainable
        if self.ratio >= 1.2 and sus is not None:
            s += " (shot volume up too)" if sus else " (same shot volume: luck-driven)"
        return s


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 3:
        return 0.0
    xs = range(n)
    mx = (n - 1) / 2
    my = sum(values) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


class TrendProvider:
    """Builds trends once per run from the stats provider's game logs."""

    def __init__(self, stats: StatsProvider, days: int = 75):
        self.stats = stats
        self.days = days
        self._skaters: dict[str, Trend] = {}
        self._goalies: dict[str, Trend] = {}
        self._built = False

    def build(self):
        if self._built:
            return
        self._built = True
        try:
            self._build_skaters()
            self._build_goalies()
        except Exception as e:
            logger.warning("Trend build failed: %s", e)
        logger.info("Trends built for %d skaters, %d goalies", len(self._skaters), len(self._goalies))

    def _build_skaters(self):
        allg = self.stats.get_skater_games("all", days=self.days)
        if allg.is_empty():
            return
        sit_rows = {}
        for sit in ("ev", "pp", "pk"):
            df = self.stats.get_skater_games(sit, days=self.days)
            idx = {}
            if not df.is_empty():
                for r in df.select(["name", "gameID", "goals", "primaryAssists", "secondaryAssists",
                                    "goalsFor", "goalsAgainst"]).iter_rows(named=True):
                    idx[(r["name"], r["gameID"])] = r
            sit_rows[sit] = idx
        rows = defaultdict(list)
        meta = {}
        cols = ["name", "team", "position", "gameID", "gameDate", "goals", "primaryAssists",
                "secondaryAssists", "shots", "hits", "penaltiesTaken", "individualxGoals"]
        for r in allg.select(cols).sort("gameDate").iter_rows(named=True):
            rows[r["name"]].append(r)
            meta[r["name"]] = (r["team"], r["position"])
        hot_n, base_n = config.TREND_HOT_GAMES, config.TREND_BASE_GAMES
        for name, games in rows.items():
            if len(games) < hot_n + 3:
                continue
            pts = [scoring.skater_points_from_game_rows(
                       g, sit_rows["ev"].get((name, g["gameID"])), sit_rows["pp"].get((name, g["gameID"])),
                       sit_rows["pk"].get((name, g["gameID"]))) for g in games]
            shots = [float(g["shots"] or 0) for g in games]
            ixg = [float(g["individualxGoals"] or 0) for g in games]
            recent, base = pts[-hot_n:], pts[-(hot_n + base_n):-hot_n]
            if len(base) < 3:
                base = pts[:-hot_n]
            season_avg = sum(pts) / len(pts)      # whole window, same formula
            team, pos = meta[name]
            self._skaters[_normalize_name(name)] = Trend(
                name=name, team=team, position=pos, games=len(games),
                per_game=pts[-(hot_n + base_n):],
                recent_avg=sum(recent) / len(recent), base_avg=sum(base) / len(base) if base else 0.0,
                season_avg=season_avg, slope=_slope(pts[-(hot_n + base_n):]),
                shots_recent=sum(shots[-hot_n:]) / hot_n,
                shots_base=(sum(shots[-(hot_n + base_n):-hot_n]) / max(1, len(shots[-(hot_n + base_n):-hot_n]))),
                ixg_recent=sum(ixg[-hot_n:]) / hot_n,
                ixg_base=(sum(ixg[-(hot_n + base_n):-hot_n]) / max(1, len(ixg[-(hot_n + base_n):-hot_n]))),
            )

    def _build_goalies(self):
        rows = defaultdict(list)
        for g in self.stats.goalie_game_points(days=self.days):
            rows[(g["name"], g["team"])].append(g["fp"])
        hot_n, base_n = config.TREND_HOT_GAMES, config.TREND_BASE_GAMES
        for (name, team), vals in rows.items():
            if len(vals) < hot_n + 2:
                continue
            recent, base = vals[-hot_n:], vals[-(hot_n + base_n):-hot_n] or vals[:-hot_n]
            self._goalies[_normalize_name(name)] = Trend(
                name=name, team=team, position="G", games=len(vals), per_game=vals[-(hot_n + base_n):],
                recent_avg=sum(recent) / len(recent), base_avg=sum(base) / len(base) if base else 0.0,
                season_avg=sum(vals) / len(vals), slope=_slope(vals[-(hot_n + base_n):]), is_goalie=True,
            )

    def get(self, name: str, is_goalie: bool = False) -> Optional[Trend]:
        self.build()
        key = _normalize_name(name)
        return (self._goalies if is_goalie else self._skaters).get(key) or self._skaters.get(key) or self._goalies.get(key)

    def hot_skaters(self) -> list[Trend]:
        self.build()
        return sorted(
            (t for t in self._skaters.values()
             if t.ratio >= config.TREND_HOT_RATIO and t.recent_avg >= config.TREND_MIN_FPPG),
            key=lambda t: -(t.recent_avg - t.base_avg))

    def cold_skaters(self) -> list[Trend]:
        self.build()
        return [t for t in self._skaters.values()
                if t.base_avg >= config.TREND_MIN_FPPG and t.ratio <= 1 / config.TREND_HOT_RATIO]
