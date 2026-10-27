"""5-minute NSE bars: Yahoo chart fetch, session splitting, and a growing local store.

Yahoo serves only the last 60 days of 5-minute bars. ``update`` merges each fetch into
``~/.vibe-trading/alphadesk/intraday/bars/<SYMBOL>.parquet`` and never drops old sessions, so
calibration has more history every week you run it. Bars are indexed by their naive IST start.
Today's session is stored only once the market has closed (a live bar is not a finished bar).
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.alphadesk.intraday.rules import BAR, CLOSE_MIN, OPEN_MIN, Day, day_from_frame
from src.alphadesk.store import alphadesk_root
from src.alphadesk.symbols import india_fetch_candidates

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

def intraday_dir() -> Path:
    path = alphadesk_root() / "intraday"
    (path / "bars").mkdir(parents=True, exist_ok=True)
    return path


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def parse_chart(payload: dict) -> pd.DataFrame:
    """Yahoo v8 chart JSON -> OHLCV frame on the 5-minute grid, naive IST bar-start index."""
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    offset = int(result.get("meta", {}).get("gmtoffset", 19800))
    q = result["indicators"]["quote"][0]
    frame = pd.DataFrame({k: q.get(k) for k in ("open", "high", "low", "close", "volume")},
                         index=pd.to_datetime(pd.Series(result["timestamp"]) + offset, unit="s"))
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame["volume"] = frame["volume"].fillna(0)
    frame[["open", "high", "low", "close"]] = frame[["open", "high", "low", "close"]].round(2)
    minutes = frame.index.hour * 60 + frame.index.minute
    on_grid = (minutes % BAR == 0) & (frame.index.second == 0) & (minutes >= OPEN_MIN) & (minutes < CLOSE_MIN)
    return frame[on_grid].sort_index()[~frame[on_grid].sort_index().index.duplicated(keep="last")]


def _get(symbol: str, range_: str, interval: str) -> dict | None:
    """One chart request through the project's shared, throttled Yahoo client (one process-wide
    pace and 429 back-off for every loader). None on a failure."""
    from backtest.loaders import yahoo_client
    from backtest.loaders._http import throttled_get_json

    try:
        return throttled_get_json(
            f"{yahoo_client._CHART_BASE}/{symbol}",
            host_key=yahoo_client.HOST_KEY,
            min_interval=yahoo_client._min_interval(),
            params={"range": range_, "interval": interval},
        )
    except Exception as exc:  # noqa: BLE001 - network/HTTP errors: caller tries the next ticker
        logger.warning("yahoo chart %s failed: %s", symbol, exc)
        return None


_MEMO: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}


def fetch_ticker(ticker: str, range_: str = "60d", ttl: float = 0.0) -> pd.DataFrame:
    """Bars for an exact ticker (``RELIANCE.NS``, ``^NSEI``) from the configured source, memoised
    for ``ttl`` seconds (history is reused across a research session; live reads pass 0)."""
    import time as _time

    key = (ticker, range_)
    hit = _MEMO.get(key)
    if ttl > 0 and hit and _time.monotonic() - hit[0] < ttl:
        return hit[1]
    from src.alphadesk.intraday.providers import get_source

    frame = get_source().bars(ticker, range_)
    if not frame.empty and ttl > 0:
        _MEMO[key] = (_time.monotonic(), frame)
    return frame


def fetch(symbol: str, range_: str = "60d", interval: str = "5m",
          offline: str | None = None, ttl: float = 0.0) -> tuple[str, pd.DataFrame]:
    """(ticker used, bars) for an NSE symbol; tries known renames. ``offline`` reads a saved
    Yahoo chart JSON (``.json`` / ``.json.gz``)."""
    if offline:
        path = Path(offline)
        raw = gzip.open(path, "rt").read() if path.suffix == ".gz" else path.read_text()
        return symbol, parse_chart(json.loads(raw))
    for ticker in india_fetch_candidates(symbol):
        frame = fetch_ticker(ticker, range_, ttl)
        if not frame.empty:
            return ticker, frame
    return india_fetch_candidates(symbol)[0], pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"])


def split_sessions(frame: pd.DataFrame) -> dict[date, pd.DataFrame]:
    return {d: g for d, g in frame.groupby(frame.index.date)}


def days_from_frame(frame: pd.DataFrame, only_full: bool = True,
                    before: date | None = None) -> list[Day]:
    out = []
    for d, g in sorted(split_sessions(frame).items()):
        if before is not None and d >= before:
            continue
        day = day_from_frame(d, g)
        if day is not None and (day.full or not only_full):
            out.append(day)
    return out


# --- local store ---------------------------------------------------------------------------------


def store_path(symbol: str) -> Path:
    bare = symbol.upper().removesuffix(".NS").replace("&", "_AND_").replace("/", "_")
    return intraday_dir() / "bars" / f"{bare}.parquet"


def load_store(symbol: str) -> pd.DataFrame:
    path = store_path(symbol)
    if not path.exists():
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.read_parquet(path)


def settled(frame: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop today's bars unless the session has closed."""
    now = now or now_ist()
    if now.hour * 60 + now.minute >= CLOSE_MIN + 5:
        return frame
    return frame[frame.index.date < now.date()]


def merge_into_store(symbol: str, frame: pd.DataFrame) -> int:
    """Add settled sessions of ``frame`` to the symbol's store; returns bars added."""
    fresh = settled(frame)
    if fresh.empty:
        return 0
    old = load_store(symbol)
    merged = pd.concat([old, fresh])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged.to_parquet(store_path(symbol))
    return len(merged) - len(old)


def update(symbol: str, offline: str | None = None) -> dict:
    """Fetch the last 60 days and merge the settled sessions into the store."""
    ticker, fresh = fetch(symbol, offline=offline)
    fresh = settled(fresh)
    if fresh.empty:
        return {"symbol": symbol, "ticker": ticker, "added": 0, "sessions": 0, "ok": False}
    old = load_store(symbol)
    merged = pd.concat([old, fresh])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged.to_parquet(store_path(symbol))
    return {"symbol": symbol, "ticker": ticker, "ok": True, "added": len(merged) - len(old),
            "sessions": len(set(merged.index.date))}
