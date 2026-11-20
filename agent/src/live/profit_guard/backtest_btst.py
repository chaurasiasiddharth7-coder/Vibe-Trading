"""P7: which exit plan should the overnight (BTST) strategy use? Compared with the live exit code.

Every replayed overnight pick (``~/.vibe-trading/alphadesk/btst/replay``) is bought at the day's close (standing
in for 15:20) and managed through the next session by the SAME ``rules.update_position`` / ``update_portfolio``
that run live, under each exit variant below.

The store only has daily bars, so the next day's path inside the bar is unknown. Each trade is run twice:
* ``worst``: open -> the adverse extreme -> the favourable extreme -> close (a stop is hit before a target);
* ``best``:  open -> the favourable extreme -> the adverse extreme -> close.
The truth lies between. A variant is only judged on the ``worst`` path; ``best`` shows how much is unknown.

Variants (stop -2% everywhere unless noted; costs 0.25% round trip; stop/lock exits pay 0.1% slippage):
  A  current rules: +1% target / -2% stop (the GTT OCO), else sell at the close
  B  hold to the close, no target, no stop
  C  sell at the open
  D  lock_fixed: no target; once up +0.5% the floor is +0.25%
  E  tiered (the live default): no target; arms at +0.5%, keeps 50/65/75% of the peak
  F  lock_and_trail: no target; floor +0.25% at +0.5%, +0.15% per extra +0.25%
  G  +2% target / -2% stop

Robustness: both halves of the dates, 2x costs, +-20% on the lock settings, the same exits on a random candidate
from the same day's list (does picking matter, or only the exits?), and a 70/30 walk-forward: choose on the first
70% of dates, then run the last 30% once.

    python -m src.live.profit_guard.backtest_btst --replay ~/.vibe-trading/alphadesk/btst/replay \
        --bars ~/.vibe-trading/nse/bars.parquet --out ../docs/profit_guard_p7
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from src.live.profit_guard import rules
from src.live.profit_guard.config import GuardConfig, load_config
from src.live.profit_guard.models import PortfolioState, PositionPlan, Side, Tick, dec

IST = timezone(timedelta(hours=5, minutes=30))
COST_PCT = 0.25
SLIP_PCT = 0.10
STOP_PCT = 2.0


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    target_pct: float | None = None
    stop_pct: float | None = STOP_PCT
    mode: str = "off"  # layer2 mode
    l2: dict[str, Any] | None = None
    at_open: bool = False


VARIANTS = [
    Variant("A", "Current: +1% target / -2% stop", target_pct=1.0),
    Variant("B", "Hold to close, no stop", stop_pct=None),
    Variant("C", "Sell at the open", stop_pct=None, at_open=True),
    Variant("D", "Lock fixed (+0.25% once +0.5%)", mode="lock_fixed"),
    Variant("E", "Tiered keep of peak (live default)", mode="tiered"),
    Variant("F", "Lock and trail", mode="lock_and_trail"),
    Variant("G", "+2% target / -2% stop", target_pct=2.0),
]


@dataclass
class Trade:
    day: date
    next_day: date
    symbol: str
    entry: float
    o: float
    h: float
    l: float
    c: float


# --- data ------------------------------------------------------------------------------------------------


def load_picks(replay_dir: Path, *, random_seed: int | None = None) -> list[dict[str, Any]]:
    """[{day, next_day, symbol, entry}] from replay records. With ``random_seed``, a random candidate instead."""
    rng = random.Random(random_seed) if random_seed is not None else None
    out = []
    for f in sorted(replay_dir.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pick, nxt = rec.get("pick"), rec.get("settled_on")
        if not pick or not nxt or (pick.get("outcome") or {}).get("data_check"):
            continue  # data_check = a split/bonus or bad print the settler already flagged
        if rng is not None:
            cands = [c for c in rec.get("candidates") or [] if c.get("last")]
            if not cands:
                continue
            c = rng.choice(cands)
            out.append({"day": rec["date"], "next_day": nxt, "symbol": c["symbol"], "entry": float(c["last"])})
        else:
            out.append({"day": rec["date"], "next_day": nxt, "symbol": pick["symbol"], "entry": float(pick["entry"])})
    return out


def attach_next_day(picks: list[dict[str, Any]], bars_path: Path) -> list[Trade]:
    import pandas as pd

    if not picks:
        return []
    days = sorted({p["next_day"] for p in picks})
    lo, hi = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    import pyarrow as pa
    import pyarrow.parquet as pq

    typ = pq.read_schema(bars_path).field("date").type
    b = (lambda d: d) if pa.types.is_date(typ) else pd.Timestamp
    df = pd.read_parquet(bars_path, columns=["symbol", "date", "open", "high", "low", "close"],
                         filters=[("date", ">=", b(lo)), ("date", "<=", b(hi))])
    df["d"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    idx = {(r.symbol, r.d): r for r in df.itertuples(index=False)}
    out = []
    for p in picks:
        r = idx.get((p["symbol"], p["next_day"]))
        if r is None or not (r.open > 0 and r.high > 0 and r.low > 0 and r.close > 0):
            continue
        if not (0.6 <= r.open / p["entry"] <= 1.5):
            continue  # a 40%+ overnight jump is an unadjusted split or bonus, not a trade (F&O names have no band)
        out.append(Trade(date.fromisoformat(p["day"]), date.fromisoformat(p["next_day"]), p["symbol"], p["entry"],
                         float(r.open), float(r.high), float(r.low), float(r.close)))
    return out


# --- simulation through the live rules -------------------------------------------------------------------


def _ms(d: date, hh: int, mm: int) -> int:
    return int(datetime.combine(d, time(hh, mm), IST).timestamp() * 1000)


def _cfg(base: GuardConfig, v: Variant, notional: float, wiggle: float = 1.0) -> GuardConfig:
    raw = copy.deepcopy(base.raw)
    raw = _merge(raw, raw.get("strategies", {}).get("btst", {}))
    raw["capital_inr"] = notional  # one position a day: % of capital = % of the trade
    raw["layer2"]["mode"] = v.mode
    raw["layer2"]["loss_cap_pct"] = 100.0  # the -2% stop is the loss limit here
    raw["layer2"]["arm_pct"] = 0.5 * wiggle
    raw["layer2"]["lock_fixed"] = {"at_pct": 0.5 * wiggle, "lock_pct": 0.25 * wiggle}
    raw["layer2"]["lock_and_trail"] = {"at_pct": 0.5 * wiggle, "lock_pct": 0.25 * wiggle,
                                       "every_pct": 0.25 * wiggle, "raise_pct": 0.15 * wiggle}
    raw["layer2"]["tiers"] = [[0.0, 1.0 * wiggle, 50], [1.0 * wiggle, 2.0 * wiggle, 65], [2.0 * wiggle, None, 75]]
    raw["layer1"]["breakeven_at_r"] = 999  # BTST holds one session: no breakeven / trail inside a daily bar
    raw["layer1"]["trail"] = "none"
    raw["layer3"]["weekly_expiry_weekday"] = None
    return GuardConfig(raw)


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def simulate(t: Trade, v: Variant, base: GuardConfig, path: str = "worst", wiggle: float = 1.0,
             cost_pct: float = COST_PCT) -> dict[str, Any]:
    """One trade under one variant. Returns net %, the exit reason and the day's best price (for capture)."""
    qty = 100
    notional = t.entry * qty
    cfg = _cfg(base, v, notional, wiggle)
    entry = dec(t.entry)
    stop = entry * (1 - dec(v.stop_pct) / 100) if v.stop_pct is not None else entry * Decimal("0.0001")
    target = entry * (1 + dec(v.target_pct) / 100) if v.target_pct is not None else None
    plan = PositionPlan(id="p", strategy="btst", symbol=t.symbol, side=Side.LONG, qty=qty, entry=entry,
                        initial_stop=stop, stop=stop, target=target, one_r=max(entry - stop, Decimal("0.01")),
                        opened_ms=_ms(t.day, 15, 20), carry=True)
    if v.at_open:
        return _result(t, t.o, "OPEN", cost_pct, slip=False)
    # Four prices a day: the open, then both extremes in the order the path says, then the close (15:10).
    # A fall through the stop is filled at the stop (the price passes it on the way), plus slippage.
    floor_stop = float(stop) if v.stop_pct is not None else 0.0
    down = (max(t.l, floor_stop), t.o, t.l)
    up = (t.h, t.h, t.o)
    seq = [up, down] if path == "best" else [down, up]
    steps = [(9, 15, (t.o, t.o, t.o)), (11, 0, seq[0]), (13, 0, seq[1]), (15, 10, (t.c, t.h, t.l))]
    state = PortfolioState(trade_date=t.next_day.isoformat(), scope="btst")
    for hh, mm, (ltp, hi, lo) in steps:
        now = _ms(t.next_day, hh, mm)
        # A gap above the target on the first tick sells at the open (a triggered sell fills there, not lower).
        if hh == 9 and target is not None and dec(ltp) >= target:
            return _result(t, float(ltp), "TARGET", cost_pct, slip=False)
        # The price reaches an armed floor before it can reach the stop below it: check the lock first.
        if state.floor is not None:
            floor_px = entry + state.floor / qty
            if dec(min(lo, ltp)) <= floor_px:
                px = float(floor_px) if dec(ltp) <= floor_px <= dec(max(hi, ltp)) or hh != 9 else float(ltp)
                return _result(t, px, "PROFIT_LOCK", cost_pct, slip=True)
        tick = Tick(symbol=t.symbol, ltp=dec(ltp), ts_ms=now, high=dec(max(hi, ltp)), low=dec(min(lo, ltp)))
        plan, _acts = rules.update_position(plan, tick, cfg, now)
        if plan.state.value == "EXITED":
            reason = plan.exit_reason.value if plan.exit_reason else "EXIT"
            return _result(t, float(plan.exit_price), reason, cost_pct, slip=reason in {"STOP", "GAP"})
        prev_floor = state.floor
        state, acts = rules.update_portfolio(state, (dec(ltp) - entry) * qty, cfg)
        if state.lock_hit:
            # the price fell through the floor between the last step and this one: sell at the floor
            floor = state.floor if state.floor is not None else prev_floor
            px = float(entry + floor / qty) if floor is not None else ltp
            return _result(t, px if px >= ltp else ltp, "PROFIT_LOCK", cost_pct, slip=True)
    return _result(t, t.c, "CLOSE", cost_pct, slip=False)


