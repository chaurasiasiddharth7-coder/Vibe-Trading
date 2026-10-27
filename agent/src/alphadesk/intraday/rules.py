"""Pure intraday rules: opening range, entry trigger, exit plan, bar-by-bar trade simulation.

Times are minutes since midnight, IST. NSE cash session 09:15-15:30, 75 five-minute bars. Only
completed bars count. If a bar touches both the stop and the target the stop is taken. A gap
through the stop fills at the bar's open (worse than -1R); a target fills at the target.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from src.alphadesk.intraday.costs import DEFAULT_COSTS, CostConfig, net_r

OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
BAR = 5
N_BARS = (CLOSE_MIN - OPEN_MIN) // BAR  # 75
USABLE_BARS = 72  # through the 15:10 bar (closes 15:15): Yahoo often ends NSE sessions at 15:15
TICK = 0.05
DEFAULT_FLAT_BY = "15:15"  # MIS auto square-off starts around here at most brokers
DEFAULT_NO_ENTRY_AFTER = "14:45"
MIN_RISK_PCT = 0.15  # a stop tighter than this is eaten by spread and costs
DEFAULT_RISK_INR = 1000.0


def hhmm(text: str) -> int:
    h, m = text.strip().split(":")
    return int(h) * 60 + int(m)


def clock(minutes: float) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


@dataclass
class Day:
    """One session's 5-minute bars as arrays. ``t`` is the bar start in minutes."""

    date: date
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    @property
    def full(self) -> bool:
        """Contiguous from 09:15 through the 15:15 close, enough to run a day-trade rule."""
        return len(self) >= USABLE_BARS and bool(np.all(self.t == OPEN_MIN + BAR * np.arange(len(self))))


