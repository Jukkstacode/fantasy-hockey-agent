"""Fantasy Hockey Agent - Daily Briefing.

Reads your Yahoo Fantasy Hockey roster, checks the NHL schedule,
and gives you a daily action plan: who to start, who to bench,
and who to pick up off waivers. You make the moves — it does the thinking.

Usage:
    python main.py                  # Print briefing to console
    python main.py --auth           # Initial OAuth setup
    python main.py --email          # Send briefing via email (no console output)
    python main.py --mode morning   # Full briefing (lineup + waivers) — for cron
    python main.py --mode evening   # Lineup only — for late-day check
    python main.py --status         # Show recent recommendations
"""

import argparse
import logging
import sys
import warnings
from datetime import datetime

import config
from yahoo_client import YahooClient
from nhl_client import NHLClient
from lineup_optimizer import LineupOptimizer
from waiver_manager import WaiverManager
from stats_provider import StatsProvider
from email_sender import (
    EmailSender,
    format_lineup_html,
    format_waivers_html,
    format_plain_text,
)
import decision_log

logger = logging.getLogger(__name__)


def setup_logging(level: str = None):
    """Configure logging."""
    log_level = getattr(logging, level or config.LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_DIR / "agent.log"),
        ],
    )

    # Silence the noisy yfpy "no game id" warnings
    logging.getLogger("yfpy.query").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", module="yfpy")


