"""Carries out Profit Guard actions. Makes NO trading decisions of its own.

Order of every write: HALT check -> write-ahead intent (committed) -> broker call ->
intent marked sent. Exits are re-priced and re-sent until confirmed; entries are
never retried. Every broker write passes a token-bucket throttle.
"""

from __future__ import annotations

import time as _time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable

from src.live import halt as halt_mod
from src.live.profit_guard.broker_port import BrokerPort
from src.live.profit_guard.config import GuardConfig
from src.live.profit_guard.models import (
    Action,
    Alert,
    ExitAll,
    ExitPosition,
    ExitReason,
    MoveStop,
    PositionPlan,
    Side,
    State,
    dec,
)
from src.live.profit_guard.rules import round_to_tick
from src.live.profit_guard.store import GuardStore

REPRICE_AFTER_MS = 5_000


def _jsonable(x: Any) -> Any:
    """flatten.py json-audits broker responses: hand it plain types only."""
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    return x


class Throttle:
    """At most ``rate`` calls in any rolling 1-second window (injectable clock for tests)."""

    def __init__(self, rate: int, clock: Callable[[], float] = _time.monotonic,
                 sleep: Callable[[float], None] = _time.sleep) -> None:
        self.rate, self.clock, self.sleep = rate, clock, sleep
        self.stamps: deque[float] = deque()
        self.history: list[float] = []

    def wait(self) -> None:
        while True:
            now = self.clock()
            while self.stamps and now - self.stamps[0] >= 1.0:
                self.stamps.popleft()
            if len(self.stamps) < self.rate:
                self.stamps.append(now)
                self.history.append(now)
                return
            self.sleep(1.0 - (now - self.stamps[0]) + 1e-6)


@dataclass
class PendingExit:
    intent_id: int
    plan_id: str
    symbol: str
    side: str  # order side: sell closes a long
    qty: int
    reason: str
    order_id: str | None
    first_sent_ms: int
    last_sent_ms: int
    attempts: int = 1
    alerted: bool = False
    ref: Decimal | None = None  # price rules booked the exit at (None = rules did not book it)
    booked: bool = False  # True: rules already reduced qty_open / added realized at `ref`


@dataclass
class ExecReport:
    sent: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    flatten: list[dict[str, Any]] = field(default_factory=list)


