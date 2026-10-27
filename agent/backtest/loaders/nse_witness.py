"""Yahoo as a second witness for corporate actions the NSE feed missed or the price gate refused.

Yahoo's close is split/bonus-adjusted (not dividend-adjusted). Divided by the RAW NSE close, it is
piecewise constant, and a persistent step in that ratio is an adjustment Yahoo applied on that date.
A step is turned into a factor only when (1) no accepted factor already explains it, and (2) the
stock's own ex-date opening gap agrees with it (the same price gate every factor passes). Two
independent sources must agree: Yahoo's event list and NSE's own prices. One-day disagreements
between Yahoo's last trade and NSE's official close revert next day and are not steps.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.loaders import nse_store
from backtest.loaders.nse_corporate_actions import Factor, ex_gap, gate_status

logger = logging.getLogger(__name__)

MIN_STEP = 0.05  # smallest persistent ratio change treated as an adjustment
NOISE_MULT = 6.0  # ... and it must stand this many times above the symbol's own day-to-day noise
PERSIST = 10  # sessions that must hold the new level, on each side
CACHE_DAYS = 7
MATCH_TOL = 0.04  # an accepted factor within this of Yahoo's agrees with it
DATE_TOL_DAYS = 4


def find_steps(raw: pd.Series, yahoo: pd.Series) -> list[tuple[pd.Timestamp, float]]:
    """(date, factor) of every persistent step in yahoo/raw. factor < 1 lowers pre-date prices."""
    both = pd.concat([raw.rename("raw"), yahoo.rename("y")], axis=1, join="inner").dropna()
    if len(both) < 2 * PERSIST + 2:
        return []
    c = np.log(both["y"] / both["raw"]).to_numpy()
    # Yahoo's last trade vs NSE's official close differs by a little every day on thin stocks;
    # a real adjustment must clear that noise by a wide margin, not just MIN_STEP.
    noise = float(np.median(np.abs(np.diff(c)))) * 1.4826
    threshold = max(MIN_STEP, NOISE_MULT * noise)
    out = []
    i = PERSIST
    while i < len(c) - PERSIST:
        before, after = np.median(c[i - PERSIST:i]), np.median(c[i:i + PERSIST])
        if abs(after - before) > threshold and abs(c[i] - after) < abs(c[i] - before):
            out.append((both.index[i], float(math.exp(before - after))))
            i += PERSIST  # one event per step
        else:
            i += 1
    return out


def witness_symbol(raw_bars: pd.DataFrame, yahoo_close: pd.Series, accepted: list[Factor],
                   index: pd.DataFrame | None = None) -> list[dict]:
    """Candidate factors for one symbol: Yahoo steps no accepted factor explains, gated by the bars."""
    found = []
    for day, f in find_steps(raw_bars["close"], yahoo_close):
        if f > 1 and f < 1.8:
            continue  # a price-raising step is a consolidation only at a clean ratio; below that it is noise
        near = [a for a in accepted if abs((pd.Timestamp(a.ex_date) - day).days) <= DATE_TOL_DAYS]
        if near:
            continue  # already adjusted (even if the value differs: never override an accepted one)
        # the ex-date bar is the first bar at/after the step, or the one before (Yahoo can be a day off)
        best = None
        for offset in (0, -1, 1):
            pos = int(raw_bars.index.searchsorted(day)) + offset
            if 0 < pos < len(raw_bars):
                ts = raw_bars.index[pos]
                gap = ex_gap(raw_bars, ts.date())
                if gap is None:
                    continue
                g = gap[1]
                if index is not None and ts in index.index and index.index.get_loc(ts) > 0:
                    at = index.index.get_loc(ts)
                    g /= float(index["open"].iloc[at]) / float(index["close"].iloc[at - 1])
                status = gate_status(g, f)
                if status == "confirmed":  # ambiguous gaps are noise on thin stocks: never enough
                    best = {"ex_date": ts.date().isoformat(), "factor": round(f, 6),
                            "gap": round(g, 4), "gate": status}
        if best:
            found.append(best)
    return found


def yahoo_daily(symbol: str, root: Path | None = None) -> pd.Series | None:
    from backtest.loaders import yahoo_client

    cache = (root / "yahoo_cache_daily" / f"{symbol.replace('&', '_AND_')}.parquet") if root else None
    if cache and cache.exists() and (datetime.now().timestamp() - cache.stat().st_mtime) < CACHE_DAYS * 86400:
        return pd.read_parquet(cache)["close"]
    try:
        # an explicit daily window: range="max" silently returns monthly bars
        rows = yahoo_client.get_chart(f"{symbol}.NS", interval="1d", period1=1306000000,
                                      period2=int(datetime.now().timestamp()) + 86400)
    except Exception as exc:  # noqa: BLE001 - delisted / renamed / throttled: skip this symbol
        logger.debug("yahoo witness skipped %s: %s", symbol, exc)
        return None
    if not rows:
        return None
    s = pd.Series({pd.Timestamp(r["trade_date"], unit="s").normalize(): r["close"] for r in rows})
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if cache:
        cache.parent.mkdir(exist_ok=True)
        s.rename("close").to_frame().to_parquet(cache)
    return s


def run(root: Path, symbols: list[str]) -> dict:
    """Witness each symbol against Yahoo and write ``witness.json`` (kept across runs)."""
    path = root / "witness.json"
    store = json.loads(path.read_text()) if path.exists() else {}
    factors = [f for f in json.loads((root / "factors.json").read_text()) if f["kind"] != "dividend"] \
        if (root / "factors.json").exists() else []
    index = nse_store.read_index(root)
    everything = nse_store.read_bars(root, sorted({n for s in symbols for n in nse_store.symbol_aliases(s, root)}))
    checked = added = 0
    for sym in symbols:
        hist = nse_store.symbol_history(root, sym, everything)
        y = yahoo_daily(sym, root) if hist is not None else None
        if hist is None or y is None:
            continue
        accepted = [Factor(sym, date.fromisoformat(f["ex_date"]), f["kind"], f["value"], f["text"], f["status"])
                    for f in factors if f["symbol"] == sym]
        found = witness_symbol(hist[nse_store.BAR_COLUMNS], y, accepted, index)
        store[sym] = {"checked_at": datetime.now().isoformat(timespec="seconds"), "found": found}
        checked += 1
        added += len(found)
    path.write_text(json.dumps(store, indent=1))
    return {"checked": checked, "witnessed_factors": added}
