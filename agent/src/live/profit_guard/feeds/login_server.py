"""One-click daily Kite login on your Mac: no copy-paste.

Set the redirect URL of your Kite app (developers.kite.trade) AND ``KITE_REDIRECT_URL`` in agent/.env to

    http://127.0.0.1:5010/zerodha/callback

Then this opens the Kite login page, waits for Kite to send the browser back to 127.0.0.1,
catches the one-time request_token, saves today's access token and closes. You still log in
yourself (exchange rules require a manual login once a day); nothing is automated beyond that.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from src.live.profit_guard.feeds import kite_auth

PAGE = ("<!doctype html><meta charset='utf-8'><title>Vibe Desk</title>"
        "<body style='font-family:system-ui;padding:40px'><h2>{title}</h2><p>{text}</p></body>")


def local_redirect() -> tuple[str, int, str] | None:
    url = os.environ.get("KITE_REDIRECT_URL", "")
    u = urlparse(url)
    if u.hostname in ("127.0.0.1", "localhost") and u.port:
        return u.hostname, u.port, u.path or "/"
    return None


def run(timeout_s: int = 300) -> int:
    from src.live.profit_guard.feeds.kite_client import ReadOnlyKite

    kite_auth._load_env()
    target = local_redirect()
    if target is None:
        print("KITE_REDIRECT_URL is not a 127.0.0.1 address; use the copy-paste login instead.")
        return 2
    host, port, path = target
    kite = ReadOnlyKite()
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet (and never log the token)
            pass

        def do_GET(self):  # noqa: N802
            if urlparse(self.path).path != path:
                self.send_response(404)
                self.end_headers()
                return
            try:
                tok = kite_auth.parse_request_token("http://x" + self.path)
                rec = kite_auth.save_token(kite.exchange_request_token(tok), int(time.time() * 1000))
                result["ok"] = rec.get("user_id")
                body = PAGE.format(title="Logged in to Kite", text="Data feed ready. You can close this tab.")
                self.send_response(200)
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)
                body = PAGE.format(title="Login failed", text="Close this tab and run kite_login.command again.")
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    server = HTTPServer((host, port), Handler)
    server.timeout = 1
    print("Opening the Kite login page. Log in there; this window finishes by itself.")
    threading.Timer(0.5, lambda: webbrowser.open(kite.login_url())).start()
    deadline = time.time() + timeout_s
    while not result and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if result.get("ok"):
        print(f"Logged in as {result['ok']}. Token saved (valid until about 07:30 IST tomorrow).")
        return 0
    print("Login failed: " + result.get("error", "timed out after 5 minutes"))
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
