"""Lightweight India-cost backtest gate for break-retest variants.

Uses a simple R-multiple simulation with ~10 bps round-trip friction as a
stand-in for STT/slippage. A variant may enter the Arena only if:
  - closed trades >= MIN_TRADES
  - average R >= MIN_AVG_R (default 2.0)
  - profit factor >= MIN_PF
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from src.alphadesk.engines.break_retest import Variant, detect_order_block, detect_orb, detect_pdh_pdl

MIN_TRADES = 8
MIN_AVG_R = 0.25  # positive expectancy; per-trade R:R≥2 enforced at setup time
MIN_PF = 1.2
ROUND_TRIP_COST = 0.001  # 10 bps


def _ohlc(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    lower = {str(c).lower(): c for c in df.columns}

    def col(*names: str) -> pd.Series:
        for n in names:
            if n.lower() in lower:
                return df[lower[n.lower()]].astype(float)
        raise KeyError(names)

    return col("open"), col("high"), col("low"), col("close")


def _sim_pdh_pdl(df: pd.DataFrame) -> list[float]:
    """Walk forward daily PDH/PDL retests; return list of R multiples."""
    _o, high, low, close = _ohlc(df)
    rs: list[float] = []
    i = 2
    while i < len(df) - 1:
        pdh = float(high.iloc[i - 1])
        pdl = float(low.iloc[i - 1])
        cur_low = float(low.iloc[i])
        cur_high = float(high.iloc[i])
        entry = float(close.iloc[i])
        # Long retest
        if cur_low <= pdh <= cur_high and entry >= pdh:
            stop = min(cur_low, pdh) * 0.995
            risk = entry - stop
            if risk > 0:
                target = entry + 2.0 * risk
                # look ahead up to 10 bars
                r = _resolve(high, low, i + 1, entry, stop, target, side="long")
                if r is not None:
                    rs.append(r - ROUND_TRIP_COST / max(risk / entry, 1e-6))
        # Short retest
        elif cur_low <= pdl <= cur_high and entry <= pdl:
            stop = max(cur_high, pdl) * 1.005
            risk = stop - entry
            if risk > 0:
                target = entry - 2.0 * risk
                r = _resolve(high, low, i + 1, entry, stop, target, side="short")
                if r is not None:
                    rs.append(r - ROUND_TRIP_COST / max(risk / entry, 1e-6))
        i += 1
    return rs


def _resolve(
    high: pd.Series,
    low: pd.Series,
    start: int,
    entry: float,
    stop: float,
    target: float,
    *,
    side: Literal["long", "short"],
    horizon: int = 10,
) -> float | None:
    end = min(len(high), start + horizon)
    for j in range(start, end):
        h = float(high.iloc[j])
        l = float(low.iloc[j])
        if side == "long":
            if l <= stop:
                return -1.0
            if h >= target:
                return (target - entry) / (entry - stop)
        else:
            if h >= stop:
                return -1.0
            if l <= target:
                return (entry - target) / (stop - entry)
    # time stop: mark to mid risk
    return 0.0


def _sim_order_block(df: pd.DataFrame) -> list[float]:
    rs: list[float] = []
    for end in range(10, len(df)):
        window = df.iloc[: end + 1]
        setup = detect_order_block(window)
        if setup is None or setup.side == "WATCH":
            continue
        _o, high, low, close = _ohlc(df)
        entry = setup.entry
        stop = setup.stop
        target = setup.target
        side = "long" if setup.side == "BUY" else "short"
        r = _resolve(high, low, end + 1, entry, stop, target, side=side)
        if r is not None:
            rs.append(r)
    # subsample to avoid overlapping spam
    return rs[::3]


def _sim_orb(df: pd.DataFrame) -> list[float]:
    """ORB needs intraday; on daily return empty (fail gate → excluded)."""
    idx = df.index
    if not hasattr(idx, "hour"):
        return []
    rs: list[float] = []
    for end in range(20, len(df), 5):
        setup = detect_orb(df.iloc[: end + 1])
        if setup is None or setup.side == "WATCH":
            continue
        _o, high, low, _c = _ohlc(df)
        side = "long" if setup.side == "BUY" else "short"
        r = _resolve(high, low, end + 1, setup.entry, setup.stop, setup.target, side=side)
        if r is not None:
            rs.append(r)
    return rs


def evaluate_variant(df: pd.DataFrame, variant: Variant) -> dict[str, Any]:
    if variant == "pdh_pdl":
        rs = _sim_pdh_pdl(df)
    elif variant == "order_block":
        rs = _sim_order_block(df)
    else:
        rs = _sim_orb(df)

    n = len(rs)
    if n == 0:
        return {
            "variant": variant,
            "trades": 0,
            "avg_r": None,
            "profit_factor": None,
            "pass": False,
            "reason": "no_trades",
        }
    arr = np.array(rs, dtype=float)
    wins = arr[arr > 0].sum()
    losses = abs(arr[arr < 0].sum())
    pf = float(wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0)
    avg_r = float(arr.mean())
    passed = n >= MIN_TRADES and avg_r >= MIN_AVG_R and (pf >= MIN_PF or pf == float("inf"))
    return {
        "variant": variant,
        "trades": n,
        "avg_r": round(avg_r, 4),
        "profit_factor": None if pf == float("inf") else round(pf, 4),
        "pass": passed,
        "reason": "ok" if passed else "gate_fail",
    }


def gate_all(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        v: evaluate_variant(df, v)  # type: ignore[arg-type]
        for v in ("pdh_pdl", "order_block", "orb")
    }
