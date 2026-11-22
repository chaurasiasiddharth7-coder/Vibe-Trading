"""Every daily Kronos forecast, kept so the chart can show what Kronos predicted and how it turned out.

Live forecasts are saved as they are made (``record``). Past ones for the watchlist are worked out in the
background (``backfill_step``), each from the bars up to its ``as_of`` day only, so it is the forecast
Kronos would have made that day. One JSON-lines file per symbol under ``alphadesk/kronos_log/``.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
BACKFILL_DAYS = 250        # about a year of trading days back
BACKFILL_EVERY = 5         # one past forecast a week per stock
PRED_LEN = 15
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"running": False, "done": 0, "left": None, "last": None, "error": None}


def _dir() -> Path:
    from src.alphadesk.profiles import root_for

    d = root_for(None) / "kronos_log"  # market data, shared by every profile
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(symbol: str) -> Path:
    return _dir() / f"{symbol.upper().replace('/', '_')}.jsonl"


def forecasts(symbol: str) -> list[dict[str, Any]]:
    p = _file(symbol)
    if not p.is_file():
        return []
    seen: dict[str, dict[str, Any]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = row.get("as_of")
        if key and (key not in seen or row.get("source") == "live"):
            seen[key] = row  # one forecast per day; a live one beats a backfilled one
    return [seen[k] for k in sorted(seen)]


def record(symbol: str, payload: dict[str, Any], source: str = "live") -> None:
    """Save one daily forecast (the ``forecast_symbol`` payload)."""
    hist = payload.get("historical_data") or []
    path = [{"time": c["time"], "close": c["close"]} for c in payload.get("forecast_data") or []]
    if not hist or not path:
        return
    row = {"as_of": hist[-1]["time"], "last_close": hist[-1]["close"], "path": path, "source": source,
           "model": (payload.get("kronos_projection") or {}).get("model"),
           "made_at": datetime.now(IST).isoformat(timespec="seconds")}
    with _LOCK:
        with _file(payload.get("symbol") or symbol).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


def _watchlist_symbols() -> list[str]:
    from src.alphadesk import profiles, watchlist

    out: list[str] = []
    for pid in profiles.all_ids():
        try:
            with profiles.use(pid):
                for lst in watchlist.load():
                    out += [str(s).upper() for s in lst.get("items") or []]
        except Exception:  # noqa: BLE001
            logger.warning("kronos backfill: watchlist for %s unreadable", pid, exc_info=True)
    return list(dict.fromkeys(out))


def _due(symbol: str, trading_days: list[str]) -> list[str]:
    have = {f["as_of"] for f in forecasts(symbol)}
    # every BACKFILL_EVERY-th trading day, newest first, that still has PRED_LEN days after it to check against
    wanted = trading_days[-(BACKFILL_DAYS + PRED_LEN):-PRED_LEN][::-1][::BACKFILL_EVERY]
    return [d for d in wanted if d not in have]


def backfill_step() -> bool:
    """Work out one missing past forecast for the watchlist. Returns False when nothing is left."""
    from src.alphadesk.analysis import fetch_india_ohlcv
    from src.alphadesk.kronos_service import forecast_symbol

    for sym in _watchlist_symbols():
        try:
            _, df = fetch_india_ohlcv(sym, period="3y", interval="1d")
        except Exception:  # noqa: BLE001 - a stock with no data is skipped
            continue
        days = [str(d)[:10] for d in df.index]
        due = _due(sym, days)
        if not due:
            continue
        as_of = due[0]
        payload = forecast_symbol(sym, as_of=as_of, period="3y", pred_len=PRED_LEN, record=False)
        record(sym, payload, source="backfill")
        _STATE.update(done=_STATE["done"] + 1, last=f"{sym} {as_of}", left_for_stock=len(due) - 1, error=None)
        return True
    return False


def _market_hours(now: datetime) -> bool:
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (15, 45)


def start_backfill() -> bool:
    """Background worker: outside market hours, one past forecast at a time until the watchlist is covered,
    then checks again every few hours (new watchlist stocks get covered too)."""
    if _STATE["running"]:
        return False
    _STATE["running"] = True

    def loop() -> None:
        import time

        while True:
            try:
                if _market_hours(datetime.now(IST)):
                    time.sleep(600)
                    continue
                if not backfill_step():
                    _STATE["left"] = 0
                    time.sleep(6 * 3600)
                    continue
                time.sleep(2)  # leave the CPU some room between forecasts
            except RuntimeError as exc:  # Kronos not installed / model missing on this server
                _STATE["error"] = str(exc)
                logger.warning("kronos backfill stopped: %s", exc)
                time.sleep(6 * 3600)
            except Exception as exc:  # noqa: BLE001
                _STATE["error"] = str(exc)
                logger.exception("kronos backfill step failed")
                time.sleep(300)

    threading.Thread(target=loop, daemon=True, name="kronos-backfill").start()
    return True


def status() -> dict[str, Any]:
    return dict(_STATE)

