"""Live prices from Kite for the owner's desk: today's bar (open, high, low, last price, volume) for any stock.

How the desk uses it: daily history comes from the NSE store (official, instant to read), and today's bar comes
from Kite, so every score, card and practice-trade price is real time. Quotes are fetched in bulk (up to 500 stocks
per Kite call, one call a second at most) and kept for a few seconds, so a 200-stock scan costs one request.

Only the owner's desk gets Kite numbers: exchange data-vending rules don't allow showing Kite data to other people,
so every other profile keeps end-of-day prices. Read-only: the client has no order methods.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
TTL_S = 5.0
BATCH = 450  # Kite allows 500 instruments per quote call
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_CLIENT: list[Any] = [None, 0.0]  # client, built_at


def _owner() -> bool:
    """The owner's own desk. Background jobs carry the profile via profiles.carry; requests carry the user."""
    try:
        from src.alphadesk import profiles, users

        if profiles.current_id() is not None:
            return False
        user = users.current_user()
        return user is None or (user.role == "owner" and not user.must_change)
    except Exception:  # noqa: BLE001
        return False


def _client() -> Any | None:
    """The read-only Kite client while today's login is fresh (rebuilt every 10 minutes)."""
    from src.live.profit_guard.feeds import kite_auth

    now = time.time()
    if _CLIENT[0] is not None and now - _CLIENT[1] < 600:
        return _CLIENT[0]
    try:
        kite_auth._load_env()
        tok = kite_auth.load_token() or {}
        if not kite_auth.token_is_fresh(tok, int(now * 1000)):
            _CLIENT[0] = None
            return None
        from src.live.profit_guard.feeds.kite_client import ReadOnlyKite

        _CLIENT[0] = ReadOnlyKite(api_key=tok.get("api_key") or None, access_token=tok["access_token"])
        _CLIENT[1] = now
        return _CLIENT[0]
    except Exception as exc:  # noqa: BLE001
        logger.info("live quotes: no Kite client: %s", exc)
        _CLIENT[0] = None
        return None


def session_has_started(now: datetime | None = None) -> bool:
    """Today is an NSE trading day and it is 09:15 or later (after the close, today's bar is final)."""
    now = now or datetime.now(IST)
    try:
        from src.alphadesk.intraday import calendar as cal

        trading = cal.is_trading_day(now.date())
    except Exception:  # noqa: BLE001
        trading = now.weekday() < 5
    return trading and now.time() >= dtime(9, 15)


def active() -> bool:
    """Should this request get Kite numbers? Owner only, with a fresh Kite login on this server."""
    return _owner() and _client() is not None


def _key(symbol: str) -> str:
    s = symbol.upper().replace(".NS", "")
    return f"NSE:{s}"


def prime(symbols: Iterable[str], fetch: Callable[[list[str]], dict] | None = None) -> int:
    """Fetch quotes for many stocks at once (one Kite call per 450). Returns how many came back."""
    if fetch is None:
        if not active():
            return 0
        kite = _client()
        fetch = kite.quote  # type: ignore[union-attr]
    keys = sorted({_key(s) for s in symbols if s and not s.upper().endswith(".BO")})
    got = 0
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        with _LOCK:
            wait = 1.0 - (time.time() - _LAST_CALL[0])
            if wait > 0:
                time.sleep(wait)  # Kite: about 1 quote call a second
            _LAST_CALL[0] = time.time()
        try:
            data = fetch(chunk) or {}
        except Exception as exc:  # noqa: BLE001 - one failed batch must not stop a scan
            logger.warning("live quotes batch failed: %s", exc)
            continue
        now = time.time()
        with _LOCK:
            for k, q in data.items():
                _CACHE[k] = (now, q)
        got += len(data)
    return got


def quote(symbol: str, fetch: Callable[[list[str]], dict] | None = None) -> dict[str, Any] | None:
    k = _key(symbol)
    with _LOCK:
        hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < TTL_S:
        return hit[1]
    if fetch is None and not active():
        return None
    prime([symbol], fetch=fetch)
    with _LOCK:
        hit = _CACHE.get(k)
    return hit[1] if hit else None


def today_bar(q: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """Today's daily bar from a Kite quote, or None when the quote isn't from today's session."""
    now = now or datetime.now(IST)
    ohlc = q.get("ohlc") or {}
    ltp = q.get("last_price")
    if not ltp or not ohlc.get("open"):
        return None
    ts = q.get("last_trade_time") or q.get("timestamp")
    if ts is not None:
        try:
            d = ts.date() if hasattr(ts, "date") else datetime.fromisoformat(str(ts)).date()
            if d != now.date():
                return None  # no trade yet today: yesterday's numbers, not a new bar
        except ValueError:
            pass
    return {"open": float(ohlc["open"]), "high": max(float(ohlc.get("high") or ltp), float(ltp)),
            "low": min(float(ohlc.get("low") or ltp), float(ltp)), "close": float(ltp),
            "volume": float(q.get("volume") or q.get("volume_traded") or 0)}


def overlay(df: Any, symbol: str, fetch: Callable[[list[str]], dict] | None = None,
            now: datetime | None = None) -> tuple[Any, bool]:
    """Put today's live bar on a daily frame (replace today's row, or append it). Returns (frame, used_kite)."""
    import pandas as pd

    now = now or datetime.now(IST)
    if fetch is None and not (session_has_started(now) and active()):
        return df, False
    q = quote(symbol, fetch=fetch)
    bar = today_bar(q, now) if q else None
    if bar is None or df is None or df.empty:
        return df, False
    cols = {str(c).lower(): c for c in df.columns}
    if not all(k in cols for k in ("open", "high", "low", "close")):
        return df, False
    idx = df.index
    tz = getattr(idx, "tz", None)
    today = pd.Timestamp(now.date())
    if tz is not None:
        today = today.tz_localize(tz)
    out = df.copy()
    row = {cols[k]: bar[k] for k in ("open", "high", "low", "close")}
    if "volume" in cols:
        row[cols["volume"]] = bar["volume"]
    last = pd.Timestamp(idx[-1])
    if last.normalize() == today.normalize():
        for c, v in row.items():
            out.loc[idx[-1], c] = v
    elif last < today:
        out.loc[today] = pd.Series(row)
        out = out.sort_index()
    return out, True