def day_from_frame(day: date, frame: pd.DataFrame) -> Day | None:
    """A Day from a frame of one session (index = naive IST bar start), or None if it has a hole
    before its last bar (a halt or a thin stock): the contiguous prefix from 09:15 is required."""
    if frame.empty:
        return None
    frame = frame.sort_index()
    t = (frame.index.hour * 60 + frame.index.minute).to_numpy()
    # Only bars through the 15:10 bar (closes 15:15) matter to a day-trade rule; Yahoo often drops
    # the 15:15-15:25 bars, which must not make an otherwise complete session look broken.
    keep = t < OPEN_MIN + USABLE_BARS * BAR
    frame, t = frame[keep], t[keep]
    if frame.empty:
        return None
    expected = OPEN_MIN + BAR * np.arange(len(t))
    if not np.array_equal(t, expected):
        return None
    cols = {k: frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    return Day(day, t, cols["open"], cols["high"], cols["low"], cols["close"], cols["volume"])


# --- opening range and entry ---------------------------------------------------------------------


@dataclass
class OpeningRange:
    high: float
    low: float
    bars: int
    avg_volume: float


def opening_range(day: Day, or_minutes: int = 15) -> OpeningRange | None:
    n = or_minutes // BAR
    if len(day) < n:
        return None
    return OpeningRange(float(day.h[:n].max()), float(day.l[:n].min()), n, float(day.v[:n].mean()))


def orb_trigger(day: Day, side: str = "long", or_minutes: int = 15, level: float | None = None,
                min_vol_x: float | None = None, not_after: int = hhmm(DEFAULT_NO_ENTRY_AFTER),
                now_min: int | None = None) -> dict:
    """First completed bar after the range that CLOSES beyond the level (above for a long, below
    for a short; a wick or a close exactly on the level is not a trigger).

    Returns ``{status, ...}``: ``triggered`` (idx, time, price, level, vol_x), ``waiting``,
    ``expired`` or ``forming`` / ``no_range`` when there is no usable range yet.
    """
    if side not in ("long", "short"):
        raise ValueError("side must be long or short")
    rng = opening_range(day, or_minutes)
    n = or_minutes // BAR
    if rng is None or (now_min is not None and now_min < OPEN_MIN + or_minutes):
        return {"status": "forming" if now_min is not None else "no_range"}
    lvl = level if level is not None else (rng.high if side == "long" else rng.low)
    last_close = float(day.c[n - 1])
    for i in range(n, len(day)):
        close_t = int(day.t[i]) + BAR
        if now_min is not None and close_t > now_min:
            break
        if close_t > not_after:
            return {"status": "expired", "level": lvl, "range": asdict(rng)}
        last_close = float(day.c[i])
        beyond = day.c[i] > lvl if side == "long" else day.c[i] < lvl
        vol_x = float(day.v[i] / rng.avg_volume) if rng.avg_volume > 0 else 0.0
        if beyond and (min_vol_x is None or vol_x >= min_vol_x):
            return {"status": "triggered", "idx": i, "time": close_t, "price": float(day.c[i]),
                    "level": lvl, "vol_x": round(vol_x, 2), "range": asdict(rng)}
    status = "waiting" if now_min is not None or len(day) < USABLE_BARS else "expired"
    return {"status": status, "level": lvl, "last_close": last_close,
            "distance_pct": round((last_close / lvl - 1) * 100, 3), "range": asdict(rng)}


# --- exit plan -----------------------------------------------------------------------------------


def to_tick(price: float, mode: str = "nearest", tick: float = TICK) -> float:
    units = price / tick
    fn = {"down": np.floor, "up": np.ceil, "nearest": np.round}[mode]
    return round(float(fn(units + (1e-9 if mode == "down" else -1e-9 if mode == "up" else 0))) * tick, 2)


def atr_at(day: Day, idx: int, length: int = 14) -> float:
    """Mean true range of the session's bars up to ``idx`` (fewer than ``length`` bars: all)."""
    h, l, c = day.h[: idx + 1], day.l[: idx + 1], day.c[: idx + 1]
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return float(tr[-length:].mean())


@dataclass
class Plan:
    side: str
    entry: float
    stop: float
    target: float
    risk: float
    risk_pct: float
    reward_risk: float
    stop_rule: str
    target_rule: str
    flat_by: str

    def as_dict(self) -> dict:
        return asdict(self)


def make_plan(side: str, entry: float, or_high: float, or_low: float, stop: str = "or-mid",
              target: str = "2R", atr_value: float | None = None, flat_by: str = DEFAULT_FLAT_BY,
              tick: float = TICK, min_risk_pct: float = MIN_RISK_PCT) -> Plan:
    """Stop and target, set before the entry. Raises ValueError with the reason when refused.

    Stops: ``or-mid`` (range midpoint), ``or-edge`` (the range low for a long, high for a short),
    ``pct:X``, ``price:X``, ``atr:K``. The stop rounds AWAY from the entry to the tick, the
    target TOWARD it (never flattering). Targets: ``xR`` or ``price:X``.
    """
    sign = 1 if side == "long" else -1
    kind, _, arg = stop.partition(":")
    if kind == "or-mid":
        raw = (or_high + or_low) / 2
    elif kind == "or-edge":
        raw = or_low if side == "long" else or_high
    elif kind == "pct":
        raw = entry * (1 - sign * float(arg) / 100)
    elif kind == "price":
        raw = float(arg)
    elif kind == "atr":
        if atr_value is None:
            raise ValueError("an atr stop needs the session's bars")
        raw = entry - sign * float(arg) * atr_value
    else:
        raise ValueError(f"unknown stop rule {stop!r}")
    stop_p = to_tick(raw, "down" if side == "long" else "up", tick)
    risk = (entry - stop_p) * sign
    if risk <= 0:
        raise ValueError(f"stop {stop_p} is on the wrong side of the entry {entry}")
    if risk / entry * 100 < min_risk_pct:
        raise ValueError(f"risk {risk / entry * 100:.2f}% of the price is under {min_risk_pct}%: "
                         "too tight to survive the spread and costs")
    if target.upper().endswith("R"):
        tgt = entry + sign * float(target[:-1]) * risk
        tgt = to_tick(tgt, "down" if side == "long" else "up", tick)
    elif target.startswith("price:"):
        tgt = float(target[6:])
    else:
        raise ValueError(f"unknown target rule {target!r}")
    if (tgt - entry) * sign <= 0:
        raise ValueError(f"target {tgt} is on the wrong side of the entry {entry}")
    return Plan(side, entry, stop_p, tgt, round(risk, 2), round(risk / entry * 100, 3),
                round((tgt - entry) * sign / risk, 2), stop, target, flat_by)


# --- simulation ----------------------------------------------------------------------------------


@dataclass
class Trade:
    date: str
    side: str
    entry: float
    exit: float
    reason: str  # target | stop | stop_gap | flat
    r_gross: float
    r_net: float
    bars_held: int


@dataclass
class Trace:
    """A trade walked bar by bar: where it stands (or ended) and its R at every bar close."""

    status: str  # target | stop | stop_gap | flat | open
    exit_idx: int  # index of the bar it ended on, or the last bar seen when still open
    exit_price: float
    path: list[tuple[int, float]]  # (bar close minute, R before costs at that close)
    mfe: float  # best R reached (by bar high/low)
    mae: float  # worst R reached


def trace(day: Day, idx: int, plan: Plan, flat_by: int | None = None) -> Trace:
    """Walk the bars after the entry bar: the stop is checked before the target, a bar touching
    both counts as the stop, a gap through the stop fills at the open, and the trade is flat at
    ``flat_by``. A day with no bars past ``flat_by`` yet leaves the trade ``open`` (live)."""
    flat = hhmm(plan.flat_by) if flat_by is None else flat_by
    sign = 1 if plan.side == "long" else -1
    path: list[tuple[int, float]] = [(int(day.t[idx]) + BAR, 0.0)]
    mfe = mae = 0.0
    last, last_close = idx, plan.entry
    for i in range(idx + 1, len(day)):
        close_t = int(day.t[i]) + BAR
        if close_t > flat:
            return Trace("flat", last, last_close, path, mfe, mae)
        o, h, l, c = float(day.o[i]), float(day.h[i]), float(day.l[i]), float(day.c[i])
        best, worst = (h, l) if sign == 1 else (l, h)
        mfe = max(mfe, sign * (best - plan.entry) / plan.risk)
        mae = min(mae, sign * (worst - plan.entry) / plan.risk)
        stop_hit = l <= plan.stop if sign == 1 else h >= plan.stop
        gap = o <= plan.stop if sign == 1 else o >= plan.stop
        tgt_hit = h >= plan.target if sign == 1 else l <= plan.target
        if gap:
            path.append((close_t, sign * (o - plan.entry) / plan.risk))
            return Trace("stop_gap", i, o, path, mfe, mae)
        if stop_hit:
            path.append((close_t, -1.0))
            return Trace("stop", i, plan.stop, path, mfe, mae)
        if tgt_hit:
            path.append((close_t, sign * (plan.target - plan.entry) / plan.risk))
            return Trace("target", i, plan.target, path, mfe, mae)
        path.append((close_t, sign * (c - plan.entry) / plan.risk))
        last, last_close = i, c
    still_running = (int(day.t[-1]) + BAR) < flat
    return Trace("open" if still_running else "flat", last, last_close, path, mfe, mae)


def simulate(day: Day, idx: int, plan: Plan, flat_by: int | None = None,
             cfg: CostConfig = DEFAULT_COSTS, risk_inr: float = DEFAULT_RISK_INR) -> Trade:
    """A finished session: trace the trade and price it net of costs."""
    tr = trace(day, idx, plan, flat_by)
    sign = 1 if plan.side == "long" else -1
    reason = "flat" if tr.status == "open" else tr.status
    r_gross = sign * (tr.exit_price - plan.entry) / plan.risk
    r_net = float(net_r(plan.side, plan.entry, tr.exit_price, plan.risk, risk_inr, cfg))
    return Trade(day.date.isoformat(), plan.side, plan.entry, round(tr.exit_price, 2), reason,
                 round(r_gross, 4), round(r_net, 4), tr.exit_idx - idx)


def run_from_trigger(day: Day, trig: dict, side: str, stop: str = "or-mid", target: str = "2R",
                     flat_by: str = DEFAULT_FLAT_BY, cfg: CostConfig = DEFAULT_COSTS,
                     risk_inr: float = DEFAULT_RISK_INR) -> Trade | None:
    """Plan and simulate a trade from an already-found trigger. None when the plan is refused."""
    rng = trig["range"]
    atr_v = atr_at(day, trig["idx"]) if stop.startswith("atr") else None
    try:
        plan = make_plan(side, trig["price"], rng["high"], rng["low"], stop, target, atr_v, flat_by)
    except ValueError:
        return None
    return simulate(day, trig["idx"], plan, cfg=cfg, risk_inr=risk_inr)


def run_rule(day: Day, side: str = "long", or_minutes: int = 15, stop: str = "or-mid",
             target: str = "2R", min_vol_x: float | None = None, flat_by: str = DEFAULT_FLAT_BY,
             cfg: CostConfig = DEFAULT_COSTS, risk_inr: float = DEFAULT_RISK_INR) -> Trade | None:
    """The whole rule on one finished session: trigger, plan, simulate. None when it never fired
    or the plan was refused."""
    trig = orb_trigger(day, side, or_minutes, min_vol_x=min_vol_x)
    if trig["status"] != "triggered":
        return None
    return run_from_trigger(day, trig, side, stop, target, flat_by, cfg, risk_inr)
