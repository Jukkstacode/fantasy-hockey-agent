"""Yahoo OAuth2 authorization-code flow for apps registered with a redirect URI.

Yahoo's current developer portal registers apps with explicit redirect URIs
and no longer offers the old out-of-band ("oob") code display that the
yahoo_oauth library assumes. This module runs the standard flow instead:

1. Print an authorization URL (redirect_uri = the one registered on the app).
2. The user approves in a browser. Yahoo redirects to that URI with ?code=...
   For a localhost URI the page won't load, but the code is in the address bar.
3. Exchange the code for tokens and save them in the .env format yfpy reads.

After that, yfpy refreshes the token itself on every run.
"""

import logging
import secrets
import time
from urllib.parse import urlencode, urlparse, parse_qs

import requests

import config

logger = logging.getLogger(__name__)

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"


def build_authorize_url(redirect_uri: str, state: str = None) -> str:
    state = state or secrets.token_urlsafe(12)
    params = {
        "client_id": config.YAHOO_CONSUMER_KEY,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "language": "en-us",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def code_from_input(text: str) -> str:
    """Accept either a bare code or the full URL Yahoo redirected to."""
    text = text.strip()
    if "code=" in text:
        parsed = urlparse(text if "://" in text else "http://x/?" + text.lstrip("?"))
        codes = parse_qs(parsed.query).get("code")
        if codes:
            return codes[0]
    return text


def exchange_code(code: str, redirect_uri: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        auth=(config.YAHOO_CONSUMER_KEY, config.YAHOO_CONSUMER_SECRET),
        data={"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code},
        timeout=30,
    )
    data = resp.json()
    if resp.status_code != 200 or "access_token" not in data:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): "
                           f"{data.get('error_description') or data.get('error') or resp.text[:200]}")
    return data


def save_token(data: dict) -> None:
    """Write auth/.env in the layout yfpy expects (backing up any existing file)."""
    path = config.AUTH_DIR / ".env"
    if path.exists():
        backup = config.AUTH_DIR / f".env.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        path.rename(backup)
        logger.info("Previous token saved to %s", backup.name)
    lines = {
        "YAHOO_ACCESS_TOKEN": data["access_token"],
        "YAHOO_CONSUMER_KEY": config.YAHOO_CONSUMER_KEY,
        "YAHOO_CONSUMER_SECRET": config.YAHOO_CONSUMER_SECRET,
        "YAHOO_GUID": data.get("xoauth_yahoo_guid", ""),
        "YAHOO_REFRESH_TOKEN": data["refresh_token"],
        "YAHOO_TOKEN_TIME": repr(time.time()),
        "YAHOO_TOKEN_TYPE": data.get("token_type", "bearer"),
    }
    with open(path, "w", encoding="utf-8") as f:
        for k, v in lines.items():
            f.write(f"{k}={v}\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    logger.info("Saved new Yahoo token to %s", path)
