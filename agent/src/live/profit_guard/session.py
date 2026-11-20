"""NSE session clock for Profit Guard (pure).

Every function takes ``now_ms`` (epoch milliseconds, UTC) and, where needed, the
holiday set, so tests never read the wall clock. ``nse_holidays()`` is the one
impure helper: it reads the authoritative NSE list from the NSE store.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

PREOPEN = time(9, 0)
PREOPEN_END = time(9, 8)
OPEN = time(9, 15)
CLOSE = time(15, 30)

Phase = Literal["closed", "preopen", "open", "no_new_entry", "square_off"]


def to_ist(now_ms: int) -> datetime:
    return datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).astimezone(IST)


def ist_ms(d: date, t: time) -> int:
    """Epoch ms for an IST wall-clock date + time (handy for tests and scheduling)."""
    return int(datetime.combine(d, t, tzinfo=IST).timestamp() * 1000)


def is_trading_day(d: date, holidays: Iterable[date] = ()) -> bool:
    return d.weekday() < 5 and d not in set(holidays)


def session_phase(
    now_ms: int,
    *,
    holidays: Iterable[date] = (),
    no_new_entry: time = time(14, 45),
    square_off: time = time(15, 15),
    special_sessions: dict[date, tuple[time, time]] | None = None,
) -> Phase:
    """Where the trading day is at ``now_ms``.

    ``preopen`` covers 09:00-09:08 (no stop-loss orders allowed by NSE) and the
    gap until 09:15. ``square_off`` runs from the square-off time to the close.
    """
    now = to_ist(now_ms)
    today = now.date()
    t = now.time()
    if special_sessions and today in special_sessions:
        start, end = special_sessions[today]
        return "open" if start <= t < end else "closed"
    if not is_trading_day(today, holidays):
        return "closed"
    if PREOPEN <= t < OPEN:
        return "preopen"
    if t < OPEN or t >= CLOSE:
        return "closed"
    if t >= square_off:
        return "square_off"
    if t >= no_new_entry:
        return "no_new_entry"
    return "open"


def minutes_to_close(now_ms: int) -> int:
    now = to_ist(now_ms)
    close = datetime.combine(now.date(), CLOSE, tzinfo=IST)
    return max(0, int((close - now).total_seconds() // 60))


def is_expiry_day(now_ms: int, weekly_expiry_weekday: int | None) -> bool:
    """True on the configured weekly expiry weekday (0=Mon). None disables it.

    The weekday is config, not code: NSE has moved expiry days before. A holiday
    shift of expiry is not modelled; set the override in config for that week.
    """
    if weekly_expiry_weekday is None:
        return False
    return to_ist(now_ms).weekday() == weekly_expiry_weekday


def trade_date(now_ms: int) -> str:
    return to_ist(now_ms).date().isoformat()


def nse_holidays() -> set[date]:
    """Authoritative NSE holidays from the NSE store (empty if the store is absent)."""
    try:
        from src.alphadesk.intraday.calendar import holidays
    except Exception:  # noqa: BLE001 - store optional; caller decides how to treat empty
        return set()
    try:
        return set(holidays())
    except Exception:  # noqa: BLE001
        return set()
