"""Profit Guard exit rules. PURE: no I/O, no clock, no randomness.

The same functions run in the backtest, in paper trading and (later) live, so a
result seen in the backtest is the behaviour you get in the market.

Invariants enforced in code (not only in tests):
  * a position's stop only ever moves in its favour;
  * a portfolio's profit floor only ever rises;
  * loss-cap and profit-lock exits are sticky for the rest of the day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Iterable

from src.live.profit_guard import session
from src.live.profit_guard.config import GuardConfig
from src.live.profit_guard.models import (
    ZERO,
    Action,
    Alert,
    ExitAll,
    ExitPosition,
    ExitReason,
    MoveStop,
    PortfolioState,
    PositionPlan,
    Side,
    State,
    Tick,
    dec,
)

HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Signals:
    opposite_signal: bool = False
    event_in_minutes: int | None = None  # minutes until a scheduled event (RBI, results); None = none


# --------------------------------------------------------------------------- helpers
def round_to_tick(price: Decimal, tick: Decimal, *, up: bool) -> Decimal:
    steps = (price / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return steps * tick


def _stop_round(side: Side, price: Decimal, tick: Decimal) -> Decimal:
    # long stops round down, short stops round up (never tighter than computed)
    return round_to_tick(price, tick, up=side is Side.SHORT)


def _better(side: Side, a: Decimal, b: Decimal) -> Decimal:
    """The more protective of two stops for this side."""
    return max(a, b) if side is Side.LONG else min(a, b)


def _is_tighter(side: Side, new: Decimal, old: Decimal) -> bool:
    return new > old if side is Side.LONG else new < old


def initial_levels(
    side: Side,
    entry: Decimal,
    atr: Decimal,
    cfg: GuardConfig,
    swing_level: Decimal | None = None,
) -> tuple[Decimal, Decimal | None, Decimal | None, Decimal]:
    """(stop, target1, target, one_r) for a new position.

    Stop = the tighter of the ATR stop and the swing level, but never closer than
    ``min_stop_atr`` ATRs to the entry (inside that is noise).
    """
    entry, atr = dec(entry), dec(atr)
    if atr <= 0:
        raise ValueError("atr must be > 0")
    l1 = cfg.l1
    s = side.sign
    tick = dec(cfg.ex["tick_size"])
    stop = entry - s * dec(l1["stop_atr_k"]) * atr
    if swing_level is not None:
        swing = dec(swing_level)
        if (swing - entry) * s < 0:  # swing is on the losing side of entry
            stop = _better(side, stop, swing)
    min_dist = dec(l1["min_stop_atr"]) * atr
    if abs(entry - stop) < min_dist:
        stop = entry - s * min_dist
    stop = _stop_round(side, stop, tick)
    one_r = abs(entry - stop)
    target = None
    if l1.get("target_r") is not None:
        target = round_to_tick(entry + s * dec(l1["target_r"]) * one_r, tick, up=side is Side.LONG)
    target1 = None
    if dec(l1["scale_out_frac"]) > 0:
        target1 = entry + s * dec(l1["scale_out_at_r"]) * one_r
    return stop, target1, target, one_r


# --------------------------------------------------------------------------- layer 1 + 3
def _terminal(plan: PositionPlan, reason: ExitReason, price: Decimal, now_ms: int) -> tuple[PositionPlan, list[Action]]:
    qty = plan.qty_open or 0
    realized = plan.realized + (price - plan.entry) * plan.side.sign * qty
    done = plan.with_(
        state=State.EXITED,
        exit_reason=reason,
        exit_price=price,
        exit_ms=now_ms,
        realized=realized,
        qty_open=0,
    )
    return done, [ExitPosition(plan.id, qty, reason, price)]


def update_position(
    plan: PositionPlan,
    tick: Tick,
    cfg: GuardConfig,
    now_ms: int,
    signals: Signals = Signals(),
    holidays: Iterable[date] = (),
) -> tuple[PositionPlan, list[Action]]:
    """Advance one position by one tick. Returns (new plan, actions)."""
    if plan.state is State.EXITED or (plan.qty_open or 0) <= 0:
        return plan, []
    if tick.symbol != plan.symbol:
        raise ValueError(f"tick for {tick.symbol} given to plan {plan.symbol}")

    l1, l3 = cfg.l1, cfg.l3
    side, s = plan.side, plan.side.sign
    tickp = dec(cfg.ex["tick_size"])
    phase = session.session_phase(
        now_ms,
        holidays=holidays,
        no_new_entry=cfg.time("no_new_entry"),
        square_off=cfg.time("square_off"),
    )
    if phase in ("closed", "preopen"):
        return plan, []  # no decisions outside the regular session (SL orders not allowed in pre-open)

    ltp, hi, lo = tick.ltp, tick.hi, tick.lo
    today = session.trade_date(now_ms)
    first_tick_today = plan.last_tick_ms is None or session.trade_date(plan.last_tick_ms) != today
    opened_today = session.trade_date(plan.opened_ms) == today

    # 1. session square-off (carry positions skip it only on their entry day)
    if phase == "square_off" and not (plan.carry and opened_today):
        return _terminal(plan, ExitReason.SESSION_CLOSE, ltp, now_ms)

    # 2. options on expiry day
    if plan.is_option and session.is_expiry_day(now_ms, l3.get("weekly_expiry_weekday")):
        if session.to_ist(now_ms).time() >= cfg.time("expiry_exit"):
            return _terminal(plan, ExitReason.EVENT, ltp, now_ms)

    # 3. scheduled event
    if signals.event_in_minutes is not None and signals.event_in_minutes <= int(l3["event_flat_minutes"]):
        return _terminal(plan, ExitReason.EVENT, ltp, now_ms)

    # 4. stop (a gap through the stop on the day's first tick is a GAP exit at the open price)
    stop_hit = lo <= plan.stop if side is Side.LONG else hi >= plan.stop
    if stop_hit:
        if first_tick_today and not opened_today and (ltp - plan.stop) * s < 0:
            return _terminal(plan, ExitReason.GAP, ltp, now_ms)
        reason = ExitReason.STOP if plan.state is State.OPEN else ExitReason.TRAIL
        fill_ref = plan.stop if (ltp - plan.stop) * s >= 0 else ltp  # gapped through intrabar -> worse price
        return _terminal(plan, reason, fill_ref, now_ms)

    # 5. target
    if plan.target is not None:
        target_hit = hi >= plan.target if side is Side.LONG else lo <= plan.target
        if target_hit:
            return _terminal(plan, ExitReason.TARGET, plan.target, now_ms)

    actions: list[Action] = []
    bars_held = plan.bars_held + (1 if tick.bar_closed else 0)
    plan = plan.with_(bars_held=bars_held, last_tick_ms=now_ms)

    # 6. scale-out (once)
    frac = dec(l1["scale_out_frac"])
    if frac > 0 and not plan.partial_done and plan.target1 is not None and (plan.qty_open or 0) >= 2:
        t1_hit = hi >= plan.target1 if side is Side.LONG else lo <= plan.target1
        if t1_hit:
            q = int((Decimal(plan.qty_open) * frac).to_integral_value(rounding=ROUND_FLOOR))
            if q >= 1:
                realized = plan.realized + (plan.target1 - plan.entry) * s * q
                plan = plan.with_(qty_open=plan.qty_open - q, partial_done=True, realized=realized)
                actions.append(ExitPosition(plan.id, q, ExitReason.PARTIAL, plan.target1))

    # 7. opposite signal
    if signals.opposite_signal:
        done, acts = _terminal(plan, ExitReason.OPPOSITE_SIGNAL, ltp, now_ms)
        return done, actions + acts

    # 8. time stop (dead trade)
    n = int(l1["time_stop_bars"])
    if n > 0 and bars_held >= n and plan.r_multiple(ltp) < dec(l1["time_stop_min_r"]):
        done, acts = _terminal(plan, ExitReason.TIME, ltp, now_ms)
        return done, actions + acts

    # 9. extremes + excursions
    highest = max(plan.highest, hi)
    lowest = min(plan.lowest, lo)
    fav = (highest - plan.entry) if side is Side.LONG else (plan.entry - lowest)
    adv = (plan.entry - lowest) if side is Side.LONG else (highest - plan.entry)
    plan = plan.with_(highest=highest, lowest=lowest, mfe=max(plan.mfe, fav), mae=max(plan.mae, adv))

    # 10. breakeven
    new_stop, new_state, why = plan.stop, plan.state, ""
    if plan.state is State.OPEN and plan.r_multiple(ltp) >= dec(l1["breakeven_at_r"]):
        be = plan.entry * (1 + s * cfg.pct("layer1", "cost_buffer_pct"))
        be = _stop_round(side, be, tickp)
        if _is_tighter(side, be, new_stop):
            new_stop, why = be, "breakeven"
        new_state = State.BREAKEVEN

    # 11. trail
    if new_state in (State.BREAKEVEN, State.TRAILING):
        cand: Decimal | None = None
        if l1["trail"] == "chandelier" and plan.atr is not None:
            k = dec(l1["chandelier_atr_k"]) * plan.atr
            cand = highest - k if side is Side.LONG else lowest + k
        elif l1["trail"] == "percent":
            p = cfg.pct("layer1", "trail_pct")
            cand = highest * (1 - p) if side is Side.LONG else lowest * (1 + p)
        if cand is not None:
            cand = _stop_round(side, cand, tickp)
            if _is_tighter(side, cand, new_stop):
                new_stop, new_state, why = cand, State.TRAILING, "trail"

    if new_stop != plan.stop:
        # invariant: stops never loosen
        if not _is_tighter(side, new_stop, plan.stop):
            raise AssertionError(f"stop would loosen for {plan.id}: {plan.stop} -> {new_stop}")
        actions.append(MoveStop(plan.id, new_stop, why))
    plan = plan.with_(stop=new_stop, state=new_state)
    return plan, actions


# --------------------------------------------------------------------------- layer 2
def _tier_keep(cfg: GuardConfig, peak_pct: Decimal) -> Decimal:
    for lo, hi, keep in cfg.l2["tiers"]:
        if peak_pct >= dec(lo) and (hi is None or peak_pct < dec(hi)):
            return dec(keep) / HUNDRED
    return dec(cfg.l2["tiers"][-1][2]) / HUNDRED


def candidate_floor(cfg: GuardConfig, peak: Decimal) -> Decimal | None:
    """Floor implied by ``peak`` for the configured mode, or None if not armed."""
    l2, cap = cfg.l2, cfg.capital
    mode = l2["mode"]
    if mode == "tiered":
        if peak < cap * cfg.pct("layer2", "arm_pct"):
            return None
        return peak * _tier_keep(cfg, peak / cap * HUNDRED)
    if mode == "lock_fixed":
        lf = l2["lock_fixed"]
        if peak < cap * dec(lf["at_pct"]) / HUNDRED:
            return None
        return cap * dec(lf["lock_pct"]) / HUNDRED
    if mode == "lock_and_trail":
        lt = l2["lock_and_trail"]
        at = cap * dec(lt["at_pct"]) / HUNDRED
        if peak < at:
            return None
        every = cap * dec(lt["every_pct"]) / HUNDRED
        steps = ((peak - at) / every).to_integral_value(rounding=ROUND_FLOOR)
        return cap * (dec(lt["lock_pct"]) + steps * dec(lt["raise_pct"])) / HUNDRED
    return None


def update_portfolio(
    state: PortfolioState,
    mtm: Decimal,
    cfg: GuardConfig,
) -> tuple[PortfolioState, list[Action]]:
    """Advance the day's portfolio guard with the latest MTM (realized + unrealized)."""
    mtm = dec(mtm)
    if state.loss_cap_hit or state.lock_hit:
        return state.with_(last_mtm=mtm), []  # sticky for the day

    cap_amt = cfg.capital * cfg.pct("layer2", "loss_cap_pct")
    if mtm <= -cap_amt:
        st = state.with_(loss_cap_hit=True, last_mtm=mtm, peak_mtm=max(state.peak_mtm, mtm))
        return st, [
            ExitAll(state.scope, ExitReason.LOSS_CAP),
            Alert("critical", f"Loss cap hit: MTM {mtm} <= -{cap_amt}", {"scope": state.scope}),
        ]

    if cfg.l2["mode"] == "off":
        return state.with_(last_mtm=mtm, peak_mtm=max(state.peak_mtm, mtm)), []

    peak = max(state.peak_mtm, mtm)
    cand = candidate_floor(cfg, peak)
    floor, armed = state.floor, state.armed
    actions: list[Action] = []
    if cand is not None:
        if not armed:
            actions.append(Alert("info", f"Profit lock armed at peak {peak}", {"scope": state.scope}))
        armed = True
        floor = cand if floor is None else max(floor, cand)
    if state.floor is not None and floor is not None and floor < state.floor:
        raise AssertionError("profit floor would fall")

    st = state.with_(peak_mtm=peak, floor=floor, armed=armed, last_mtm=mtm)
    if armed and floor is not None and mtm <= floor:
        st = st.with_(lock_hit=True)
        actions += [
            ExitAll(state.scope, ExitReason.PROFIT_LOCK),
            Alert("warn", f"Profit lock: MTM {mtm} <= floor {floor} (peak {peak})", {"scope": state.scope}),
        ]
    return st, actions


