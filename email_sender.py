"""Email delivery for the fantasy hockey daily briefing.

Uses Gmail SMTP with an app-specific password. No external packages required —
just Python's stdlib smtplib and email modules.

Setup:
1. Enable 2FA on your Google account: https://myaccount.google.com/security
2. Generate an app password: https://myaccount.google.com/apppasswords
3. Add to .env:
     GMAIL_USER=your.email@gmail.com
     GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
     EMAIL_TO=where.to.send@example.com
"""

import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class EmailSender:
    """Sends the daily briefing via Gmail SMTP."""

    def __init__(self):
        self.gmail_user = os.getenv("GMAIL_USER", "")
        self.gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
        self.email_to = os.getenv("EMAIL_TO", self.gmail_user)
        self.smtp_host = "smtp.gmail.com"
        self.smtp_port = 465  # SSL

    def is_configured(self) -> bool:
        """Check if email credentials are present."""
        return bool(self.gmail_user and self.gmail_password and self.email_to)

    def send_briefing(self, subject: str, lineup_html: str, waiver_html: str,
                      plain_text: str, scouting_html: str = ""):
        """Send the daily briefing email.

        Args:
            subject: Email subject line
            lineup_html: HTML for the lineup section
            waiver_html: HTML for the waiver section
            plain_text: Plain-text fallback for clients that don't support HTML
            scouting_html: HTML for the scouting section (goes first)
        """
        if not self.is_configured():
            logger.warning("Email not configured (missing GMAIL_USER, "
                           "GMAIL_APP_PASSWORD, or EMAIL_TO). Skipping send.")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"Fantasy Hockey Agent <{self.gmail_user}>"
        msg["To"] = self.email_to

        # Plain text fallback
        msg.set_content(plain_text)

        # HTML version (preferred)
        html_body = self.wrap_html(scouting_html + lineup_html, waiver_html)
        msg.add_alternative(html_body, subtype="html")

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port,
                                  context=context) as server:
                server.login(self.gmail_user, self.gmail_password)
                server.send_message(msg)
            logger.info("Briefing email sent to %s", self.email_to)
            return True
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            return False

    @staticmethod
    def wrap_html(lineup_html: str, waiver_html: str) -> str:
        """Wrap the briefing sections in a clean HTML template (also used for the site page)."""
        date_str = datetime.now().strftime("%A, %B %d %Y")
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fantasy Hockey Briefing</title>
<style>
  :root {{ --ink:#16181d; --muted:#6b7280; --line:#e6e8ec; --red:#c8102e; --green:#1f7a3a; --amber:#b7791f; --bg:#f4f5f7; --card:#fff; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 720px;
         margin: 0 auto; padding: 20px 16px 40px; color: var(--ink); background: var(--bg); line-height: 1.45; }}
  .header {{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; border-bottom: 3px solid var(--red);
             padding-bottom: 10px; margin-bottom: 18px; flex-wrap: wrap; }}
  .header h1 {{ margin: 0; font-size: 22px; }}
  .header .date {{ color: var(--muted); font-size: 14px; }}
  .section {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }}
  .section h2 {{ margin: 0 0 10px; font-size: 17px; }}
  .section h3 {{ margin: 18px 0 8px; font-size: 14px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
  .section h3:first-of-type {{ margin-top: 4px; }}
  .notes {{ background: #fff8e6; border: 1px solid #f1dfae; border-radius: 8px; padding: 8px 12px; font-size: 13px; color: #6b5210; margin-bottom: 12px; }}
  .notes div + div {{ margin-top: 4px; }}
  .player {{ padding: 8px 0; border-bottom: 1px solid var(--line); }}
  .player:last-child {{ border-bottom: none; }}
  .start {{ color: var(--green); }}
  .bench {{ color: var(--muted); }}
  .move {{ background: #fafafa; border-left: 3px solid var(--red); padding: 10px 12px; margin: 10px 0; border-radius: 6px; }}
  .move .add {{ color: var(--green); font-weight: 600; }}
  .move .drop {{ color: #999; text-decoration: line-through; }}
  .move .reason {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  .opp {{ padding: 10px 12px; margin: 8px 0; border-radius: 8px; background: #fafafa; border: 1px solid var(--line); border-left-width: 4px; }}
  .opp.now {{ border-left-color: var(--red); background: #fff6f6; }}
  .opp.rising {{ border-left-color: var(--green); }}
  .opp.watch {{ border-left-color: var(--amber); }}
  .opp.alert {{ border-left-color: #8a8f98; }}
  .opp .head {{ font-weight: 650; font-size: 15px; }}
  .opp .meta {{ color: var(--muted); font-size: 13px; margin: 3px 0 4px; }}
  .opp .why {{ font-size: 13.5px; }}
  .opp .why .trend {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; color: #374151; display:block; margin-top: 3px; }}
  .tag {{ display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 999px; background: #e9ecf1; color: #333; margin-right: 4px; }}
  .small {{ color: var(--muted); font-size: 12.5px; }}
  .archive {{ columns: 2; font-size: 13px; padding-left: 18px; }}
  .footer {{ text-align: center; color: var(--muted); font-size: 12px; margin-top: 28px; }}
  a {{ color: var(--red); }}
</style>
</head>
<body>
  <div class="header">
    <h1>🏒 Fantasy Hockey Briefing</h1>
    <div class="date">{date_str}</div>
  </div>
  {lineup_html}
  {waiver_html}
  <div class="footer">Generated by your fantasy hockey agent · make your moves before puck drop</div>
</body>
</html>"""


def format_lineup_html(changes: list[dict], league_id: str, team_id: str) -> str:
    """Format lineup changes as HTML."""
    if not changes:
        return """<div class="section">
  <h2>📋 Lineup</h2>
  <p>Your lineup is already optimal — no changes needed.</p>
</div>"""

    rows = []
    for c in changes:
        name = c.get("name", c.get("player_key", ""))
        pos = c.get("selected_position", "")
        score = c.get("score", 0)
        if pos == "BN":
            rows.append(f'<div class="player bench">🪑 BENCH &nbsp; {name}</div>')
        else:
            value_str = ""
            if score > 50:
                value_str = f' <span style="color:#999">(value {score - 50:.1f})</span>'
            rows.append(
                f'<div class="player start">✅ START &nbsp; '
                f'<strong>{name}</strong> → {pos}{value_str}</div>'
            )

    url = f"https://hockey.fantasysports.yahoo.com/hockey/{league_id}/{team_id}"
    return f"""<div class="section">
  <h2>📋 Lineup Moves</h2>
  {"".join(rows)}
  <p style="margin-top:16px;font-size:13px;">
    <a href="{url}">Open your team in Yahoo →</a>
  </p>
</div>"""


def format_waivers_html(recommendations: list[dict], league_id: str,
                        team_id: str) -> str:
    """Format waiver recommendations as HTML."""
    if not recommendations:
        return """<div class="section">
  <h2>🔄 Waivers</h2>
  <p>No clear upgrades available right now.</p>
</div>"""

    moves = []
    for i, rec in enumerate(recommendations, 1):
        add = rec["add_player"]
        drop = rec["drop_player"]
        pct = rec["improvement_pct"]
        reason = rec.get("reason", "")
        moves.append(f"""<div class="move">
  <div><strong>{i}.</strong>
    <span class="add">ADD {add['name']} ({add['nhl_team']})</span> /
    <span class="drop">DROP {drop['name']} ({drop['nhl_team']})</span>
  </div>
  <div class="reason">+{pct:.0f}% upgrade · {reason}</div>
</div>""")

    url = f"https://hockey.fantasysports.yahoo.com/hockey/{league_id}/{team_id}/players"
    return f"""<div class="section">
  <h2>🔄 Waiver Moves ({len(recommendations)} recommended)</h2>
  {"".join(moves)}
  <p style="margin-top:16px;font-size:13px;">
    <a href="{url}">Manage your roster in Yahoo →</a>
  </p>
</div>"""


def format_plain_text(changes: list[dict], recommendations: list[dict]) -> str:
    """Plain-text version of the briefing for non-HTML email clients."""
    lines = []
    lines.append("FANTASY HOCKEY BRIEFING")
    lines.append("=" * 40)
    lines.append("")
    lines.append("LINEUP MOVES")
    lines.append("-" * 40)
    if changes:
        for c in changes:
            name = c.get("name", "")
            pos = c.get("selected_position", "")
            if pos == "BN":
                lines.append(f"  BENCH  {name}")
            else:
                lines.append(f"  START  {name} -> {pos}")
    else:
        lines.append("  No changes needed.")
    lines.append("")
    lines.append("WAIVER MOVES")
    lines.append("-" * 40)
    if recommendations:
        for i, rec in enumerate(recommendations, 1):
            add = rec["add_player"]
            drop = rec["drop_player"]
            pct = rec["improvement_pct"]
            lines.append(
                f"  {i}. ADD {add['name']} ({add['nhl_team']}) / "
                f"DROP {drop['name']} ({drop['nhl_team']})"
            )
            lines.append(f"     +{pct:.0f}% upgrade · {rec.get('reason', '')}")
    else:
        lines.append("  No clear upgrades available.")
    lines.append("")
    return "\n".join(lines)

# ── Scouting section ──────────────────────────────────────────────

_SIGNAL_LABELS = {
    "injury_return": "Back from injury",
    "goalie_backup": "Goalie backup",
    "goalie_start_share": "Taking starts",
    "pp_promotion": "PP promotion",
    "line_promotion": "Line promotion",
    "regression_buy": "Buy low",
    "ownership_surge": "Ownership surge",
    "news": "News",
    "pp_demotion": "PP demotion",
    "line_demotion": "Line demotion",
    "running_hot": "Running hot",
    "injury_out": "Injured",
    "hot_streak": "Heating up",
    "cold_streak": "Cooling off",
}


def _tags(entry: dict) -> str:
    return "".join(f'<span class="tag">{_SIGNAL_LABELS.get(s, s)}</span>' for s in entry.get("signals", []))


def _opp_html(entry: dict, css: str, league_id: str, team_id: str) -> str:
    name = entry["player_name"]
    team = entry.get("nhl_team", "")
    pos = "/".join(p for p in entry.get("positions", []) if p not in ("BN", "IR", "IR+", "NA", "Util"))
    head = f"{name} ({team}{', ' + pos if pos else ''})"
    meta = []
    if entry.get("drop_candidate"):
        meta.append(f"drop candidate: {entry['drop_candidate']} (+{entry.get('gain', 0):.0f} value)")
    if entry.get("games_next_7") is not None:
        meta.append(f"{entry['games_next_7']} games next 7 days")
    if entry.get("note"):
        meta.append(entry["note"])
    if entry.get("rostered_by") and not entry.get("note"):
        meta.append(f"rostered by {entry['rostered_by']}")
    why = "<br>".join(
        f'<span class="trend">{line}</span>' if line.startswith("Trend") else line
        for line in entry.get("evidence_lines", [])
    )
    if entry.get("source_url"):
        why += f' <a href="{entry["source_url"]}">source</a>'
    return (f'<div class="opp {css}"><div class="head">{head}</div>'
            f'<div class="meta">{_tags(entry)} {" · ".join(meta)}</div>'
            f'<div class="why">{why}</div></div>')


def format_scouting_html(report: dict, league_id: str, team_id: str) -> str:
    """Render the ranked scouting report as HTML sections."""
    parts = []
    url = f"https://hockey.fantasysports.yahoo.com/hockey/{league_id}/{team_id}/players"
    notes = report.get("notes", [])
    if notes:
        parts.append('<div class="notes">' + "".join(f"<div>{n}</div>" for n in notes) + "</div>")
    sections = [
        ("act_now", "🚨 Act now: pick up", "now"),
        ("rising", "📈 Rising free agents", "rising"),
        ("roster_alerts", "🌡️ Your roster", "alert"),
        ("watchlist", "👀 Watchlist", "watch"),
    ]
    any_content = False
    for key, title, css in sections:
        entries = report.get(key, [])
        if not entries:
            continue
        any_content = True
        parts.append(f"<h3>{title}</h3>")
        parts.extend(_opp_html(e, css, league_id, team_id) for e in entries)
    still = report.get("still_available", [])
    if still:
        names = ", ".join(f"{e['player_name']} ({e.get('nhl_team', '')})" for e in still)
        parts.append(f'<p class="small">Still available from earlier alerts: {names}</p>')
    if not any_content and not still:
        parts.append("<p>No new opportunities today.</p>")
    body = "".join(parts)
    return f"""<div class="section">
  <h2>🔭 Scouting Report</h2>
  {body}
  <p style="margin-top:16px;font-size:13px;"><a href="{url}">Open the player list in Yahoo →</a></p>
</div>"""


def format_scouting_text(report: dict) -> str:
    lines = ["SCOUTING REPORT", "-" * 40]
    for note in report.get("notes", []):
        lines.append(f"  ! {note}")
    for key, title in (("act_now", "ACT NOW: PICK UP"), ("rising", "RISING FREE AGENTS"),
                       ("roster_alerts", "YOUR ROSTER"), ("watchlist", "WATCHLIST")):
        entries = report.get(key, [])
        if not entries:
            continue
        lines.append(f"  {title}")
        for e in entries:
            sig = ", ".join(_SIGNAL_LABELS.get(s, s) for s in e.get("signals", []))
            drop = f" / drop {e['drop_candidate']}" if e.get("drop_candidate") else ""
            note = f" — {e['note']}" if e.get("note") else ""
            lines.append(f"    {e['player_name']} ({e.get('nhl_team', '')}) [{sig}]{drop}{note}")
            for ev in e.get("evidence_lines", []):
                lines.append(f"      {ev}")
    still = report.get("still_available", [])
    if still:
        lines.append("  Still available: " + ", ".join(e["player_name"] for e in still))
    if len(lines) == 2:
        lines.append("  No new opportunities today.")
    lines.append("")
    return "\n".join(lines)
