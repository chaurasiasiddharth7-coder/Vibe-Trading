"""Replay a trade plan 10,000 ways from the stock's own recent 5-minute bars.

Each remaining bar of the session is drawn whole from a past bar at the same time of day
(plus or minus ``window`` bars), as ratios to the bar before it, and scaled to the current
price. The stop is checked before the target; a gap through the stop fills at the open. The
same rule is also re-run on every real past session. A simulation built from recent bars, not
a prediction: results are net of NSE intraday costs and always carry the seed.
"""

from __future__ import annotations

import numpy as np

from src.alphadesk.intraday.costs import DEFAULT_COSTS, CostConfig, net_r
from src.alphadesk.intraday.rules import (
    BAR, DEFAULT_RISK_INR, OPEN_MIN, USABLE_BARS, Day, Plan, hhmm, run_rule,
)

DEFAULT_SEED = 19
MIN_SESSIONS = 20


def build_pool(days: list[Day]) -> np.ndarray:
    """(sessions, USABLE_BARS, 4) ratios open/high/low/close over the previous bar's close.
    The first bar of a session is measured against the previous session's last close."""
    rows = []
    prev_close = None
    for d in days:
        if len(d) < USABLE_BARS:
            continue
        c = d.c[:USABLE_BARS]
        prev = np.concatenate(([prev_close if prev_close is not None else d.o[0]], c[:-1]))
        rows.append(np.stack([d.o[:USABLE_BARS] / prev, d.h[:USABLE_BARS] / prev,
                              d.l[:USABLE_BARS] / prev, c / prev], axis=1))
        prev_close = float(d.c[USABLE_BARS - 1])
    return np.array(rows)


def replay(plan: Plan, entry_bar: int, pool: np.ndarray, n: int = 10_000, seed: int = DEFAULT_SEED,
           window: int = 2, flat_bar: int | None = None, cfg: CostConfig = DEFAULT_COSTS,
           risk_inr: float = DEFAULT_RISK_INR) -> dict:
    """Monte Carlo of the rest of the session after the entry bar (index into the day)."""
    if len(pool) < MIN_SESSIONS:
        raise ValueError(f"only {len(pool)} usable sessions; need at least {MIN_SESSIONS}")
    last = (hhmm(plan.flat_by) - OPEN_MIN) // BAR - 1 if flat_bar is None else flat_bar
    last = min(last, USABLE_BARS - 1)
    if entry_bar >= last:
        raise ValueError("the entry is at or after the flat-by time")
    rng = np.random.default_rng(seed)
    sign = 1.0 if plan.side == "long" else -1.0
    price = np.full(n, plan.entry)
    alive = np.ones(n, dtype=bool)
    exit_p = np.full(n, plan.entry)
    reason = np.zeros(n, dtype=np.int8)  # 0 open, 1 target, 2 stop
    for k in range(entry_bar + 1, last + 1):
        lo, hi = max(0, k - window), min(USABLE_BARS - 1, k + window)
        ks = rng.integers(lo, hi + 1, size=n)
        ss = rng.integers(0, len(pool), size=n)
        r = pool[ss, ks]
        o, h, l, c = (price * r[:, i] for i in range(4))
        gap = (o <= plan.stop) if sign == 1 else (o >= plan.stop)
        stop_hit = (l <= plan.stop) if sign == 1 else (h >= plan.stop)
        tgt_hit = (h >= plan.target) if sign == 1 else (l <= plan.target)
        hit_stop = alive & (gap | stop_hit)
        hit_tgt = alive & ~hit_stop & tgt_hit
        exit_p = np.where(hit_stop, np.where(gap, o, plan.stop), exit_p)
        exit_p = np.where(hit_tgt, plan.target, exit_p)
        reason[hit_stop] = 2
        reason[hit_tgt] = 1
        alive &= ~(hit_stop | hit_tgt)
        price = np.where(alive, c, price)
    exit_p = np.where(alive, price, exit_p)
    r_gross = sign * (exit_p - plan.entry) / plan.risk
    r_net = net_r(plan.side, plan.entry, exit_p, plan.risk, risk_inr, cfg)
    green = (reason == 1) | ((reason == 0) & (sign * (price - plan.entry) > 0))
    final = price[alive] if alive.any() else np.array([plan.entry])
    return {
        "replays": n, "seed": seed, "sessions_in_pool": int(len(pool)),
        "target_first": float((reason == 1).mean()), "stop_first": float((reason == 2).mean()),
        "neither": float((reason == 0).mean()), "green": float(green.mean()),
        "avg_r_gross": float(r_gross.mean()), "avg_r_net": float(np.mean(r_net)),
        "price_at_flat": {"median": float(np.median(final)), "p5": float(np.percentile(final, 5)),
                          "p95": float(np.percentile(final, 95))},
    }


def history(days: list[Day], plan: Plan, or_minutes: int, min_vol_x: float | None,
            cfg: CostConfig = DEFAULT_COSTS, risk_inr: float = DEFAULT_RISK_INR) -> dict:
    """The plan's own rule (same range, stop rule, target rule) on every real past session."""
    trades = [t for d in days if (t := run_rule(d, plan.side, or_minutes, plan.stop_rule,
                                                 plan.target_rule, min_vol_x, plan.flat_by, cfg,
                                                 risk_inr))]
    n = len(trades)
    count = lambda *rs: sum(t.reason in rs for t in trades)  # noqa: E731
    return {"sessions": len(days), "fired": n, "target": count("target"),
            "stop": count("stop", "stop_gap"), "flat": count("flat"),
            "avg_r_gross": round(sum(t.r_gross for t in trades) / n, 4) if n else None,
            "avg_r_net": round(sum(t.r_net for t in trades) / n, 4) if n else None}


def verdict(mc: dict, reward_risk: float) -> str:
    """One honest line. A bigger target needs to win less often to break even: 1/(1+R)."""
    hit = mc["target_first"] + mc["stop_first"]
    if hit <= 0:
        return "Neither side was reached in the replays; the price at the close decides."
    share = mc["target_first"] / hit
    need = 1 / (1 + reward_risk)
    gap = (share - need) * 100
    detail = (f"target first {mc['target_first'] * 100:.1f}% vs stop first "
              f"{mc['stop_first'] * 100:.1f}% (needs {need * 100:.0f}% of the decided replays "
              f"to break even before costs, got {share * 100:.0f}%)")
    if abs(gap) < 5:
        label = "Close to a coin flip" if abs(reward_risk - 1) < 1e-9 else "Close to break-even"
    elif abs(gap) < 15:
        label = "Leans toward the target" if gap > 0 else "Leans toward the stop"
    else:
        label = "Strongly toward the target" if gap > 0 else "Strongly toward the stop"
    line = f"{label}: {detail}."
    if mc["avg_r_net"] <= 0 < mc["avg_r_gross"]:
        line += f" Costs turn the average from {mc['avg_r_gross']:+.3f}R to {mc['avg_r_net']:+.3f}R."
    return line
