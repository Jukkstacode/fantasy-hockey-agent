"""Dashboard-style HTML for the hosted report (hockey.bimm.dev).

Not email-safe on purpose: real CSS grid, web fonts, inline SVG sparklines,
NHL headshots. The email is a short link to this page.

Palette follows weddingsnipe.ca (gold accent, Barlow Condensed display face)
on a near-black base. Status colors: critical red = act now, good green =
rising, warning amber = watch, serious orange = roster warnings. Every
status color ships with an icon + label, never color alone.
"""

import html
from datetime import datetime, date

import config

# ── tokens ───────────────────────────────────────────────────────
C = {
    "bg": "#0b0c0f", "surface": "#14161a", "card": "#1a1d23", "card2": "#20242b", "border": "#262a31",
    "text": "#eef1f5", "muted": "#98a2b3", "faint": "#5f6874", "accent": "#e0b04c",
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
    "spark": "#6f7d92",     # de-emphasis hue for the sparkline body
}
SIGNAL = {
    "dropped":            ("🗑️", "Just dropped"),
    "injury_return":      ("🩹", "Back from injury"),
    "goalie_backup":      ("🥅", "Goalie backup"),
    "goalie_start_share": ("🥅", "Taking starts"),
    "pp_promotion":       ("⚡", "PP promotion"),
    "line_promotion":     ("⬆️", "Line promotion"),
    "regression_buy":     ("🎯", "Buy low"),
    "ownership_surge":    ("📈", "Ownership surge"),
    "news":               ("📰", "News"),
    "hot_streak":         ("🔥", "Heating up"),
    "pp_demotion":        ("⚡", "PP demotion"),
    "line_demotion":      ("⬇️", "Line demotion"),
    "running_hot":        ("🎲", "Running hot"),
    "cold_streak":        ("🧊", "Cooling off"),
    "injury_out":         ("🚑", "Injured"),
}
SECTIONS = [
    ("act_now", "Act now", "Free agents worth a roster move today", C["critical"], "🚨"),
    ("rising", "Rising free agents", "Available players trending the right way", C["good"], "📈"),
    ("roster_alerts", "Your roster", "Warnings about players you own", C["serious"], "🌡️"),
    ("watchlist", "Watchlist", "Worth tracking, not worth a drop yet", C["warning"], "👀"),
]


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


# ── sparkline ────────────────────────────────────────────────────

