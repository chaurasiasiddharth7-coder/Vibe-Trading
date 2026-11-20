"""P6: the guard loop. Runs every 10 s in market hours on live Kite prices, PAPER fills only.

    cd agent && ../.venv/bin/python -m src.live.profit_guard.runner            # run until Ctrl+C
    cd agent && ../.venv/bin/python -m src.live.profit_guard.runner --once     # one tick, then exit
    cd agent && ../.venv/bin/python -m src.live.profit_guard.runner enter --strategy btst --symbol SBIN --qty 10 --carry

Entries are paper-only and go through an inbox file, so only this process ever writes the store.
Kite is used for prices only (feeds.kite_client.ReadOnlyKite has no order methods).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time as _time
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from src.live import halt as halt_mod
from src.live.profit_guard import rules, session
from src.live.profit_guard.alerts import AlertSender
from src.live.profit_guard.broker_port import is_option
from src.live.profit_guard.config import GuardConfig, load_config
from src.live.profit_guard.executor import Executor
from src.live.profit_guard.models import Alert, ExitPosition, PortfolioState, PositionPlan, Side, State, Tick, dec
from src.live.profit_guard.paper_broker import PaperBroker
from src.live.profit_guard.reconcile import reconcile
from src.live.profit_guard.store import GuardStore

logger = logging.getLogger(__name__)
STALE_MS = 30_000
ENTRY_SLIP = Decimal("0.005")  # paper entry limit 0.5% through the price
PriceFn = Callable[[list[str]], dict[str, Decimal]]


def inbox_path() -> Path:
    from src.live.paths import live_root

    return live_root() / "profit_guard_inbox.jsonl"


def queue_entry(req: dict[str, Any], path: Path | None = None) -> None:
    """Append a paper entry request (any process may call this; only the runner reads it)."""
    p = path or inbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(req) + "\n")


def readiness(cfg: GuardConfig) -> list[str]:
    """Settings that still look like placeholders (shown at start, sent as one alert)."""
    warn = []
    if int(cfg.raw.get("capital_inr", 0)) == 1_000_000 and not cfg.raw.get("capital_confirmed"):
        warn.append("capital_inr is still the ₹10L placeholder: set your real capital and capital_confirmed: true")
    if cfg.l3.get("weekly_expiry_weekday") is None:
        warn.append("weekly_expiry_weekday is not set: option expiry-day exits are OFF")
    return warn


@dataclass
class GuardRunner:
    cfg: GuardConfig
    store: GuardStore
    broker: PaperBroker
    prices: PriceFn  # live prices: symbol -> Decimal (fresh only)
    fallback: PriceFn | None = None  # REST quote fallback
    alerts: AlertSender = field(default_factory=AlertSender)
    holidays: set[date] = field(default_factory=set)
    broker_state: Path | None = None
    inbox: Path | None = None
    broker_key: str = "zerodha"
    executor: Executor = field(init=False)
    _last_reconcile: int = 0
    _inbox_offset: int = 0
    _entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    _gtt_checked: str = ""
    blocked_entries: bool = False

    def __post_init__(self) -> None:
        self.executor = Executor(self.store, self.broker, self.cfg, broker_key=self.broker_key)
        from src.live.profit_guard.bridge import Bridge

        self.bridge = Bridge(self.cfg.raw, self.store.get_meta, self.store.set_meta,
                             lambda req: queue_entry(req, self.inbox) if self.inbox else None,
                             lambda: {p.symbol.upper() for p in self.store.open_plans()}
                             | {str(e["symbol"]).upper() for e in self._entries.values()})

    # -- lifecycle ---------------------------------------------------------------
    def start(self, now_ms: int) -> list[Alert]:
        out = [Alert("warn", w) for w in readiness(self.cfg)]
        if not self.holidays:
            out.append(Alert("warn", "NSE holiday list is empty: the guard will treat holidays as trading days"))
        if hasattr(self.broker, "reserve_ids_above"):
            import re as _re

            seen = [int(m.group(1)) for it in self.store.intents()
                    for m in [_re.fullmatch(r"(?:PG|GTT)-(\d+)", str(it.get("broker_order_id") or ""))] if m]
            seen += [int(m.group(1)) for p in self.store.all_plans()
                     for m in [_re.fullmatch(r"GTT-(\d+)", str(p.gtt_id or ""))] if m]
            if seen:
                self.broker.reserve_ids_above(max(seen))
        rep = self.executor.rebuild_pending(now_ms)
        out += rep.alerts
        out += reconcile(self.store, self.broker, self.executor, self.cfg, now_ms, self.broker_key)
        self._last_reconcile = now_ms
        self.alerts.send(out)
        return out

    def save_broker(self) -> None:
        if self.broker_state:
            tmp = self.broker_state.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.broker.to_state()), encoding="utf-8")
            tmp.replace(self.broker_state)

    # -- scopes --------------------------------------------------------------------
    def _scope_of(self, plan: PositionPlan) -> str:
        return plan.strategy if self.cfg.l2["scope"] == "per_strategy" else "account"

    def _portfolio(self, scope: str, trade_date: str) -> PortfolioState:
        st = self.store.get_portfolio(trade_date, scope)
        if st is None:
            prev = self.store.last_portfolio_before(trade_date, scope)
            st = rules.start_of_day(prev, trade_date, self.cfg, prev.last_mtm if prev else None, scope=scope)
        return st

    # -- prices --------------------------------------------------------------------
    def _get_prices(self, symbols: list[str], out_alerts: list[Alert]) -> dict[str, Decimal]:
        if not symbols:
            return {}
        px = dict(self.prices(symbols))
        missing = [s for s in symbols if s not in px]
        if missing and self.fallback:
            try:
                px.update(self.fallback(missing))
            except Exception as exc:  # noqa: BLE001
                logger.warning("REST fallback failed: %s", exc)
        still = [s for s in symbols if s not in px]
        self.blocked_entries = bool(still)
        if still:
            out_alerts.append(Alert("warn", f"No fresh price for {', '.join(still)}: no decisions on them, entries blocked"))
        return px

    # -- entries (paper) ---------------------------------------------------------------
    def _read_inbox(self) -> list[dict[str, Any]]:
        """New entry requests since the last read. The read position is kept in the store (not just in memory),
        so a restart never replays old requests. Entries are at-most-once: a crash after this read drops the
        requests just read rather than risking a duplicate position."""
        p = self.inbox
        if not p or not p.exists():
            return []
        size = p.stat().st_size
        saved = self.store.get_meta("inbox_offset")
        offset = int(saved) if saved and saved.isdigit() else 0
        if offset > size:  # the inbox was rotated or replaced
            offset = 0
        with open(p, encoding="utf-8") as fh:
            fh.seek(offset)
            lines = fh.readlines()
            end = fh.tell()
        if end != offset:
            self.store.set_meta("inbox_offset", str(end))
        self._inbox_offset = end
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                logger.warning("bad inbox line skipped")
        return out

    def _accept_entry(self, req: dict[str, Any], phase: str, now_ms: int, mult: Decimal) -> Alert:
        sym = str(req["symbol"]).upper()
        carry = bool(req.get("carry"))
        if halt_mod.halt_flag_set(self.broker_key):
            return Alert("warn", f"Entry {sym} refused: HALT is set")
        if self.blocked_entries:
            return Alert("warn", f"Entry {sym} refused: prices are stale")
        allowed = phase == "open" or (carry and phase in ("no_new_entry", "square_off"))
        if not allowed:
            return Alert("warn", f"Entry {sym} refused: market phase is {phase}")
        if any(p.symbol == sym for p in self.store.open_plans()) or any(e["symbol"] == sym for e in self._entries.values()):
            return Alert("warn", f"Entry {sym} refused: already held by another plan (one plan per stock)")
        qty = int((Decimal(int(req["qty"])) * mult).to_integral_value())
        if qty < 1:
            return Alert("warn", f"Entry {sym} refused: size after the losing-streak cut is 0")
        ltp = self.broker.ltp(sym)
        if ltp is None:
            return Alert("warn", f"Entry {sym} refused: no price yet")
        side = Side(req.get("side", "LONG"))
        if req.get("stop") is not None and (dec(req["stop"]) - ltp) * side.sign >= 0:
            return Alert("warn", f"Entry {sym} refused: price {ltp} is already past its stop {req['stop']}")
        tick = dec(self.cfg.ex["tick_size"])
        limit = rules.round_to_tick(ltp * (1 + side.sign * ENTRY_SLIP), tick, up=side is Side.LONG)
        o = self.broker.submit_limit(sym, "buy" if side is Side.LONG else "sell", qty, limit, tag="entry", now_ms=now_ms)
        self._entries[o["order_id"]] = {**req, "symbol": sym, "qty": qty, "side": side.value, "carry": carry,
                                        "placed_ms": now_ms}
        self.store.audit("paper_entry", now_ms, order_id=o["order_id"], symbol=sym, qty=qty, limit=str(limit))
        return Alert("info", f"Paper entry sent: {side.value} {qty} {sym} @ ≤{limit}")

    def _fill_entries(self, now_ms: int) -> list[Alert]:
        out = []
        for oid, e in list(self._entries.items()):
            o = self.broker.order(oid)
            if o is None or o["status"] in ("CANCELLED", "REJECTED"):
                self._entries.pop(oid)
                out.append(Alert("warn", f"Paper entry {e['symbol']} not filled ({o['status'] if o else 'lost'})"))
                continue
            if o["status"] == "OPEN" and now_ms - e["placed_ms"] > 60_000:
                self.broker.cancel(oid)  # entries are never chased
                self._entries.pop(oid)
                out.append(Alert("info", f"Paper entry {e['symbol']} cancelled after 60s unfilled"))
                continue
            if o["status"] != "FILLED":
                continue
            self._entries.pop(oid)
            side, cfg = Side(e["side"]), self.cfg.for_strategy(e.get("strategy", "manual"))
            fill = dec(o["avg_price"])
            atr = dec(e["atr"]) if e.get("atr") else fill * Decimal("0.01")
            stop, t1, target, one_r = rules.initial_levels(side, fill, atr, cfg,
                                                           dec(e["swing"]) if e.get("swing") else None)
            if e.get("stop") is not None and (fill - dec(e["stop"])) * side.sign > 0:
                # A stop handed over by a strategy (overnight pick, practice trade) replaces the ATR stop.
                stop = rules.round_to_tick(dec(e["stop"]), dec(cfg.ex["tick_size"]), up=side is not Side.LONG)
                one_r = abs(fill - stop)
                t1 = fill + side.sign * one_r * dec(cfg.l1.get("scale_out_at_r") or 1)
                if target is not None and cfg.l1.get("target_r") is not None:
                    target = rules.round_to_tick(fill + side.sign * one_r * dec(cfg.l1["target_r"]),
                                                 dec(cfg.ex["tick_size"]), up=side is Side.LONG)
            if e.get("target") is not None and (dec(e["target"]) - fill) * side.sign > 0:
                target = rules.round_to_tick(dec(e["target"]), dec(cfg.ex["tick_size"]), up=side is Side.LONG)
            if e.get("target_pct") is not None:
                target = rules.round_to_tick(fill * (1 + side.sign * dec(e["target_pct"]) / 100), dec(cfg.ex["tick_size"]),
                                             up=side is Side.LONG)
            plan = PositionPlan(id=f"{e.get('strategy','manual')}-{e['symbol']}-{now_ms}", strategy=e.get("strategy", "manual"),
                                symbol=e["symbol"], side=side, qty=int(e["qty"]), entry=fill, initial_stop=stop, stop=stop,
                                target=target, target1=t1, one_r=one_r, opened_ms=now_ms, atr=atr,
                                is_option=is_option(e["symbol"]), carry=bool(e["carry"]),
                                realized=-dec(o.get("cost") or 0))
            if plan.carry and cfg.l3.get("use_gtt_for_btst") and target is not None:
                exit_side = "sell" if side is Side.LONG else "buy"
                g = self.broker.place_gtt_oco(plan.symbol, exit_side, plan.qty, stop, stop, target, target)
                plan = plan.with_(gtt_id=g["gtt_id"])
            self.store.save_plan(plan, now_ms)
            out.append(Alert("info", f"Paper position opened: {side.value} {plan.qty} {plan.symbol} @ {fill} · stop {stop}"
                                     + (f" · target {target}" if target else "")))
        return out

    # -- one tick ----------------------------------------------------------------------
    def tick(self, now_ms: int) -> dict[str, Any]:
        alerts: list[Alert] = []
        phase = session.session_phase(now_ms, holidays=self.holidays, no_new_entry=self.cfg.time("no_new_entry"),
                                      square_off=self.cfg.time("square_off"))
        today = session.trade_date(now_ms)
        plans = self.store.open_plans()
        # Strategies hand their positions over (overnight pick, practice trades) through the inbox.
        if self.inbox is not None:
            try:
                for line in self.bridge.poll(now_ms, phase, today):
                    logger.info("bridge queued %s", line)
            except Exception as exc:  # noqa: BLE001 - the bridge must never stop the guard
                logger.warning("bridge failed: %s", exc)
        # Before the open the requests stay in the file and are read at 09:15, instead of being refused and lost.
        requests = self._read_inbox() if phase not in ("closed", "preopen") else []
        symbols = sorted({p.symbol for p in plans} | {str(r["symbol"]).upper() for r in requests}
                         | {e["symbol"] for e in self._entries.values()}
                         | {pe.symbol for pe in self.executor.pending.values()}  # exits still working
                         | {q["symbol"] for q in self.broker.positions()})
        if phase == "closed":
            return {"phase": phase, "open_plans": len(plans)}

        px = self._get_prices(symbols, alerts)
        for sym, price in px.items():
            self.broker.on_tick(sym, price, now_ms)  # paper fills happen here
        alerts += self.executor.poll_exits(now_ms).alerts
        alerts += self._fill_entries(now_ms)

        actions_by_scope: dict[str, list] = {}
        plans = {p.id: p for p in self.store.open_plans()}
        actions: list = []
        deferred: dict[str, PositionPlan] = {}
        for p in list(plans.values()):
            if p.symbol not in px:
                continue
            cfg = self.cfg.for_strategy(p.strategy)
            np_, acts = rules.update_position(p, Tick(p.symbol, px[p.symbol], now_ms), cfg, now_ms,
                                              holidays=self.holidays)
            if np_ != p:
                if any(isinstance(a, ExitPosition) for a in acts):
                    # Saved together with the exit's write-ahead intent (one transaction), never before it:
                    # a crash in between would otherwise mark the plan exited with no exit order on record.
                    deferred[np_.id] = np_
                else:
                    self.store.save_plan(np_, now_ms)
            plans[np_.id] = np_
            actions += acts

        # portfolio guard per scope, on today's P&L only
        day_start = session.ist_ms(date.fromisoformat(today), time(9, 15))
        everything = self.store.all_plans()
        scopes = {self._scope_of(p) for p in everything if p.state is not State.EXITED or (p.exit_ms or 0) >= day_start}
        status: dict[str, Any] = {}
        mult = Decimal("1")
        for scope in sorted(scopes):
            members = [p for p in everything if self._scope_of(p) == scope]
            mtm = rules.portfolio_mtm(members, px, day_start_ms=day_start)
            st, pacts = rules.update_portfolio(self._portfolio(scope, today), mtm, self.cfg)
            self.store.save_portfolio(st, now_ms)
            actions_by_scope[scope] = pacts
            actions += pacts
            mult = min(mult, st.size_multiplier)
            status[scope] = {"mtm": str(mtm), "peak": str(st.peak_mtm), "floor": str(st.floor) if st.floor else None,
                             "armed": st.armed, "lock_hit": st.lock_hit, "loss_cap_hit": st.loss_cap_hit}

        rep = self.executor.execute(actions, plans, now_ms)
        alerts += rep.alerts
        for pid, np_ in deferred.items():
            if pid not in self.executor.saved_with_intent:  # exit refused or not sent: keep what the rules decided
                self.store.save_plan(np_, now_ms)

        for req in requests:
            alerts.append(self._accept_entry(req, phase, now_ms, mult))

        if now_ms - self._last_reconcile >= 60_000:
            alerts += reconcile(self.store, self.broker, self.executor, self.cfg, now_ms, self.broker_key)
            self._last_reconcile = now_ms

        # 15:30 check: every carry plan must have its GTT backstop
        if session.to_ist(now_ms).time() >= time(15, 25) and self._gtt_checked != today:
            self._gtt_checked = today
            live = {g["gtt_id"] for g in self.broker.gtts() if g["status"] == "ACTIVE"}
            for p in self.store.open_plans():
                if p.carry and (not p.gtt_id or p.gtt_id not in live):
                    alerts.append(Alert("critical", f"{p.symbol}: carry position has NO active GTT backstop overnight"))

        self.save_broker()
        self.alerts.send([a for a in alerts if a.level != "info" or "Paper" in a.text])
        return {"phase": phase, "open_plans": len(self.store.open_plans()), "scopes": status,
                "pending_exits": len(self.executor.pending), "alerts": [a.text for a in alerts]}


# ---------------------------------------------------------------------- live wiring (Mac / VPS)
def build_live(cfg_path: str | None = None) -> tuple[GuardRunner, Any]:
    from src.live.paths import live_root
    from src.live.profit_guard.feeds import kite_auth
    from src.live.profit_guard.feeds.instruments import load_instruments, token_map
    from src.live.profit_guard.feeds.kite_client import ReadOnlyKite
    from src.live.profit_guard.feeds.kite_ticker import TickFeed

    kite_auth._load_env()
    now = int(_time.time() * 1000)
    tok = kite_auth.load_token()
    if not kite_auth.token_is_fresh(tok, now):
        raise SystemExit("No fresh Kite login today. Double-click scripts/kite_login.command first (after 07:35 IST).")
    kite = ReadOnlyKite(access_token=tok["access_token"])
    feed = TickFeed(kite.api_key, tok["access_token"])
    rows: list[dict[str, Any]] = []
    known: dict[str, int] = {}

    def ensure_subscribed(symbols: list[str]) -> None:
        nonlocal rows
        new = [s for s in symbols if s not in known]
        if not new:
            return
        if not rows:
            rows = load_instruments(kite, "NSE", date.today()) + load_instruments(kite, "NFO", date.today())
        found = token_map(rows, new)
        known.update(found)
        if found:
            feed.subscribe(found)

    def live_prices(symbols: list[str]) -> dict[str, Decimal]:
        ensure_subscribed(symbols)
        out = {}
        for s in symbols:
            t = feed.latest(s)
            if t is not None and not feed.is_stale(s):
                out[s] = t.ltp
        return out

    def rest_prices(symbols: list[str]) -> dict[str, Decimal]:
        keys = [f"{'NFO' if is_option(s) else 'NSE'}:{s}" for s in symbols]
        q = kite.ltp(keys)
        return {k.split(":", 1)[1]: Decimal(str(v["last_price"])) for k, v in q.items()}

    root = live_root()
    state = root / "profit_guard_paper_broker.json"
    broker = PaperBroker.from_state(json.loads(state.read_text())) if state.exists() else PaperBroker()
    runner = GuardRunner(load_config(cfg_path), GuardStore(), broker, live_prices, rest_prices,
                         holidays=session.nse_holidays(), broker_state=state, inbox=inbox_path())
    feed.start()
    return runner, feed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="profit_guard.runner")
    sub = ap.add_subparsers(dest="cmd")
    e = sub.add_parser("enter", help="queue a PAPER entry for the running guard")
    e.add_argument("--strategy", default="manual")
    e.add_argument("--symbol", required=True)
    e.add_argument("--qty", type=int, required=True)
    e.add_argument("--side", default="LONG", choices=["LONG", "SHORT"])
    e.add_argument("--carry", action="store_true", help="hold overnight (BTST / swing)")
    e.add_argument("--target-pct", type=float, default=None, help="e.g. 1 for BTST's +1%% target")
    e.add_argument("--atr", type=float, default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=10)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.cmd == "enter":
        queue_entry({"strategy": args.strategy, "symbol": args.symbol, "qty": args.qty, "side": args.side,
                     "carry": args.carry, "target_pct": args.target_pct, "atr": args.atr})
        print("Queued. The running guard picks it up on its next tick (paper only).")
        return 0

    runner, feed = build_live()
    try:
        runner.start(int(_time.time() * 1000))
        while True:
            now = int(_time.time() * 1000)
            st = runner.tick(now)
            print(json.dumps({k: v for k, v in st.items() if k != "alerts"}, default=str), flush=True)
            if args.once:
                return 0
            _time.sleep(args.interval if st["phase"] != "closed" else 60)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    finally:
        runner.save_broker()
        feed.stop()
        runner.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
