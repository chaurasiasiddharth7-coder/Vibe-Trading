"""One-shot check that live Kite DATA works: REST quotes + 15 s of WebSocket ticks.

Prints and saves a short report (never the token). Places nothing.
    cd agent && ../.venv/bin/python -m src.live.profit_guard.feeds.check_live
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from src.live.profit_guard.feeds import kite_auth
from src.live.profit_guard.feeds.instruments import load_instruments, token_map
from src.live.profit_guard.feeds.kite_client import ReadOnlyKite
from src.live.profit_guard.feeds.kite_ticker import TickFeed

SYMBOLS = ["RELIANCE", "SBIN", "HDFCBANK", "INFY"]
INDEX = ("NIFTY 50", 256265)  # NSE index instrument token


def run(seconds: int = 15) -> dict:
    kite_auth._load_env()
    now = int(time.time() * 1000)
    tok = kite_auth.load_token()
    report: dict = {"token_fresh": kite_auth.token_is_fresh(tok, now)}
    if not report["token_fresh"]:
        report["error"] = "no fresh login token: run kite_auth url + exchange first"
        return report
    kite = ReadOnlyKite(access_token=tok["access_token"])
    report["user_id"] = kite.profile().get("user_id")
    quotes = kite.ltp(["NSE:NIFTY 50"] + [f"NSE:{s}" for s in SYMBOLS])
    report["rest_ltp"] = {k.split(":", 1)[1]: v["last_price"] for k, v in quotes.items()}
    tokens = token_map(load_instruments(kite, "NSE", date.today()), SYMBOLS)
    tokens[INDEX[0]] = INDEX[1]
    feed = TickFeed(kite.api_key, tok["access_token"])
    feed.subscribe(tokens)
    feed.start()
    time.sleep(seconds)
    report["websocket"] = feed.health()
    report["websocket_ltp"] = {s: str(feed.latest(s).ltp) for s in tokens if feed.latest(s)}
    feed.stop()
    report["orders_possible"] = False  # ReadOnlyKite has no order methods at all
    return report


def main() -> int:
    rep = run()
    text = json.dumps(rep, indent=2, default=str)
    print(text)
    out = Path(__file__).resolve().parents[5] / "kite_check_result.txt"
    out.write_text(text, encoding="utf-8")
    return 0 if rep.get("token_fresh") else 1


if __name__ == "__main__":
    raise SystemExit(main())
