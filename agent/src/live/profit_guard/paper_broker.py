"""In-memory paper broker that is deliberately NOT flattering.

* Orders never fill on the tick that placed them; they fill on a LATER tick that crosses.
* Fills pay slippage (default 0.1% per side) plus a flat cost per side.
* Market and SL-M orders on options are rejected (NSE/Zerodha rules).
* SL-limit orders triggered with a limit outside the price-protection band are rejected,
  so the executor's re-price path is exercised.
* GTT OCO legs sit dormant until triggered, then become limit orders (fill next tick).
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Any

from src.live.profit_guard.broker_port import OPEN_STATUSES, is_option
from src.live.profit_guard.models import dec


class PaperBroker:
    name = "paper"

    def __init__(
        self,
        *,
        slippage_pct: Decimal | str = "0.1",
        cost_pct_per_side: Decimal | str = "0.05",
        lpp_pct: Decimal | str = "5",
        fail_next_submits: int = 0,
    ) -> None:
        self.slip = dec(slippage_pct) / 100
        self.cost = dec(cost_pct_per_side) / 100
        self.lpp = dec(lpp_pct) / 100
        self._ids = itertools.count(1)
        self._seq: dict[str, int] = {}
        self._ltp: dict[str, Decimal] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._gtts: dict[str, dict[str, Any]] = {}
        self._pos: dict[str, dict[str, Decimal]] = {}
        self.fills: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []  # (method, order/gtt id) for ordering assertions
        self.fail_next_submits = fail_next_submits  # test hook: raise on next N submits

    # -- market data / setup ---------------------------------------------------
    def seed_position(self, symbol: str, qty: int, avg_price: Any) -> None:
        self._pos[symbol] = {"qty": Decimal(qty), "avg": dec(avg_price)}

    def ltp(self, symbol: str) -> Decimal | None:
        return self._ltp.get(symbol)

    def on_tick(self, symbol: str, ltp: Any, ts_ms: int) -> list[dict[str, Any]]:
        """Advance the book for one symbol; returns fills produced by this tick."""
        price = dec(ltp)
        self._ltp[symbol] = price
        self._seq[symbol] = self._seq.get(symbol, 0) + 1
        seq = self._seq[symbol]
        out: list[dict[str, Any]] = []
        for gid, g in list(self._gtts.items()):
            if g["symbol"] != symbol or g["status"] != "ACTIVE" or g["placed_seq"] >= seq:
                continue
            leg = None
            if g["side"] == "sell":
                if price <= g["stop_trigger"]:
                    leg = ("stop", g["stop_price"])
                elif price >= g["target_trigger"]:
                    leg = ("target", g["target_price"])
            else:
                if price >= g["stop_trigger"]:
                    leg = ("stop", g["stop_price"])
                elif price <= g["target_trigger"]:
                    leg = ("target", g["target_price"])
            if leg:
                g["status"] = "TRIGGERED"
                g["leg"] = leg[0]
                o = self._new_order(symbol, g["side"], g["qty"], "limit", leg[1], None, f"gtt:{gid}", ts_ms, seq)
                g["order_id"] = o["order_id"]
        for oid, o in list(self._orders.items()):
            if o["symbol"] != symbol or o["status"] not in OPEN_STATUSES or o["placed_seq"] >= seq:
                continue
            if o["status"] == "TRIGGER_PENDING":
                hit = price <= o["trigger"] if o["side"] == "sell" else price >= o["trigger"]
                if not hit:
                    continue
                if abs(o["price"] - price) / price > self.lpp:
                    o["status"], o["reason"] = "REJECTED", "LPP: limit outside price protection range"
                    continue
                o["status"] = "OPEN"
            fill = self._try_fill(o, price, ts_ms)
            if fill:
                out.append(fill)
        return out

    # -- reads -----------------------------------------------------------------
    def positions(self) -> list[dict[str, Any]]:
        return [{"symbol": s, "qty": int(p["qty"]), "avg_price": p["avg"]} for s, p in self._pos.items() if p["qty"] != 0]

    def position_qty(self, symbol: str) -> int:
        p = self._pos.get(symbol)
        return int(p["qty"]) if p else 0

    def open_orders(self) -> list[dict[str, Any]]:
        return [dict(o) for o in self._orders.values() if o["status"] in OPEN_STATUSES]

    def order(self, order_id: str) -> dict[str, Any] | None:
        o = self._orders.get(order_id)
        return dict(o) if o else None

    def gtts(self) -> list[dict[str, Any]]:
        return [dict(g) for g in self._gtts.values()]

    # -- writes ----------------------------------------------------------------
    def _check_fail(self) -> None:
        if self.fail_next_submits > 0:
            self.fail_next_submits -= 1
            raise ConnectionError("paper broker: simulated network failure")

    def _new_order(self, symbol, side, qty, typ, price, trigger, tag, now_ms, seq=None) -> dict[str, Any]:
        oid = f"PG-{next(self._ids)}"
        o = {
            "order_id": oid, "symbol": symbol, "side": side, "qty": int(qty), "filled_qty": 0, "type": typ,
            "price": None if price is None else dec(price), "trigger": None if trigger is None else dec(trigger),
            "status": "TRIGGER_PENDING" if typ == "sl_limit" else "OPEN", "avg_price": None, "reason": None,
            "tag": tag, "placed_ms": now_ms,
            "placed_seq": self._seq.get(symbol, 0) if seq is None else seq,
        }
        self._orders[oid] = o
        return dict(o)

    def submit_limit(self, symbol, side, qty, price, *, tag="", now_ms=0):
        self._check_fail()
        self._validate(side, qty)
        o = self._new_order(symbol, side, qty, "limit", price, None, tag, now_ms)
        self.calls.append(("submit", o["order_id"]))
        return o

    def submit_sl_limit(self, symbol, side, qty, trigger, price, *, tag="", now_ms=0):
        self._check_fail()
        self._validate(side, qty)
        o = self._new_order(symbol, side, qty, "sl_limit", price, trigger, tag, now_ms)
        self.calls.append(("submit", o["order_id"]))
        return o

    def submit_market(self, symbol, side, qty, *, tag="", now_ms=0):
        self._check_fail()
        if is_option(symbol):
            oid = f"PG-{next(self._ids)}"
            o = {"order_id": oid, "symbol": symbol, "status": "REJECTED",
                 "reason": "market orders not allowed for options"}
            self._orders[oid] = {**o, "side": side, "qty": qty, "filled_qty": 0, "type": "market", "price": None,
                                 "trigger": None, "avg_price": None, "tag": tag, "placed_ms": now_ms, "placed_seq": 0}
            self.calls.append(("submit", oid))
            return dict(o)
        ref = self._ltp.get(symbol)
        if ref is None:
            raise ValueError(f"no price for {symbol}")
        far = ref * (Decimal("1.5") if side == "buy" else Decimal("0.5"))
        o = self._new_order(symbol, side, qty, "limit", far, None, tag, now_ms)
        self.calls.append(("submit", o["order_id"]))
        return o

    def modify(self, order_id, *, price=None, trigger=None):
        o = self._orders[order_id]
        if o["status"] not in OPEN_STATUSES:
            return {"status": "error", "error": f"order {order_id} is {o['status']}"}
        if price is not None:
            o["price"] = dec(price)
        if trigger is not None:
            o["trigger"] = dec(trigger)
        self.calls.append(("modify", order_id))
        return dict(o)

    def cancel(self, order_id):
        o = self._orders.get(order_id)
        if o is None:
            return {"status": "error", "error": "unknown order"}
        if o["status"] in OPEN_STATUSES:
            o["status"] = "CANCELLED"
        self.calls.append(("cancel", order_id))
        return dict(o)

    def place_gtt_oco(self, symbol, side, qty, stop_trigger, stop_price, target_trigger, target_price):
        gid = f"GTT-{next(self._ids)}"
        self._gtts[gid] = {
            "gtt_id": gid, "symbol": symbol, "side": side, "qty": int(qty), "status": "ACTIVE",
            "stop_trigger": dec(stop_trigger), "stop_price": dec(stop_price),
            "target_trigger": dec(target_trigger), "target_price": dec(target_price),
            "placed_seq": self._seq.get(symbol, 0), "order_id": None, "leg": None,
        }
        self.calls.append(("gtt", gid))
        return dict(self._gtts[gid])

    def modify_gtt(self, gtt_id, *, stop_trigger=None, stop_price=None, target_trigger=None, target_price=None):
        g = self._gtts[gtt_id]
        if g["status"] != "ACTIVE":
            return {"status": "error", "error": f"gtt {gtt_id} is {g['status']}"}
        for k, v in (("stop_trigger", stop_trigger), ("stop_price", stop_price),
                     ("target_trigger", target_trigger), ("target_price", target_price)):
            if v is not None:
                g[k] = dec(v)
        self.calls.append(("modify_gtt", gtt_id))
        return dict(g)

    def cancel_gtt(self, gtt_id):
        g = self._gtts.get(gtt_id)
        if g and g["status"] == "ACTIVE":
            g["status"] = "CANCELLED"
        self.calls.append(("cancel_gtt", gtt_id))
        return dict(g) if g else {"status": "error", "error": "unknown gtt"}

    # -- persistence (paper positions survive restarts) ---------------------------
    def to_state(self) -> dict[str, Any]:
        enc = lambda d: {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}  # noqa: E731
        return {
            "next_id": next(self._ids), "seq": self._seq, "ltp": {k: str(v) for k, v in self._ltp.items()},
            "orders": {k: enc(v) for k, v in self._orders.items()},
            "gtts": {k: enc(v) for k, v in self._gtts.items()},
            "pos": {k: {"qty": str(v["qty"]), "avg": str(v["avg"])} for k, v in self._pos.items()},
        }

    @classmethod
    def from_state(cls, st: dict[str, Any], **kw: Any) -> "PaperBroker":
        b = cls(**kw)
        money = {"price", "trigger", "avg_price", "cost", "stop_trigger", "stop_price", "target_trigger", "target_price"}
        dec_d = lambda d: {k: (dec(v) if k in money and v not in (None, "None") else (None if v == "None" else v))  # noqa: E731
                           for k, v in d.items()}
        b._ids = itertools.count(int(st.get("next_id", 1)))
        b._seq = {k: int(v) for k, v in st.get("seq", {}).items()}
        b._ltp = {k: dec(v) for k, v in st.get("ltp", {}).items()}
        b._orders = {k: dec_d(v) for k, v in st.get("orders", {}).items()}
        b._gtts = {k: dec_d(v) for k, v in st.get("gtts", {}).items()}
        b._pos = {k: {"qty": Decimal(v["qty"]), "avg": dec(v["avg"])} for k, v in st.get("pos", {}).items()}
        return b

    def reserve_ids_above(self, highest: int) -> None:
        """Never hand out an id at or below ``highest`` (ids the store already references, e.g. after a crash
        restored an older broker state). Otherwise a new order could be mistaken for an old exit."""
        nxt = next(self._ids)
        self._ids = itertools.count(max(nxt, int(highest) + 1))

    # -- internals ---------------------------------------------------------------
    @staticmethod
    def _validate(side: str, qty: int) -> None:
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy|sell")
        if int(qty) <= 0:
            raise ValueError("qty must be > 0")

    def _try_fill(self, o: dict[str, Any], price: Decimal, ts_ms: int) -> dict[str, Any] | None:
        if o["side"] == "sell":
            px = price * (1 - self.slip)
            if px < o["price"]:
                return None
        else:
            px = price * (1 + self.slip)
            if px > o["price"]:
                return None
        qty = o["qty"] - o["filled_qty"]
        o["filled_qty"] = o["qty"]
        o["avg_price"] = px
        o["status"] = "FILLED"
        signed = Decimal(qty) if o["side"] == "buy" else -Decimal(qty)
        p = self._pos.setdefault(o["symbol"], {"qty": Decimal(0), "avg": px})
        if p["qty"] == 0 or (p["qty"] > 0) == (signed > 0):
            tot = p["qty"] + signed
            p["avg"] = (p["avg"] * abs(p["qty"]) + px * abs(signed)) / abs(tot)
            p["qty"] = tot
        else:
            p["qty"] += signed
        cost = px * qty * self.cost
        o["cost"] = cost
        fill = {"order_id": o["order_id"], "symbol": o["symbol"], "side": o["side"], "qty": qty,
                "price": px, "cost": cost, "ts_ms": ts_ms, "tag": o.get("tag")}
        self.fills.append(fill)
        if str(o.get("tag", "")).startswith("gtt:"):
            gid = o["tag"].split(":", 1)[1]
            if gid in self._gtts:
                self._gtts[gid]["status"] = "COMPLETE"
        return fill
