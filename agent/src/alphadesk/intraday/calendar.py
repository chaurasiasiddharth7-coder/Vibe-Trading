"""NSE trading days: weekdays minus NSE holidays (current-year list from the NSE store, plus any
dates in ``holidays_extra.txt``), and the session clock in IST."""

from __future__ import annotations

import json
from datetime import date, datetime

from backtest.loaders import nse_store
from src.alphadesk.intraday.rules import CLOSE_MIN, OPEN_MIN


def holidays() -> set[date]:
    root = nse_store.store_dir()
    out: set[date] = set()
    path = root / "holidays.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for rows in payload.values() if isinstance(payload, dict) else []:
                for row in rows if isinstance(rows, list) else []:
                    d = nse_store.parse_holiday(row.get("tradingDate"))
                    if d:
                        out.add(d)
        except (ValueError, AttributeError):
            pass
    extra = root / "holidays_extra.txt"
    if extra.exists():
        for token in extra.read_text().split():
            try:
                out.add(date.fromisoformat(token))
            except ValueError:
                pass
    return out


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays()


def market_state(now: datetime) -> str:
    """open | pre_open | closed | holiday (weekend or NSE holiday)."""
    if not is_trading_day(now.date()):
        return "holiday"
    minutes = now.hour * 60 + now.minute
    if minutes < OPEN_MIN - 15:
        return "closed"
    if minutes < OPEN_MIN:
        return "pre_open"
    return "open" if minutes < CLOSE_MIN else "closed"
