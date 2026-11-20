"""Keep the store and the (paper) broker in step. Unknown state => HALT and an alert, never a guess."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from src.live import halt as halt_mod
from src.live.profit_guard.broker_port import BrokerPort, is_option
from src.live.profit_guard.config import GuardConfig
from src.live.profit_guard.executor import Executor
from src.live.profit_guard.models import Alert, ExitReason, PositionPlan, Side, State, dec
from src.live.profit_guard.rules import round_to_tick
from src.live.profit_guard.store import GuardStore

ORPHAN_STOP_PCT = Decimal("0.02")  # default stop for a position the guard did not open


def reconcile(store: GuardStore, broker: BrokerPort, executor: Executor, cfg: GuardConfig, now_ms: int,
              broker_key: str = "zerodha") -> list[Alert]:
    alerts: list[Alert] = []
    plans = store.open_plans()
    by_sym: dict[str, list[PositionPlan]] = defaultdict(list)
    for p in plans:
        by_sym[p.symbol].append(p)
    held = {p["symbol"]: int(p["qty"]) for p in broker.positions()}
    in_flight: dict[str, int] = defaultdict(int)
    for pe in executor.pending.values():
        in_flight[pe.symbol] += pe.qty if pe.side == "sell" else -pe.qty
    tick = dec(cfg.ex["tick_size"])

    for sym, qty in held.items():
        if sym in by_sym or qty == 0 or in_flight.get(sym, 0) != 0:
            continue  # planned, flat, or an exit for it is already on its way
        ltp = broker.ltp(sym)
        if ltp is None:
            alerts.append(Alert("critical", f"Orphan position {sym} ({qty}) and no price: HALT"))
            halt_mod.trip_halt(by="profit_guard", reason=f"orphan {sym} without price", broker=broker_key)
            continue
        side = Side.LONG if qty > 0 else Side.SHORT
        stop = round_to_tick(ltp * (1 - side.sign * ORPHAN_STOP_PCT), tick, up=side is Side.SHORT)
        plan = PositionPlan(id=f"orphan-{sym}-{now_ms}", strategy="orphan", symbol=sym, side=side, qty=abs(qty),
                            entry=ltp, initial_stop=stop, stop=stop, target=None, one_r=abs(ltp - stop),
                            opened_ms=now_ms, is_option=is_option(sym), carry=True)
        store.save_plan(plan, now_ms)
        alerts.append(Alert("warn", f"Orphan {sym} ({qty}) adopted with a 2% stop at {stop}"))

    for sym, ps in by_sym.items():
        want = sum((p.qty_open or 0) * p.side.sign for p in ps)
        have = held.get(sym, 0)
        if have == 0 and in_flight.get(sym, 0) == 0:
            for p in ps:
                store.save_plan(p.with_(state=State.EXITED, exit_reason=ExitReason.EXTERNAL, exit_ms=now_ms,
                                        qty_open=0), now_ms)
            alerts.append(Alert("warn", f"{sym}: no position at the broker; plan closed as EXTERNAL"))
        elif have != want and in_flight.get(sym, 0) == 0:
            alerts.append(Alert("critical", f"{sym}: broker holds {have}, plans expect {want}: HALT"))
            halt_mod.trip_halt(by="profit_guard", reason=f"qty mismatch {sym}", broker=broker_key)

    if alerts:
        store.audit("reconcile", now_ms, alerts=[a.text for a in alerts])
    return alerts
