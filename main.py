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
    logging.getLogger("yfpy.query").setLevel(logging.CRITICAL)
    logging.getLogger("yfpy.query").propagate = False
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


def run_draft(top_n: int, md_path: str = None, include_contracted: bool = False):
    """Print (and optionally save) the draft board."""
    from draft_board import build_board, format_board_text, format_board_markdown
    from contracts import refresh_contracts

    stats = StatsProvider()
    league = None
    try:
        from yahoo_client import YahooClient
        league = YahooClient().get_league_scoring()
    except Exception as e:
        logger.warning("Using configured scoring (Yahoo unavailable: %s)", str(e)[:80])
    stats.configure_scoring(league)
    contracts = refresh_contracts()
    board = build_board(stats, top_n=top_n, contracts=contracts, include_contracted=include_contracted)
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
    roster: list[PlayerInfo] = []
    try:
        from yahoo_client import YahooClient
        roster = [p for p in YahooClient().get_player_universe() if p.on_my_roster]
    except Exception as e:
        logger.warning("Yahoo API unavailable (%s); trying web session / roster file", str(e)[:60])
        players, _ = load_from_yahoo_web()
        roster = [p for p in players if p.on_my_roster] or load_roster_file()
    roster = merge_contracts(roster)
    roster = [p for p in roster if p.on_my_roster]
    pool = [p.name for p in roster] + stats.top_skaters(150) + stats.top_goalies(30)
    trends = TrendProvider(stats, pool=pool)
    trends.build()
    w = " ".join(f"{v:g}*{k}" for k, v in config.FANTASY_POINTS_WEIGHTS.items())
    print(f"\n📈 TREND LINES as of {as_of}  (skater FP = {w}; "
          f"last {config.TREND_HOT_GAMES} games vs prior {config.TREND_BASE_GAMES}; source: {trends.source})\n")
    if roster:
        print("YOUR ROSTER")
        for p in roster:
            t = trends.get(p.name, is_goalie=p.is_goalie)
            print(f"  {p.name:24} {t.label(stale=trends.stale) if t else 'no recent game data'}")
    else:
        print("No roster available (Yahoo unreachable and no state/my_roster.txt).")
    hot = [] if trends.stale else [t for t in trends.hot_skaters()
                                    if not any(_normalize(p.name) == _normalize(t.name) for p in roster)]
    print("\nHOTTEST SKATERS (not on your roster; check availability in Yahoo)"
          + ("\n  (no current-season games yet)" if trends.stale else ""))
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


def friendly_yahoo_error(e: Exception) -> str:
    msg = str(e)
    if "not authorized to perform this action" in msg:
        return ("Yahoo API access pending approval (403 'not authorized'). "
                "Apply at https://sports.yahoo.com/developer/access/ — see DEPLOY.md.")
    return msg[:200]


def merge_contracts(players: list) -> list:
    """Add keeper contracts: mine join the roster, others are marked as owned."""
    from contracts import contract_player_infos
    from stats_provider import name_keys
    have = {k for p in players for k in name_keys(p.name)}
    added = []
    for c in contract_player_infos():
        keys = name_keys(c.name)
        if any(k in have for k in keys):
            continue
        have.update(keys)
        added.append(c)
    if added:
        logger.info("Added %d keeper contracts to the player universe (%d on my roster)",
                    len(added), sum(1 for c in added if c.on_my_roster))
    return players + added


