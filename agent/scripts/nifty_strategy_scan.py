"""Scan simple NIFTY strategies on 1-minute data, traded as 1 futures lot.

    python agent/scripts/nifty_strategy_scan.py --minute-dir path/to/1min

Spot index stands in for the future (basis and roll ignored). Costs per round
trip per lot (NSE, Nov 2026): ₹20 brokerage per order + 18% GST, 0.02% STT on
the sell side, 0.00173% exchange fee, 0.002% stamp on the buy side, and
1 index point of slippage per side.
"""

from __future__ import annotations

import argparse
import glob
import math
from pathlib import Path

import numpy as np
import pandas as pd

LOT = 65
CAPITAL = 250_000.0
SLIP = 1.0


def cost(price: pd.Series | float) -> pd.Series | float:
    notional = price * LOT
    return 2 * 20 * 1.18 + notional * (0.0002 + 2 * 0.0000173 + 0.00002) + 2 * SLIP * LOT


def load(folder: str) -> pd.Series:
    files = sorted(glob.glob(str(Path(folder) / "**" / "*.csv"), recursive=True))
    df = pd.concat(pd.read_csv(f, usecols=["Timestamp", "Close"]) for f in files)
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    s = df.drop_duplicates("Timestamp").set_index("Timestamp")["Close"].sort_index()
    t = s.index.hour * 60 + s.index.minute
    return s[(t >= 555) & (t <= 929)]


def at(px: pd.Series, hhmm: int) -> pd.Series:
    """Per-day price at the last minute <= hh:mm."""
    t = px.index.hour * 100 + px.index.minute
    x = px[t <= hhmm]
    return x.groupby(x.index.normalize()).last()


def trades_to_daily(pnl: pd.Series) -> pd.Series:
    return pnl.groupby(pnl.index.normalize()).sum()


def overnight(px):
    entry, exit_ = at(px, 1515), at(px, 930).shift(-1)
    pts = (exit_ - entry).dropna()
    return pts * LOT - cost(entry.loc[pts.index])


def intraday_long(px):
    a, b = at(px, 930), at(px, 1515)
    return (b - a) * LOT - cost(a)


def orb(px, minutes=15):
    """Break of the first `minutes` range; stop at the other side; exit 15:15."""
    out = {}
    end = 915 + minutes
    for day, d in px.groupby(px.index.normalize()):
        t = d.index.hour * 100 + d.index.minute
        rng = d[t < end]
        rest = d[(t >= end) & (t <= 1515)]
        if len(rng) < minutes // 2 or rest.empty:
            continue
        hi, lo = rng.max(), rng.min()
        up = rest[rest > hi]
        dn = rest[rest < lo]
        first_up = up.index[0] if len(up) else None
        first_dn = dn.index[0] if len(dn) else None
        if first_up is None and first_dn is None:
            continue
        # fill at the minute close that confirms the break, not at the level
        if first_dn is None or (first_up is not None and first_up < first_dn):
            side, stop, after = 1, lo, rest.loc[first_up:]
        else:
            side, stop, after = -1, hi, rest.loc[first_dn:]
        entry = after.iloc[0]
        after = after.iloc[1:]
        if after.empty:
            continue
        hit = after[after <= stop] if side == 1 else after[after >= stop]
        exit_ = hit.iloc[0] if len(hit) else after.iloc[-1]
        out[day] = side * (exit_ - entry) * LOT - cost(entry)
    return pd.Series(out)


def gap_fade(px, min_gap=0.004):
    prev = at(px, 1529).shift(1)
    o, c = at(px, 920), at(px, 1515)
    gap = o / prev - 1
    side = -np.sign(gap).where(gap.abs() >= min_gap)
    pnl = (side * (c - o) * LOT - cost(o)).where(side.notna())
    return pnl.dropna()


