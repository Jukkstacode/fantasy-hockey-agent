"""Draft board: rank the player pool by category-weighted value with flags
that matter on draft day.

Flags:
  PP1     top-5 share of his team's power-play time last season
  COLD    scored well under his expected goals (positive regression due)
  HOT     scored far over expected (expect fewer goals when the luck runs out)
  1A/1B   goalie start share last season (workhorse vs tandem)
  FP/wk   goalie points per start x start share x 3.5 team games: what the
          roster slot actually returns in a typical week
"""

import logging
from collections import defaultdict

import polars as pl

import config
from stats_provider import StatsProvider, _normalize_name, name_keys

logger = logging.getLogger(__name__)

MIN_GP_SKATER = 30
MIN_GP_GOALIE = 15


def _pp_shares(stats: StatsProvider) -> dict[str, float]:
    """Skater share of his team's PP ice time, from the season table."""
    rows = {}
    team_total = defaultdict(float)
    for row in stats._skaters_by_sit.get("pp", {}).values():
        key = (row["name"], row.get("team"))
        if key in rows:
            continue
        rows[key] = row
        team_total[row.get("team")] += float(row.get("iceTime") or 0.0)
    shares = {}
    for (name, team), row in rows.items():
        team_pp = team_total.get(team, 0.0) / 5.0
        if team_pp > 0:
            shares[_normalize_name(name)] = float(row.get("iceTime") or 0.0) / team_pp
    return shares