def _result(t: Trade, px: float, reason: str, cost_pct: float, slip: bool) -> dict[str, Any]:
    gross = (px - t.entry) / t.entry * 100
    net = gross - cost_pct - (SLIP_PCT if slip else 0.0)
    best = (t.h - t.entry) / t.entry * 100
    return {"net": net, "gross": gross, "reason": reason, "best": best, "day": t.day.isoformat()}


# --- scoring ---------------------------------------------------------------------------------------------


def score(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"n": 0}
    nets = [r["net"] for r in results]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    up = [r for r in results if r["best"] > 0.25]
    capture = statistics.mean(max(-1.0, min(1.0, r["gross"] / r["best"])) for r in up) if up else None
    reasons: dict[str, int] = {}
    for r in results:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {"n": len(nets), "total_pct": round(sum(nets), 2), "avg_pct": round(statistics.mean(nets), 3),
            "win_rate": round(sum(1 for x in nets if x > 0) / len(nets) * 100, 1), "worst_pct": round(min(nets), 2),
            "best_pct": round(max(nets), 2), "max_dd_pct": round(dd, 2),
            "capture": round(capture * 100, 1) if capture is not None else None, "exits": reasons}


def run_all(trades: list[Trade], base: GuardConfig, variants: Iterable[Variant] = VARIANTS, **kw: Any) -> dict[str, Any]:
    out = {}
    for v in variants:
        res = [simulate(t, v, base, **kw) for t in trades]
        out[v.key] = {"label": v.label, **score(res)}
    return out


