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

    def send_raw(self, subject: str, html_body: str, plain_text: str) -> bool:
        """Send an already-rendered HTML email (used for the short link email)."""
        if not self.is_configured():
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"Fantasy Hockey Agent <{self.gmail_user}>"
        msg["To"] = self.email_to
        msg.set_content(plain_text)
        msg.add_alternative(f"<!DOCTYPE html><html><body style=\"margin:0;background:{C['bg']};\">{html_body}</body></html>",
                            subtype="html")
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                server.login(self.gmail_user, self.gmail_password)
                server.send_message(msg)
            logger.info("Link email sent to %s", self.email_to)
            return True
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            return False

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
        """Wrap the briefing sections in the dark template (email and site page).

        Colors follow weddingsnipe.ca (gold accent, condensed display font)
        on a near-black base. Structural elements get inline styles so Gmail
        and other clients that ignore <style> still render the design.
        """
        date_str = datetime.now().strftime("%A, %B %d %Y")
        body = _inline_styles(lineup_html + waiver_html)
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Fantasy Hockey Briefing</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
  body {{ margin:0; background:{C['bg']}; color:{C['text']}; font-family:{FONT_BODY}; line-height:1.45; }}
  a {{ color:{C['accent']}; }}
  .archive {{ columns:2; font-size:13px; padding-left:18px; color:{C['muted']}; }}
  .archive a {{ color:{C['muted']}; }}
  @media (max-width: 480px) {{ .archive {{ columns:1; }} .wrap {{ padding:14px 12px 32px !important; }} }}
</style>
</head>
<body style="margin:0;background:{C['bg']};">
<div class="wrap" style="max-width:720px;margin:0 auto;padding:22px 16px 40px;background:{C['bg']};color:{C['text']};font-family:{FONT_BODY};">
  <div class="header" style="{ST['header']}">
    <h1 style="{ST['h1']}">🏒 Fantasy Hockey Briefing</h1>
    <div class="date" style="{ST['date']}">{date_str}</div>
  </div>
  {body}
  <div class="footer" style="{ST['footer']}">Generated by your fantasy hockey agent · make your moves before puck drop</div>
</div>
</body>
</html>"""


# ── Design tokens and email-safe inlining ────────────────────────

C = {
    "bg": "#0b0c0f", "surface": "#14161a", "card": "#1a1d23", "border": "#262a31",
    "text": "#eef1f5", "muted": "#98a2b3", "accent": "#e0b04c", "red": "#ff5a5a",
    "green": "#4cd77a", "gray": "#7d8796", "mono": "#b7c0cc",
}
FONT_BODY = "'Atkinson Hyperlegible', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
FONT_DISPLAY = "'Barlow Condensed', 'Arial Narrow', Impact, sans-serif"

ST = {
    "header": f"display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;"
              f"border-bottom:2px solid {C['accent']};padding-bottom:10px;margin-bottom:18px;",
    "h1": f"margin:0;font-family:{FONT_DISPLAY};font-weight:700;font-size:30px;letter-spacing:.02em;"
          f"text-transform:uppercase;color:{C['accent']};",
    "date": f"color:{C['muted']};font-size:14px;",
    "section": f"background:{C['surface']};border:1px solid {C['border']};border-radius:12px;"
               f"padding:16px 18px;margin-bottom:16px;",
    "h2": f"margin:0 0 10px;font-family:{FONT_DISPLAY};font-weight:700;font-size:22px;letter-spacing:.03em;"
          f"text-transform:uppercase;color:{C['accent']};",
    "h3": f"margin:16px 0 8px;font-family:{FONT_DISPLAY};font-weight:600;font-size:15px;letter-spacing:.08em;"
          f"text-transform:uppercase;color:{C['muted']};",
    "notes": f"background:#221d10;border:1px solid #4a3d1a;border-radius:8px;padding:8px 12px;"
             f"font-size:13px;color:#e6d3a0;margin-bottom:12px;",
    "player": f"padding:8px 0;border-bottom:1px solid {C['border']};",
    "start": f"color:{C['green']};",
    "bench": f"color:{C['muted']};",
    "move": f"background:{C['card']};border-left:3px solid {C['accent']};padding:10px 12px;margin:10px 0;border-radius:6px;",
    "add": f"color:{C['green']};font-weight:700;",
    "drop": f"color:{C['gray']};text-decoration:line-through;",
    "reason": f"color:{C['muted']};font-size:13px;margin-top:4px;",
    "opp": f"padding:10px 12px;margin:8px 0;border-radius:8px;background:{C['card']};"
           f"border:1px solid {C['border']};border-left:4px solid {C['gray']};",
    "opp now": f"border-left-color:{C['red']};background:#221416;",
    "opp rising": f"border-left-color:{C['green']};",
    "opp watch": f"border-left-color:{C['accent']};",
    "opp alert": f"border-left-color:{C['gray']};",
    "head": f"font-weight:700;font-size:16px;color:{C['text']};",
    "meta": f"color:{C['muted']};font-size:13px;margin:3px 0 4px;",
    "why": f"font-size:13.5px;color:{C['text']};",
    "trend": f"font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;"
             f"color:{C['mono']};display:block;margin-top:3px;",
    "tag": f"display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;background:#262a31;"
           f"color:#d5dbe3;margin-right:4px;",
    "small": f"color:{C['muted']};font-size:12.5px;",
    "footer": f"text-align:center;color:{C['muted']};font-size:12px;margin-top:28px;",
}


def _inline_styles(markup: str) -> str:
    """Attach inline styles for known classes so clients without <style> support match."""
    import re

    def repl(m):
        classes = m.group(1).split()
        css = ""
        for cls in classes:
            css += ST.get(cls, "")
        combo = " ".join(classes[:2])
        css += ST.get(combo, "")
        return f'class="{m.group(1)}" style="{css}"' if css else m.group(0)

    markup = re.sub(r'class="([^"]+)"(?![^>]*style=)', repl, markup)
    # section headings: inline style on <h2>/<h3> inside sections
    markup = re.sub(r"<h2>", f'<h2 style="{ST["h2"]}">', markup)
    markup = re.sub(r"<h3>", f'<h3 style="{ST["h3"]}">', markup)
    return markup


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
