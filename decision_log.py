"""Decision logging for audit trail and review.

Logs all agent decisions to a JSON file so you can review
what the agent did (or would have done in dry-run mode).
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)

LOG_FILE = config.LOG_DIR / "decisions.jsonl"


def log_decision(action: str, details: dict, dry_run: bool = None):
    """Append a decision to the log file.

    Args:
        action: Type of action (lineup_change, waiver_add_drop, etc.)
        details: Dict with action-specific details
        dry_run: Whether this was a dry run (defaults to config.DRY_RUN)
    """
    if dry_run is None:
        dry_run = False

    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "dry_run": dry_run,
        "details": details,
    }

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.debug("Logged decision: %s", action)


def log_lineup_changes(date_str: str, changes: list[dict]):
    """Log lineup optimization results."""
    log_decision("lineup_optimization", {
        "date": date_str,
        "changes_count": len(changes),
        "changes": changes,
    })


def log_waiver_moves(recommendations: list[dict]):
    """Log waiver wire evaluation results."""
    log_decision("waiver_evaluation", {
        "recommendations_count": len(recommendations),
        "recommendations": [
            {
                "add": rec["add_player"]["name"],
                "drop": rec["drop_player"]["name"],
                "improvement_pct": rec["improvement_pct"],
                "reason": rec["reason"],
            }
            for rec in recommendations
        ],
    })


def log_scouting(report: dict):
    """Log the ranked scouting report (names and signals only)."""
    def brief(entries):
        return [{"player": e["player_name"], "team": e.get("nhl_team", ""),
                 "signals": e.get("signals", []), "score": e.get("score", 0),
                 "drop": e.get("drop_candidate")} for e in entries]
    log_decision("scouting", {
        "act_now": brief(report.get("act_now", [])),
        "rising": brief(report.get("rising", [])),
        "watchlist": brief(report.get("watchlist", [])),
        "roster_alerts": brief(report.get("roster_alerts", [])),
        "notes": report.get("notes", []),
    })


def log_error(context: str, error: str):
    """Log an error that occurred during agent execution."""
    log_decision("error", {
        "context": context,
        "error": error,
    })


def get_recent_decisions(count: int = 20) -> list[dict]:
    """Read the most recent decisions from the log.

    Args:
        count: Number of recent decisions to return

    Returns:
        List of decision dicts, most recent first
    """
    if not LOG_FILE.exists():
        return []

    decisions = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    decisions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return decisions[-count:][::-1]


def print_summary():
    """Print a summary of recent agent activity."""
    decisions = get_recent_decisions(50)
    if not decisions:
        print("No decisions logged yet.")
        return

    print(f"\n{'=' * 60}")
    print("FANTASY HOCKEY AGENT - RECENT ACTIVITY")
    print(f"{'=' * 60}")

    for d in decisions[:10]:
        ts = d["timestamp"][:16]
        action = d["action"]
        dry = " [DRY RUN]" if d.get("dry_run") else ""
        details = d.get("details", {})

        if action == "lineup_optimization":
            n = details.get("changes_count", 0)
            date = details.get("date", "?")
            print(f"  {ts} | LINEUP{dry} | {date} | {n} changes")
        elif action == "waiver_evaluation":
            n = details.get("recommendations_count", 0)
            print(f"  {ts} | WAIVER{dry} | {n} recommendations")
            for rec in details.get("recommendations", []):
                print(f"           ADD {rec['add']} / DROP {rec['drop']} "
                      f"({rec['improvement_pct']}%)")
        elif action == "scouting":
            n_now = len(details.get("act_now", []))
            n_rise = len(details.get("rising", []))
            print(f"  {ts} | SCOUT  | {n_now} act-now, {n_rise} rising")
            for e in details.get("act_now", []):
                print(f"           NOW {e['player']} ({e['team']}) {', '.join(e['signals'])}")
        elif action == "error":
            print(f"  {ts} | ERROR  | {details.get('context')}: {details.get('error')}")
        else:
            print(f"  {ts} | {action}{dry}")

    print(f"{'=' * 60}\n")
