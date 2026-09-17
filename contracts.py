"""Keeper contracts from the league site (weddingsnipe.ca).

The site stores contracts in the Firestore collection `contracts` of project
`wedding-snipe`, which its security rules make world-readable, so every run
fetches them straight from the Firestore REST API (no credentials) and
caches the result in state/contracts.json. If the fetch fails the cache is
used. Manual fallback:

  python main.py --import-contracts saved-page.html   # "Save page as" in a browser
  python main.py --import-contracts contracts.json    # a JSON export

Cache shape: {"players": [{"name", "positions", "team", "gm", "years", "stolen", "nhl_id"}]}.
Contracted players are unavailable in the draft and in free agency; MY_GM's
contracts are part of your roster.
"""

import html
import json
import logging
import re
from datetime import date
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

CONTRACTS_FILE = config.STATE_DIR / "contracts.json"
FIRESTORE_URL = (f"https://firestore.googleapis.com/v1/projects/{config.CONTRACTS_FIRESTORE_PROJECT}"
                 f"/databases/(default)/documents/contracts")
POSITION_CODES = {"L": "LW", "R": "RW"}


def _field(fields: dict, name: str, default=None):
    v = fields.get(name)
    if not v:
        return default
    for key in ("stringValue", "integerValue", "booleanValue", "doubleValue", "timestampValue"):
        if key in v:
            val = v[key]
            return int(val) if key == "integerValue" else val
    return default


def fetch_contracts() -> list[dict]:
    """Read the whole contracts collection from Firestore (public read)."""
    players, token = [], None
    while True:
        params = {"pageSize": 300}
        if token:
            params["pageToken"] = token
        r = requests.get(FIRESTORE_URL, params=params, timeout=20,
                         headers={"User-Agent": "FantasyHockeyAgent/1.0"})
        r.raise_for_status()
        data = r.json()
        for doc in data.get("documents", []):
            f = doc.get("fields", {})
            pos = str(_field(f, "position", "") or "")
            positions = [POSITION_CODES.get(p.strip(), p.strip()) for p in pos.split(",") if p.strip()]
            players.append({
                "name": _field(f, "player", ""), "positions": positions, "team": _field(f, "team", ""),
                "gm": _field(f, "gm", ""), "years": int(_field(f, "years", 0) or 0),
                "stolen": bool(_field(f, "stolen", False)), "nhl_id": str(_field(f, "nhlId", "") or ""),
                "updated_at": _field(f, "updatedAt", ""),
            })
        token = data.get("nextPageToken")
        if not token:
            break
    return players


def refresh_contracts() -> list[dict]:
    """Fetch from Firestore and update the cache; fall back to the cache on failure."""
    try:
        players = fetch_contracts()
        if not players:
            raise ValueError("empty contracts collection")
        CONTRACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"source": FIRESTORE_URL, "updated": date.today().isoformat(), "players": players},
                  open(CONTRACTS_FILE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        logger.info("Fetched %d keeper contracts from Firestore", len(players))
        return players
    except Exception as e:
        logger.warning("Contract fetch failed (%s); using cached %s", str(e)[:80], CONTRACTS_FILE.name)
        return load_contracts()


def load_contracts() -> list[dict]:
    if not CONTRACTS_FILE.exists():
        return []
    try:
        doc = json.loads(CONTRACTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s: %s", CONTRACTS_FILE.name, e)
        return []
    players = doc.get("players", [])
    logger.info("Loaded %d keeper contracts (updated %s)", len(players), doc.get("updated", "?"))
    return players


def parse_contracts_html(text: str) -> list[dict]:
    """Extract contracts from the rendered contracts page (data-* attributes)."""
    players = []
    for sec in re.finditer(r'<section class="gm" data-gm="([^"]+)"(.*?)</section>', text, re.S):
        gm = html.unescape(sec.group(1))
        for card in re.finditer(r'<a class="card"[^>]*data-pos="([^"]*)"[^>]*data-years="(\d+)"[^>]*data-stolen="(\d)"'
                                r'[^>]*>.*?<div class="pname">([^<]+)</div>.*?<span class="pos[^"]*">[^<]*</span>\s*<span>([^<]*)</span>',
                                sec.group(2), re.S):
            pos, years, stolen, name, team = card.groups()
            players.append({"name": html.unescape(name).strip(), "positions": [p.strip() for p in pos.split(",") if p.strip()],
                            "team": team.strip(), "gm": gm, "years": int(years), "stolen": stolen == "1"})
    return players


def import_contracts(path: str) -> int:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        doc = json.loads(text)
        players = doc.get("players", doc if isinstance(doc, list) else [])
    else:
        players = parse_contracts_html(text)
    if not players:
        raise ValueError("No contracts found in the file")
    CONTRACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"source": str(p), "updated": date.today().isoformat(), "players": players},
              open(CONTRACTS_FILE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return len(players)


def contract_player_infos():
    """PlayerInfo records: my contracts as roster, others as owned by that GM."""
    from scouts.base import PlayerInfo
    infos = []
    for c in refresh_contracts():
        mine = c.get("gm", "").lower() == config.MY_GM.lower()
        infos.append(PlayerInfo(
            name=c["name"], team=c.get("team", ""), positions=list(c.get("positions", [])),
            ownership_type="team", owner_team=f"{c['gm']} (contract, {c.get('years', '?')}y)",
            on_my_roster=mine, nhl_id=str(c.get("nhl_id", "") or "")))
    return infos