def report(replay_dir: Path, bars: Path, cfg_path: str | None = None, seeds: int = 5) -> dict[str, Any]:
    base = load_config(cfg_path)
    trades = attach_next_day(load_picks(replay_dir), bars)
    trades.sort(key=lambda t: t.day)
    n = len(trades)
    half = n // 2
    cut = int(n * 0.7)
    rep: dict[str, Any] = {
        "generated": datetime.now(IST).isoformat(timespec="seconds"),
        "trades": n, "from": trades[0].day.isoformat() if trades else None, "to": trades[-1].day.isoformat() if trades else None,
        "worst_path": run_all(trades, base, path="worst"),
        "best_path": run_all(trades, base, path="best"),
        "first_half": run_all(trades[:half], base, path="worst"),
        "second_half": run_all(trades[half:], base, path="worst"),
        "double_costs": run_all(trades, base, path="worst", cost_pct=COST_PCT * 2),
        "wiggle_minus20": run_all(trades, base, [v for v in VARIANTS if v.mode != "off"], path="worst", wiggle=0.8),
        "wiggle_plus20": run_all(trades, base, [v for v in VARIANTS if v.mode != "off"], path="worst", wiggle=1.2),
    }
    rand = []
    for s in range(seeds):
        rt = attach_next_day(load_picks(replay_dir, random_seed=s), bars)
        rand.append(run_all(rt, base, path="worst"))
    rep["random_entries"] = {k: {"label": rand[0][k]["label"],
                                 "avg_pct": round(statistics.mean(r[k]["avg_pct"] for r in rand), 3),
                                 "total_pct": round(statistics.mean(r[k]["total_pct"] for r in rand), 2)}
                             for k in rand[0]} if rand else {}
    train = run_all(trades[:cut], base, path="worst")
    chosen = max(train, key=lambda k: train[k]["avg_pct"])
    hold = run_all(trades[cut:], base, [v for v in VARIANTS if v.key == chosen], path="worst")[chosen]
    rep["walk_forward"] = {"train_dates": [trades[0].day.isoformat(), trades[cut - 1].day.isoformat()],
                           "holdout_dates": [trades[cut].day.isoformat(), trades[-1].day.isoformat()],
                           "chosen_on_train": chosen, "train": train[chosen], "holdout": hold,
                           "holdout_baseline_A": run_all(trades[cut:], base, [VARIANTS[0]], path="worst")["A"]}
    rep["gate"] = gate(rep)
    return rep


