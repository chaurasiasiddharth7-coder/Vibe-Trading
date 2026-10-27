"""Calibrate entry and exit rules on stored 5-minute history, honestly.

Every combination of opening range, side, stop rule, target and volume filter is run on every
usable stored session of every symbol, net of NSE costs. Sessions are split by date: the grid is
ranked on the first 70% (in-sample) and the winner is judged on the last 30% (out-of-sample) it
never saw. The winner's in-sample Sharpe is also held against the multiple-testing selection bar:
the Sharpe the best of that many worthless variants would reach by luck (backtest.validation).
A config is only called an edge candidate when it is positive net of costs out-of-sample, has
enough trades, and clears the bar. Most will not: that is the useful answer.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from datetime import datetime

import numpy as np

from backtest.validation import selection_bar
from src.alphadesk.intraday import bars as ibars
from src.alphadesk.intraday.costs import DEFAULT_COSTS, CostConfig
from src.alphadesk.intraday.rules import DEFAULT_RISK_INR, orb_trigger, run_from_trigger

GRID = {
    "or_minutes": [15, 30],
    "side": ["long", "short"],
    "stop": ["or-mid", "or-edge", "atr:1.5"],
    "target": ["1R", "2R"],
    "min_vol_x": [None, 1.5],
}
IN_SAMPLE_FRACTION = 0.7
MIN_TRADES = 30
TRADING_DAYS = 250


def _metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": None, "avg_r_net": None, "avg_r_gross": None,
                "profit_factor": None}
    net = np.array([t["r_net"] for t in trades])
    gross = np.array([t["r_gross"] for t in trades])
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    return {"trades": len(net), "win_rate": round(float((net > 0).mean()), 4),
            "avg_r_net": round(float(net.mean()), 4), "avg_r_gross": round(float(gross.mean()), 4),
            "profit_factor": round(float(wins / losses), 3) if losses > 0 else None}


def _daily_sharpe(trades: list[dict], sessions: list[str]) -> tuple[float, int]:
    """Sharpe of the per-session mean net R (0 on sessions with no trade), annualised."""
    if not sessions:
        return 0.0, 0
    by_day: dict[str, list[float]] = {s: [] for s in sessions}
    for t in trades:
        if t["date"] in by_day:
            by_day[t["date"]].append(t["r_net"])
    series = np.array([np.mean(v) if v else 0.0 for v in by_day.values()])
    sd = series.std()
    return (float(series.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0), len(series)


def load_universe(symbols: list[str], refresh: bool = False) -> dict[str, list]:
    out = {}
    for s in symbols:
        if refresh:
            ibars.update(s)
        days = ibars.days_from_frame(ibars.load_store(s))
        if days:
            out[s] = days
    return out


def calibrate(symbols: list[str], refresh: bool = False, cfg: CostConfig = DEFAULT_COSTS,
              risk_inr: float = DEFAULT_RISK_INR, grid: dict | None = None) -> dict:
    grid = grid or GRID
    universe = load_universe(symbols, refresh)
    if not universe:
        raise RuntimeError("no stored 5-minute history: run with --refresh to fetch it first")
    dates = sorted({d.date.isoformat() for days in universe.values() for d in days})
    cut = dates[int(len(dates) * IN_SAMPLE_FRACTION)]
    is_sessions = [d for d in dates if d < cut]
    oos_sessions = [d for d in dates if d >= cut]

    keys = list(grid)
    configs = [dict(zip(keys, vals)) for vals in itertools.product(*grid.values())]
    results = {json.dumps(c, sort_keys=True): [] for c in configs}
    trigger_keys = sorted({(c["or_minutes"], c["side"], c["min_vol_x"]) for c in configs},
                          key=str)
    for sym, days in universe.items():
        for day in days:
            for om, side, mv in trigger_keys:
                trig = orb_trigger(day, side, om, min_vol_x=mv)
                if trig["status"] != "triggered":
                    continue
                for c in configs:
                    if (c["or_minutes"], c["side"], c["min_vol_x"]) != (om, side, mv):
                        continue
                    t = run_from_trigger(day, trig, side, c["stop"], c["target"], cfg=cfg,
                                         risk_inr=risk_inr)
                    if t is not None:
                        results[json.dumps(c, sort_keys=True)].append(
                            {**t.__dict__, "symbol": sym})

    table = []
    for key, trades in results.items():
        ins = [t for t in trades if t["date"] < cut]
        oos = [t for t in trades if t["date"] >= cut]
        sharpe_is, n_is = _daily_sharpe(ins, is_sessions)
        sharpe_oos, _ = _daily_sharpe(oos, oos_sessions)
        table.append({"config": json.loads(key), "in_sample": {**_metrics(ins), "sharpe": round(sharpe_is, 3)},
                      "out_of_sample": {**_metrics(oos), "sharpe": round(sharpe_oos, 3)}})
    ranked = sorted((r for r in table if r["in_sample"]["trades"] >= MIN_TRADES),
                    key=lambda r: r["in_sample"]["avg_r_net"], reverse=True)
    bar = selection_bar(len(configs), len(is_sessions), TRADING_DAYS)
    best = ranked[0] if ranked else None
    positive_is = sum(1 for r in ranked if r["in_sample"]["avg_r_net"] > 0)
    candidate = bool(
        best and best["out_of_sample"]["trades"] >= MIN_TRADES
        and (best["out_of_sample"]["avg_r_net"] or 0) > 0
        and best["in_sample"]["sharpe"] > bar)
    if not best:
        verdict = f"Fewer than {MIN_TRADES} trades for every variant: not enough history yet."
    elif candidate:
        verdict = ("Edge candidate: positive net of costs out-of-sample and above the selection bar. "
                   "Paper trade it before trusting it.")
    else:
        why = []
        if best["out_of_sample"]["trades"] < MIN_TRADES:
            why.append(f"only {best['out_of_sample']['trades']} out-of-sample trades")
        if (best["out_of_sample"]["avg_r_net"] or 0) <= 0:
            why.append(f"out-of-sample {best['out_of_sample']['avg_r_net']}R net")
        if best["in_sample"]["sharpe"] <= bar:
            why.append(f"in-sample Sharpe {best['in_sample']['sharpe']} is under the selection bar {bar:.2f}")
        verdict = "No edge after costs: " + "; ".join(why) + "."
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "symbols": len(universe), "sessions": len(dates), "split_date": cut,
        "in_sample_sessions": len(is_sessions), "out_of_sample_sessions": len(oos_sessions),
        "variants_tried": len(configs), "variants_positive_in_sample": positive_is,
        "selection_bar": round(bar, 3), "costs": cfg.as_dict(), "risk_inr": risk_inr,
        "best": best, "edge_candidate": candidate, "verdict": verdict,
        "top": ranked[:5], "worst": ranked[-1] if ranked else None,
        "_trades": results[json.dumps(best["config"], sort_keys=True)] if best else [],
    }


# --- feeding the paper ledger --------------------------------------------------------------------

ENGINE_ID = "orb_in"
_LEDGER = "recorded_oos.json"


def record_oos(result: dict, risk_inr: float = DEFAULT_RISK_INR) -> dict:
    """Push the best config's OUT-OF-SAMPLE trades into the ``orb_in`` survival engine, once each.

    Only trades from sessions the ranking never saw are recorded, and each (config, symbol,
    date) at most once, so rerunning cannot farm the scorecard.
    """
    from src.alphadesk.cio_schema import CioTradePlan
    from src.alphadesk.store import record_outcome, save_signal
    from src.alphadesk.survival import apply_outcome_to_engine, load_engine, scorecard_row

    best = result.get("best")
    if not best:
        return {"recorded": 0, "reason": "no best config"}
    path = ibars.intraday_dir() / _LEDGER
    seen = set(json.loads(path.read_text())) if path.exists() else set()
    cfg_id = hashlib.sha1(json.dumps(best["config"], sort_keys=True).encode()).hexdigest()[:10]
    n = 0
    for t in result["_trades"]:
        if t["date"] < result["split_date"]:
            continue
        key = f"{cfg_id}|{t['symbol']}|{t['date']}"
        if key in seen:
            continue
        side = t["side"]
        plan = CioTradePlan(
            action="BUY" if side == "long" else "SELL", confidence_score=50,
            entry_range=f"{t['entry']}", target_price=0.0, stop_loss=0.0,
            risk_reward_ratio=str(best["config"]["target"]), position_size_percent=1.0,
            holding_horizon="intraday", engine_id=ENGINE_ID, invalidation="n/a",
            executive_summary=f"intraday ORB {side} {t['symbol']} {t['date']} (oos backtest)",
            conviction_drivers=[json.dumps(best["config"], sort_keys=True)])
        sid = save_signal(t["symbol"], plan, extras={"source": "intraday_backtest_oos", "config": best["config"]})
        pnl = t["r_net"] * risk_inr
        record_outcome(sid, {"engine_id": ENGINE_ID, "r_multiple": t["r_net"], "pnl_inr": pnl,
                             "symbol": t["symbol"], "source": "intraday_backtest_oos",
                             "notes": f"{t['reason']} {t['date']}"})
        apply_outcome_to_engine(ENGINE_ID, r_multiple=t["r_net"], pnl_inr=pnl)
        seen.add(key)
        n += 1
    path.write_text(json.dumps(sorted(seen)))
    return {"recorded": n, "engine": scorecard_row(load_engine(ENGINE_ID))}