def run_auth():
    """Run the initial OAuth flow."""
    print("\n🏒 Fantasy Hockey Agent - Yahoo OAuth Setup")
    print("=" * 50)
    print("\nThis will open a browser window for Yahoo authentication.")
    print("Make sure you have your Consumer Key and Secret in .env\n")

    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Configuration error:\n{e}")
        print("\nCopy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    client = YahooClient()
    try:
        settings = client.get_league_settings()
        print(f"\n✅ Connected! League: {getattr(settings, 'name', 'OK')}")
        print("OAuth tokens saved. You're ready to go.\n")
    except Exception as e:
        print(f"\n❌ Connection test failed: {e}")
        sys.exit(1)


def collect_lineup(yahoo: YahooClient, nhl: NHLClient,
                   stats: StatsProvider) -> list[dict]:
    """Run the lineup optimizer and return the changes."""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info("Running lineup optimizer for %s", today)

    optimizer = LineupOptimizer(yahoo, nhl, stats)
    changes = optimizer.optimize_lineup(today)
    decision_log.log_lineup_changes(today, changes)
    return changes


def collect_waivers(yahoo: YahooClient, nhl: NHLClient,
                    stats: StatsProvider) -> list[dict]:
    """Run the waiver wire evaluator and return the recommendations."""
    logger.info("Running waiver wire evaluation")

    manager = WaiverManager(yahoo, nhl, stats)
    recommendations = manager.evaluate_moves()
    decision_log.log_waiver_moves(recommendations)
    return recommendations


def print_lineup(changes: list[dict]):
    """Print the lineup section to the console."""
    today = datetime.now().strftime("%Y-%m-%d")
    if changes:
        print(f"\n📋 LINEUP MOVES ({today})")
        print(f"   https://hockey.fantasysports.yahoo.com/hockey/"
              f"{config.YAHOO_LEAGUE_ID}/{config.YAHOO_TEAM_ID}")
        print()
        for c in changes:
            name = c.get('name', c['player_key'])
            pos = c['selected_position']
            score = c.get('score', 0)
            if pos == "BN":
                print(f"   🪑 BENCH  {name}")
            else:
                if score > 50:
                    val = score - 50
                    print(f"   ✅ START  {name} → {pos}  (value {val:.1f})")
                else:
                    print(f"   ✅ START  {name} → {pos}")
    else:
        print("\n📋 Lineup: Looks good, no changes needed")


def print_waivers(recommendations: list[dict]):
    """Print the waiver section to the console."""
    if recommendations:
        print(f"\n🔄 WAIVER MOVES ({len(recommendations)} recommended)")
        print(f"   https://hockey.fantasysports.yahoo.com/hockey/"
              f"{config.YAHOO_LEAGUE_ID}/{config.YAHOO_TEAM_ID}/players")
        print()
        for i, rec in enumerate(recommendations, 1):
            add_name = rec["add_player"]["name"]
            add_team = rec["add_player"]["nhl_team"]
            drop_name = rec["drop_player"]["name"]
            drop_team = rec["drop_player"]["nhl_team"]
            pct = rec["improvement_pct"]
            print(f"   {i}. ADD {add_name} ({add_team}) / "
                  f"DROP {drop_name} ({drop_team})")
            print(f"      +{pct:.0f}% upgrade · {rec['reason']}")
            print()
    else:
        print("\n🔄 Waivers: No clear upgrades available")


def send_email_briefing(changes: list[dict], recommendations: list[dict],
                        mode: str):
    """Format and send the briefing as an email."""
    sender = EmailSender()
    if not sender.is_configured():
        logger.warning("Email not configured — printing to console instead")
        print_lineup(changes)
        if recommendations is not None:
            print_waivers(recommendations)
        return

    lineup_html = format_lineup_html(
        changes, config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID
    )
    if recommendations is not None:
        waiver_html = format_waivers_html(
            recommendations, config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID
        )
    else:
        waiver_html = ""

    plain = format_plain_text(changes, recommendations or [])

    today = datetime.now().strftime("%a %b %d")
    if mode == "morning":
        subject = f"🏒 Fantasy Hockey Briefing — {today}"
    elif mode == "evening":
        subject = f"🏒 Lineup Check — {today}"
    else:
        subject = f"🏒 Fantasy Hockey — {today}"

    sender.send_briefing(subject, lineup_html, waiver_html, plain)


def run_status():
    """Show recent agent activity."""
    decision_log.print_summary()


def main():
    parser = argparse.ArgumentParser(
        description="Fantasy Hockey Agent - Daily lineup and waiver advisor"
    )
    parser.add_argument("--auth", action="store_true",
                        help="Run initial Yahoo OAuth setup")
    parser.add_argument("--email", action="store_true",
                        help="Send the briefing as email instead of printing")
    parser.add_argument("--mode", choices=["morning", "evening", "full"],
                        default="full",
                        help="morning=lineup+waivers, evening=lineup only, "
                             "full=both (default)")
    parser.add_argument("--lineup-only", action="store_true",
                        help="Only show lineup recommendations")
    parser.add_argument("--waivers-only", action="store_true",
                        help="Only show waiver recommendations")
    parser.add_argument("--status", action="store_true",
                        help="Show recent recommendations")
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Set logging level")

    args = parser.parse_args()
    setup_logging(args.log_level)

    if args.auth:
        run_auth()
        return

    if args.status:
        run_status()
        return

    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Configuration error:\n{e}")
        sys.exit(1)

    if not args.email:
        print("\n🏒 Fantasy Hockey Agent - Daily Briefing")
        print(f"   {datetime.now().strftime('%A, %B %d %Y')}")
        print("=" * 45)

    try:
        yahoo = YahooClient()
        nhl = NHLClient()
        stats = StatsProvider()

        # Determine what to run based on flags
        run_lineup_section = True
        run_waivers_section = True

        if args.lineup_only or args.mode == "evening":
            run_waivers_section = False
        if args.waivers_only:
            run_lineup_section = False

        changes = collect_lineup(yahoo, nhl, stats) if run_lineup_section else []
        recommendations = (
            collect_waivers(yahoo, nhl, stats) if run_waivers_section else None
        )

        if args.email:
            send_email_briefing(changes, recommendations, args.mode)
        else:
            if run_lineup_section:
                print_lineup(changes)
            if run_waivers_section:
                print_waivers(recommendations)
            print(f"\n{'=' * 45}")
            print("Make your moves, then check back tomorrow.\n")

    except Exception as e:
        logger.exception("Agent error: %s", e)
        decision_log.log_error("main", str(e))
        if not args.email:
            print(f"\n❌ Error: {e}")
            print("Check logs/agent.log for details.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()