def gate(rep: dict[str, Any]) -> dict[str, Any]:
    wf = rep["walk_forward"]
    k = wf["chosen_on_train"]
    w = rep["worst_path"]
    ra = rep["random_entries"].get(k, {})
    checks = {
        "holdout_profitable_after_costs": wf["holdout"]["avg_pct"] > 0,
        "worst_trade_better_than_baseline": w[k]["worst_pct"] >= w["A"]["worst_pct"],
        "capture_above_50pct": (w[k]["capture"] or 0) > 50,
        "both_halves_profitable": rep["first_half"][k]["avg_pct"] > 0 and rep["second_half"][k]["avg_pct"] > 0,
        "survives_double_costs": rep["double_costs"][k]["avg_pct"] > 0,
        "beats_random_entries": bool(ra) and w[k]["avg_pct"] > ra["avg_pct"],
    }
    return {"variant": k, "checks": checks, "pass": all(checks.values())}


def to_markdown(rep: dict[str, Any]) -> str:
    lines = [f"# P7 overnight exit backtest ({rep['from']} to {rep['to']}, {rep['trades']} trades)", "",
             "Same rules.py as live. Worst-case path inside each daily bar unless noted. Net of 0.25% costs.", "",
             "| Var | Plan | Avg % | Total % | Win % | Worst % | Max DD % | Capture % | Best-path avg % | Random-entry avg % |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in rep["worst_path"].items():
        b = rep["best_path"][k]
        ra = rep["random_entries"].get(k, {})
        lines.append(f"| {k} | {r['label']} | {r['avg_pct']} | {r['total_pct']} | {r['win_rate']} | {r['worst_pct']} | "
                     f"{r['max_dd_pct']} | {r['capture']} | {b['avg_pct']} | {ra.get('avg_pct')} |")
    wf, g = rep["walk_forward"], rep["gate"]
    lines += ["", f"Walk-forward: chose **{wf['chosen_on_train']}** on {wf['train_dates'][0]}..{wf['train_dates'][1]} "
                  f"(avg {wf['train']['avg_pct']}%), holdout {wf['holdout_dates'][0]}..{wf['holdout_dates'][1]}: "
                  f"avg {wf['holdout']['avg_pct']}% over {wf['holdout']['n']} trades (baseline A {wf['holdout_baseline_A']['avg_pct']}%).",
              "", f"Gate for {g['variant']}: **{'PASS' if g['pass'] else 'FAIL'}**", ""]
    lines += [f"- [{'x' if ok else ' '}] {name.replace('_', ' ')}" for name, ok in g["checks"].items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.live.profit_guard.backtest_btst")
    p.add_argument("--replay", type=Path, default=Path.home() / ".vibe-trading" / "alphadesk" / "btst" / "replay")
    p.add_argument("--bars", type=Path, default=Path.home() / ".vibe-trading" / "nse" / "bars.parquet")
    p.add_argument("--out", type=Path, default=Path("profit_guard_p7"))
    p.add_argument("--seeds", type=int, default=5)
    a = p.parse_args(argv)
    rep = report(a.replay.expanduser(), a.bars.expanduser(), seeds=a.seeds)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "p7_results.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    (a.out / "p7_results.md").write_text(to_markdown(rep), encoding="utf-8")
    print(to_markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