def start_of_day(
    prev: PortfolioState | None,
    trade_date: str,
    cfg: GuardConfig,
    prev_day_pnl: Decimal | None = None,
    scope: str = "account",
) -> PortfolioState:
    """Fresh day: reset peak/floor/flags; carry the losing-day streak and size cut."""
    streak = prev.consecutive_losing_days if prev else 0
    if prev_day_pnl is not None:
        streak = streak + 1 if dec(prev_day_pnl) < 0 else 0
    n = int(cfg.l2["losing_days_halve_after"])
    mult = Decimal("0.5") if n > 0 and streak >= n else Decimal("1")
    return PortfolioState(
        trade_date=trade_date,
        scope=prev.scope if prev else scope,
        consecutive_losing_days=streak,
        size_multiplier=mult,
    )


def portfolio_mtm(
    plans: Iterable[PositionPlan],
    ltps: dict[str, Decimal],
    day_start_ms: int | None = None,
) -> Decimal:
    """Today's realized + unrealized P&L across plans (unrealized only where a price is known).

    Pass ``day_start_ms`` (09:15 IST of the trade date): plans that closed before it belong to
    earlier days and are left out, so yesterday's profit can't arm today's lock or hide today's
    loss cap. Carry positions count unrealized from their entry price.
    """
    total = ZERO
    for p in plans:
        if day_start_ms is not None and p.state is State.EXITED and (p.exit_ms or 0) < day_start_ms:
            continue
        total += p.realized
        if p.state is not State.EXITED and (p.qty_open or 0) > 0 and p.symbol in ltps:
            total += p.unrealized(ltps[p.symbol])
    return total
