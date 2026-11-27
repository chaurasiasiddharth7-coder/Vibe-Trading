"""Intraday backtest of the NIFTY bull call spread on 15-minute candles,
AlgoBulls Odyssey ABODSYM084 rules vs a trend-filtered version.

Input: a folder of NIFTY 1-minute OHLC CSVs (columns Timestamp, Open, High,
Low, Close; IST), e.g. github.com/technovusin/nifty50-historical-data/1min.

    python agent/scripts/bull_call_spread_intraday.py --minute-dir path/to/1min

Model, per trading day:
- Weekly expiry: Thursday until 28 Aug 2025, Tuesday from 1 Sep 2025; if that
  day isn't in the data (holiday) the previous trading day is used.
- Entry at the close of a 15-minute candle, from 09:30 until 14:30. Buy the
  call ``itm_steps`` strikes below ATM, sell the call ``otm_steps`` strikes
  above ATM (50-point strikes).
- Exits, all legs together:
  * short-leg stop: short call trades >= (1 + short_sl) x its entry price,
    checked every minute (AlgoBulls rule; off in the filtered version);
  * net premium target / stop as a fraction of the debit, checked at each
    15-minute candle;
  * day P&L tracker: stop trading for the day once day P&L crosses
    +day_target / -day_stop (in ₹ per lot), checked every minute;
  * 15:15 time limit.
- Re-entry at the next 15-minute candle after an exit (cap: ``max_entries``).
- Filtered version only enters when the 15-minute close is above its own
  20-period EMA *and* the previous daily close is above the 20-day EMA.

Option prices are Black-Scholes on the prior day's 20-day realized vol (no
historical option chain is used). Costs per lot (NSE, Nov 2026): ₹20 brokerage
per order + 18% GST, 0.1% STT on sell premium, 0.0353% exchange fee on
premium, 0.5 point slippage per leg per side.
"""

from __future__ import annotations

import argparse
import glob
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

LOT = 65            # NIFTY lot size; held constant so years compare
STEP = 50
RATE = 0.065
SLIPPAGE = 0.5      # points per leg per side
BROKERAGE = 20 * 1.18
STT = 0.001
EXCH = 0.000353
TUESDAY_FROM = pd.Timestamp("2025-09-01")


@dataclass(frozen=True)
class Config:
    name: str
    itm_steps: int = 2
    otm_steps: int = 2
    short_sl: float | None = 0.3
    target: float | None = 0.3      # of debit
    stop: float | None = 0.3        # of debit
    day_target: float | None = 3000.0
    day_stop: float | None = 2000.0
    max_entries: int = 3
    trend_filter: bool = False


from scipy.special import ndtr as _ncdf  # noqa: E402


def call_price(S: np.ndarray, K: float, T: np.ndarray, vol: float) -> np.ndarray:
    T = np.maximum(T, 1e-6)
    sd = vol * np.sqrt(T)
    d1 = (np.log(S / K) + (RATE + 0.5 * vol * vol) * T) / sd
    return S * _ncdf(d1) - K * np.exp(-RATE * T) * _ncdf(d1 - sd)


def load_minutes(folder: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(Path(folder) / "**" / "*.csv"), recursive=True))
    df = pd.concat(pd.read_csv(f, usecols=["Timestamp", "Close"]) for f in files)
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    df = df.drop_duplicates("Timestamp").set_index("Timestamp").sort_index()
    t = df.index.time
    keep = (t >= pd.Timestamp("09:15").time()) & (t <= pd.Timestamp("15:29").time())
    return df.loc[keep, "Close"]


def expiries(days: pd.DatetimeIndex) -> dict:
    """Map each trading day to its weekly expiry trading day."""
    dayset = set(days)
    out = {}
    for d in days:
        wd = 1 if d >= TUESDAY_FROM else 3
        e = d + pd.Timedelta(days=(wd - d.weekday()) % 7)
        while e not in dayset and e > d:
            e -= pd.Timedelta(days=1)
        if e < d or e not in dayset:      # holiday fell on the expiry, roll to next week
            e = d + pd.Timedelta(days=(wd - d.weekday()) % 7 + 7)
            while e not in dayset and e > d:
                e -= pd.Timedelta(days=1)
        out[d] = e
    return out


