"""Fantasy Hockey Agent - Daily Briefing.

Reads your Yahoo Fantasy Hockey roster, checks the NHL schedule, scouts the
player pool for opportunities, and gives you a daily action plan: who to
start, who to bench, who to pick up, and what changed overnight (injury
returns, goalie injuries, power-play promotions). You make the moves.

Usage:
    python main.py                      # Print full briefing to console
    python main.py --auth               # Initial OAuth setup (prints a URL when headless)
    python main.py --email              # Send briefing via email
    python main.py --mode morning       # Lineup + waivers + scouting — for cron
    python main.py --mode evening       # Lineup only — late-day check
    python main.py --scout-only         # Scouting report only
    python main.py --as-of 2026-03-20   # Backtest the scouts on a past date
    python main.py --status             # Show recent recommendations
"""

import argparse
import logging
import sys
import warnings
from datetime import date, datetime

import config
from nhl_client import NHLClient
from stats_provider import StatsProvider
from state_store import StateStore
from email_sender import (
    EmailSender,
    format_lineup_html,
    format_waivers_html,
    format_scouting_html,
    format_scouting_text,
    format_plain_text,
)
import decision_log

logger = logging.getLogger(__name__)


def setup_logging(level: str = None):
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
    logging.getLogger("yfpy.query").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", module="yfpy")
    warnings.filterwarnings("ignore", module="pyhockey")


def run_auth():
    """Run the initial OAuth flow (works over SSH: prints a URL, asks for the code)."""
    from yahoo_client import YahooClient

    print("\n🏒 Fantasy Hockey Agent - Yahoo OAuth Setup")
    print("=" * 50)
    print("\nIf no browser opens, copy the printed URL to any device, approve,")
    print("and paste the verifier code back here.\n")
    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Configuration error:\n{e}")
        sys.exit(1)
    client = YahooClient()
    try:
        scoring = client.get_league_scoring()
        print(f"\n✅ Connected. Categories: {', '.join(scoring['categories'])}")
        print("OAuth tokens saved to auth/.env. You're ready to go.\n")
    except Exception as e:
        print(f"\n❌ Connection test failed: {e}")
        print("If Yahoo says the application is not authorized, check the app at")
        print("https://developer.yahoo.com/apps/ has Fantasy Sports Read/Write,")
        print("then delete auth/.env and run --auth again.")
        sys.exit(1)


# ── Collectors ───────────────────────────────────────────────────

def collect_lineup(yahoo, nhl, stats) -> list[dict]:
    from lineup_optimizer import LineupOptimizer
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info("Running lineup optimizer for %s", today)
    changes = LineupOptimizer(yahoo, nhl, stats).optimize_lineup(today)
    decision_log.log_lineup_changes(today, changes)
    return changes


def collect_waivers(yahoo, nhl, stats) -> list[dict]:
    from waiver_manager import WaiverManager
    logger.info("Running waiver wire evaluation")
    recommendations = WaiverManager(yahoo, nhl, stats).evaluate_moves()
    decision_log.log_waiver_moves(recommendations)
    return recommendations


def collect_scouting(yahoo, nhl, stats, store, as_of: date, yahoo_error: str = None) -> dict:
    from scouts import ScoutContext, ALL_SCOUTS
    from ranker import rank

    logger.info("Running scouts as of %s", as_of)
    players = []
    if yahoo is not None:
        try:
            players = yahoo.get_player_universe()
        except Exception as e:
            logger.warning("Could not fetch player universe from Yahoo: %s", e)
            yahoo_error = yahoo_error or str(e)
    ctx = ScoutContext(stats, nhl, store, as_of=as_of, players=players, yahoo_ok=bool(players))
    opps = []
    for scout_cls in ALL_SCOUTS:
        opps.extend(scout_cls().safe_scan(ctx))
    report = rank(opps, ctx)
    if yahoo_error:
        report.notes.insert(0, f"Yahoo error: {yahoo_error[:160]}")
    if stats.season_is_stale:
        report.notes.append(f"No {stats.requested_season}-{stats.requested_season + 1} game data yet; "
                            f"values are from {stats.season}-{stats.season + 1}.")
    if players:
        store.save("players", ctx.snapshot())
    result = report.to_dict()
    decision_log.log_scouting(result)
    return result


# ── Console output ───────────────────────────────────────────────

def print_lineup(changes: list[dict]):
    today = datetime.now().strftime("%Y-%m-%d")
    if changes:
        print(f"\n📋 LINEUP MOVES ({today})")
        print(f"   https://hockey.fantasysports.yahoo.com/hockey/"
              f"{config.YAHOO_LEAGUE_ID}/{config.YAHOO_TEAM_ID}\n")
        for c in changes:
            name = c.get('name', c['player_key'])
            pos = c['selected_position']
            score = c.get('score', 0)
            if pos == "BN":
                print(f"   🪑 BENCH  {name}")
            elif score > 50:
                print(f"   ✅ START  {name} → {pos}  (value {score - 50:.1f})")
            else:
                print(f"   ✅ START  {name} → {pos}")
    else:
        print("\n📋 Lineup: Looks good, no changes needed")


