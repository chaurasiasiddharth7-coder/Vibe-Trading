"""The one broker interface Profit Guard talks to.

``PaperBroker`` implements it today. A live Kite implementation must implement the
same methods and must NOT be added until the go-live gates in the plan pass.

Order dicts are normalized: {order_id, symbol, side ('buy'|'sell'), qty, filled_qty,
type ('limit'|'sl_limit'), price, trigger, status, avg_price, reason, placed_ms}.
Status values: OPEN, TRIGGER_PENDING, FILLED, CANCELLED, REJECTED.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

OPEN_STATUSES = {"OPEN", "TRIGGER_PENDING"}


class BrokerPort(Protocol):
    name: str

    def ltp(self, symbol: str) -> Decimal | None: ...

    def positions(self) -> list[dict[str, Any]]: ...  # [{symbol, qty (signed), avg_price}]

    def open_orders(self) -> list[dict[str, Any]]: ...

    def order(self, order_id: str) -> dict[str, Any] | None: ...

    def submit_limit(self, symbol: str, side: str, qty: int, price: Decimal, *, tag: str = "", now_ms: int = 0) -> dict[str, Any]: ...

    def submit_sl_limit(self, symbol: str, side: str, qty: int, trigger: Decimal, price: Decimal, *, tag: str = "", now_ms: int = 0) -> dict[str, Any]: ...

    def submit_market(self, symbol: str, side: str, qty: int, *, tag: str = "", now_ms: int = 0) -> dict[str, Any]: ...

    def modify(self, order_id: str, *, price: Decimal | None = None, trigger: Decimal | None = None) -> dict[str, Any]: ...

    def cancel(self, order_id: str) -> dict[str, Any]: ...

    def place_gtt_oco(self, symbol: str, side: str, qty: int, stop_trigger: Decimal, stop_price: Decimal,
                      target_trigger: Decimal, target_price: Decimal) -> dict[str, Any]: ...

    def modify_gtt(self, gtt_id: str, *, stop_trigger: Decimal | None = None, stop_price: Decimal | None = None,
                   target_trigger: Decimal | None = None, target_price: Decimal | None = None) -> dict[str, Any]: ...

    def cancel_gtt(self, gtt_id: str) -> dict[str, Any]: ...

    def gtts(self) -> list[dict[str, Any]]: ...


def is_option(symbol: str) -> bool:
    s = symbol.upper()
    return (s.endswith("CE") or s.endswith("PE")) and any(ch.isdigit() for ch in s)