def costs(prem_buy: float, prem_sell: float) -> float:
    """₹ per lot for one order pair (one buy, one sell) of the given premiums."""
    turnover = (prem_buy + prem_sell) * LOT
    return 2 * BROKERAGE + STT * prem_sell * LOT + EXCH * turnover


def run(minutes: pd.Series, cfg: Config) -> pd.DataFrame:
    daily = minutes.groupby(minutes.index.normalize()).last()
    hv = np.log(daily).diff().rolling(20).std().mul(math.sqrt(252)).shift(1).clip(lower=0.08)
    day_trend = (daily > daily.ewm(span=20, adjust=False).mean()).shift(1, fill_value=False)
    exp_map = expiries(daily.index)
    all_mins = minutes.index.hour * 60 + minutes.index.minute
    c15_all = minutes[((all_mins - 555) % 15) == 14]
    above15 = c15_all > c15_all.ewm(span=20, adjust=False).mean()
    rows = []
    for day, px in minutes.groupby(minutes.index.normalize()):
        vol = hv.get(day, np.nan)
        if np.isnan(vol) or len(px) < 300:
            continue
        expiry_close = exp_map[day] + pd.Timedelta(hours=15, minutes=30)
        S = px.to_numpy()
        ts = px.index
        T = ((expiry_close - ts).total_seconds().to_numpy() / (365 * 86400))
        mins = ts.hour * 60 + ts.minute
        candle_end = ((mins - 555) % 15 == 14)           # last minute of each 15m candle

        pnl_day, entries, pos, stopped = 0.0, 0, None, False
        legs_px = {}
        for i in range(len(S)):
            m = mins[i]
            if pos is not None:
                kl, ks, debit, short_in, j0, entry_cost = pos
                lp, sp = legs_px[kl][i], legs_px[ks][i]
                net = lp - sp
                open_pnl = (net - debit) * LOT
                why = None
                if cfg.short_sl is not None and sp >= (1 + cfg.short_sl) * short_in:
                    why = "short_sl"
                elif candle_end[i - 1] if i else False:
                    if cfg.target is not None and net >= debit * (1 + cfg.target):
                        why = "target"
                    elif cfg.stop is not None and net <= debit * (1 - cfg.stop):
                        why = "stop"
                if why is None and (
                    (cfg.day_target is not None and pnl_day + open_pnl >= cfg.day_target)
                    or (cfg.day_stop is not None and pnl_day + open_pnl <= -cfg.day_stop)
                ):
                    why, stopped = "day_tracker", True
                if why is None and (m >= 15 * 60 + 15 or i == len(S) - 1):
                    why = "time"
                if why:
                    exit_l, exit_s = lp - SLIPPAGE, sp + SLIPPAGE
                    exit_cost = costs(exit_s, exit_l) + 2 * SLIPPAGE * LOT
                    exit_part = (exit_l - exit_s - debit) * LOT - costs(exit_s, exit_l)
                    pnl_day += exit_part                    # entry cost already booked
                    rows.append({"day": day, "entry": ts[j0], "exit": ts[i], "why": why,
                                 "pnl": exit_part - entry_cost,
                                 "cost": exit_cost + entry_cost + 2 * SLIPPAGE * LOT, "dte": (exp_map[day] - day).days})
                    pos = None
                continue
            if stopped or entries >= cfg.max_entries or not candle_end[i]:
                continue
            if m < 9 * 60 + 29 or m > 14 * 60 + 30 or i + 1 >= len(S):
                continue
            if cfg.trend_filter and not (day_trend.get(day, False) and bool(above15.get(ts[i], False))):
                continue
            atm = round(S[i] / STEP) * STEP
            kl, ks = atm - cfg.itm_steps * STEP, atm + cfg.otm_steps * STEP
            for k in (kl, ks):
                if k not in legs_px:
                    legs_px[k] = call_price(S, k, T, vol)
            j = i + 1                                   # fill on the next minute
            buy_l, sell_s = legs_px[kl][j] + SLIPPAGE, legs_px[ks][j] - SLIPPAGE
            debit = buy_l - sell_s
            if debit <= 0:
                continue
            entry_cost = costs(buy_l, sell_s)
            pnl_day -= entry_cost
            pos = (kl, ks, debit, legs_px[ks][j], j, entry_cost)
            entries += 1
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame, start: str, end: str, capital: float = 200_000.0) -> dict:
    t = trades[(trades["day"] >= start) & (trades["day"] <= end)]
    daily = t.groupby("day")["pnl"].sum()
    real = t
    per_trade = t["pnl"].to_numpy()
    eq = capital + daily.cumsum()
    dd = (eq - eq.cummax()).min()
    years = max((min(pd.Timestamp(end), daily.index.max()) - daily.index.min()).days / 365.25, 0.1) if len(daily) else 1
    yearly = daily.groupby(daily.index.year).sum()
    return {
        "₹/yr per lot": daily.sum() / years,
        "Return/yr on ₹2L": daily.sum() / years / capital,
        "Max DD ₹": dd,
        "Sharpe": daily.mean() / daily.std() * math.sqrt(252) if daily.std() > 0 else np.nan,
        "Costs ₹/yr": t["cost"].sum() / years if len(t) else 0.0,
        "Trades": len(real),
        "Win rate": float((per_trade > 0).mean()) if len(per_trade) else np.nan,
        "Green years": f"{int((yearly > 0).sum())}/{len(yearly)}",
        "Exit mix": ", ".join(f"{k} {v:.0%}" for k, v in real["why"].value_counts(normalize=True).items()),
    }


