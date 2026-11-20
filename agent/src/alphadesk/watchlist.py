"""Watchlists and quotes for the Kite-style home screen.

* Five lists per desk profile, up to 250 stocks each, renamable; stored in ``alphadesk_root()/watchlists.json``
  so each user's lists are private.
* Quotes: on the owner's desk with a fresh Kite login, live from Kite (one bulk call, refreshed every few
  seconds). Everyone else, or before the login, gets end-of-day prices from the NSE store with ``live: false``,
  because exchange rules don't allow showing Kite data to other people.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

MAX_LISTS = 5
MAX_ITEMS = 250
INDICES = ["NIFTY 50", "NIFTY BANK"]
_SYM = re.compile(r"^[A-Z0-9&\-]{1,20}$")
_LOCK = threading.Lock()
_EOD: dict[str, Any] = {"at": 0.0, "mtime": 0.0, "stocks": {}, "indices": {}}


def _path() -> Path:
    from src.alphadesk.store import alphadesk_root

    return alphadesk_root() / "watchlists.json"


def _default() -> list[dict[str, Any]]:
    return [{"name": f"Watchlist {i + 1}", "items": []} for i in range(MAX_LISTS)]


def load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        lists = [{"name": str(x.get("name") or f"Watchlist {i + 1}")[:24],
                  "items": [s for s in x.get("items", []) if isinstance(s, str)][:MAX_ITEMS]}
                 for i, x in enumerate(data[:MAX_LISTS])]
        return lists + _default()[len(lists):]
    except (OSError, ValueError, TypeError, AttributeError):
        return _default()


def _save(lists: list[dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(lists, indent=2), encoding="utf-8")
    tmp.replace(p)


def clean_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace(".NS", "")
    if s.startswith("NSE:"):
        s = s[4:]
    if not _SYM.match(s):
        raise ValueError("Use an NSE symbol like RELIANCE or LT")
    return s


def _index(i: int) -> int:
    if not 0 <= i < MAX_LISTS:
        raise ValueError(f"list must be 0..{MAX_LISTS - 1}")
    return i


def add(i: int, symbol: str) -> list[dict[str, Any]]:
    with _LOCK:
        lists, sym = load(), clean_symbol(symbol)
        items = lists[_index(i)]["items"]
        if sym not in items:
            if len(items) >= MAX_ITEMS:
                raise ValueError(f"A list holds up to {MAX_ITEMS} stocks")
            items.append(sym)
            _save(lists)
        return lists


def remove(i: int, symbol: str) -> list[dict[str, Any]]:
    with _LOCK:
        lists, sym = load(), clean_symbol(symbol)
        lists[_index(i)]["items"] = [s for s in lists[i]["items"] if s != sym]
        _save(lists)
        return lists


def rename(i: int, name: str) -> list[dict[str, Any]]:
    with _LOCK:
        name = (name or "").strip()[:24]
        if not name:
            raise ValueError("Give the list a name")
        lists = load()
        lists[_index(i)]["name"] = name
        _save(lists)
        return lists


def reorder(i: int, items: list[str]) -> list[dict[str, Any]]:
    with _LOCK:
        lists = load()
        have = lists[_index(i)]["items"]
        want = [clean_symbol(s) for s in items]
        if sorted(want) != sorted(have):
            raise ValueError("Reorder must keep the same stocks")
        lists[i]["items"] = want
        _save(lists)
        return lists


# --- quotes ------------------------------------------------------------------------------------------


def _eod_tables() -> tuple[dict[str, dict], dict[str, dict]]:
    """Last two closes per stock and index from the NSE store, re-read when the store changes."""
    import pandas as pd

    from backtest.loaders import nse_store

    root = nse_store.store_dir()
    bars = root / "bars.parquet"
    try:
        mtime = bars.stat().st_mtime
    except OSError:
        return {}, {}
    if _EOD["mtime"] == mtime and _EOD["stocks"]:
        return _EOD["stocks"], _EOD["indices"]
    end = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)
    recent = pd.read_parquet(bars, columns=["symbol", "date", "close"],
                             filters=[("date", ">=", end - pd.Timedelta(days=20))])
    recent = recent.sort_values("date")
    stocks: dict[str, dict] = {}
    for sym, g in recent.groupby("symbol"):
        closes = g["close"].tolist()
        stocks[str(sym)] = {"ltp": float(closes[-1]), "prev": float(closes[-2]) if len(closes) > 1 else None,
                            "as_of": str(pd.Timestamp(g["date"].iloc[-1]).date())}
    indices: dict[str, dict] = {}
    ip = root / "indices.parquet"
    if ip.exists():
        frame = pd.read_parquet(ip).sort_values("date")
        for name, g in frame.groupby("index_name"):
            closes = g["close"].tolist()
            indices[str(name)] = {"ltp": float(closes[-1]), "prev": float(closes[-2]) if len(closes) > 1 else None,
                                  "as_of": str(pd.Timestamp(g["date"].iloc[-1]).date())}
    _EOD.update(mtime=mtime, stocks=stocks, indices=indices, at=time.time())
    return stocks, indices


def _row(sym: str, ltp: float | None, prev: float | None, **extra: Any) -> dict[str, Any]:
    chg = (ltp - prev) if ltp is not None and prev else None
    return {"symbol": sym, "exchange": "NSE", "ltp": ltp, "prev_close": prev,
            "change": round(chg, 2) if chg is not None else None,
            "change_pct": round(chg / prev * 100, 2) if chg is not None and prev else None, **extra}


def quotes(symbols: list[str], *, with_indices: bool = True) -> dict[str, Any]:
    """``{live, source, at, indices: [...], quotes: {SYM: row}}`` for up to 500 names."""
    from src.live.profit_guard.feeds import live_quotes

    syms = []
    for s in symbols[:MAX_ITEMS * 2]:
        try:
            syms.append(clean_symbol(s))
        except ValueError:
            continue
    names = syms + (INDICES if with_indices else [])
    live = live_quotes.active()
    out: dict[str, Any] = {"live": False, "source": "nse_eod", "at": time.time(), "indices": [], "quotes": {}}
    if live and names:
        live_quotes.prime(names)
        got = 0
        for n in names:
            q = live_quotes.quote(n)
            if not q:
                continue
            got += 1
            prev = (q.get("ohlc") or {}).get("close")
            row = _row(n, q.get("last_price"), prev, volume=q.get("volume"), open=(q.get("ohlc") or {}).get("open"),
                       high=(q.get("ohlc") or {}).get("high"), low=(q.get("ohlc") or {}).get("low"))
            if n in INDICES:
                out["indices"].append({**row, "exchange": "INDEX"})
            else:
                out["quotes"][n] = row
        if got:
            out.update(live=True, source="kite")
            return out
    stocks, idx = _eod_tables()
    for n in syms:
        e = stocks.get(n)
        out["quotes"][n] = _row(n, e["ltp"], e["prev"], as_of=e["as_of"]) if e else _row(n, None, None)
    if with_indices:
        for n in INDICES:
            e = idx.get(n)
            if e:
                out["indices"].append({**_row(n, e["ltp"], e["prev"], as_of=e["as_of"]), "exchange": "INDEX"})
    return out


def search(q: str, limit: int = 20) -> list[dict[str, str]]:
    """NSE symbols that start with (then contain) ``q``, from the NSE store's latest day."""
    q = (q or "").strip().upper().replace(".NS", "")
    if not q:
        return []
    stocks, _ = _eod_tables()
    names = sorted(stocks)
    starts = [s for s in names if s.startswith(q)]
    has = [s for s in names if q in s and not s.startswith(q)]
    return [{"symbol": s, "exchange": "NSE"} for s in (starts + has)[:limit]]
