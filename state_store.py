"""Tiny JSON state store so scouts can compare today against the last run.

Every signal the scouts look for is a *change* (healthy today, injured
yesterday; on PP1 today, PP2 last week). This module persists what was
observed so the next run has a baseline. Files live in `state/`, which is
mounted as a Docker volume so they survive container restarts.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)


class StateStore:
    """Load/save named JSON documents under the state directory."""

    def __init__(self, base_dir: Path = None):
        self.base_dir = Path(base_dir or config.STATE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.base_dir / f"{name}.json"

    def load(self, name: str, default: Any = None) -> Any:
        path = self._path(name)
        if not path.exists():
            return default if default is not None else {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            logger.warning("Could not read state %s: %s (starting fresh)", name, e)
            return default if default is not None else {}

    def save(self, name: str, data: Any) -> None:
        """Atomic write so a crash mid-save never leaves a half-written file."""
        path = self._path(name)
        fd, tmp = tempfile.mkstemp(dir=self.base_dir, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, ensure_ascii=False, default=str)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def exists(self, name: str) -> bool:
        return self._path(name).exists()
