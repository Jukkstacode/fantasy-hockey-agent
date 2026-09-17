"""Fantasy point formulas applied to MoneyPuck rows.

Skater FP = 3*G + 2*A + 1*(+/-) + 0.25*PIM + 1*PPP + 1*SHP + 1.5*GWG
Goalie FP = 3*W - 1.5*GA + 0.2*(SA - GA) + 6*SO
(weights come from config so the formula can be changed in .env)

MoneyPuck approximations:
  +/-   on-ice EV goal differential plus short-handed goals for (PP goals for
        and PK goals against are excluded, matching the NHL definition)
  PIM   2 minutes per penalty taken (majors/misconducts undercounted)
  GWG   not available per game in MoneyPuck; ignored
  W     team scored more than it allowed in regulation/OT (shootout wins not
        credited); starter = goalie with the most ice time
  SO    zero goals against with a full game (>= 58 minutes)
"""

import config


def skater_points(g: float, a: float, plus_minus: float, pim: float, ppp: float, shp: float,
                  gwg: float = 0.0) -> float:
    w = config.FANTASY_POINTS_WEIGHTS
    return (w.get("G", 0) * g + w.get("A", 0) * a + w.get("+/-", 0) * plus_minus
            + w.get("PIM", 0) * pim + w.get("PPP", 0) * ppp + w.get("SHP", 0) * shp
            + w.get("GWG", 0) * gwg + w.get("P", 0) * (g + a))


def goalie_points(win: int, ga: float, sa: float, shutout: int) -> float:
    w = config.GOALIE_POINTS_WEIGHTS
    return (w.get("W", 0) * win + w.get("GA", 0) * ga + w.get("SV", 0) * max(sa - ga, 0)
            + w.get("SO", 0) * shutout)


def skater_points_from_season_rows(all_row: dict, ev_row: dict, pp_row: dict, pk_row: dict) -> float:
    """Season fantasy points from the situation tables."""
    g = all_row.get("goals") or 0
    a = (all_row.get("points") or 0) - g
    plus_minus = ((ev_row or {}).get("goalsFor") or 0) - ((ev_row or {}).get("goalsAgainst") or 0) \
        + ((pk_row or {}).get("goalsFor") or 0)
    pim = 2 * (all_row.get("penaltiesTaken") or 0)
    ppp = (pp_row or {}).get("points") or 0
    shp = (pk_row or {}).get("points") or 0
    return skater_points(g, a, plus_minus, pim, ppp, shp)


def skater_points_from_game_rows(all_row: dict, ev_row: dict, pp_row: dict, pk_row: dict) -> float:
    """Single-game fantasy points from the situation game logs."""
    g = all_row.get("goals") or 0
    a = (all_row.get("primaryAssists") or 0) + (all_row.get("secondaryAssists") or 0)
    plus_minus = ((ev_row or {}).get("goalsFor") or 0) - ((ev_row or {}).get("goalsAgainst") or 0) \
        + ((pk_row or {}).get("goalsFor") or 0)
    pim = 2 * (all_row.get("penaltiesTaken") or 0)

    def pts(r):
        return ((r or {}).get("goals") or 0) + ((r or {}).get("primaryAssists") or 0) + ((r or {}).get("secondaryAssists") or 0)

    return skater_points(g, a, plus_minus, pim, pts(pp_row), pts(pk_row))


# ── NHL API rows (exact league stats) ────────────────────────────

def skater_points_from_nhl_season(row: dict) -> float:
    """Season totals from api.nhle.com skater/summary."""
    g = row.get("goals") or 0
    a = row.get("assists") or 0
    return skater_points(g, a, row.get("plusMinus") or 0, row.get("penaltyMinutes") or 0,
                         row.get("ppPoints") or 0, row.get("shPoints") or 0, row.get("gameWinningGoals") or 0)


def skater_points_from_nhl_game(row: dict) -> float:
    """One game from api-web.nhle.com player game-log."""
    return skater_points(row.get("goals") or 0, row.get("assists") or 0, row.get("plusMinus") or 0,
                         row.get("pim") or 0, row.get("powerPlayPoints") or 0,
                         row.get("shorthandedPoints") or 0, row.get("gameWinningGoals") or 0)


def goalie_points_from_nhl_season(row: dict) -> float:
    """Season totals from api.nhle.com goalie/summary."""
    ga = row.get("goalsAgainst") or 0
    sa = row.get("shotsAgainst") or 0
    return goalie_points(row.get("wins") or 0, ga, sa, row.get("shutouts") or 0)


def goalie_points_from_nhl_game(row: dict) -> float:
    """One game from the goalie game-log (decision W/L/O; no points for L/OTL)."""
    win = 1 if row.get("decision") == "W" else 0
    ga = row.get("goalsAgainst") or 0
    sa = row.get("shotsAgainst") or 0
    return goalie_points(win, ga, sa, row.get("shutouts") or 0)
