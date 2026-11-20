"""Local cache of Kite historical candles, one CSV per symbol/interval/month.

Backtests read ONLY from this cache (never live API calls). A complete past month is never
downloaded twice; the current month is refreshed. Kite limits how many days one request may
cover per interval, so ranges are split into windows that respect those caps.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

#: Max days per historical request, per Kite interval.
KITE_DAY_CAPS = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100, "15minute": 200,
                 "30minute": 200, "60minute": 400, "day": 2000}
FIELDS = ["date", "open", "high", "low", "close", "volume"]


def cache_root() -> Path:
    from src.config.paths import get_runtime_root

    return get_runtime_root() / "data" / "candles"


def _month_file(exchange: str, symbol: str, interval: str, y: int, m: int) -> Path:
    return cache_root() / exchange.upper() / symbol.upper() / interval / f"{y:04d}-{m:02d}.csv"


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_bounds(y: int, m: int) -> tuple[date, date]:
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first, nxt - timedelta(days=1)


def windows(start: date, end: date, interval: str) -> list[tuple[date, date]]:
    cap = KITE_DAY_CAPS[interval]
    out, cur = [], start
    while cur <= end:
        stop = min(end, cur + timedelta(days=cap - 1))
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def _read(path: Path) -> list[dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            d = r["date"]
            w.writerow({**{k: r.get(k) for k in FIELDS}, "date": d.isoformat() if hasattr(d, "isoformat") else d})
    tmp.replace(path)


def get_candles(kite: Any, symbol: str, token: int, interval: str, start: date, end: date,
                exchange: str = "NSE", today: date | None = None) -> list[dict[str, Any]]:
    """Candles for [start, end], from cache where complete, from Kite otherwise."""
    if interval not in KITE_DAY_CAPS:
        raise ValueError(f"unsupported interval {interval!r}")
    today = today or date.today()
    out: list[dict[str, Any]] = []
    for y, m in _months(start, end):
        first, last = _month_bounds(y, m)
        path = _month_file(exchange, symbol, interval, y, m)
        complete = last < today
        if path.exists() and complete:
            rows = _read(path)
        else:
            rows = []
            for a, b in windows(first, min(last, today), interval):
                rows += kite.historical_data(token, datetime.combine(a, datetime.min.time()),
                                             datetime.combine(b, datetime.max.time().replace(microsecond=0)), interval)
            _write(path, rows)
            rows = _read(path)
        lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
        out += [r for r in rows if lo <= str(r["date"])[:10] < hi]
    return out