def sparkline_svg(trend: dict, width: int = 160, height: int = 36) -> str:
    """2px line, round joins; last N points in the accent; end marker r=4 with
    a 2px surface ring; per-point <title> as the hover layer."""
    vals = trend.get("per_game") or []
    if len(vals) < 2:
        return ""
    n = len(vals)
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pad = 5
    xs = [pad + i * (width - 2 * pad) / (n - 1) for i in range(n)]
    ys = [height - pad - (v - lo) / span * (height - 2 * pad) for v in vals]
    recent_n = min(trend.get("recent_n", 5), n)
    split = n - recent_n
    pts = lambda a, b: " ".join(f"{xs[i]:.1f},{ys[i]:.1f}" for i in range(a, b))
    body = f'<polyline points="{pts(0, split + 1)}" fill="none" stroke="{C["spark"]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' if split >= 1 else ""
    recent = f'<polyline points="{pts(max(split, 0), n)}" fill="none" stroke="{C["accent"]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
    unit = "FP" if not trend.get("is_goalie") else "FP"
    dots = "".join(
        f'<circle cx="{xs[i]:.1f}" cy="{ys[i]:.1f}" r="6" fill="transparent"><title>Game {i + 1}: {vals[i]:.1f} {unit}</title></circle>'
        for i in range(n)
    )
    end = (f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="5" fill="{C["card"]}"/>'
           f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3.5" fill="{C["accent"]}"/>')
    return (f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="Fantasy points per game, last {n} games">{body}{recent}{dots}{end}</svg>')


def trend_block(trend: dict) -> str:
    if not trend:
        return '<div class="trend"><span class="muted">No recent game data</span></div>'
    unit = "FP/start" if trend.get("is_goalie") else "FPPG"
    n = trend.get("recent_n", 5)
    stale = trend.get("stale")
    caption = (f'Last {n}: <b>{trend["recent_avg"]:.1f}</b> · prior <b>{trend["base_avg"]:.1f}</b> · '
               f'{trend["games"]} g avg <b>{trend["season_avg"]:.1f}</b> {unit}')
    if stale:
        caption = f'<span class="pill faint">end of last season</span> ' + caption
    sus = trend.get("sustainable")
    tail = ""
    if trend.get("ratio", 1) >= 1.2 and sus is not None and not stale:
        tail = ' <span class="pill good">shot volume up</span>' if sus else ' <span class="pill warn">same shot volume</span>'
    return f'<div class="trend">{sparkline_svg(trend)}<div class="trend-cap">{caption}{tail}</div></div>'


# ── cards ────────────────────────────────────────────────────────

def headshot(entry: dict, size: int = 56) -> str:
    url = entry.get("headshot")
    initials = "".join(w[0] for w in str(entry.get("player_name") or entry.get("name") or "?").split()[:2]).upper()
    if url:
        return (f'<img class="mug" src="{esc(url)}" width="{size}" height="{size}" alt="" loading="lazy" '
                f'onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),{{className:\'mug mug-fallback\',textContent:\'{initials}\'}}))">')
    return f'<div class="mug mug-fallback">{initials}</div>'


def tags(signals: list) -> str:
    out = []
    for s in signals:
        icon, label = SIGNAL.get(s, ("•", s.replace("_", " ")))
        out.append(f'<span class="tag"><span aria-hidden="true">{icon}</span> {esc(label)}</span>')
    return "".join(out)


def opp_card(e: dict, accent: str) -> str:
    team = e.get("nhl_team") or e.get("team") or ""
    pos = "/".join(p for p in e.get("positions", []) if p not in ("BN", "IR", "IR+", "NA", "Util"))
    unit = e.get("unit")
    why = [l for l in e.get("evidence_lines", []) if not l.startswith(("Trend:", "DailyFaceoff:"))]
    if e.get("trend"):
        # the chart and caption already carry the numbers; keep only the claim
        why = [l.split(":", 1)[0] + "." if l.startswith(("Heating up:", "Cooling off:")) else l for l in why]
    facts = []
    if e.get("drop_candidate"):
        facts.append(f'<span class="fact"><span class="k">Drop</span> {esc(e["drop_candidate"])} <span class="muted">(+{e.get("gain", 0):.0f})</span></span>')
    if e.get("games_next_7") is not None:
        facts.append(f'<span class="fact"><span class="k">Next 7d</span> {e["games_next_7"]} games</span>')
    if unit:
        facts.append(f'<span class="fact"><span class="k">Lines</span> {esc(unit)}</span>')
    if e.get("note"):
        facts.append(f'<span class="fact muted">{esc(e["note"])}</span>')
    src = f' <a class="src" href="{esc(e["source_url"])}" target="_blank" rel="noopener">source ↗</a>' if e.get("source_url") else ""
    logo = f'<img class="logo" src="https://assets.nhle.com/logos/nhl/svg/{esc(team)}_dark.svg" alt="" width="22" height="22" loading="lazy" onerror="this.remove()">' if team else ""
    return f'''<article class="card" style="--accent:{accent}">
  <div class="card-top">
    {headshot(e)}
    <div class="who">
      <div class="name">{esc(e.get("player_name"))}</div>
      <div class="sub">{logo}<span>{esc(team)}</span>{f'<span class="dot">·</span><span>{esc(pos)}</span>' if pos else ''}</div>
      <div class="tags">{tags(e.get("signals", []))}</div>
    </div>
  </div>
  {trend_block(e.get("trend"))}
  {"".join(f'<p class="why">{esc(w)}</p>' for w in why)}{src}
  {f'<div class="facts">{"".join(facts)}</div>' if facts else ''}
</article>'''


def roster_row(r: dict) -> str:
    t = r.get("trend")
    status = r.get("status") or ""
    st = f'<span class="pill crit" title="{esc(r.get("status_full"))}">{esc(status)}</span>' if status else ""
    alert = ""
    if r.get("alert"):
        icon, label = SIGNAL.get(r["alert"], ("•", r["alert"]))
        alert = f'<span class="pill warn" title="{esc(r.get("alert_text"))}">{icon} {esc(label)}</span>'
    unit = f'<span class="muted small">{esc(r["unit"])}</span>' if r.get("unit") else ""
    num = lambda v: f"{v:.1f}" if isinstance(v, (int, float)) else "–"
    return f'''<tr>
  <td class="pl">{headshot(r, 36)}<div><div class="name">{esc(r["name"])}</div><div class="sub small">{esc(r.get("team"))} · {esc("/".join(r.get("positions", [])))} {unit}</div></div></td>
  <td>{sparkline_svg(t, 120, 30) if t else '<span class="faint">—</span>'}</td>
  <td class="num">{num(t["recent_avg"]) if t else "–"}</td>
  <td class="num">{num(t["season_avg"]) if t else num(r.get("value"))}</td>
  <td>{st}{alert}</td>
</tr>'''


# ── page ─────────────────────────────────────────────────────────

def stat_tile(label: str, value, note: str = "", color: str = None) -> str:
    style = f' style="--v:{color}"' if color else ""
    return f'<div class="tile"{style}><div class="tile-label">{esc(label)}</div><div class="tile-value">{esc(value)}</div><div class="tile-note">{esc(note)}</div></div>'


def render_page(report: dict, changes=None, recommendations=None) -> str:
    now = datetime.now()
    meta = report.get("meta", {})
    n_now, n_rise = len(report.get("act_now", [])), len(report.get("rising", []))
    n_alerts = len(report.get("roster_alerts", []))
    opener = meta.get("next_game_date") or ""
    days_to = ""
    if opener:
        try:
            d = (date.fromisoformat(opener) - date.today()).days
            days_to = f"{d} days" if d > 0 else ("tonight" if d == 0 else "")
        except ValueError:
            pass
    src_mode = {"api": "Yahoo API", "web": "Yahoo (session)", "predraft": "Yahoo (pre-draft)", "none": "roster file"}.get(meta.get("yahoo_mode"), "")

    tiles = [
        stat_tile("Pickups to make", n_now, "act now" if n_now else "nothing urgent", C["critical"] if n_now else None),
        stat_tile("Rising free agents", n_rise, "trending up", C["good"] if n_rise else None),
        stat_tile("Roster warnings", n_alerts, "on your team", C["serious"] if n_alerts else None),
        stat_tile("Next NHL game", opener[5:].replace("-", "/") if opener else "—", days_to, None),
    ]

    notes = report.get("notes", [])
    notes_html = f'<div class="notes">{"".join(f"<div>⚠️ {esc(n)}</div>" for n in notes)}</div>' if notes else ""

    sections = []
    for key, title, sub, color, icon in SECTIONS:
        entries = report.get(key, [])
        if key == "roster_alerts":
            continue   # rendered inside the roster section below
        if entries:
            cards = "".join(opp_card(e, color) for e in entries)
            body = f'<div class="grid">{cards}</div>'
        else:
            empty = {
                "act_now": "No free agent is worth a move right now.",
                "rising": "Nobody available is trending up yet." + (" Streak detection starts with the season." if meta.get("trend_stale") else ""),
                "watchlist": "Nothing to track.",
            }.get(key, "Nothing here.")
            body = f'<div class="empty">{esc(empty)}</div>'
        sections.append(f'''<section class="sec" style="--accent:{color}">
  <header class="sec-head"><span class="sec-icon" aria-hidden="true">{icon}</span><div><h2>{esc(title)}</h2><p class="sec-sub">{esc(sub)}</p></div><span class="count">{len(entries)}</span></header>
  {body}
</section>''')

    roster = report.get("roster", [])
    if roster:
        rows = "".join(roster_row(r) for r in roster)
        roster_html = f'''<section class="sec" style="--accent:{C["serious"]}">
  <header class="sec-head"><span class="sec-icon" aria-hidden="true">🌡️</span><div><h2>Your roster</h2><p class="sec-sub">{len(roster)} players · fantasy points per game, last {config.TREND_HOT_GAMES} vs season</p></div><span class="count">{n_alerts} warning{"s" if n_alerts != 1 else ""}</span></header>
  <div class="tablewrap"><table class="roster">
    <thead><tr><th>Player</th><th>Trend</th><th class="num">Last {config.TREND_HOT_GAMES}</th><th class="num">Season</th><th>Status</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
</section>'''
    else:
        roster_html = ""

    lineup_html = ""
    if changes:
        items = "".join(
            f'<li class="{"bench" if c["selected_position"] == "BN" else "start"}">'
            f'{"🪑 Bench" if c["selected_position"] == "BN" else "✅ Start"} <b>{esc(c.get("name", ""))}</b>'
            f'{"" if c["selected_position"] == "BN" else " → " + esc(c["selected_position"])}</li>'
            for c in changes)
        lineup_html = f'<section class="sec" style="--accent:{C["accent"]}"><header class="sec-head"><span class="sec-icon">📋</span><div><h2>Lineup</h2><p class="sec-sub">Moves for today</p></div></header><ul class="lineup">{items}</ul></section>'
    elif changes is not None:
        lineup_html = f'<section class="sec" style="--accent:{C["accent"]}"><header class="sec-head"><span class="sec-icon">📋</span><div><h2>Lineup</h2><p class="sec-sub">Today</p></div></header><div class="empty">Lineup is already optimal.</div></section>'

    waiver_html = ""
    if recommendations:
        moves = "".join(
            f'<div class="move"><b class="good-t">ADD {esc(r["add_player"]["name"])}</b> <span class="muted">({esc(r["add_player"]["nhl_team"])})</span>'
            f' <span class="muted">for</span> <s class="muted">{esc(r["drop_player"]["name"])}</s>'
            f'<div class="small muted">+{r["improvement_pct"]:.0f}% · {esc(r.get("reason", ""))}</div></div>'
            for r in recommendations)
        waiver_html = f'<section class="sec" style="--accent:{C["good"]}"><header class="sec-head"><span class="sec-icon">🔄</span><div><h2>Waiver moves</h2><p class="sec-sub">Value upgrades over your weakest slot</p></div></header>{moves}</section>'

    footer_bits = [b for b in [
        f"Rosters: {src_mode}" if src_mode else "",
        f"Values: {meta.get('values_source')}" if meta.get("values_source") else "",
        f"Trends: {meta.get('trend_source')}" if meta.get("trend_source") else "",
        "News: on" if meta.get("news_enabled") else "News: off",
        f"Lines: DailyFaceoff {meta.get('dfo_date')}" if meta.get("dfo_date") else "",
    ] if b]

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Fantasy Hockey Briefing</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {{ --bg:{C["bg"]}; --surface:{C["surface"]}; --card:{C["card"]}; --card2:{C["card2"]}; --border:{C["border"]};
           --text:{C["text"]}; --muted:{C["muted"]}; --faint:{C["faint"]}; --gold:{C["accent"]};
           --good:{C["good"]}; --warn:{C["warning"]}; --serious:{C["serious"]}; --crit:{C["critical"]}; }}
  * {{ box-sizing: border-box; }}
  html {{ background: var(--bg); }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:'Atkinson Hyperlegible', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; line-height:1.45; }}
  .wrap {{ max-width: 1040px; margin: 0 auto; padding: 24px 20px 48px; }}
  a {{ color: var(--gold); }}
  h1, h2, .tile-value, .count {{ font-family: 'Barlow Condensed', 'Arial Narrow', sans-serif; }}
  .top {{ display:flex; align-items:flex-end; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom: 18px; }}
  .top h1 {{ margin:0; font-size: 44px; line-height:1; font-weight:700; letter-spacing:.02em; text-transform:uppercase; color: var(--gold); }}
  .top .date {{ color: var(--muted); font-size: 15px; }}
  .top .date b {{ color: var(--text); font-weight: 400; }}
  .tiles {{ display:grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }}
  .tile {{ background: var(--surface); border:1px solid var(--border); border-radius: 12px; padding: 14px 16px; border-top: 3px solid var(--v, var(--border)); }}
  .tile-label {{ color: var(--muted); font-size: 13px; }}
  .tile-value {{ font-size: 40px; font-weight: 700; line-height: 1.1; margin: 2px 0; color: var(--v, var(--text)); }}
  .tile-note {{ color: var(--faint); font-size: 12.5px; }}
  .notes {{ background:#221d10; border:1px solid #4a3d1a; border-radius:10px; padding:10px 14px; font-size:13.5px; color:#e6d3a0; margin-bottom:18px; }}
  .notes div + div {{ margin-top: 4px; }}
  .sec {{ background: var(--surface); border:1px solid var(--border); border-radius: 14px; padding: 18px 18px 14px; margin-bottom: 18px; }}
  .sec-head {{ display:flex; align-items:center; gap: 12px; margin-bottom: 12px; }}
  .sec-icon {{ font-size: 22px; width: 40px; height: 40px; display:grid; place-items:center; border-radius: 10px; background: color-mix(in srgb, var(--accent) 18%, transparent); }}
  .sec h2 {{ margin:0; font-size: 26px; font-weight: 700; letter-spacing: .03em; text-transform: uppercase; color: var(--gold); line-height: 1.05; }}
  .sec-sub {{ margin: 1px 0 0; color: var(--muted); font-size: 13px; }}
  .count {{ margin-left:auto; font-size: 22px; color: var(--accent); font-weight: 700; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }}
  .card {{ background: var(--card); border:1px solid var(--border); border-left: 4px solid var(--accent); border-radius: 12px; padding: 14px 14px 12px; display:flex; flex-direction:column; gap: 10px; }}
  .card-top {{ display:flex; gap: 12px; align-items:center; }}
  .mug {{ width:56px; height:56px; border-radius:50%; object-fit:cover; object-position: top; background: var(--card2); border: 2px solid var(--border); flex: none; }}
  .mug-fallback {{ display:grid; place-items:center; color: var(--muted); font-weight:700; font-size: 16px; }}
  .who {{ min-width: 0; }}
  .name {{ font-weight: 700; font-size: 17px; line-height: 1.15; }}
  .sub {{ color: var(--muted); font-size: 13px; display:flex; align-items:center; gap: 6px; margin-top: 2px; }}
  .sub .logo {{ width: 22px; height: 22px; }}
  .dot {{ color: var(--faint); }}
  .tags {{ margin-top: 6px; display:flex; flex-wrap:wrap; gap: 4px; }}
  .tag {{ font-size: 11.5px; padding: 2px 8px; border-radius: 999px; background: var(--card2); color: #d5dbe3; border: 1px solid var(--border); }}
  .trend {{ display:flex; align-items:center; gap: 12px; flex-wrap: wrap; background: var(--card2); border-radius: 8px; padding: 8px 10px; }}
  .trend-cap {{ font-size: 12.5px; color: var(--muted); }}
  .trend-cap b {{ color: var(--text); font-weight: 700; }}
  .spark {{ flex: none; display:block; }}
  .why {{ margin: 0; font-size: 14px; }}
  .src {{ font-size: 12.5px; }}
  .facts {{ display:flex; flex-wrap:wrap; gap: 6px 14px; font-size: 13px; padding-top: 8px; border-top: 1px solid var(--border); }}
  .fact .k {{ color: var(--faint); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; margin-right: 4px; }}
  .pill {{ display:inline-block; font-size: 11px; padding: 1px 7px; border-radius: 999px; border:1px solid var(--border); color: var(--muted); margin-left: 4px; }}
  .pill.good {{ color: var(--good); border-color: color-mix(in srgb, var(--good) 50%, transparent); }}
  .pill.warn {{ color: var(--warn); border-color: color-mix(in srgb, var(--warn) 50%, transparent); }}
  .pill.crit {{ color: var(--crit); border-color: color-mix(in srgb, var(--crit) 50%, transparent); }}
  .pill.faint {{ color: var(--faint); }}
  .empty {{ color: var(--muted); padding: 10px 4px 6px; font-size: 14px; }}
  .tablewrap {{ overflow-x: auto; }}
  table.roster {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  table.roster th {{ text-align:left; color: var(--faint); font-weight: 400; font-size: 11.5px; text-transform: uppercase; letter-spacing: .06em; padding: 6px 8px; border-bottom: 1px solid var(--border); }}
  table.roster td {{ padding: 8px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  table.roster tr:last-child td {{ border-bottom: none; }}
  table.roster .pl {{ display:flex; align-items:center; gap: 10px; }}
  table.roster .pl .mug {{ width:36px; height:36px; border-width: 1px; }}
  table.roster .num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .small {{ font-size: 12.5px; }} .muted {{ color: var(--muted); }} .faint {{ color: var(--faint); }} .good-t {{ color: var(--good); }}
  .lineup {{ list-style:none; padding:0; margin:0; }} .lineup li {{ padding: 6px 0; border-bottom: 1px solid var(--border); }} .lineup li:last-child {{ border-bottom:none; }}
  .lineup .bench {{ color: var(--muted); }}
  .move {{ background: var(--card); border-left: 3px solid var(--good); padding: 10px 12px; margin: 8px 0; border-radius: 8px; }}
  .foot {{ color: var(--faint); font-size: 12px; display:flex; flex-wrap:wrap; gap: 6px 14px; margin-top: 8px; }}
  details.archive {{ margin-top: 14px; color: var(--muted); font-size: 13px; }}
  details.archive summary {{ cursor: pointer; color: var(--muted); }}
  details.archive ul {{ columns: 3; padding-left: 18px; margin: 8px 0 0; }}
  @media (max-width: 760px) {{ .tiles {{ grid-template-columns: repeat(2, 1fr); }} .top h1 {{ font-size: 34px; }} details.archive ul {{ columns: 1; }} .wrap {{ padding: 16px 14px 40px; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <h1>🏒 Fantasy Hockey Briefing</h1>
    <div class="date">{esc(now.strftime("%A, %B %d"))} · <b>{esc(now.strftime("%H:%M"))}</b>{f' · {esc(meta.get("team_name"))}' if meta.get("team_name") else ''}</div>
  </div>
  <div class="tiles">{"".join(tiles)}</div>
  {notes_html}
  {sections[0]}
  {sections[1]}
  {roster_html}
  {lineup_html}
  {waiver_html}
  {sections[2]}
  <div class="foot">{"".join(f"<span>{esc(b)}</span>" for b in footer_bits)}</div>
  <div class="footer-slot"></div>
</div>
</body>
</html>'''
