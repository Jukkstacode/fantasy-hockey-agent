"""DailyFaceoffScout: line combinations and power-play units as a human
editor publishes them, one page per team.

Two uses:
1. Diff against yesterday's snapshot: a player moving onto PP1 or up to the
   top six (top four for D) is a promotion; the reverse for your own
   players is a warning.
2. Corroboration: the ranker appends "DailyFaceoff: F1 / PP1" to every
   opportunity so a stats-derived signal can be sanity-checked at a glance.

Fetched at most once per day (cached in state/dfo_lines.json), 32 requests
with a short pause, descriptive User-Agent. Preseason pages carry projected
lines (sourceName says "Offseason (Projected)"); those are still useful.
"""

import json
import logging
import re
import time
from datetime import date

import requests

from opportunity import (Opportunity, PP_PROMOTION, PP_DEMOTION, LINE_PROMOTION,
                         LINE_DEMOTION, URGENCY_WEEK, URGENCY_WATCH)
from scouts.base import Scout, ScoutContext
from stats_provider import _normalize_name

logger = logging.getLogger(__name__)

BASE = "https://www.dailyfaceoff.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) FantasyHockeyAgent/1.0 (personal use)"}
FIRST_TEAM = "anaheim-ducks"
UNIT_RANK = {"f1": 1, "f2": 2, "f3": 3, "f4": 4, "d1": 1, "d2": 2, "d3": 3, "d4": 4}


def _fetch_page(slug: str) -> dict | None:
    url = f"{BASE}/teams/{slug}/line-combinations"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning("DailyFaceoff fetch failed for %s: %s", slug, e)
        return None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        logger.warning("DailyFaceoff page for %s had no data block", slug)
        return None
    try:
        return json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    except ValueError:
        return None


def fetch_all_lines() -> dict:
    """{'_date', '_source', 'players': {norm_name: {...}}}"""
    first = _fetch_page(FIRST_TEAM)
    if not first:
        return {}
    slugs = [t.get("slug") for t in first.get("sortedTeams", []) if t.get("slug")]
    players = {}
    sources = set()
    pages = {FIRST_TEAM: first}
    for slug in slugs:
        if slug not in pages:
            time.sleep(0.4)
            pages[slug] = _fetch_page(slug)
    for slug, page in pages.items():
        combo = (page or {}).get("combinations") or {}
        team = combo.get("teamAbbreviation", "")
        sources.add(combo.get("sourceName", ""))
        for p in combo.get("players", []):
            name = p.get("name") or ""
            if not name:
                continue
            key = _normalize_name(name)
            entry = players.setdefault(key, {"name": name, "team": team, "ev": "", "pp": "", "pk": "",
                                             "injury": p.get("injuryStatus") or ""})
            cat = p.get("categoryIdentifier") or ""
            grp = p.get("groupIdentifier") or ""
            if cat in ("ev", "pp", "pk") and grp:
                entry[cat] = grp
            elif cat == "oi":          # out / injured list
                entry["injury"] = entry["injury"] or grp.upper()
    logger.info("DailyFaceoff: %d players across %d teams (%s)", len(players), len(pages),
                ", ".join(s for s in sources if s)[:80])
    return {"_date": date.today().isoformat(), "_sources": sorted(s for s in sources if s), "players": players}


def describe_unit(entry: dict) -> str:
    parts = []
    if entry.get("ev"):
        parts.append(entry["ev"].upper())
    if entry.get("pp"):
        parts.append(entry["pp"].upper())
    if entry.get("injury"):
        parts.append(f"injury: {entry['injury']}")
    return " / ".join(parts)


class DailyFaceoffScout(Scout):
    name = "dailyfaceoff"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        prev = ctx.store.load("dfo_lines", {})
        today = date.today().isoformat()
        if prev.get("_date") == today and prev.get("players"):
            current = prev          # already fetched today (evening run)
            prev = ctx.store.load("dfo_lines_prev", {})
        else:
            current = fetch_all_lines()
            if not current:
                return []
            if prev.get("players"):
                ctx.store.save("dfo_lines_prev", prev)
            ctx.store.save("dfo_lines", current)
        ctx.extras["dfo"] = current.get("players", {})
        if not prev.get("players"):
            logger.info("DailyFaceoff baseline saved; line-change detection starts next run")
            return []
        projected = any("Projected" in s for s in current.get("_sources", []))
        conf_scale = 0.6 if projected else 1.0
        opps = []
        for key, cur in current["players"].items():
            old = prev["players"].get(key)
            if not old:
                continue
            name, team = cur["name"], cur["team"]
            mine = ctx.on_my_roster(name)
            # Power play
            if cur["pp"] == "pp1" and old["pp"] != "pp1":
                if not mine:
                    opps.append(Opportunity(
                        player_name=name, nhl_team=team, signal=PP_PROMOTION,
                        evidence=f"DailyFaceoff moved him to PP1 (was {old['pp'].upper() or 'off the PP'})",
                        confidence=0.75 * conf_scale, urgency=URGENCY_WEEK,
                        projected_value=ctx.value_of(name)))
            elif old["pp"] == "pp1" and cur["pp"] != "pp1" and mine:
                opps.append(Opportunity(
                    player_name=name, nhl_team=team, signal=PP_DEMOTION,
                    evidence=f"DailyFaceoff dropped him from PP1 to {cur['pp'].upper() or 'no PP unit'}",
                    confidence=0.7 * conf_scale, urgency=URGENCY_WATCH, projected_value=ctx.value_of(name)))
            # Even strength
            r_new, r_old = UNIT_RANK.get(cur["ev"], 9), UNIT_RANK.get(old["ev"], 9)
            top = 2 if cur["ev"].startswith(("f", "d")) and cur["ev"][0] == "f" else 2
            if r_new <= top < r_old and not mine:
                opps.append(Opportunity(
                    player_name=name, nhl_team=team, signal=LINE_PROMOTION,
                    evidence=f"DailyFaceoff moved him from {old['ev'].upper()} to {cur['ev'].upper()}",
                    confidence=0.6 * conf_scale, urgency=URGENCY_WEEK, projected_value=ctx.value_of(name)))
            elif r_old <= top < r_new and mine:
                opps.append(Opportunity(
                    player_name=name, nhl_team=team, signal=LINE_DEMOTION,
                    evidence=f"DailyFaceoff moved him from {old['ev'].upper()} to {cur['ev'].upper()}",
                    confidence=0.6 * conf_scale, urgency=URGENCY_WATCH, projected_value=ctx.value_of(name)))
        return opps