def build_board(stats: StatsProvider, top_n: int = 200, contracts: list[dict] = None,
                include_contracted: bool = False) -> dict:
    """Assumes stats.configure_scoring() has been called.

    Contracted players are dropped from the board unless include_contracted,
    in which case they're kept with the owning GM shown in the flags.
    """
    stats.load()
    pp_share = _pp_shares(stats)
    contracted = {}
    for c in contracts or []:
        for key in name_keys(c["name"]):
            contracted.setdefault(key, c)
    my_gm = config.MY_GM.lower()

    def contract_flag(name: str):
        c = next((contracted[k] for k in name_keys(name) if k in contracted), None)
        if not c:
            return None
        return f"[{c['gm']} {c.get('years', '?')}y]" if c.get("gm", "").lower() != my_gm else "[MINE]"

    reg = {r["name"]: r for r in stats.regression_table()}

    skaters, seen = [], set()
    for row in stats._skaters.values():
        name = row["name"]
        if name in seen:
            continue
        seen.add(name)
        gp = row.get("gamesPlayed") or 0
        if gp < MIN_GP_SKATER:
            continue
        value = stats.get_skater_value(name)
        if value <= 0:
            continue
        flags = []
        cf = contract_flag(name)
        if cf and not include_contracted:
            continue
        if cf:
            flags.append(cf)
        share = pp_share.get(_normalize_name(name), 0.0)
        if share >= 0.5:
            flags.append("PP1")
        r = reg.get(name)
        # Elite finishers beat their xG every year, so HOT needs a bigger gap
        # in both absolute and relative terms than COLD does.
        if r and r["diff"] >= config.SCOUT_REGRESSION_XG_DIFF:
            flags.append("COLD")
        elif r and r["diff"] <= -max(6.0, 0.35 * r["ixg"]):
            flags.append("HOT")
        official = stats.nhl.official_row(name) if getattr(stats, "nhl", None) else None
        if official:
            gp = official.get("gamesPlayed") or gp
        skaters.append({
            "name": name, "team": (official or {}).get("teamAbbrevs") or row.get("team", ""),
            "pos": row.get("position", ""),
            "gp": gp, "value": round(value, 1), "pp_share": round(share, 2),
            "points": (official or {}).get("points", row.get("points", 0)),
            "goals": (official or {}).get("goals", row.get("goals", 0)),
            "ixg": round(row.get("individualxGoals") or 0.0, 1), "flags": flags,
        })
    skaters.sort(key=lambda r: -r["value"])
    for i, r in enumerate(skaters, 1):
        r["rank"] = i

    goalies, seen = [], set()
    team_games = defaultdict(int)
    for row in stats._goalies.values():
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        team_games[row.get("team")] += row.get("gamesPlayed") or 0
    seen = set()
    for row in stats._goalies.values():
        name = row["name"]
        if name in seen:
            continue
        seen.add(name)
        gp = row.get("gamesPlayed") or 0
        if gp < MIN_GP_GOALIE:
            continue
        value = stats.get_goalie_value(name)
        cf = contract_flag(name)
        if cf and not include_contracted:
            continue
        gsax = (row.get("xGoals") or 0.0) - (row.get("goals") or 0.0)
        share = gp / max(team_games.get(row.get("team"), 82), 1)
        goalies.append({
            "name": name, "team": row.get("team", ""), "pos": "G", "gp": gp,
            "value": round(value, 1), "gsax": round(gsax, 1), "start_share": round(share, 2),
            # what a roster slot actually yields: per-start points x how often he starts
            "per_team_game": round(value * share, 1),
            "flags": (["1A"] if share >= 0.6 else ["1B"]) + ([cf] if cf else []),
        })
    goalies.sort(key=lambda r: -(r["per_team_game"] if stats.points_mode else r["value"]))
    for i, r in enumerate(goalies, 1):
        r["rank"] = i

    return {
        "season": stats.season, "points_mode": stats.points_mode,
        "contracted_excluded": 0 if include_contracted else len(contracts or []),
        "scoring": ("points: " + " ".join(f"{v:g}*{k}" for k, v in config.FANTASY_POINTS_WEIGHTS.items())
                    + f" [{stats._values_source}]")
                   if stats.points_mode else "categories: " + ", ".join(stats._categories),
        "skaters": skaters[:top_n], "goalies": goalies[:max(20, top_n // 8)],
    }


def format_board_text(board: dict, by_position: bool = False) -> str:
    val = "FPPG" if board.get("points_mode") else "Val"
    lines = [f"DRAFT BOARD — values from {board['season']}-{board['season'] + 1}, {board['scoring']}"]
    if board.get("contracted_excluded"):
        lines.append(f"({board['contracted_excluded']} keeper contracts excluded; --include-contracted to show them)")
    lines.append("")
    lines.append(f"{'#':>3}  {'Player':24} {'Tm':4} {'Pos':4} {val:>6} {'GP':>3} {'Pts':>4} {'PP%':>4}  Flags")
    lines.append("-" * 72)
    for r in board["skaters"]:
        v = r['value'] / 10 if board.get("points_mode") else r['value']
        lines.append(f"{r['rank']:>3}  {r['name'][:24]:24} {r['team']:4} {r['pos']:4} {v:>6.1f} "
                     f"{r['gp']:>3} {r['points']:>4} {r['pp_share']*100:>3.0f}%  {' '.join(r['flags'])}")
    gval = "FP/st" if board.get("points_mode") else "Val"
    lines += ["", f"{'#':>3}  {'Goalie':24} {'Tm':4} {gval:>6} {'FP/wk':>6} {'GP':>3} {'GSAx':>5} {'Start%':>6}  Role"]
    lines.append("-" * 72)
    for r in board["goalies"]:
        v = r['value'] / 10 if board.get("points_mode") else r['value']
        wk = r['per_team_game'] / 10 * 3.5 if board.get("points_mode") else r['per_team_game']
        lines.append(f"{r['rank']:>3}  {r['name'][:24]:24} {r['team']:4} {v:>6.1f} {wk:>6.1f} {r['gp']:>3} "
                     f"{r['gsax']:>5.1f} {r['start_share']*100:>5.0f}%  {' '.join(r['flags'])}")
    if by_position:
        lines.append("")
        for pos in ("C", "L", "R", "D"):
            group = [r for r in board["skaters"] if r["pos"] == pos][:25]
            if not group:
                continue
            lines.append(f"TOP {pos}: " + ", ".join(f"{r['name']} ({r['value']:.0f})" for r in group))
    return "\n".join(lines)


def format_board_markdown(board: dict) -> str:
    out = [f"# Draft Board ({board['season']}-{board['season'] + 1} data)", "",
           f"Scoring: {board['scoring']}", "",
           "| # | Player | Team | Pos | Value | GP | Pts | PP share | Flags |", "|--:|---|---|---|--:|--:|--:|--:|---|"]
    for r in board["skaters"]:
        out.append(f"| {r['rank']} | {r['name']} | {r['team']} | {r['pos']} | {r['value']:.1f} | {r['gp']} | "
                   f"{r['points']} | {r['pp_share']:.0%} | {' '.join(r['flags'])} |")
    out += ["", "| # | Goalie | Team | Value | GP | GSAx | Start share | Role |", "|--:|---|---|--:|--:|--:|--:|---|"]
    for r in board["goalies"]:
        out.append(f"| {r['rank']} | {r['name']} | {r['team']} | {r['value']:.1f} | {r['gp']} | "
                   f"{r['gsax']:.1f} | {r['start_share']:.0%} | {' '.join(r['flags'])} |")
    return "\n".join(out) + "\n"
