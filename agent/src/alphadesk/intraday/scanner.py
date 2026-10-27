"""Chart scanner: a pre-market daily scan on split/bonus-adjusted bars and an intraday
opening-range scan on 5-minute bars. A scan is a list of charts that match a rule, not a list of
stocks to buy.

Daily bars come from the confirmed-adjusted NSE store (``scripts/nse_pull.py``) when it is
fresh, so a bonus or split never shows up as a fake breakout; otherwise from the india_equity
loader chain. Liquidity is traded value (price x volume), not volume alone.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from backtest.loaders import nse_store
from backtest.loaders.nse_corporate_actions import apply_factors
from src.alphadesk.indicators import rsi
from src.alphadesk.intraday import bars as ibars
from src.alphadesk.intraday.rules import BAR, hhmm, day_from_frame, orb_trigger
from src.alphadesk.symbols import normalize_india_symbol

logger = logging.getLogger(__name__)

MIN_PRICE = 50.0
MIN_TRADED_VALUE = 10e7  # Rs 10 crore a day
STORE_STALE_DAYS = 5


def watchlist_path():
    return ibars.intraday_dir() / "watchlist.json"


# --- universe ------------------------------------------------------------------------------------


def universe(source: str = "nifty200", min_price: float = MIN_PRICE,
             min_traded_value: float = MIN_TRADED_VALUE, limit: int | None = None) -> list[str]:
    """Bare NSE symbols. ``store`` = liquid ordinary equity from the NSE store (needs a pull);
    anything else is an index list key (nifty50, nifty100, nifty200, nifty500, ...)."""
    if source == "store":
        root = nse_store.store_dir()
        if not (root / "bars.parquet").exists():
            raise RuntimeError("no NSE store: run scripts/nse_pull.py first, or use an index list")
        since = pd.Timestamp(date.today() - timedelta(days=45))
        recent = pd.read_parquet(root / "bars.parquet", filters=[("date", ">=", since)],
                                 columns=["symbol", "date", "close", "volume"])
        recent["tv"] = recent["close"] * recent["volume"]
        g = recent.groupby("symbol").agg(tv=("tv", "median"), price=("close", "last"))
        syms = g[(g.tv >= min_traded_value) & (g.price >= min_price)].sort_values(
            "tv", ascending=False).index.tolist()
    else:
        from src.alphadesk.index_lists import members
        syms = [m["symbol"] for m in members(source)]
    return syms[:limit] if limit else syms


# --- daily scan ----------------------------------------------------------------------------------


def daily_setups(df: pd.DataFrame) -> list[str]:
    """Setups on the last bar of an adjusted daily frame (open/high/low/close/volume)."""
    if len(df) < 25:
        return []
    c, h, l = df["close"], df["high"], df["low"]
    out = []
    if c.iloc[-1] > c.iloc[-21:-1].max():
        out.append("breakout_20d")
    run_up = c.iloc[-6] / c.iloc[-16] - 1
    last5 = c.iloc[-5:]
    if run_up > 0.10 and last5.max() / last5.min() - 1 <= 0.05:
        out.append("bull_flag")
    ma20 = c.rolling(20).mean()
    if c.iloc[-2] < ma20.iloc[-2] and c.iloc[-1] > ma20.iloc[-1]:
        out.append("reclaim_ma20")
    r = rsi(c, 14)
    if r.iloc[-1] < 30:
        out.append("oversold")
    if r.iloc[-2] < 30 and c.iloc[-1] > c.iloc[-2]:
        out.append("oversold_bounce")
    rng = (h - l).iloc[-7:]
    if rng.iloc[-1] == rng.min():
        out.append("nr7")
    return out


def daily_frames(symbols: list[str], lookback: int = 160) -> tuple[dict[str, pd.DataFrame], str]:
    """Adjusted daily bars per symbol and the source name. The NSE store is used when fresh."""
    root = nse_store.store_dir()
    out: dict[str, pd.DataFrame] = {}
    source = "loader_chain"
    if (root / "bars.parquet").exists():
        last = pd.read_parquet(root / "bars.parquet", columns=["date"])["date"].max()
        if (date.today() - pd.Timestamp(last).date()).days <= STORE_STALE_DAYS:
            names = sorted({n for s in symbols for n in nse_store.symbol_aliases(s, root)})
            everything = nse_store.read_bars(root, names)
            for s in symbols:
                hist = nse_store.symbol_history(root, s, everything)
                if hist is None:
                    continue
                f, d = nse_store.load_factors(root, s)
                adj = apply_factors(hist[nse_store.BAR_COLUMNS], f, d, "price")
                out[s] = adj.tail(lookback)
            source = "nse_store"
        else:
            logger.warning("NSE store ends %s: falling back to the loader chain "
                           "(scripts/nse_pull.py --update)", pd.Timestamp(last).date())
    missing = [s for s in symbols if s not in out]
    if missing:
        from backtest.loaders.registry import fetch_chain
        start = (date.today() - timedelta(days=int(lookback * 1.6))).isoformat()
        for s in missing:
            frames, _ = fetch_chain("india_equity", [normalize_india_symbol(s)], start,
                                    date.today().isoformat())
            f = next(iter(frames.values()), None) if frames else None
            if f is not None and not f.empty:
                out[s] = f.tail(lookback)
        source = source if source == "nse_store" and not out.keys() - set(symbols) else (
            "mixed" if source == "nse_store" else source)
    return out, source


def scan_daily(symbols: list[str], min_price: float = MIN_PRICE,
               min_traded_value: float = MIN_TRADED_VALUE, as_of: date | None = None) -> dict:
    """Daily scan. ``as_of`` plans a past day using only bars BEFORE that day (no lookahead).
    ``rows`` holds every liquid symbol; ``hits`` only those with a setup."""
    frames, source = daily_frames(symbols)
    rows, liquid = [], 0
    for sym, df in frames.items():
        if as_of is not None:
            df = df[df.index < pd.Timestamp(as_of)]
        if len(df) < 25:
            continue
        price = float(df["close"].iloc[-1])
        tv = float((df["close"] * df["volume"]).iloc[-20:].mean())
        if price < min_price or tv < min_traded_value:
            continue
        liquid += 1
        vol_x = float(df["volume"].iloc[-1] / max(df["volume"].iloc[-21:-1].mean(), 1))
        rows.append({"symbol": sym, "price": round(price, 2), "traded_value_cr": round(tv / 1e7, 1),
                     "setups": daily_setups(df), "volume_x": round(vol_x, 2),
                     "as_of": df.index[-1].date().isoformat()})
    rows.sort(key=lambda h: h["traded_value_cr"], reverse=True)
    return {"mode": "daily", "source": source, "universe": len(symbols), "fetched": len(frames),
            "liquid": liquid, "rows": rows, "hits": [r for r in rows if r["setups"]],
            "generated_at": datetime.now().isoformat(timespec="seconds")}


# --- opening-range scan --------------------------------------------------------------------------


def scan_orb(symbols: list[str], or_minutes: int = 15, side: str = "both",
             now: datetime | None = None, min_vol_x: float | None = None,
             offline_dir: str | None = None) -> dict:
    """Opening-range breakouts (and breakdowns) on today's completed 5-minute bars."""
    now = now or ibars.now_ist()
    now_min = now.hour * 60 + now.minute
    sides = ("long", "short") if side == "both" else (side,)
    hits, usable, failed = [], 0, 0
    for sym in symbols:
        off = f"{offline_dir}/{sym}.json" if offline_dir else None
        _, frame = ibars.fetch(sym, range_="1d", offline=off)
        frame = frame[frame.index.date == now.date()] if not frame.empty else frame
        day = day_from_frame(now.date(), frame) if not frame.empty else None
        if day is None:
            failed += 1
            continue
        usable += 1
        for s in sides:
            trig = orb_trigger(day, s, or_minutes, min_vol_x=min_vol_x, now_min=now_min)
            if trig["status"] == "triggered":
                hits.append({"symbol": sym, "side": s, "time": f"{trig['time'] // 60:02d}:{trig['time'] % 60:02d}",
                             "price": trig["price"], "level": trig["level"], "volume_x": trig["vol_x"],
                             "range_high": trig["range"]["high"], "range_low": trig["range"]["low"]})
    hits.sort(key=lambda h: h["time"])
    return {"mode": "orb", "or_minutes": or_minutes, "as_of": now.isoformat(timespec="minutes"),
            "universe": len(symbols), "usable": usable, "failed": failed, "hits": hits}


def save_watchlist(result: dict) -> str:
    path = watchlist_path()
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return str(path)
