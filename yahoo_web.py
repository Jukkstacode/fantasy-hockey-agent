"""Read league rosters and the free-agent list from Yahoo's web pages using
an exported browser session (state/yahoo_cookies.json).

Used while the Fantasy Sports API is unavailable. A dozen or so GET
requests per run, plain HTML parsing, no browser and no LLM. If Yahoo
redirects to its login page the cookies have expired: YahooLoginRequired is
raised and the briefing says to re-export them.
"""

import html
import json
import logging
import re
from typing import Optional

import requests

import config
from scouts.base import PlayerInfo

logger = logging.getLogger(__name__)

COOKIES_FILE = config.STATE_DIR / "yahoo_cookies.json"
BASE = "https://hockey.fantasysports.yahoo.com/hockey"
PAGE = 25
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120 Safari/537.36")

ROW_RE = re.compile(r"<tr\b.*?</tr>", re.S)
PLAYER_RE = re.compile(r'data-ys-playerid="(\d+)"[^>]*title="([^"]+)"')
TEAMPOS_RE = re.compile(r'<span class="Fz-xxs">([A-Za-z]{2,3}) - ([A-Z,]+)</span>')
STATUS_RE = re.compile(r'class="ysf-player-status[^"]*">\s*<span[^>]*title="([^"]*)"[^>]*>\s*([A-Za-z+\-]{1,6})\s*<')
TEAM_LINK_RE = re.compile(r'href="https://hockey\.fantasysports\.yahoo\.com/hockey/(\d+)/(\d+)"[^>]*>([^<]{2,60})<')


class YahooLoginRequired(Exception):
    pass


class YahooWeb:
    def __init__(self, league_id: str = None, team_id: str = None, cookies_path=None):
        self.league_id = str(league_id or config.YAHOO_LEAGUE_ID)
        self.team_id = str(team_id or config.YAHOO_TEAM_ID)
        self.cookies_path = cookies_path or COOKIES_FILE
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-CA,en;q=0.9"})
        self._load_cookies()
        self._teams: Optional[dict[str, str]] = None
        self._predraft: Optional[bool] = None

    @classmethod
    def available(cls) -> bool:
        return COOKIES_FILE.exists()

    def _load_cookies(self):
        cookies = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        for c in cookies:
            self.session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".yahoo.com"),
                                     path=c.get("path", "/"))

    def _get(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        if "login.yahoo.com" in r.url or "<title>Login" in r.text[:2000]:
            raise YahooLoginRequired("Yahoo redirected to login: re-export state/yahoo_cookies.json")
        r.raise_for_status()
        return r.text

    # ── pages ────────────────────────────────────────────────────

    def league_teams(self) -> dict[str, str]:
        if self._teams is None:
            page = self._get(f"{BASE}/{self.league_id}")
            teams = {}
            for lid, tid, name in TEAM_LINK_RE.findall(page):
                if lid == self.league_id:
                    teams.setdefault(tid, html.unescape(name).strip())
            self._teams = teams
        return self._teams

    @staticmethod
    def _parse_rows(page: str) -> list[dict]:
        out, seen = [], set()
        for row in ROW_RE.findall(page):
            m = PLAYER_RE.search(row)
            if not m:
                continue
            yid, name = m.group(1), html.unescape(m.group(2)).strip()
            if yid in seen:
                continue
            seen.add(yid)
            tp = TEAMPOS_RE.search(row)
            st = STATUS_RE.search(row)
            out.append({
                "yahoo_id": yid, "name": name,
                "team": tp.group(1).upper() if tp else "",
                "positions": tp.group(2).split(",") if tp else [],
                "status": st.group(2).upper() if st else "",
                "status_full": html.unescape(st.group(1)) if st else "",
            })
        return out

    def team_roster(self, team_id: str) -> list[dict]:
        return self._parse_rows(self._get(f"{BASE}/{self.league_id}/{team_id}"))

    def is_predraft(self) -> bool:
        """Before the draft Yahoo shows last season's rosters and disables adds."""
        if self._predraft is None:
            page = self._get(f"{BASE}/{self.league_id}/players?status=A&pos=P&sort=AR&count=0")
            self._predraft = "predraft" in page
            self._first_players_page = page
        return self._predraft

    def available_players(self, max_count: int = None) -> list[dict]:
        max_count = max_count or config.SCOUT_AVAILABLE_POOL
        out, offset = [], 0
        while offset < max_count:
            if offset == 0 and getattr(self, "_first_players_page", None):
                page = self._first_players_page
            else:
                page = self._get(f"{BASE}/{self.league_id}/players?status=A&pos=P&sort=AR&count={offset}")
            rows = self._parse_rows(page)
            if not rows:
                break
            out.extend(rows)
            if len(rows) < PAGE:
                break
            offset += PAGE
        return out

    # ── player universe ──────────────────────────────────────────

    def player_universe(self) -> list[PlayerInfo]:
        teams = self.league_teams()
        infos = []
        for tid, tname in teams.items():
            mine = tid == self.team_id
            for p in self.team_roster(tid):
                infos.append(PlayerInfo(
                    name=p["name"], key=f"web.{p['yahoo_id']}", team=p["team"], positions=p["positions"],
                    status=p["status"], status_full=p["status_full"],
                    ownership_type="team", owner_team=tname, on_my_roster=mine))
        for p in self.available_players():
            infos.append(PlayerInfo(
                name=p["name"], key=f"web.{p['yahoo_id']}", team=p["team"], positions=p["positions"],
                status=p["status"], status_full=p["status_full"], ownership_type="freeagents"))
        my_name = teams.get(self.team_id, "?")
        logger.info("Yahoo web: %d teams, %d players (%d on my team '%s'), %d available",
                    len(teams), sum(1 for i in infos if i.ownership_type == "team"),
                    sum(1 for i in infos if i.on_my_roster), my_name,
                    sum(1 for i in infos if i.ownership_type == "freeagents"))
        return infos

    def my_team_name(self) -> str:
        return self.league_teams().get(self.team_id, "")