def table(rows: list[dict]) -> str:
    cols = list(rows[0])
    pct = {"Return/yr on ₹2L", "Win rate"}

    def f(k, v):
        if isinstance(v, (float, np.floating)):
            if k in pct:
                return f"{v:.1%}"
            return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}"
        return str(v)

    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(f(k, r[k]) for k in cols) + " |" for r in rows]
    return "\n".join(out)


VARIANTS = [
    Config("algobulls_style"),
    Config("filtered_no_short_sl", short_sl=None, trend_filter=True),
    Config("filtered_keep_short_sl", trend_filter=True),
    Config("algobulls_no_short_sl", short_sl=None),
]
PERIODS = {"2017-2021": ("2017-04-01", "2021-12-31"), "2022-2026": ("2022-01-01", "2026-12-31"),
           "Full": ("2017-04-01", "2026-12-31")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minute-dir", required=True)
    ap.add_argument("--out", default=None, help="write per-trade CSVs here")
    a = ap.parse_args()
    minutes = load_minutes(a.minute_dir)
    results = {}
    rows = []
    for cfg in VARIANTS:
        tr = run(minutes, cfg)
        results[cfg.name] = tr
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            tr.to_csv(Path(a.out) / f"{cfg.name}.csv", index=False)
        for p, (s, e) in PERIODS.items():
            rows.append({"Variant": cfg.name, "Period": p, **summarize(tr, s, e)})
    print("## 1 lot, 15-minute candles, real costs\n\n" + table(rows) + "\n")

    sens = []
    base = VARIANTS[1]
    for c in (replace(base, name="strikes ±1", itm_steps=1, otm_steps=1),
              replace(base, name="strikes ±4", itm_steps=4, otm_steps=4),
              replace(base, name="1 entry/day", max_entries=1),
              replace(base, name="no day tracker", day_target=None, day_stop=None),
              replace(base, name="target/stop 50%", target=0.5, stop=0.5)):
        sens.append({"Test": c.name, **summarize(run(minutes, c), *PERIODS["Full"])})
    print("## filtered_no_short_sl, full period, sensitivity\n\n" + table(sens))


if __name__ == "__main__":
    main()
