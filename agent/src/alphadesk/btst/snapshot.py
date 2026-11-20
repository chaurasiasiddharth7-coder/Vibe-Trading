"""Live prices for the whole market at about 15:20 IST.

Sources, tried in the order of ``BTST_QUOTE_SOURCES`` (default ``nse,yahoo``):

* ``nse``: NSE's public JSON. Two broad index pages (Nifty Total Market, about 750 stocks, and
  Nifty Microcap 250) plus the "top gainers, all securities" list, which catches big movers that
  are in no index. Three requests. Works from a home connection; NSE often refuses cloud servers.
* ``yahoo``: one 5-minute chart per symbol for liquid stocks the NSE pages did not cover, capped by
  ``BTST_YAHOO_CAP`` (default 150, busiest first). Slow (the shared Yahoo client is throttled).
* ``offline``: a saved snapshot JSON (``BTST_SNAPSHOT_FILE``), for tests and replays.

[OWNER-CHECK] The NSE field names below are the ones its pages used in 2026. Run
``python -m src.alphadesk.btst snapshot`` once on a trading afternoon and check the coverage line.
Read-only: nothing here places an order.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd

logger = logging.getLogger(__name__)

COLUMNS = ["symbol", "series", "open", "high", "low", "last", "prev_close", "traded", "source"]
NSE_HOME = "https://www.nseindia.com"
NSE_INDEX_URL = NSE_HOME + "/api/equity-stockIndices?index={index}"
NSE_GAINERS_URL = NSE_HOME + "/api/live-analysis-variations?index=gainers"
NSE_INDICES = ("NIFTY TOTAL MARKET", "NIFTY MICROCAP 250")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
}


def _f(row: dict, *keys: str) -> float | None:
    for k in keys:
        v = row.get(k)
        if v in (None, "", "-"):
            continue
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            continue
    return None


def parse_index_page(payload: dict) -> pd.DataFrame:
    """``/api/equity-stockIndices`` -> snapshot rows. The first row is the index itself: dropped."""
    index_name = str((payload.get("metadata") or {}).get("indexName") or payload.get("name") or "").upper()
    rows = []
    for r in payload.get("data") or []:
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym or sym == index_name or r.get("priority") == 1:
            continue
        meta = r.get("meta") or {}
        rows.append({
            "symbol": sym,
            "series": str(r.get("series") or meta.get("series") or "EQ").upper(),
            "open": _f(r, "open"), "high": _f(r, "dayHigh", "high"), "low": _f(r, "dayLow", "low"),
            "last": _f(r, "lastPrice", "ltp", "last"), "prev_close": _f(r, "previousClose", "prevClose"),
            # totalTradedValue is in rupees on this page
            "traded": _f(r, "totalTradedValue"),
            "source": "nse",
        })
    return _clean(pd.DataFrame(rows, columns=COLUMNS))


def parse_gainers(payload: dict, segment: str = "allSec") -> pd.DataFrame:
    """``/api/live-analysis-variations?index=gainers`` -> snapshot rows (turnover there is in lakh)."""
    block = payload.get(segment) or {}
    rows = []
    for r in block.get("data") or []:
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        lakh = _f(r, "turnover")
        rows.append({
            "symbol": sym, "series": str(r.get("series") or "EQ").upper(),
            "open": _f(r, "open_price", "open"), "high": _f(r, "high_price", "high"),
            "low": _f(r, "low_price", "low"), "last": _f(r, "ltp", "lastPrice"),
            "prev_close": _f(r, "prev_price", "previousClose"),
            "traded": lakh * 1e5 if lakh is not None else None, "source": "nse_gainers",
        })
    return _clean(pd.DataFrame(rows, columns=COLUMNS))


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.dropna(subset=["last", "prev_close"])
    df = df[(df["last"] > 0) & (df["prev_close"] > 0)].copy()
    for c in ("open", "high", "low"):
        df[c] = df[c].fillna(df["last"])
    df["high"] = df[["high", "last"]].max(axis=1)
    df["low"] = df[["low", "last"]].min(axis=1)
    df["traded"] = df["traded"].fillna(0.0)
    return df


class NseClient:
    """Cookie-warmed session for NSE's JSON pages (the site wants its home page visited first)."""

    def __init__(self, interval: float = 1.0) -> None:
        import httpx

        self.http = httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True)
        self.interval, self.last, self.warm = interval, 0.0, False

    def json(self, url: str) -> dict:
        if not self.warm:
            self.http.get(NSE_HOME)
            self.warm = True
        err = None
        for attempt in range(3):
            time.sleep(max(0.0, self.last + self.interval - time.monotonic()))
            self.last = time.monotonic()
            try:
                r = self.http.get(url)
                if r.status_code == 200:
                    return r.json()
                err = f"HTTP {r.status_code}"
                if r.status_code in (401, 403):
                    self.http.get(NSE_HOME)  # cookies expired: warm again
            except Exception as exc:  # noqa: BLE001 - network errors are retried, then reported
                err = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"NSE request failed ({err}): {url}")


