"""NewsScout: poll RSS feeds and turn beat-reporter text into typed events
with one Claude call per run.

Skips itself (with a log line) when ANTHROPIC_API_KEY is not set, so the
rest of the agent works without it.
"""

import hashlib
import logging
import re
from typing import Literal, Optional

import config
from opportunity import (Opportunity, NEWS, INJURY_OUT, URGENCY_NOW, URGENCY_WEEK, URGENCY_WATCH)
from scouts.base import Scout, ScoutContext

logger = logging.getLogger(__name__)

EVENT_TYPES = Literal[
    "injury_return", "injury_out", "goalie_injury", "goalie_start_change",
    "pp_promotion", "pp_demotion", "line_promotion", "line_demotion",
    "callup", "trade", "healthy_scratch", "other",
]

# event -> (urgency, base confidence multiplier, is_pickup_signal)
EVENT_META = {
    "injury_return": (URGENCY_NOW, 1.0, True),
    "goalie_injury": (URGENCY_NOW, 1.0, False),      # handled via the backup below
    "goalie_start_change": (URGENCY_WEEK, 0.9, True),
    "pp_promotion": (URGENCY_WEEK, 0.9, True),
    "line_promotion": (URGENCY_WEEK, 0.8, True),
    "callup": (URGENCY_WATCH, 0.6, True),
    "trade": (URGENCY_WATCH, 0.5, True),
    "injury_out": (URGENCY_NOW, 1.0, False),
    "pp_demotion": (URGENCY_WATCH, 0.8, False),
    "line_demotion": (URGENCY_WATCH, 0.7, False),
    "healthy_scratch": (URGENCY_WATCH, 0.7, False),
}

SYSTEM_PROMPT = """You extract fantasy-hockey-relevant roster events from NHL news items.
Only emit events that change a player's expected production: injuries and returns,
goalie starts or injuries, power-play or line assignments, call-ups, trades, scratches.
Ignore contract news, milestones, prospect rankings, opinion pieces, and anything not
about a specific NHL player. Use the source URL given with each item. Team is the
3-letter NHL abbreviation; infer it from context when not stated. If a goalie is
injured, also emit a goalie_start_change event for the teammate expected to start,
when the item names him. Set fantasy_relevant=false for noise."""


class NewsScout(Scout):
    name = "news"

    def scan(self, ctx: ScoutContext) -> list[Opportunity]:
        if not config.ANTHROPIC_API_KEY:
            logger.info("ANTHROPIC_API_KEY not set; skipping news classification")
            return []
        items = self._fetch_new_items(ctx)
        if not items:
            logger.info("No new news items")
            return []
        events = self._classify(items)
        logger.info("News: %d items → %d relevant events", len(items), len(events))
        return self._to_opportunities(ctx, events)

    # ── feeds ────────────────────────────────────────────────────

    def _fetch_new_items(self, ctx: ScoutContext) -> list[dict]:
        try:
            import feedparser
        except ImportError:
            logger.warning("feedparser not installed; pip install feedparser")
            return []
        seen = ctx.store.load("news_seen", {})
        cutoff_keep = 5000
        new_items = []
        for url in config.NEWS_FEEDS:
            try:
                feed = feedparser.parse(url, agent="Mozilla/5.0 FantasyHockeyAgent/1.0")
            except Exception as e:
                logger.warning("Feed failed %s: %s", url, e)
                continue
            for e in feed.entries:
                title = getattr(e, "title", "") or ""
                body = getattr(e, "summary", "") or getattr(e, "description", "") or ""
                body = re.sub(r"<[^>]+>", " ", body)
                body = re.sub(r"\s+", " ", body).strip()[:600]
                link = getattr(e, "link", "") or ""
                h = hashlib.sha1(f"{title}|{link}".encode()).hexdigest()[:16]
                if h in seen:
                    continue
                seen[h] = ctx.as_of.isoformat()
                new_items.append({"title": title, "body": body, "url": link})
        if len(seen) > cutoff_keep:
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-cutoff_keep:])
        ctx.store.save("news_seen", seen)
        return new_items[:config.NEWS_MAX_ITEMS_PER_RUN]

    # ── classification ───────────────────────────────────────────

    def _classify(self, items: list[dict]) -> list:
        try:
            import anthropic
            from pydantic import BaseModel
        except ImportError:
            logger.warning("anthropic/pydantic not installed; pip install anthropic pydantic")
            return []

        class NewsEvent(BaseModel):
            player_name: str
            nhl_team: str
            event: EVENT_TYPES
            fantasy_relevant: bool
            confidence: float
            summary: str
            source_url: str

        class NewsBatch(BaseModel):
            events: list[NewsEvent]

        text = "\n\n".join(
            f"[{i}] {it['title']}\n{it['body']}\nURL: {it['url']}" for i, it in enumerate(items)
        )
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        try:
            response = client.messages.parse(
                model=config.ANTHROPIC_MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": text}],
                output_format=NewsBatch,
            )
        except anthropic.RateLimitError as e:
            logger.warning("Claude rate limited: %s", e)
            return []
        except anthropic.APIStatusError as e:
            logger.warning("Claude API error %s: %s", e.status_code, e.message)
            return []
        except anthropic.APIConnectionError as e:
            logger.warning("Claude connection error: %s", e)
            return []
        if response.stop_reason == "refusal":
            logger.warning("Claude declined the classification request")
            return []
        parsed = response.parsed_output
        return [e for e in (parsed.events if parsed else []) if e.fantasy_relevant]

    # ── conversion ───────────────────────────────────────────────

    def _to_opportunities(self, ctx: ScoutContext, events) -> list[Opportunity]:
        opps = []
        for ev in events:
            meta = EVENT_META.get(ev.event)
            if not meta:
                continue
            urgency, mult, is_pickup = meta
            mine = ctx.on_my_roster(ev.player_name)
            if is_pickup and mine:
                continue
            if not is_pickup and not mine:
                continue
            signal = NEWS if is_pickup else INJURY_OUT if ev.event in ("injury_out", "goalie_injury") else ev.event
            info = ctx.lookup(ev.player_name)
            pos = "G" if (info and info.is_goalie) else ""
            opps.append(Opportunity(
                player_name=ev.player_name, nhl_team=ev.nhl_team.upper(), signal=signal,
                evidence=f"News ({ev.event.replace('_', ' ')}): {ev.summary}",
                confidence=max(0.1, min(1.0, ev.confidence)) * mult, urgency=urgency,
                projected_value=ctx.value_of(ev.player_name, pos),
                source_url=ev.source_url or None,
            ))
        return opps
