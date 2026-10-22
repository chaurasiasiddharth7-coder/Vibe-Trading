"""History test: replay the signal-board rules over past daily bars.

For every past session the board's classifier sees only the indicators known
that day. When it says LONG or SHORT (and no trade is already open for that
symbol) a trade opens at that close and exits with the same rules as live
paper trades (``board_paper.simulate_exit``).

Kronos is not replayed (one model run per bar is too slow), so history uses
price votes only: all three of trend, structure, momentum must agree. These
results are kept apart from the paper ledger — they show how the rules
behaved in the past, not how they trade from today.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src.alphadesk.board_paper import COST_PCT, DEFAULT_PLAN, EXIT_PLANS, MAX_BARS, simulate_exit
from src.alphadesk.indicators import atr, ema, macd, rsi, supertrend_direction
from src.alphadesk.signal_board import classify
from src.alphadesk.universes import asset_class_for, mentor_scan_universe

WARMUP_BARS = 200


def _path(universe: str = "core"):
    from src.alphadesk.store import alphadesk_root

    name = "board_replay.json" if universe == "core" else f"board_replay_{universe}.json"
    return alphadesk_root() / name


def _frame(symbol: str, period: str) -> pd.DataFrame:
    from src.alphadesk.analysis import _col, fetch_india_ohlcv

    _, df = fetch_india_ohlcv(symbol, period=period, interval="1d")
    out = pd.DataFrame(
        {
            "open": _col(df, "open"),
            "high": _col(df, "high"),
            "low": _col(df, "low"),
            "close": _col(df, "close"),
        }
    )
    out.index = pd.to_datetime(out.index)
    return out


def replay_symbol(symbol: str, df: pd.DataFrame, plan: str = DEFAULT_PLAN) -> list[dict[str, Any]]:
    close, high, low = df["close"], df["high"], df["low"]
    ema50, ema200 = ema(close, 50), ema(close, 200)
    macd_line, macd_sig, _ = macd(close)
    rsi14 = rsi(close, 14)
    atr14 = atr(high, low, close, 14)
    st = supertrend_direction(high, low, close)

    bars = [
        {"time": ts.strftime("%Y-%m-%d"), "open": o, "high": h, "low": lo, "close": c}
        for ts, o, h, lo, c in zip(df.index, df["open"], high, low, close)
    ]
    trades: list[dict[str, Any]] = []
    i = WARMUP_BARS
    while i < len(df) - 1:
        metrics = {
            "supertrend": "BULLISH" if st.iloc[i] > 0 else "BEARISH",
            "ema_50": float(ema50.iloc[i]),
            "ema_200": float(ema200.iloc[i]),
            "macd_status": (
                "Bullish Crossover" if macd_line.iloc[i] > macd_sig.iloc[i] else "Bearish Crossover"
            ),
            "rsi_14": None if pd.isna(rsi14.iloc[i]) else float(rsi14.iloc[i]),
            "atr": None if pd.isna(atr14.iloc[i]) else float(atr14.iloc[i]),
        }
        row = classify(symbol, {"current_price": float(close.iloc[i]), "metrics": metrics}, None)
        if row["side"] not in {"LONG", "SHORT"}:
            i += 1
            continue
        exit_ = simulate_exit(
            bars[i + 1 :],
            side=row["side"],
            entry=row["entry"],
            stop=row["stop"],
            target=row["target"],
            atr=row["atr"],
            plan=plan,
        )
        if exit_ is None:  # still open at the end of history
            break
        trades.append(
            {"symbol": symbol, "side": row["side"], "entry_date": bars[i]["time"], **exit_}
        )
        i += exit_["bars_held"] + 1
    return trades


def _stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "avg_r": None, "total_r": 0.0, "profit_factor": None}
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gl = abs(sum(losses))
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 4),
        "avg_r": round(sum(rs) / n, 4),
        "total_r": round(sum(rs), 2),
        "profit_factor": round(sum(wins) / gl, 3) if gl > 0 else None,
    }


def run_replay(
    symbols: list[str] | None = None,
    *,
    period: str = "5y",
    universe_key: str = "core",
    progress=None,
) -> dict[str, Any]:
    universe = symbols or [
        s for s in mentor_scan_universe(include_etfs=True) if asset_class_for(s) != "liquid"
    ]
    trades: list[dict[str, Any]] = []
    by_plan: dict[str, list[dict[str, Any]]] = {p: [] for p in EXIT_PLANS}
    failed: list[str] = []
    bars: dict[str, int] = {}
    for n, sym in enumerate(universe):
        if progress:
            progress(n, len(universe))
        try:
            df = _frame(sym, period)
        except Exception:
            failed.append(sym)
            continue
        bars[sym] = len(df)
        if len(df) <= WARMUP_BARS + 5:
            failed.append(sym)
            continue
        for plan in EXIT_PLANS:
            got = replay_symbol(sym, df, plan)
            by_plan[plan].extend(got)
            if plan == DEFAULT_PLAN:
                trades.extend(got)

    by_symbol = {}
    for sym in sorted({t["symbol"] for t in trades}):
        mine = [t for t in trades if t["symbol"] == sym]
        by_symbol[sym] = {
            **_stats(mine),
            "LONG": _stats([t for t in mine if t["side"] == "LONG"]),
            "SHORT": _stats([t for t in mine if t["side"] == "SHORT"]),
        }
    result = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "period": period,
        "universe": universe_key,
        "rules": f"price votes only (no Kronos) · exit at stop / target / {MAX_BARS} sessions · cost {COST_PCT}%",
        "overall": _stats(trades),
        "long": _stats([t for t in trades if t["side"] == "LONG"]),
        "short": _stats([t for t in trades if t["side"] == "SHORT"]),
        "by_symbol": by_symbol,
        "plans": {
            plan: {
                "overall": _stats(ts),
                "long": _stats([t for t in ts if t["side"] == "LONG"]),
                "short": _stats([t for t in ts if t["side"] == "SHORT"]),
                "exit_mix": {
                    reason: sum(1 for t in ts if t["exit_reason"] == reason)
                    for reason in ("target", "stop", "trail", "time")
                },
            }
            for plan, ts in by_plan.items()
        },
        "bars": bars,
        "recent": sorted(trades, key=lambda t: t["exit_date"], reverse=True)[:25],
        "failed": failed,
    }
    _path(universe_key).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def load_replay(universe_key: str = "core") -> dict[str, Any] | None:
    """Past-test result for a universe; falls back to the core list's result."""
    for key in dict.fromkeys([universe_key, "core"]):
        path = _path(key)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None
