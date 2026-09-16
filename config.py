"""Configuration and environment loading."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
AUTH_DIR = BASE_DIR / "auth"
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
AUTH_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

# Yahoo API
YAHOO_CONSUMER_KEY = os.getenv("YAHOO_CONSUMER_KEY", "")
YAHOO_CONSUMER_SECRET = os.getenv("YAHOO_CONSUMER_SECRET", "")
YAHOO_LEAGUE_ID = os.getenv("YAHOO_LEAGUE_ID", "")
YAHOO_GAME_CODE = os.getenv("YAHOO_GAME_CODE", "nhl")
YAHOO_TEAM_ID = os.getenv("YAHOO_TEAM_ID", "")


# Agent behavior
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Waiver thresholds
WAIVER_MIN_IMPROVEMENT_PCT = float(os.getenv("WAIVER_MIN_IMPROVEMENT_PCT", "15"))
WAIVER_MIN_GAMES = int(os.getenv("WAIVER_MIN_GAMES", "3"))
WAIVER_MAX_ADDS_PER_WEEK = int(os.getenv("WAIVER_MAX_ADDS_PER_WEEK", "3"))

# Lineup optimizer
MATCHUP_GAA_THRESHOLD = float(os.getenv("MATCHUP_GAA_THRESHOLD", "3.0"))

# Scouts (see SCOUTING_PLAN.md)
SCOUT_RECENT_GAMES = int(os.getenv("SCOUT_RECENT_GAMES", "3"))       # "now" window
SCOUT_BASELINE_GAMES = int(os.getenv("SCOUT_BASELINE_GAMES", "10"))  # comparison window
SCOUT_PP_SHARE_JUMP = float(os.getenv("SCOUT_PP_SHARE_JUMP", "0.15"))  # share of team PP time
SCOUT_PP_TOP_N = int(os.getenv("SCOUT_PP_TOP_N", "5"))               # PP1 proxy: top N on team
SCOUT_ES_JUMP_MIN = float(os.getenv("SCOUT_ES_JUMP_MIN", "2.0"))     # minutes/game
SCOUT_REGRESSION_MIN_GP = int(os.getenv("SCOUT_REGRESSION_MIN_GP", "15"))
SCOUT_REGRESSION_XG_DIFF = float(os.getenv("SCOUT_REGRESSION_XG_DIFF", "3.0"))
SCOUT_OWNERSHIP_JUMP = float(os.getenv("SCOUT_OWNERSHIP_JUMP", "10.0"))  # pct points
SCOUT_GOALIE_START_SHARE = float(os.getenv("SCOUT_GOALIE_START_SHARE", "0.6"))
SCOUT_REPEAT_DAYS = int(os.getenv("SCOUT_REPEAT_DAYS", "3"))         # don't re-alert within N days
SCOUT_MAX_ACT_NOW = int(os.getenv("SCOUT_MAX_ACT_NOW", "5"))
SCOUT_MAX_RISING = int(os.getenv("SCOUT_MAX_RISING", "8"))
SCOUT_MAX_WATCHLIST = int(os.getenv("SCOUT_MAX_WATCHLIST", "8"))
SCOUT_AVAILABLE_POOL = int(os.getenv("SCOUT_AVAILABLE_POOL", "300")) # Yahoo available players to scan

# News scout (Claude API). Leave ANTHROPIC_API_KEY unset to skip news classification.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
NEWS_FEEDS = [u.strip() for u in os.getenv(
    "NEWS_FEEDS",
    "https://www.rotowire.com/rss/news.php?sport=NHL,"
    "https://www.dailyfaceoff.com/feed/,"
    "https://www.reddit.com/r/fantasyhockey/new/.rss",
).split(",") if u.strip()]
NEWS_MAX_ITEMS_PER_RUN = int(os.getenv("NEWS_MAX_ITEMS_PER_RUN", "150"))

# NHL API base URL
NHL_API_BASE = "https://api-web.nhle.com/v1"
NHL_STATS_BASE = "https://api-web.nhle.com/v1"


def validate():
    """Check that required config is present."""
    errors = []
    if not YAHOO_CONSUMER_KEY:
        errors.append("YAHOO_CONSUMER_KEY is not set")
    if not YAHOO_CONSUMER_SECRET:
        errors.append("YAHOO_CONSUMER_SECRET is not set")
    if not YAHOO_LEAGUE_ID:
        errors.append("YAHOO_LEAGUE_ID is not set")
    if errors:
        raise ValueError(
            "Missing required configuration:\n" + "\n".join(f"  - {e}" for e in errors)
        )
