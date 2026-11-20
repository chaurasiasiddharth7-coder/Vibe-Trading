"""Keeps the NSE price store current: one ``scripts/nse_pull.py --update`` every trading evening.

Why: with the store, a 200-stock scan reads every stock's daily bars locally (about 0.1 s a stock). Without it
every stock goes to Yahoo one by one, which is slow and rate-limited from a datacenter. NSE publishes the day's
bhavcopy in the evening, so the update runs at 19:15 IST on NSE trading days (and catches up on the next start).

Started inside the API server when INTRADAY_SCHEDULER=1 (the hosted server). Only runs when a store exists.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
UPDATE_AT = (19, 15)
_STARTED = False


def script_path() -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / "nse_pull.py"


def store_last_day() -> str | None:
    """Last session in the store, from its bars file (cheap: only the date column)."""
    try:
        import pyarrow.parquet as pq

        from backtest.loaders import nse_store

        path = nse_store.store_dir() / "bars.parquet"
        if not path.exists():
            return None
        col = pq.read_table(path, columns=["date"]).column("date")
        return str(max(col.to_pylist()))[:10]
    except Exception as exc:  # noqa: BLE001
        logger.info("nse store unreadable: %s", exc)
        return None


def due(now: datetime, last_day: str | None, is_trading_day) -> bool:
    """An update is owed when the store is behind the latest trading day whose bhavcopy is out."""
    if last_day is None:
        return False  # no store here: nothing to keep current (seed it first)
    d = now.date()
    if not (is_trading_day(d) and (now.hour, now.minute) >= UPDATE_AT):
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return last_day < d.isoformat()


def run_update(timeout_s: int = 1800) -> tuple[bool, str]:
    script = script_path()
    if not script.exists():
        return False, f"{script} not found"
    try:
        p = subprocess.run([sys.executable, str(script), "--update"], capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    tail = (p.stdout or "")[-400:] + (p.stderr or "")[-400:]
    return p.returncode == 0, tail


def tick(now: datetime | None = None, runner=run_update) -> str | None:
    from src.alphadesk.intraday import calendar as cal

    now = now or datetime.now(IST)
    last = store_last_day()
    if not due(now, last, cal.is_trading_day):
        return None
    ok, note = runner()
    msg = f"NSE store update {'ok' if ok else 'FAILED'} (was {last}, now {store_last_day()}): {note.strip()[-200:]}"
    (logger.info if ok else logger.warning)(msg)
    return msg


def start() -> bool:
    global _STARTED
    if _STARTED:
        return False
    _STARTED = True

    def loop() -> None:
        failed_at = 0.0
        while True:
            try:
                if time.time() - failed_at > 3600:  # after a failure, retry hourly, not every minute
                    msg = tick()
                    if msg and "FAILED" in msg:
                        failed_at = time.time()
            except Exception as exc:  # noqa: BLE001
                logger.warning("nse daily tick failed: %s", exc)
                failed_at = time.time()
            time.sleep(300)

    threading.Thread(target=loop, daemon=True, name="nse-store-daily").start()
    return True
