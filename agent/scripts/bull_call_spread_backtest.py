"""Backtest the trend-filtered NIFTY bull call spread against the AlgoBulls-style
original (always-on entry, stop on the short leg).

Daily bars. Pass a CSV of NIFTY daily OHLC (columns date, open, high, low,
close[, volume]) or let the script pull ^NSEI from yfinance:

    PYTHONPATH=agent python agent/scripts/bull_call_spread_backtest.py
    PYTHONPATH=agent python agent/scripts/bull_call_spread_backtest.py --csv nifty_daily.csv

Parameters are frozen (no tuning); the two periods are reported separately so
a result that only holds in one of them is visible. Prints markdown tables.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engines.options_portfolio import run_options_backtest

_spec = importlib.util.spec_from_file_location(
    "options_strategy_engines",
    Path(__file__).parents[1] / "src" / "skills" / "options-strategy" / "example_signal_engine.py",
)
engines = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engines)

CODE = "^NSEI"
CASH = 500_000.0
# India: ~6.5% risk-free; NIFTY index options have a put skew, so keep the
# engine's default smile. Commission is % of premium per side and stands in
# for brokerage + STT + exchange fees + half the bid-ask spread.
OPTIONS = {"risk_free_rate": 0.065, "iv_skew": -0.3, "iv_curvature": 0.0,
           "default_iv": 0.15, "contract_multiplier": 1.0, "margin_enabled": False}
COMMISSION = 0.005   # ~₹20/order + 0.1% STT on sells + fees + ~1pt slippage
PERIODS = {
    "2017-2021": ("2017-04-03", "2017-07-03", "2021-12-31"),   # load from, trade from, end
    "2022-2026": ("2021-09-01", "2022-01-03", "2026-12-31"),
}
VARIANTS = {
    # The Odyssey strategy as described: enter every day, stop on the short leg.
    "algobulls_style": {"trend": "none", "short_leg_stop": 0.5, "cooldown_after_loss": 0},
    "filter_only": {"trend": "ema", "short_leg_stop": 0.5, "cooldown_after_loss": 0},
    "no_short_stop_only": {"trend": "none", "short_leg_stop": None},
    "improved": {},   # engine defaults: EMA(20) gate, no short-leg stop, 2-bar cooldown
}

_data: pd.DataFrame | None = None


class _Loader:
    name = "nifty"

    def fetch(self, codes, start_date, end_date):  # noqa: ANN001
        return {CODE: _data.loc[start_date:end_date].copy()}


def load(csv: str | None) -> pd.DataFrame:
    if csv:
        df = pd.read_csv(csv, index_col=0, parse_dates=True)
    else:
        from backtest.loaders.yfinance_loader import DataLoader

        df = DataLoader().fetch([CODE], "2017-01-01", "2026-12-31")[CODE]
    df.columns = [c.lower() for c in df.columns]
    if "volume" not in df:
        df["volume"] = 0
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def run(params: dict, window: tuple, commission: float = COMMISSION):
    load_from, trade_from, end = window
    engine = engines.BullCallSpreadEngine(
        **{k: OPTIONS[k] for k in ("risk_free_rate", "iv_skew", "iv_curvature", "default_iv")},
        commission=commission, capital=CASH, start_date=trade_from, **params)
    config = {
        "codes": [CODE], "start_date": load_from, "end_date": end, "engine": "options",
        "initial_cash": CASH, "commission": commission,
        "evaluation_start_date": trade_from, "options_config": dict(OPTIONS),
    }
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        run_options_backtest(config, _Loader(), engine, Path(tmp))
        art = Path(tmp) / "artifacts"
        equity = pd.read_csv(art / "equity.csv", index_col="timestamp", parse_dates=True)["equity"]
        trades = pd.read_csv(art / "trades.csv")
    return equity.loc[trade_from:], trades


def stats(equity: pd.Series, trades: pd.DataFrame | None = None) -> dict:
    ret = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    dd = equity / equity.cummax() - 1
    out = {
        "CAGR": (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1,
        "Sharpe": ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else np.nan,
        "MaxDD": dd.min(),
        "WorstMo": equity.resample("ME").last().pct_change().dropna().min(),
    }
    if trades is not None:
        settled = trades[trades["side"].isin(["close", "exercise", "expire"])]
        per_trade = settled.groupby(["entry_date", "expiry"])["pnl"].sum()
        losing = (per_trade < 0).astype(int)
        streak = losing.groupby((losing != losing.shift()).cumsum()).cumsum().max()
        out.update({
            "Trades": len(per_trade),
            "WinRate": (per_trade > 0).mean() if len(per_trade) else np.nan,
            "AvgWin/AvgLoss": (per_trade[per_trade > 0].mean() / -per_trade[per_trade < 0].mean()
                               if (per_trade < 0).any() and (per_trade > 0).any() else np.nan),
            "LossStreak": int(streak) if len(per_trade) else 0,
            "Rejects": int((trades["side"] == "reject").sum()),
        })
    return out


def table(rows: list[dict]) -> str:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    pct = {"CAGR", "MaxDD", "WorstMo", "WinRate"}

    def fmt(k: str, v: object) -> str:
        if isinstance(v, float):
            return f"{v:.1%}" if k in pct else f"{v:.2f}"
        return str(v)

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(k, r.get(k, "")) for k in cols) + " |" for r in rows]
    return "\n".join(lines)


def main() -> None:
    global _data
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="NIFTY daily OHLC CSV; default pulls ^NSEI from yfinance")
    _data = load(ap.parse_args().csv)

    rows = []
    for period, window in PERIODS.items():
        close = _data.loc[window[1]:window[2], "close"]
        rows.append({"Period": period, "Variant": "nifty_buy_and_hold", **stats(close)})
        for name, params in VARIANTS.items():
            rows.append({"Period": period, "Variant": name, **stats(*run(params, window))})
    print("## Variants (5% of capital per spread, 0.5% cost per side)\n\n" + table(rows) + "\n")

    sens = []
    full = ("2017-04-03", "2017-07-03", "2026-12-31")
    for span in (10, 20, 50):
        sens.append({"Test": f"ema_span={span}",
                     **stats(*run({"ema_span": span}, full))})
    for cost in (0.0, 0.01, 0.02):
        sens.append({"Test": f"cost={cost:.0%}/side", **stats(*run({}, full, cost))})
    for skew in (0.0, -0.6):
        OPTIONS["iv_skew"] = skew
        sens.append({"Test": f"iv_skew={skew}", **stats(*run({}, full))})
    OPTIONS["iv_skew"] = -0.3
    print("## Improved variant, full period, sensitivity\n\n" + table(sens))


if __name__ == "__main__":
    main()