class Executor:
    def __init__(self, store: GuardStore, broker: BrokerPort, cfg: GuardConfig, *, broker_key: str = "zerodha",
                 throttle: Throttle | None = None) -> None:
        self.store, self.broker, self.cfg, self.broker_key = store, broker, cfg, broker_key
        self.throttle = throttle or Throttle(int(cfg.ex["max_orders_per_sec"]))
        self.tick = dec(cfg.ex["tick_size"])
        self.through = dec(cfg.ex["limit_through_ltp_pct"]) / 100
        self.pending: dict[int, PendingExit] = {}
        #: plan ids saved together with their exit intent in the last execute() (the runner skips saving those)
        self.saved_with_intent: set[str] = set()
        # HALT stops new risk. With this on (default), exits that only REDUCE risk still go out, so a
        # paused bot never leaves positions without their stops. Set false for a total freeze.
        self.exits_when_halted = bool(cfg.ex.get("exits_when_halted", True))

    # -- helpers ---------------------------------------------------------------
    def _halted(self) -> bool:
        return halt_mod.halt_flag_set(self.broker_key)

    def _exit_limit(self, symbol: str, side: str, ref: Decimal | None) -> Decimal | None:
        ltp = self.broker.ltp(symbol) or ref
        if ltp is None:
            return None
        if side == "sell":
            return round_to_tick(ltp * (1 - self.through), self.tick, up=False)
        return round_to_tick(ltp * (1 + self.through), self.tick, up=True)

    def _held(self, symbol: str) -> int:
        for p in self.broker.positions():
            if p["symbol"] == symbol:
                return int(p["qty"])
        return 0

    def _cancel_symbol_orders(self, symbol: str, now_ms: int, keep: set[str] = frozenset()) -> None:
        for o in self.broker.open_orders():
            if o["symbol"] == symbol and o["order_id"] not in keep:
                self.throttle.wait()
                self.broker.cancel(o["order_id"])
                self.store.audit("cancel", now_ms, order_id=o["order_id"], symbol=symbol)
                self._forget_order(o["order_id"], now_ms)

    def _forget_order(self, order_id: str, now_ms: int) -> None:
        """We cancelled one of our own exits on purpose: it must not be re-sent later."""
        for iid, pe in list(self.pending.items()):
            if pe.order_id == order_id:
                self.store.mark_intent(iid, "failed", now_ms, error="superseded")
                del self.pending[iid]
                self._unbook(pe, now_ms)

    def _unbook(self, pe: PendingExit, now_ms: int) -> None:
        """An exit the rules had booked was cancelled before filling: take the booking back.

        Whatever replaces it (flatten, a later exit) books its own real fill, so nothing is counted twice.
        """
        if not (pe.booked and pe.ref is not None):
            return
        plan = self.store.get_plan(pe.plan_id)
        if plan is None:
            return
        s = plan.side.sign
        plan = plan.with_(realized=plan.realized - (pe.ref - plan.entry) * s * pe.qty,
                          qty_open=(plan.qty_open or 0) + pe.qty)
        self.store.save_plan(plan, now_ms)
        self.store.audit("unbook", now_ms, plan_id=pe.plan_id, qty=pe.qty, ref=pe.ref)

    def _send_exit(self, plan_id: str, symbol: str, side: str, qty: int, reason: str, ref: Decimal | None,
                   now_ms: int, rep: ExecReport, prior: PendingExit | None = None, booked: bool = False,
                   plan: PositionPlan | None = None) -> None:
        if prior is not None:
            ref, booked = prior.ref, prior.booked
        price = self._exit_limit(symbol, side, ref)
        if price is None:
            rep.alerts.append(Alert("critical", f"No price to exit {symbol}; exit NOT sent", {"plan_id": plan_id}))
            return
        payload = {"symbol": symbol, "side": side, "qty": qty, "price": str(price), "reason": reason,
                   "attempt": (prior.attempts + 1) if prior else 1, "booked": booked,
                   "ref": None if ref is None else str(ref),
                   "first_sent_ms": prior.first_sent_ms if prior else now_ms}
        intent_id = self.store.record_intent(plan_id, "exit", payload, now_ms, plan=plan)  # write-ahead (+ plan, atomically)
        if plan is not None:
            self.saved_with_intent.add(plan_id)
        self.throttle.wait()
        try:
            o = self.broker.submit_limit(symbol, side, qty, price, tag=f"exit:{plan_id}", now_ms=now_ms)
        except Exception as exc:  # noqa: BLE001 - exits retry on the next poll
            self.store.mark_intent(intent_id, "failed", now_ms, error=str(exc))
            rep.alerts.append(Alert("warn", f"Exit send failed for {symbol}: {exc}; will retry", {"plan_id": plan_id}))
            pe = PendingExit(intent_id, plan_id, symbol, side, qty, reason, None,
                             prior.first_sent_ms if prior else now_ms, now_ms,
                             (prior.attempts + 1) if prior else 1, prior.alerted if prior else False,
                             ref, booked)
            self.pending[intent_id] = pe
            return
        self.store.mark_intent(intent_id, "sent", now_ms, broker_order_id=o.get("order_id"))
        rep.sent.append({"intent_id": intent_id, **payload, "order_id": o.get("order_id"), "status": o.get("status")})
        self.pending[intent_id] = PendingExit(
            intent_id, plan_id, symbol, side, qty, reason, o.get("order_id"),
            prior.first_sent_ms if prior else now_ms, now_ms,
            (prior.attempts + 1) if prior else 1, prior.alerted if prior else False,
            ref, booked,
        )

    # -- main entry ------------------------------------------------------------
    def execute(self, actions: Iterable[Action], plans: dict[str, PositionPlan], now_ms: int) -> ExecReport:
        rep = ExecReport()
        self.saved_with_intent = set()
        for a in actions:
            if isinstance(a, Alert):
                self.store.audit("alert", now_ms, level=a.level, text=a.text, data=a.data)
                rep.alerts.append(a)
                continue
            loss_cap = isinstance(a, ExitAll) and a.reason is ExitReason.LOSS_CAP
            protective = isinstance(a, (ExitPosition, ExitAll)) and self.exits_when_halted
            if self._halted() and not (loss_cap or protective):
                rep.refused.append({"action": type(a).__name__, "why": "HALT set"})
                self.store.audit("refused", now_ms, action=type(a).__name__, why="HALT set")
                rep.alerts.append(Alert("warn", f"HALT set: {type(a).__name__} not executed"))
                continue
            if isinstance(a, MoveStop):
                self._move_stop(a, plans.get(a.plan_id), now_ms, rep)
            elif isinstance(a, ExitPosition):
                self._exit_position(a, plans.get(a.plan_id), now_ms, rep)
            elif isinstance(a, ExitAll):
                self._exit_all(a, plans, now_ms, rep)
        return rep

    def _move_stop(self, a: MoveStop, plan: PositionPlan | None, now_ms: int, rep: ExecReport) -> None:
        self.store.audit("move_stop", now_ms, plan_id=a.plan_id, new_stop=a.new_stop, why=a.reason)
        if plan is None or not plan.gtt_id:
            return  # software stop: the guard itself watches ticks; nothing at the broker to move
        side = "sell" if plan.side is Side.LONG else "buy"
        limit = self._exit_limit(plan.symbol, side, a.new_stop) or a.new_stop
        limit = min(limit, a.new_stop) if side == "sell" else max(limit, a.new_stop)
        intent_id = self.store.record_intent(plan.id, "modify_gtt", {"gtt_id": plan.gtt_id, "stop": str(a.new_stop)}, now_ms)
        self.throttle.wait()
        res = self.broker.modify_gtt(plan.gtt_id, stop_trigger=a.new_stop, stop_price=limit)
        ok = res.get("status") != "error"
        self.store.mark_intent(intent_id, "confirmed" if ok else "failed", now_ms, error=None if ok else res.get("error"))
        if not ok:
            rep.alerts.append(Alert("warn", f"GTT stop move failed for {plan.symbol}: {res.get('error')}"))

    def _exit_position(self, a: ExitPosition, plan: PositionPlan | None, now_ms: int, rep: ExecReport) -> None:
        if plan is None:
            rep.refused.append({"plan_id": a.plan_id, "why": "unknown plan"})
            rep.alerts.append(Alert("critical", f"Exit for unknown plan {a.plan_id}"))
            return
        side = "sell" if plan.side is Side.LONG else "buy"
        held = self._held(plan.symbol)
        held_same_side = held if plan.side is Side.LONG else -held
        if held_same_side <= 0:
            self._refuse_qty(plan, held, a.qty, now_ms, rep)
            return
        if a.reason is not ExitReason.PARTIAL:
            # quiesce the symbol first: resting orders (incl. our own partial exits) and the GTT backstop
            self._cancel_symbol_orders(plan.symbol, now_ms)
            if plan.gtt_id:
                self.throttle.wait()
                self.broker.cancel_gtt(plan.gtt_id)
            qty = held_same_side  # final exit closes whatever is really held for this plan's symbol
            if qty < a.qty:
                rep.alerts.append(Alert("warn", f"{plan.symbol}: closing {qty} held, plan expected {a.qty}",
                                        {"plan_id": plan.id}))
        else:
            in_flight = sum(p.qty for p in self.pending.values() if p.symbol == plan.symbol and p.side == side)
            if held_same_side - in_flight < a.qty:
                self._refuse_qty(plan, held, a.qty, now_ms, rep)
                return
            qty = a.qty
        self._send_exit(plan.id, plan.symbol, side, qty, a.reason.value, a.ref_price, now_ms, rep, booked=True, plan=plan)

    def _refuse_qty(self, plan: PositionPlan, held: int, qty: int, now_ms: int, rep: ExecReport) -> None:
        rep.refused.append({"plan_id": plan.id, "why": f"holds {held}, asked to close {qty}"})
        rep.alerts.append(Alert("critical", f"Refused exit {plan.symbol}: broker holds {held}, exit asks {qty}",
                                {"plan_id": plan.id}))
        self.store.audit("refused", now_ms, plan_id=plan.id, why="qty exceeds holding", held=held, qty=qty)

    def _exit_all(self, a: ExitAll, plans: dict[str, PositionPlan], now_ms: int, rep: ExecReport) -> None:
        from src.live.runtime.flatten import flatten_and_cancel

        in_scope = {p.symbol for p in plans.values()
                    if a.scope in ("account", p.strategy)}
        plan_by_symbol = {p.symbol: p for p in plans.values() if p.symbol in in_scope}

        def read_positions() -> list[dict[str, Any]]:
            return [_jsonable(p) for p in self.broker.positions() if p["symbol"] in in_scope or a.scope == "account"]

        def read_open_orders() -> list[dict[str, Any]]:
            return [_jsonable(o) for o in self.broker.open_orders() if o["symbol"] in in_scope or a.scope == "account"]

        def submit(req: dict[str, Any]) -> dict[str, Any]:
            if req.get("action") == "cancel":
                self.throttle.wait()
                res = self.broker.cancel(req["order_id"])
                self._forget_order(req["order_id"], now_ms)
                return _jsonable(res)
            # flatten.py asks for a market close; options forbid market orders, so price a limit through LTP
            sym, side, qty = req["symbol"], req["side"], int(req["qty"])
            plan = plan_by_symbol.get(sym)
            before = len(rep.sent)
            self._send_exit(plan.id if plan else f"orphan:{sym}", sym, side, qty, a.reason.value, None, now_ms, rep)
            return _jsonable(rep.sent[-1]) if len(rep.sent) > before else {"status": "error", "error": "exit not sent"}

        for p in plan_by_symbol.values():
            if p.gtt_id:
                self.throttle.wait()
                self.broker.cancel_gtt(p.gtt_id)
        report = flatten_and_cancel(self.broker_key, submit, read_positions, read_open_orders, allow_flatten=True)
        rep.flatten.append(report)
        self.store.audit("exit_all", now_ms, scope=a.scope, reason=a.reason.value, report=report)
        if a.reason is ExitReason.LOSS_CAP and not self._halted():
            halt_mod.trip_halt(by="profit_guard", reason=f"loss cap hit ({a.scope})", broker=self.broker_key)
            rep.alerts.append(Alert("critical", f"Loss cap: flattened {a.scope} and tripped HALT for {self.broker_key}"))

    def _book_fill(self, pe: PendingExit, fill_px: Decimal, now_ms: int, cost: Decimal = Decimal(0)) -> None:
        """Make the plan's P&L match the broker fill (net of charges); close plans flattened by ExitAll."""
        plan = self.store.get_plan(pe.plan_id)
        if plan is None:
            return  # orphan symbol: nothing to book against
        s = plan.side.sign
        plan = plan.with_(realized=plan.realized - cost)
        if pe.booked and pe.ref is not None:
            # rules booked this exit at the trigger price: correct to the real fill
            plan = plan.with_(realized=plan.realized + (fill_px - pe.ref) * s * pe.qty)
            if plan.state is State.EXITED:
                plan = plan.with_(exit_price=fill_px)
        else:
            # ExitAll / flatten: rules never booked it, so book it now from the fill
            left = max(0, (plan.qty_open or 0) - pe.qty)
            plan = plan.with_(realized=plan.realized + (fill_px - plan.entry) * s * pe.qty, qty_open=left)
            if left == 0:
                if plan.state is not State.EXITED:
                    plan = plan.with_(state=State.EXITED, exit_reason=ExitReason(pe.reason), exit_ms=now_ms)
                plan = plan.with_(exit_price=fill_px)
        self.store.save_plan(plan, now_ms)

    def rebuild_pending(self, now_ms: int) -> ExecReport:
        """After a restart: re-track exits that were sent, and send exits that never left.

        Never doubles an exit: a 'sent' intent is only re-tracked (poll_exits confirms or re-prices it);
        a 'pending' intent (crash between write-ahead and send) is sent once, capped at what is held.
        """
        rep = ExecReport()
        for it in self.store.intents():
            if it["kind"] != "exit" or it["status"] not in ("pending", "sent"):
                continue
            pl = it["payload"]
            ref = dec(pl["ref"]) if pl.get("ref") else None
            first = int(pl.get("first_sent_ms") or it["created_ms"])
            if it["status"] == "sent" and it["broker_order_id"]:
                self.pending[it["id"]] = PendingExit(it["id"], it["plan_id"], pl["symbol"], pl["side"], int(pl["qty"]),
                                                     pl["reason"], it["broker_order_id"], first, it["updated_ms"],
                                                     int(pl.get("attempt", 1)), False, ref, bool(pl.get("booked")))
                continue
            self.store.mark_intent(it["id"], "failed", now_ms, error="never sent before restart")
            held = self._held(pl["symbol"])
            still = (held if pl["side"] == "sell" else -held)
            still -= sum(p.qty for p in self.pending.values() if p.symbol == pl["symbol"] and p.side == pl["side"])
            if still > 0:
                self._send_exit(it["plan_id"], pl["symbol"], pl["side"], min(int(pl["qty"]), still), pl["reason"],
                                ref, now_ms, rep, booked=bool(pl.get("booked")))
        if rep.sent or self.pending:
            self.store.audit("rebuild_pending", now_ms, tracked=len(self.pending), resent=len(rep.sent))
        return rep

    # -- confirmation loop -----------------------------------------------------
    def poll_exits(self, now_ms: int) -> ExecReport:
        """Confirm fills; re-price and re-send exits that are rejected, cancelled or stale."""
        rep = ExecReport()
        alert_ms = int(self.cfg.ex["exit_alert_after_s"]) * 1000
        for intent_id, pe in list(self.pending.items()):
            o = self.broker.order(pe.order_id) if pe.order_id else None
            status = o["status"] if o else "FAILED_SEND"
            if status == "FILLED":
                self.store.mark_intent(intent_id, "confirmed", now_ms, broker_order_id=pe.order_id)
                self.store.record_exit(pe.plan_id, pe.qty, pe.reason, o.get("avg_price"), pe.order_id, now_ms)
                self._book_fill(pe, dec(o.get("avg_price")), now_ms, dec(o.get("cost") or 0))
                del self.pending[intent_id]
                continue
            if not pe.alerted and now_ms - pe.first_sent_ms >= alert_ms:
                pe.alerted = True
                rep.alerts.append(Alert("critical", f"Exit for {pe.symbol} still not filled after "
                                                    f"{(now_ms - pe.first_sent_ms) // 1000}s (attempt {pe.attempts})",
                                        {"plan_id": pe.plan_id}))
            stale = status in ("OPEN", "TRIGGER_PENDING") and now_ms - pe.last_sent_ms >= REPRICE_AFTER_MS
            dead = status in ("REJECTED", "CANCELLED", "FAILED_SEND")
            if not (stale or dead):
                continue
            if self._halted() and not self.exits_when_halted:
                continue  # total freeze: no re-sends; the alert above still fires
            if status in ("OPEN", "TRIGGER_PENDING"):
                self.throttle.wait()
                self.broker.cancel(pe.order_id)
            if status != "FAILED_SEND":
                self.store.mark_intent(intent_id, "failed", now_ms, error=f"order {status}")
            del self.pending[intent_id]
            held = self._held(pe.symbol)
            still = held if pe.side == "sell" else -held
            # never oversell: other live exits for this symbol already cover part of the holding
            still -= sum(p.qty for p in self.pending.values() if p.symbol == pe.symbol and p.side == pe.side)
            if still <= 0:
                continue  # nothing left to close (filled elsewhere / closed externally)
            self._send_exit(pe.plan_id, pe.symbol, pe.side, min(pe.qty, still), pe.reason, None, now_ms, rep, prior=pe)
        return rep
