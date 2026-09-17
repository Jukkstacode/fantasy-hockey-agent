"""Write each briefing as a static HTML page under state/site/.

index.html is always the latest briefing; dated copies are kept for a few
weeks and listed at the bottom of the page. Serve the folder with any
static file server (see DEPLOY.md, "Hosting the latest report").
"""

import html
import logging
from datetime import datetime, timedelta
from pathlib import Path

import config

logger = logging.getLogger(__name__)

SITE_DIR = config.STATE_DIR / "site"
KEEP_DAYS = 21


def write_briefing(html_body: str, mode: str, when: datetime = None) -> Path:
    """Save the rendered briefing; returns the path of index.html."""
    when = when or datetime.now()
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = when.strftime("%Y-%m-%d-%H%M")
    dated = SITE_DIR / f"briefing-{stamp}-{mode}.html"
    dated.write_text(html_body, encoding="utf-8")
    _prune(when)
    index = SITE_DIR / "index.html"
    index.write_text(_with_archive(html_body, when), encoding="utf-8")
    logger.info("Wrote briefing page %s", index)
    return index


def _prune(now: datetime):
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for f in SITE_DIR.glob("briefing-*.html"):
        if f.name[len("briefing-"):len("briefing-") + 10] < cutoff:
            f.unlink(missing_ok=True)


def _with_archive(body: str, now: datetime) -> str:
    files = sorted(SITE_DIR.glob("briefing-*.html"), reverse=True)[:30]
    items = "".join(
        f'<li><a href="{html.escape(f.name)}">{html.escape(f.name[len("briefing-"):-5])}</a></li>'
        for f in files
    )
    archive = (f'<div class="section"><h2>Archive</h2><ul class="archive">{items}</ul>'
               f'<p class="small">Generated {now:%A %B %d %Y %H:%M}. Older reports are removed after {KEEP_DAYS} days.</p></div>')
    marker = '<div class="footer">'
    if marker in body:
        return body.replace(marker, archive + marker, 1)
    return body + archive
