"""Daily Kite login, done by YOU. No password, PIN or TOTP is ever stored or automated.

Flow (once each trading day, before 09:15 IST):

    cd agent && ../.venv/bin/python -m src.live.profit_guard.feeds.kite_auth url
        -> open the printed link, log in to Kite yourself
        -> Kite sends you to the redirect URL with ?request_token=... in the address bar
           (with a 127.0.0.1 redirect URL, kite_login.command catches it automatically)
    ../.venv/bin/python -m src.live.profit_guard.feeds.kite_auth exchange '<the whole redirected URL>'
        -> saves today's access token (chmod 600). Kite flushes tokens every morning around 07:30 IST;
           log in after 07:35.
    ../.venv/bin/python -m src.live.profit_guard.feeds.kite_auth status
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.live.profit_guard import session

# Kite flushes access tokens daily around 07:30 IST; new logins work from about 07:35.
EXPIRY_TIME_IST = time(7, 30)


def token_path() -> Path:
    from src.live.paths import broker_dir

    return broker_dir("zerodha") / "access_token.json"


def next_expiry_ms(now_ms: int) -> int:
    now = session.to_ist(now_ms)
    expiry = datetime.combine(now.date(), EXPIRY_TIME_IST, tzinfo=session.IST)
    if now >= expiry:
        expiry += timedelta(days=1)
    return int(expiry.timestamp() * 1000)


def token_is_fresh(data: dict[str, Any] | None, now_ms: int) -> bool:
    """Pure: is this saved token still valid at ``now_ms``?"""
    if not data or not data.get("access_token"):
        return False
    try:
        return int(data["expires_at_ms"]) > now_ms
    except (KeyError, TypeError, ValueError):
        return False


def load_token(path: Path | None = None) -> dict[str, Any] | None:
    p = path or token_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_token(session_data: dict[str, Any], now_ms: int, path: Path | None = None) -> dict[str, Any]:
    p = path or token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "access_token": session_data["access_token"],
        "user_id": session_data.get("user_id"),
        "created_at_ms": now_ms,
        "expires_at_ms": next_expiry_ms(now_ms),
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return record


def parse_request_token(text: str) -> str:
    """Accept either the bare request_token or the whole redirected URL."""
    text = text.strip()
    if "request_token=" in text:
        qs = parse_qs(urlparse(text).query)
        tok = (qs.get("request_token") or [""])[0]
        if (qs.get("status") or ["success"])[0] != "success":
            raise ValueError("Kite login did not succeed (status is not 'success')")
        text = tok
    if not text or not text.isalnum():
        raise ValueError("no valid request_token found")
    return text


def status(now_ms: int, path: Path | None = None) -> dict[str, Any]:
    data = load_token(path)
    fresh = token_is_fresh(data, now_ms)
    return {
        "token_fresh": fresh,
        "user_id": (data or {}).get("user_id"),
        "expires_at_ist": session.to_ist(int(data["expires_at_ms"])).strftime("%d %b %H:%M") if data and data.get("expires_at_ms") else None,
        "api_key_set": bool(os.environ.get("KITE_API_KEY")),
        "api_secret_set": bool(os.environ.get("KITE_API_SECRET")),
    }


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _load_env() -> None:
    """Read agent/.env into os.environ (only keys not already set). Values are never printed."""
    env = Path(__file__).resolve().parents[4] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def main(argv: list[str] | None = None) -> int:
    from src.live.profit_guard.feeds.kite_client import ReadOnlyKite

    argv = sys.argv[1:] if argv is None else argv
    _load_env()
    cmd = argv[0] if argv else "status"
    if cmd == "url":
        print(ReadOnlyKite().login_url())
        return 0
    if cmd == "exchange":
        if len(argv) < 2:
            print("usage: kite_auth exchange '<redirected URL or request_token>'")
            return 2
        kite = ReadOnlyKite()
        rec = save_token(kite.exchange_request_token(parse_request_token(argv[1])), _now_ms())
        print(f"Saved. Logged in as {rec.get('user_id')}; token valid until "
              f"{session.to_ist(rec['expires_at_ms']).strftime('%d %b %H:%M')} IST.")
        return 0
    if cmd == "status":
        print(json.dumps(status(_now_ms()), indent=2))
        return 0
    print("commands: url | exchange <url-or-token> | status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
