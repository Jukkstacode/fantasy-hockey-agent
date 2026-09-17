"""Fantasy Hockey Agent - Daily Briefing.

Reads your Yahoo Fantasy Hockey roster, checks the NHL schedule, scouts the
player pool for opportunities, and gives you a daily action plan: who to
start, who to bench, who to pick up, and what changed overnight (injury
returns, goalie injuries, power-play promotions). You make the moves.

Usage:
    python main.py                      # Print full briefing to console
    python main.py --auth               # Yahoo OAuth: prints a URL, then asks for the code
    python main.py --auth --code CODE   # Same, non-interactive (paste the code or redirected URL)
    python main.py --email              # Send briefing via email
    python main.py --mode morning       # Lineup + waivers + scouting — for cron
    python main.py --mode evening       # Lineup only — late-day check
    python main.py --scout-only         # Scouting report only
    python main.py --as-of 2026-03-20   # Backtest the scouts on a past date
    python main.py --draft --top 150    # Draft board (category-weighted values, PP1/COLD/HOT flags)
    python main.py --trends             # FPPG trend lines for your roster (+ hottest available skaters)
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
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def run_auth(code: str = None, url_only: bool = False):
    """Yahoo authorization using the app's registered redirect URI.

    Works over SSH: prints a URL, you approve on any device, then paste the
    code (or the whole redirected URL) back. `--code` supplies it directly.
    """
    import yahoo_auth
    from yahoo_client import YahooClient

    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Configuration error:\n{e}")
        sys.exit(1)

    if code is None:
        url = yahoo_auth.build_authorize_url(config.YAHOO_REDIRECT_URI)
        print("\n🏒 Fantasy Hockey Agent - Yahoo OAuth Setup")
        print("=" * 50)
        print("\n1. Open this URL and approve access:\n")
        print(f"   {url}\n")
        print(f"2. Yahoo redirects to {config.YAHOO_REDIRECT_URI} (the page may not load).")
        print("   Copy the code from the address bar (?code=...), or the whole URL.")
        if url_only:
            print("\n3. Then run:  python main.py --auth --code <code-or-url>\n")
            return
        code = input("\n3. Paste the code or URL here: ")
    code = yahoo_auth.code_from_input(code)
    if not code:
        print("❌ No code provided.")
        sys.exit(1)
    try:
        data = yahoo_auth.exchange_code(code, config.YAHOO_REDIRECT_URI)
        yahoo_auth.save_token(data)
    except Exception as e:
        print(f"\n❌ {e}")
        sys.exit(1)
    try:
        scoring = YahooClient().get_league_scoring()
        print(f"\n✅ Connected. Categories: {', '.join(scoring['categories'])}")
        print(f"   Roster slots: {scoring['roster_positions']}")
        print("Token saved to auth/.env. You're ready to go.\n")
    except Exception as e:
        print(f"\n❌ Token saved but the API test failed: {e}")
        print("Check the app at https://developer.yahoo.com/apps/ has the Fantasy Sports")
        print("permission and that YAHOO_LEAGUE_ID / YAHOO_TEAM_ID in .env are current.")
        sys.exit(1)


def run_draft(top_n: int, md_path: str = None):
    """Print (and optionally save) the draft board."""
    from draft_board import build_board, format_board_text, format_board_markdown

    stats = StatsProvider()
    league = None
    try:
        from yahoo_client import YahooClient
        league = YahooClient().get_league_scoring()
    except Exception as e:
        logger.warning("Using configured scoring (Yahoo unavailable: %s)", str(e)[:80])
    stats.configure_scoring(league)
    board = build_board(stats, top_n=top_n)
    print(format_board_text(board, by_position=True))
    if md_path:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(format_board_markdown(board))
        print(f"\nSaved markdown board to {md_path}")


def run_trends(as_of: date):
    """Print FPPG trend lines for the roster and the hottest available skaters."""
    from trend import TrendProvider
    from scouts.base import PlayerInfo

    stats = StatsProvider(as_of=as_of)
    stats.configure_scoring(None)
    trends = TrendProvider(stats)
    roster: list[PlayerInfo] = []
    try:
        from yahoo_client import YahooClient
        roster = [p for p in YahooClient().get_player_universe() if p.on_my_roster]
    except Exception as e:
        logger.warning("Yahoo unavailable (%s); using roster file", str(e)[:60])
        roster = load_roster_file()
    w = " ".join(f"{v:g}*{k}" for k, v in config.FANTASY_POINTS_WEIGHTS.items())
    print(f"\n📈 TREND LINES as of {as_of}  (skater FP = {w}; "
          f"last {config.TREND_HOT_GAMES} games vs prior {config.TREND_BASE_GAMES})\n")
    if roster:
        print("YOUR ROSTER")
        for p in roster:
            t = trends.get(p.name, is_goalie=p.is_goalie)
            print(f"  {p.name:24} {t.label() if t else 'no recent game data'}")
    else:
        print("No roster available (Yahoo unreachable and no state/my_roster.txt).")
    hot = [t for t in trends.hot_skaters() if not any(_normalize(p.name) == _normalize(t.name) for p in roster)]
    print("\nHOTTEST SKATERS (not on your roster; check availability in Yahoo)")
    for t in hot[:15]:
        print(f"  {t.name:24} {t.team:4} {t.label()}")
    print()


def _normalize(name: str) -> str:
    from stats_provider import _normalize_name
    return _normalize_name(name)


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


def load_roster_file() -> list:
    """Roster from state/my_roster.txt (one name per line, optional ', G' for goalies)."""
    from scouts.base import PlayerInfo
    path = config.ROSTER_FILE
    if not path.exists():
        return []
    players = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, pos = line.partition(",")
        positions = [p.strip().upper() for p in pos.split("/") if p.strip()] or ["C", "LW", "RW", "D"]
        players.append(PlayerInfo(name=name.strip(), positions=positions,
                                  ownership_type="team", on_my_roster=True))
    logger.info("Loaded %d roster players from %s", len(players), path.name)
    return players


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
    roster_note = None
    if not players:
        players = load_roster_file()
        if players:
            roster_note = (f"Using {len(players)} players from {config.ROSTER_FILE.name} as your roster; "
                           f"free-agent availability is unknown until Yahoo access is restored.")
    ctx = ScoutContext(stats, nhl, store, as_of=as_of, players=players,
                       yahoo_ok=bool(players) and yahoo is not None and not yahoo_error)
    opps = []
    for scout_cls in ALL_SCOUTS:
        opps.extend(scout_cls().safe_scan(ctx))
    report = rank(opps, ctx)
    if yahoo_error:
        report.notes.insert(0, f"Yahoo error: {yahoo_error[:160]}")
    if roster_note:
        report.notes.append(roster_note)
    if stats.season_is_stale:
        report.notes.append(f"No {stats.requested_season}-{stats.requested_season + 1} game data yet; "
                            f"values are from {stats.season}-{stats.season + 1}.")
    if players and not roster_note:
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
    parser.add_argument("--auth", action="store_true", help="Run Yahoo OAuth setup")
    parser.add_argument("--code", default=None, help="With --auth: the code (or redirected URL) from Yahoo")
    parser.add_argument("--url-only", action="store_true", help="With --auth: print the URL and exit")
    parser.add_argument("--email", action="store_true", help="Send the briefing as email instead of printing")
    parser.add_argument("--mode", choices=["morning", "evening", "full"], default="full",
                        help="morning=lineup+waivers+scouting, evening=lineup only, full=everything (default)")
    parser.add_argument("--lineup-only", action="store_true")
    parser.add_argument("--waivers-only", action="store_true")
    parser.add_argument("--scout-only", action="store_true", help="Only run the scouts")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD: run the scouts as of a past date (backtest)")
    parser.add_argument("--trends", action="store_true", help="Print fantasy-points-per-game trend lines and exit")
    parser.add_argument("--draft", action="store_true", help="Print a draft board and exit")
    parser.add_argument("--top", type=int, default=200, help="Draft board size (default 200)")
    parser.add_argument("--draft-md", default=None, help="Also write the draft board to this markdown file")
    parser.add_argument("--status", action="store_true", help="Show recent recommendations")
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    setup_logging(args.log_level)

    if args.auth:
        run_auth(code=args.code, url_only=args.url_only)
        return
    if args.status:
        decision_log.print_summary()
        return
    if args.draft:
        run_draft(args.top, args.draft_md)
        return
    if args.trends:
        run_trends(as_of=date.fromisoformat(args.as_of) if args.as_of else date.today())
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
            league = yahoo.get_league_scoring()
        except Exception as e:
            yahoo_error = str(e)
            logger.error("Yahoo unavailable: %s", e)
            decision_log.log_error("yahoo", yahoo_error)
            yahoo, league = None, None
        stats.configure_scoring(league)

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
