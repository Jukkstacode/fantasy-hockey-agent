"""Official NHL stats by player ID: league-wide season summaries and per-player
game logs from the NHL's public APIs, with a daily disk cache.

Used for exact fantasy points (every stat in the league formula is here,
including game-winning goals, wins and shutouts). MoneyPuck (stats_provider)
stays the source for power-play share, expected goals and on-ice numbers.

Endpoints:
  https://api.nhle.com/stats/rest/en/skater/summary?limit=-1&cayenneExp=seasonId=...
  https://api.nhle.com/stats/rest/en/goalie/summary?limit=-1&cayenneExp=seasonId=...
  https://api-web.nhle.com/v1/player/{id}/game-log/{season}/2
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Optional

import requests

import config
import scoring
from stats_provider import name_keys, _normalize_name

logger = logging.getLogger(__name__)

STATS_BASE = "https://api.nhle.com/stats/rest/en"
WEB_BASE = "https://api-web.nhle.com/v1"
MIN_GAMES_CURRENT = 10


def season_id(as_of: date) -> str:
    y = as_of.year if as_of.month >= 9 else as_of.year - 1
    return f"{y}{y + 1}"


def prev_season_id(sid: str) -> str:
    y = int(sid[:4]) - 1
    return f"{y}{y + 1}"


class NHLStats:
    def __init__(self, as_of: Optional[date] = None, cache_dir: Optional[Path] = None):
        self.as_of = as_of or date.today()
        self.cache_dir = Path(cache_dir or (config.STATE_DIR / "nhl_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FantasyHockeyAgent/1.0"})
        self.requested_season = season_id(self.as_of)
        self.season = self.requested_season
        self.season_is_stale = False
        self._skaters: dict[str, dict[int, dict]] = {}     # season -> id -> row
        self._goalies: dict[str, dict[int, dict]] = {}
        self._index: Optional[dict[str, int]] = None       # name key -> id
        self._positions: dict[int, str] = {}
        self._names: dict[int, str] = {}
        self._loaded = False

    # ── cache helpers ────────────────────────────────────────────

    def _cached(self, name: str, fetch):
        path = self.cache_dir / f"{name}.{self.as_of.isoformat()}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        data = fetch()
        if data is not None:
            for old in self.cache_dir.glob(f"{name}.*.json"):
                if old != path:
                    old.unlink(missing_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _get_json(self, url: str, params: dict = None):
        r = self.session.get(url, params=params, timeout=25)
        r.raise_for_status()
        return r.json()

    # ── summaries ────────────────────────────────────────────────

    def _summary(self, kind: str, sid: str) -> dict[int, dict]:
        def fetch():
            data = self._get_json(f"{STATS_BASE}/{kind}/summary",
                                  {"limit": -1, "cayenneExp": f"seasonId={sid} and gameTypeId=2"})
            return data.get("data", [])
        rows = self._cached(f"{kind}_summary_{sid}", fetch) or []
        return {int(r["playerId"]): r for r in rows if r.get("playerId")}

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            sk = self._summary("skater", self.requested_season)
            if not sk:
                self.season = prev_season_id(self.requested_season)
                self.season_is_stale = True
                sk = self._summary("skater", self.season)
                logger.info("NHL API has no %s data yet; using %s", self.requested_season, self.season)
            self._skaters[self.season] = sk
            self._goalies[self.season] = self._summary("goalie", self.season)
            prev = prev_season_id(self.season)
            self._skaters[prev] = self._summary("skater", prev)
            self._goalies[prev] = self._summary("goalie", prev)
            logger.info("NHL summaries: %d skaters, %d goalies (%s)",
                        len(sk), len(self._goalies[self.season]), self.season)
        except Exception as e:
            logger.warning("NHL summary load failed: %s", e)

    def _build_index(self):
        self._index = {}
        for sid in sorted(self._skaters.keys()):           # later season wins
            for pid, r in self._skaters[sid].items():
                self._names[pid] = r.get("skaterFullName", "")
                self._positions[pid] = r.get("positionCode", "")
                for k in name_keys(r.get("skaterFullName", "")):
                    self._index[k] = pid
        for sid in sorted(self._goalies.keys()):
            for pid, r in self._goalies[sid].items():
                self._names[pid] = r.get("goalieFullName", "")
                self._positions[pid] = "G"
                for k in name_keys(r.get("goalieFullName", "")):
                    self._index[k] = pid

    def _ensure_index(self):
        self.load()
        if self._index is None:
            self._build_index()

    def resolve_id(self, name: str) -> Optional[int]:
        self._ensure_index()
        for k in name_keys(name):
            if k in self._index:
                return self._index[k]
        return None

    def name_of(self, pid: int) -> str:
        self._ensure_index()
        return self._names.get(pid, str(pid))

    def is_goalie(self, pid: int) -> bool:
        self._ensure_index()
        return self._positions.get(pid) == "G"

    def position_of(self, pid: int) -> str:
        self._ensure_index()
        return self._positions.get(pid, "")

    def official_row(self, name: str) -> Optional[dict]:
        """Season summary row (current season, else previous) for display."""
        pid = self.resolve_id(name)
        if pid is None:
            return None
        table = self._goalies if self.is_goalie(pid) else self._skaters
        row, _ = self._row_with_fallback(table, pid)
        return row

    # ── season values ────────────────────────────────────────────

    def _row_with_fallback(self, table: dict, pid: int) -> tuple[Optional[dict], bool]:
        cur = table.get(self.season, {}).get(pid)
        if cur and (cur.get("gamesPlayed") or 0) >= MIN_GAMES_CURRENT:
            return cur, False
        prev = table.get(prev_season_id(self.season), {}).get(pid)
        if prev and (prev.get("gamesPlayed") or 0) >= MIN_GAMES_CURRENT:
            return prev, True
        return cur or prev, cur is None

    def skater_fppg(self, pid: int) -> Optional[float]:
        self.load()
        row, _ = self._row_with_fallback(self._skaters, pid)
        if not row or not row.get("gamesPlayed"):
            return None
        return scoring.skater_points_from_nhl_season(row) / row["gamesPlayed"]

    def goalie_fp_per_start(self, pid: int) -> Optional[tuple[float, int]]:
        self.load()
        row, _ = self._row_with_fallback(self._goalies, pid)
        if not row:
            return None
        starts = row.get("gamesStarted") or row.get("gamesPlayed") or 0
        if not starts:
            return None
        return scoring.goalie_points_from_nhl_season(row) / starts, starts

    def all_skater_values(self) -> dict[int, float]:
        self.load()
        out = {}
        for pid in set(self._skaters.get(self.season, {})) | set(self._skaters.get(prev_season_id(self.season), {})):
            v = self.skater_fppg(pid)
            if v is not None:
                out[pid] = v
        return out

    def all_goalie_values(self) -> dict[int, tuple[float, int]]:
        self.load()
        out = {}
        for pid in set(self._goalies.get(self.season, {})) | set(self._goalies.get(prev_season_id(self.season), {})):
            v = self.goalie_fp_per_start(pid)
            if v is not None:
                out[pid] = v
        return out

    # ── game logs ────────────────────────────────────────────────

    def game_log(self, pid: int) -> list[dict]:
        """Regular-season game log for the active season (falls back one season
        when the current one has no games yet), oldest first, up to as_of."""
        def fetch():
            for sid in (self.requested_season, prev_season_id(self.requested_season)):
                try:
                    data = self._get_json(f"{WEB_BASE}/player/{pid}/game-log/{sid}/2")
                except Exception as e:
                    logger.debug("game log %s/%s failed: %s", pid, sid, e)
                    continue
                games = data.get("gameLog", [])
                if games:
                    return {"season": sid, "games": games}
            return {"season": None, "games": []}
        data = self._cached(f"gamelog_{pid}", fetch) or {}
        games = [g for g in data.get("games", []) if g.get("gameDate", "") <= self.as_of.isoformat()]
        games.sort(key=lambda g: g.get("gameDate", ""))
        return games

    def fetch_game_logs(self, pids: list[int], workers: int = 8) -> dict[int, list[dict]]:
        pids = list(dict.fromkeys(pids))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            logs = list(pool.map(self.game_log, pids))
        return dict(zip(pids, logs))