def print_waivers(recommendations: list[dict]):
    if recommendations:
        print(f"\n🔄 WAIVER MOVES ({len(recommendations)} recommended)")
        print(f"   https://hockey.fantasysports.yahoo.com/hockey/"
              f"{config.YAHOO_LEAGUE_ID}/{config.YAHOO_TEAM_ID}/players\n")
        for i, rec in enumerate(recommendations, 1):
            a, d = rec["add_player"], rec["drop_player"]
            print(f"   {i}. ADD {a['name']} ({a['nhl_team']}) / DROP {d['name']} ({d['nhl_team']})")
            print(f"      +{rec['improvement_pct']:.0f}% upgrade · {rec['reason']}\n")
    else:
        print("\n🔄 Waivers: No clear upgrades available")


def print_scouting(report: dict):
    print("\n🔭 " + format_scouting_text(report).replace("\n", "\n   "))


# ── Email ────────────────────────────────────────────────────────

def send_email_briefing(changes, recommendations, scouting, mode: str):
    sender = EmailSender()
    if not sender.is_configured():
        logger.warning("Email not configured — printing to console instead")
        if changes is not None:
            print_lineup(changes)
        if recommendations is not None:
            print_waivers(recommendations)
        if scouting is not None:
            print_scouting(scouting)
        return

    lineup_html = format_lineup_html(changes or [], config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID) \
        if changes is not None else ""
    waiver_html = format_waivers_html(recommendations, config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID) \
        if recommendations is not None else ""
    scouting_html = format_scouting_html(scouting, config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID) \
        if scouting is not None else ""
    plain = format_plain_text(changes or [], recommendations or [])
    if scouting is not None:
        plain = format_scouting_text(scouting) + "\n" + plain

    today = datetime.now().strftime("%a %b %d")
    n_now = len(scouting.get("act_now", [])) if scouting else 0
    hot = f" · {n_now} act-now" if n_now else ""
    if mode == "morning":
        subject = f"🏒 Fantasy Hockey Briefing — {today}{hot}"
    elif mode == "evening":
        subject = f"🏒 Lineup Check — {today}"
    else:
        subject = f"🏒 Fantasy Hockey — {today}{hot}"
    sender.send_briefing(subject, lineup_html, waiver_html, plain, scouting_html=scouting_html)


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fantasy Hockey Agent - daily lineup, waiver and scouting advisor")
    parser.add_argument("--auth", action="store_true", help="Run initial Yahoo OAuth setup")
    parser.add_argument("--email", action="store_true", help="Send the briefing as email instead of printing")
    parser.add_argument("--mode", choices=["morning", "evening", "full"], default="full",
                        help="morning=lineup+waivers+scouting, evening=lineup only, full=everything (default)")
    parser.add_argument("--lineup-only", action="store_true")
    parser.add_argument("--waivers-only", action="store_true")
    parser.add_argument("--scout-only", action="store_true", help="Only run the scouts")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD: run the scouts as of a past date (backtest)")
    parser.add_argument("--status", action="store_true", help="Show recent recommendations")
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    setup_logging(args.log_level)

    if args.auth:
        run_auth()
        return
    if args.status:
        decision_log.print_summary()
        return
    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Configuration error:\n{e}")
        sys.exit(1)

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    run_lineup = run_waivers = run_scouts = True
    if args.mode == "evening":
        run_waivers = run_scouts = False
    if args.lineup_only:
        run_waivers = run_scouts = False
    if args.waivers_only:
        run_lineup = run_scouts = False
    if args.scout_only:
        run_lineup = run_waivers = False

    if not args.email:
        print("\n🏒 Fantasy Hockey Agent - Daily Briefing")
        print(f"   {datetime.now().strftime('%A, %B %d %Y')}")
        print("=" * 45)

    try:
        nhl = NHLClient()
        store = StateStore()
        stats = StatsProvider(as_of=as_of)

        # Yahoo can fail (expired app permission, outage). Degrade instead of dying:
        # the stats-only scouts still run and the briefing says what's missing.
        yahoo, yahoo_error = None, None
        try:
            from yahoo_client import YahooClient
            yahoo = YahooClient()
            scoring = yahoo.get_league_scoring()
            stats.set_league_categories(scoring.get("categories", []))
        except Exception as e:
            yahoo_error = str(e)
            logger.error("Yahoo unavailable: %s", e)
            decision_log.log_error("yahoo", yahoo_error)
            yahoo = None

        changes = recommendations = scouting = None
        if yahoo is not None:
            if run_lineup:
                changes = collect_lineup(yahoo, nhl, stats)
            if run_waivers:
                recommendations = collect_waivers(yahoo, nhl, stats)
        if run_scouts:
            scouting = collect_scouting(yahoo, nhl, stats, store, as_of, yahoo_error)

        if args.email:
            send_email_briefing(changes, recommendations, scouting, args.mode)
        else:
            if yahoo_error:
                print(f"\n⚠️  Yahoo unavailable: {yahoo_error[:200]}")
                if run_lineup or run_waivers:
                    print("   Lineup and waiver sections skipped.")
            if changes is not None:
                print_lineup(changes)
            if recommendations is not None:
                print_waivers(recommendations)
            if scouting is not None:
                print_scouting(scouting)
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
