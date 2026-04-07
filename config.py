"""Configuration and environment loading."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
AUTH_DIR = BASE_DIR / "auth"
LOG_DIR = BASE_DIR / "logs"
AUTH_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

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