def from_nse(client: NseClient | None = None) -> tuple[pd.DataFrame, list[str]]:
    client = client or NseClient()
    frames, notes = [], []
    for name in NSE_INDICES:
        try:
            frames.append(parse_index_page(client.json(NSE_INDEX_URL.format(index=quote(name)))))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"NSE {name}: {exc}")
    try:
        frames.append(parse_gainers(client.json(NSE_GAINERS_URL)))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"NSE gainers: {exc}")
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS), notes
    snap = pd.concat(frames).drop_duplicates("symbol", keep="first")
    return snap.reset_index(drop=True), notes


def from_yahoo(symbols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Today's open/high/low/last and traded value from 5-minute bars (one request per symbol)."""
    from src.alphadesk.intraday import bars as ibars

    rows, failed = [], 0
    today = ibars.now_ist().date()
    for sym in symbols:
        try:
            _, frame = ibars.fetch(sym, range_="5d")
        except Exception:  # noqa: BLE001
            frame = pd.DataFrame()
        if frame.empty:
            failed += 1
            continue
        days = sorted(set(frame.index.date))
        if days[-1] != today or len(days) < 2:
            failed += 1
            continue
        t, y = frame[frame.index.date == today], frame[frame.index.date == days[-2]]
        rows.append({"symbol": sym, "series": "EQ", "open": float(t["open"].iloc[0]), "high": float(t["high"].max()),
                     "low": float(t["low"].min()), "last": float(t["close"].iloc[-1]),
                     "prev_close": float(y["close"].iloc[-1]),
                     "traded": float((t["close"] * t["volume"]).sum()), "source": "yahoo"})
    notes = [f"Yahoo: {failed} of {len(symbols)} symbols had no bars for today"] if failed else []
    return _clean(pd.DataFrame(rows, columns=COLUMNS)), notes


def from_file(path: str | Path) -> pd.DataFrame:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("rows", raw) if isinstance(raw, dict) else raw
    df = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in df:
            df[c] = "offline" if c == "source" else ("EQ" if c == "series" else None)
    return _clean(df[COLUMNS])


def take(universe: list[str], sources: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """(snapshot, info) where info has the sources used, coverage of ``universe`` and notes."""
    sources = sources or [s.strip() for s in os.environ.get("BTST_QUOTE_SOURCES", "nse,yahoo").split(",") if s.strip()]
    snap, notes, used = pd.DataFrame(columns=COLUMNS), [], []
    for src in sources:
        if src == "offline":
            path = os.environ.get("BTST_SNAPSHOT_FILE")
            if not path:
                notes.append("offline source needs BTST_SNAPSHOT_FILE")
                continue
            part, more = from_file(path), []
        elif src == "nse":
            part, more = from_nse()
        elif src == "yahoo":
            have = set(snap["symbol"]) if not snap.empty else set()
            todo = [s for s in universe if s not in have][: int(os.environ.get("BTST_YAHOO_CAP", "150"))]
            if not todo:
                continue
            part, more = from_yahoo(todo)
        else:
            notes.append(f"unknown quote source {src!r}")
            continue
        notes += more
        if not part.empty:
            used.append(src)
            snap = pd.concat([snap, part]).drop_duplicates("symbol", keep="first") if not snap.empty else part
    uni = set(universe)
    covered = len(uni & set(snap["symbol"])) if not snap.empty else 0
    return snap.reset_index(drop=True), {"sources": used, "quotes": int(len(snap)), "universe": len(uni),
                                         "covered": covered, "notes": notes}
