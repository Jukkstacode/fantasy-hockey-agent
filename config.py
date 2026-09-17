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
# Must match a Redirect URI registered on the app at developer.yahoo.com/apps
YAHOO_REDIRECT_URI = os.getenv("YAHOO_REDIRECT_URI", "https://localhost:8080/")


# Agent behavior
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Waiver thresholds
WAIVER_MIN_IMPROVEMENT_PCT = float(os.getenv("WAIVER_MIN_IMPROVEMENT_PCT", "15"))
WAIVER_MIN_GAMES = int(os.getenv("WAIVER_MIN_GAMES", "3"))
WAIVER_MAX_ADDS_PER_WEEK = int(os.getenv("WAIVER_MAX_ADDS_PER_WEEK", "3"))

# Lineup optimizer
MATCHUP_GAA_THRESHOLD = float(os.getenv("MATCHUP_GAA_THRESHOLD", "3.0"))

# Fallback roster while the Yahoo API is unavailable: one player name per line
ROSTER_FILE = BASE_DIR / "state" / "my_roster.txt"
# Your GM name on the league site (keeper contracts under this name are your roster)
MY_GM = os.getenv("MY_GM", "Bimm")
# League site (weddingsnipe.ca) Firestore project; its contracts collection is public-read
CONTRACTS_FIRESTORE_PROJECT = os.getenv("CONTRACTS_FIRESTORE_PROJECT", "wedding-snipe")

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

# League scoring. VALUATION=points ranks players by projected fantasy points
# per game under the formulas below (web-app/js/scoring.js is the source of
# truth); VALUATION=categories uses category z-scores instead.
VALUATION = os.getenv("VALUATION", "points").lower()


def _weights(env: str, default: str) -> dict:
    out = {}
    for pair in os.getenv(env, default).split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            try:
                out[k.strip().upper()] = float(v)
            except ValueError:
                pass
    return out


# Skater FP = 3*G + 2*A + 1*(+/-) + 0.25*PIM + 1*PPP + 1*SHP + 1.5*GWG
FANTASY_POINTS_WEIGHTS = _weights("FANTASY_POINTS_WEIGHTS", "G:3,A:2,+/-:1,PIM:0.25,PPP:1,SHP:1,GWG:1.5")
# Goalie FP = 3*W - 1.5*GA + 0.2*(SA - GA) + 6*SO
GOALIE_POINTS_WEIGHTS = _weights("GOALIE_POINTS_WEIGHTS", "W:3,GA:-1.5,SV:0.2,SO:6")
TREND_HOT_GAMES = int(os.getenv("TREND_HOT_GAMES", "5"))       # recent window
TREND_BASE_GAMES = int(os.getenv("TREND_BASE_GAMES", "20"))    # comparison window
TREND_HOT_RATIO = float(os.getenv("TREND_HOT_RATIO", "1.5"))   # recent / baseline
TREND_MIN_FPPG = float(os.getenv("TREND_MIN_FPPG", "3.0"))     # recent FPPG floor

# Fallback scoring categories when Yahoo league settings can't be read
# (standard Yahoo H2H categories). Overridden by the league's real settings.
LEAGUE_CATEGORIES = [c.strip() for c in os.getenv(
    "LEAGUE_CATEGORIES", "G,A,+/-,PIM,PPP,SOG,HIT,BLK,W,GAA,SV%,SHO").split(",") if c.strip()]

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