def daily_position_pnl(close: pd.Series, pos: pd.Series) -> pd.Series:
    """Positions decided on close t, held to close t+1, costs on changes."""
    pos = pos.reindex(close.index).fillna(0)
    held = pos.shift(1).fillna(0)
    pnl = held * close.diff().fillna(0) * LOT
    turns = pos.diff().abs().fillna(pos.abs())
    return pnl - turns * cost(close) / 2       # cost() is a round trip; charge half per side


def ema_trend(close, sig, fast=20, slow=50, short=False):
    """Signal on the 15:15 price, trade at the 15:29 close."""
    f, s = sig.ewm(span=fast, adjust=False).mean(), close.ewm(span=slow, adjust=False).mean()
    pos = (f > s).astype(float)
    if short:
        pos = pos - (f < s).astype(float)
    return daily_position_pnl(close, pos)


def rsi2(close, sig, entry=10, exit_=70, trend=200):
    d = sig.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rs = up.ewm(alpha=0.5, adjust=False).mean() / dn.ewm(alpha=0.5, adjust=False).mean()
    rsi = 100 - 100 / (1 + rs)
    above = sig > sig.rolling(trend).mean()
    pos, state = [], 0.0
    for r, a in zip(rsi, above):
        if state == 0 and r < entry and a:
            state = 1.0
        elif state == 1 and r > exit_:
            state = 0.0
        pos.append(state)
    return daily_position_pnl(close, pd.Series(pos, index=close.index))


def buy_hold(close):
    pos = pd.Series(1.0, index=close.index)
    return daily_position_pnl(close, pos)


DAYS: pd.DatetimeIndex = pd.DatetimeIndex([])


def summary(name: str, daily: pd.Series, start: str, end: str) -> dict:
    full = daily.reindex(DAYS).fillna(0).loc[start:end]   # flat days count as 0
    d = full
    years = max((d.index.max() - d.index.min()).days / 365.25, 0.1)
    eq = CAPITAL + d.cumsum()
    yearly = d.groupby(d.index.year).sum()
    return {
        "Strategy": name,
        "₹/yr per lot": d.sum() / years,
        "Return/yr on ₹2.5L": d.sum() / years / CAPITAL,
        "Max DD ₹": (eq - eq.cummax()).min(),
        "Sharpe": d.mean() / d.std() * math.sqrt(252) if d.std() > 0 else np.nan,
        "Trade days/yr": int((d != 0).sum() / years),
        "Green years": f"{int((yearly > 0).sum())}/{len(yearly)}",
    }


def table(rows):
    cols = list(rows[0])
    def f(k, v):
        if isinstance(v, (float, np.floating)):
            if "Return" in k:
                return f"{v:.1%}"
            return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}"
        return str(v)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    return "\n".join(out + ["| " + " | ".join(f(k, r[k]) for k in cols) + " |" for r in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minute-dir", required=True)
    px = load(ap.parse_args().minute_dir)
    close = at(px, 1529)
    sig = at(px, 1515).reindex(close.index).ffill()
    global DAYS
    DAYS = close.index
    strategies = {
        "Buy & hold (1 lot)": buy_hold(close),
        "Overnight: buy 15:15, sell 09:30": trades_to_daily(overnight(px)),
        "Intraday long: 09:30 → 15:15": intraday_long(px),
        "15-min opening range breakout": orb(px, 15),
        "30-min opening range breakout": orb(px, 30),
        "Gap fade (gap ≥ 0.4%)": gap_fade(px),
        "EMA 20/50 trend, long only": ema_trend(close, sig),
        "EMA 20/50 trend, long/short": ema_trend(close, sig, short=True),
        "RSI(2) dip buy above 200-DMA": rsi2(close, sig),
    }
    for period, (s, e) in {"2017-2021": ("2017-04-01", "2021-12-31"),
                           "2022-2026": ("2022-01-01", "2026-12-31"),
                           "Full 2017-2026": ("2017-04-01", "2026-12-31")}.items():
        rows = [summary(n, d, s, e) for n, d in strategies.items()]
        print(f"## {period}\n\n" + table(rows) + "\n")


if __name__ == "__main__":
    main()