def load_from_yahoo_web() -> tuple[list, str]:
    """Rosters and free agents from Yahoo's web pages via saved cookies."""
    from yahoo_web import YahooWeb, YahooLoginRequired
    if not YahooWeb.available():
        return [], None
    try:
        web = YahooWeb()
        if web.is_predraft():
            return [], (f"Yahoo league is pre-draft (team: {web.my_team_name()}): keeper contracts "
                        f"count as rosters and everyone else is draftable.")
        players = web.player_universe()
        note = f"Rosters and free agents read from Yahoo (team: {web.my_team_name()})."
        return players, note
    except YahooLoginRequired as e:
        logger.error("%s", e)
        return [], ("Yahoo session cookies have expired: export them again from your browser to "
                    "state/yahoo_cookies.json. Free-agent availability is unknown until then.")
    except Exception as e:
        logger.warning("Yahoo web read failed: %s", e)
        return [], f"Yahoo web read failed ({str(e)[:80]})."


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
    web_ok = predraft = False
    if not players:
        players, web_note = load_from_yahoo_web()
        web_ok = bool(players)
        predraft = bool(web_note and "pre-draft" in web_note)
        if web_note:
            roster_note = web_note
    if not players:
        players = load_roster_file()
        if players:
            roster_note = (f"Using {len(players)} players from {config.ROSTER_FILE.name} as your roster; "
                           f"free-agent availability is unknown until Yahoo access is restored.")
    players = merge_contracts(players)
    ctx = ScoutContext(stats, nhl, store, as_of=as_of, players=players,
                       yahoo_ok=web_ok or (bool(players) and yahoo is not None and not yahoo_error))
    if predraft:
        ctx.default_available = True
    opps = []
    for scout_cls in ALL_SCOUTS:
        opps.extend(scout_cls().safe_scan(ctx))
    report = rank(opps, ctx)
    if yahoo_error:
        report.notes = [n for n in report.notes if not n.startswith("Yahoo was unreachable")]
        report.notes.insert(0, f"Yahoo unavailable: {yahoo_error[:160]}")
    if roster_note:
        report.notes.append(roster_note)
    if stats.season_is_stale:
        report.notes.append(f"No {stats.requested_season}-{stats.requested_season + 1} game data yet; "
                            f"values are from {stats.season}-{stats.season + 1}.")
    if players and (web_ok or not roster_note):
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


# ── Email / site page ────────────────────────────────────────────

def render_briefing_html(changes, recommendations, scouting) -> str:
    lineup_html = format_lineup_html(changes or [], config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID) \
        if changes is not None else ""
    waiver_html = format_waivers_html(recommendations, config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID) \
        if recommendations is not None else ""
    scouting_html = format_scouting_html(scouting, config.YAHOO_LEAGUE_ID, config.YAHOO_TEAM_ID) \
        if scouting is not None else ""
    return EmailSender.wrap_html(scouting_html + lineup_html, waiver_html)


def write_site_page(changes, recommendations, scouting, mode: str):
    """Always save the briefing as a static page (served at hockey.bimm.dev)."""
    try:
        from site_writer import write_briefing
        write_briefing(render_briefing_html(changes, recommendations, scouting), mode)
    except Exception as e:
        logger.warning("Could not write site page: %s", e)


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
    parser.add_argument("--include-contracted", action="store_true", help="With --draft: keep contracted players on the board")
    parser.add_argument("--import-contracts", metavar="FILE", help="Import keeper contracts from a saved contracts page (.html) or .json")
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
    if args.import_contracts:
        from contracts import import_contracts, CONTRACTS_FILE
        n = import_contracts(args.import_contracts)
        print(f"✅ Imported {n} contracts to {CONTRACTS_FILE}")
        return
    if args.draft:
        run_draft(args.top, args.draft_md, include_contracted=args.include_contracted)
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
        # Backtests (--as-of) keep their own state so they never pollute real runs
        store = StateStore() if as_of == date.today() else StateStore(config.STATE_DIR / "backtest")
        stats = StatsProvider(as_of=as_of)

        # Yahoo can fail (expired app permission, outage). Degrade instead of dying:
        # the stats-only scouts still run and the briefing says what's missing.
        yahoo, yahoo_error = None, None
        try:
            from yahoo_client import YahooClient
            yahoo = YahooClient()
            league = yahoo.get_league_scoring()
        except Exception as e:
            yahoo_error = friendly_yahoo_error(e)
            logger.error("Yahoo unavailable: %s", yahoo_error)
            decision_log.log_error("yahoo", str(e))
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

        write_site_page(changes, recommendations, scouting, args.mode)
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
