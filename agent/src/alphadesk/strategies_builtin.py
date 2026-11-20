"""Past-price tests for the desk's built-in strategies, scored like Lab strategies.

The built-ins already have replay engines; this module runs them and turns their trades into the same
result the Strategies page shows for a Lab test (summary, robustness, backtest gate, equity, trades):

* ``signal_board``: ``board_replay.replay_symbol`` on daily bars. Kronos is not replayed (one model run per
  bar is too slow), so history uses the price votes only: trend, structure and momentum must all agree.
  Same stops, targets, 20-session limit and cost as the paper trades. The "random entries" check opens the
  same number of trades per stock on random days with the same exits.
* ``btst``: ``btst.days.replay`` (unchanged), which rebuilds each past session from the NSE store with the
  closing price standing in for 15:20 and settles the pick on the next session. Days it already replayed
  are reused. The "random" check compares the pick with an average stock from the same day's shortlist.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable

import numpy as np

from src.alphadesk import strategies as S

logger = logging.getLogger(__name__)
TESTABLE = ("signal_board", "btst")
NOTES = {
    "signal_board": ("Price votes only (Kronos isn't replayed): trend, structure and momentum must all agree. "
                     "Trades open at the signal day's close and exit at the stop (1.5x ATR), the target (3x ATR) "
                     "or after 20 sessions. If a day touched both, the stop is assumed first."),
    "btst": ("Each past day rebuilt from the NSE closing prices (they stand in for 15:20), the pick bought at the "
             "close and sold the next session with the live rules' take-profit and stop. If the next day touched "
             "both, the stop is assumed first."),
}


def scorecard(trades: list[dict[str, Any]], *, random_avg: float | None, random_label: str, years: int,
              cost_pct: float, symbols_tested: int, note: str) -> dict[str, Any]:
    """Lab-style result from trades (each: symbol, entry_date, exit_date, net_pct, gross_pct, reason)."""
    trades = sorted(trades, key=lambda t: (t["exit_date"], t["symbol"]))
    pct = [t["net_pct"] for t in trades]
    main = S.stats(pct, [t["exit_date"] for t in trades])
    out: dict[str, Any] = {"summary": main, "cost_pct": cost_pct, "symbols_tested": symbols_tested,
                           "symbols_with_trades": len({t["symbol"] for t in trades}), "years": years, "note": note}
    if not trades:
        out.update(gate={"passed": False, "checks": [{"label": f"At least {S.MIN_TRADES_FOR_GATE} trades", "ok": False, "value": 0}]},
                   trades=[], equity=[], by_symbol=[])
        return out
    half, cut = len(trades) // 2, int(len(trades) * 0.7)
    first, second = S.stats(pct[:half]), S.stats(pct[half:])
    double = S.stats([t["gross_pct"] - 2 * cost_pct for t in trades])
    holdout = S.stats(pct[cut:])
    out["robustness"] = {"first_half": first, "second_half": second, "double_costs": double, "holdout_last_30pct": holdout}
    checks = [
        {"label": f"At least {S.MIN_TRADES_FOR_GATE} trades", "ok": main["trades"] >= S.MIN_TRADES_FOR_GATE, "value": main["trades"]},
        {"label": "Profitable after costs", "ok": main["avg_pct"] > 0, "value": main["avg_pct"]},
        {"label": "Both halves of the dates profitable", "ok": first.get("avg_pct", 0) > 0 and second.get("avg_pct", 0) > 0,
         "value": [first.get("avg_pct"), second.get("avg_pct")]},
        {"label": "Still profitable at double costs", "ok": double.get("avg_pct", 0) > 0, "value": double.get("avg_pct")},
        {"label": "Last 30% of trades profitable", "ok": holdout.get("avg_pct", 0) > 0, "value": holdout.get("avg_pct")},
    ]
    if random_avg is not None:
        out["robustness"]["random_entries"] = {"trades": main["trades"], "avg_pct": round(random_avg, 3)}
        checks.append({"label": random_label, "ok": main["avg_pct"] > random_avg, "value": [main["avg_pct"], round(random_avg, 3)]})
    out["gate"] = {"passed": all(c["ok"] for c in checks), "checks": checks}
    cum = np.cumsum(pct)
    step = max(1, len(trades) // 300)
    out["equity"] = [{"date": trades[i]["exit_date"], "cum_pct": round(float(cum[i]), 2)} for i in range(0, len(trades), step)]
    out["equity"].append({"date": trades[-1]["exit_date"], "cum_pct": round(float(cum[-1]), 2)})
    by_sym: dict[str, list[float]] = {}
    for t in trades:
        by_sym.setdefault(t["symbol"], []).append(t["net_pct"])
    out["by_symbol"] = sorted(({"symbol": s, **S.stats(v)} for s, v in by_sym.items()), key=lambda r: r["total_pct"], reverse=True)[:40]
    out["trades"] = trades[-200:]
    return out


# --- signal board -------------------------------------------------------------------------------------


def _board_trade(t: dict[str, Any], cost_pct: float) -> dict[str, Any]:
    sign = 1 if t["side"] == "LONG" else -1
    gross = (t["exit_price"] - t["entry"]) * sign / t["entry"] * 100
    return {"symbol": t["symbol"], "side": t["side"], "entry_date": t["entry_date"], "exit_date": t["exit_date"],
            "gross_pct": round(gross, 3), "net_pct": round(gross - cost_pct, 3), "reason": t["exit_reason"]}


def run_signal_board(years: int, universe: str, progress: Callable[[int, int], None] | None = None,
                     frame: Callable[[str, str], Any] | None = None, seed: int = 7) -> dict[str, Any]:
    from src.alphadesk import board_replay as BR
    from src.alphadesk.board_paper import COST_PCT, simulate_exit
    from src.alphadesk.index_lists import scan_list

    syms = [m["symbol"] for m in scan_list(universe, include_funds=False)][: S.MAX_SYMBOLS]
    since = (date.today() - timedelta(days=int(365.25 * years))).isoformat()
    period = f"{min(10, years + 1)}y"  # one extra year for the 200-day warm-up
    load = frame or BR._frame
    rng = random.Random(seed)
    trades: list[dict[str, Any]] = []
    rand: list[float] = []
    tested = 0
    for n, sym in enumerate(syms):
        if progress:
            progress(n, len(syms))
        try:
            df = load(sym, period)
        except Exception as exc:  # noqa: BLE001
            logger.info("board test: no prices for %s: %s", sym, exc)
            continue
        if df is None or len(df) <= BR.WARMUP_BARS + 5:
            continue
        tested += 1
        got = [t for t in BR.replay_symbol(sym, df) if t["entry_date"] >= since and t.get("entry")]
        mine = [_board_trade(t, COST_PCT) for t in got]
        trades += mine
        # The same exits from random days: does the signal pick better days than chance?
        bars = [{"time": ts.strftime("%Y-%m-%d"), "open": o, "high": h, "low": lo, "close": c}
                for ts, o, h, lo, c in zip(df.index, df["open"], df["high"], df["low"], df["close"])]
        start = next((i for i, b in enumerate(bars) if b["time"] >= since), len(bars))
        pool = list(range(max(start, BR.WARMUP_BARS), len(bars) - 2))
        for t in got:
            if not pool:
                break
            i = rng.choice(pool)
            entry, sign = bars[i]["close"], 1 if t["side"] == "LONG" else -1
            risk = abs(t["entry"] - t["stop"]) / t["entry"] * entry
            ex = simulate_exit(bars[i + 1:], side=t["side"], entry=entry, stop=entry - sign * risk,
                               target=entry + sign * 2 * risk)
            if ex:
                rand.append((ex["exit_price"] - entry) * sign / entry * 100 - COST_PCT)
    if progress:
        progress(len(syms), len(syms))
    return scorecard(trades, random_avg=float(np.mean(rand)) if rand else None,
                     random_label="Beats the same exits on random entry days", years=years, cost_pct=COST_PCT,
                     symbols_tested=tested, note=NOTES["signal_board"]) | {"universe": universe}


# --- BTST ---------------------------------------------------------------------------------------------


def run_btst(years: int, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    from src.alphadesk.btst import days
    from src.alphadesk.btst import rules as R

    last = days.store_last_date()
    if last is None:
        raise S.StrategyError("No NSE price store on this server yet, so there's no history to replay.")
    end = last - timedelta(days=1)
    start = end - timedelta(days=int(365.25 * years))
    have = {r["date"] for r in days.list_days("replay")}
    sessions_needed = [d for d in (start + timedelta(days=i) for i in range((end - start).days + 1)) if d.weekday() < 5]
    missing = [d for d in sessions_needed if d.isoformat() not in have]
    if len(missing) > 10:  # holidays explain a few; more means this range hasn't been replayed yet
        try:
            with days.guard("replay"):
                days.replay(min(missing), end, progress=progress)
        except RuntimeError as exc:
            raise S.StrategyError(f"The overnight replay is already running ({exc}). Try again in a few minutes.") from exc
    recs = [r for r in days.list_days("replay") if start.isoformat() <= r["date"] <= end.isoformat()]
    trades: list[dict[str, Any]] = []
    rand: list[float] = []
    for r in recs:
        p = r.get("pick") or {}
        o = p.get("outcome") or {}
        if r.get("status") != "settled" or not o or o.get("data_check") or o.get("net_pct") is None:
            continue
        trades.append({"symbol": p.get("symbol"), "side": "LONG", "entry_date": r["date"],
                       "exit_date": r.get("settled_on") or r["date"], "gross_pct": o.get("gross_pct", o["net_pct"]),
                       "net_pct": o["net_pct"], "reason": o.get("reason") or ""})
        shortlist = [c["outcome"]["net_pct"] for c in r.get("candidates") or []
                     if (c.get("outcome") or {}).get("net_pct") is not None and not c["outcome"].get("data_check")]
        if shortlist:
            rand.append(float(np.mean(shortlist)))
    cost = round(R.DEFAULT_RULES.cost_pct * 100, 3)
    res = scorecard(trades, random_avg=float(np.mean(rand)) if rand else None,
                    random_label="Beats an average stock from the same day's shortlist", years=years, cost_pct=cost,
                    symbols_tested=len({t["symbol"] for t in trades}), note=NOTES["btst"])
    res["days"] = len(recs)
    res["skipped_days"] = sum(1 for r in recs if not r.get("pick"))
    return res


# --- jobs ---------------------------------------------------------------------------------------------


def start(sid: str, years: int = 3, universe: str = "nifty100") -> str:
    """Run a built-in's past test in the background; poll it with ``strategies.job``."""
    from src.alphadesk import profiles
    from src.alphadesk.index_lists import UNIVERSES

    if sid not in TESTABLE:
        raise S.StrategyError("This strategy can't be tested on past prices.")
    years = int(min(10 if sid == "btst" else 5, max(1, years)))
    if universe not in UNIVERSES:
        universe = "nifty100"
    job_id = uuid.uuid4().hex[:12]
    pid = profiles.current_id()
    with S._JOBS_LOCK:
        if any(j["status"] == "running" and j.get("strategy_id") == sid and j["profile"] == pid for j in S._JOBS.values()):
            raise S.StrategyError("This test is already running.")
        S._JOBS[job_id] = {"id": job_id, "strategy_id": sid, "status": "running", "profile": pid, "started": time.time(),
                           "result": None, "error": None, "done": 0, "total": 0}

    def progress(i: int, n: int) -> None:
        with S._JOBS_LOCK:
            S._JOBS[job_id].update(done=i, total=n)

    def work() -> None:
        try:
            res = run_btst(years, progress) if sid == "btst" else run_signal_board(years, universe, progress)
            res["ran_at"] = datetime.now().isoformat(timespec="seconds")
            S.record_test(sid, res, years)
            with S._JOBS_LOCK:
                S._JOBS[job_id].update(status="done", result=res)
        except Exception as exc:  # noqa: BLE001 - shown to the person
            logger.warning("built-in test %s failed: %s", sid, exc)
            with S._JOBS_LOCK:
                S._JOBS[job_id].update(status="failed", error=str(exc)[:300])

    threading.Thread(target=profiles.carry(work), daemon=True, name=f"builtin-test-{sid}").start()
    return job_id
