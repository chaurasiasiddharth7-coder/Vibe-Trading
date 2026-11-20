"""Live prices over Kite's WebSocket (KiteTicker). Streams ticks and order updates; places nothing.

Uses ONE WebSocket connection (Kite allows 3 per API key). KiteTicker reconnects by itself;
we cap its back-off at 30 s and log every reconnect. Order updates are normalized and queued
for reconcile (P6) — with paper trading they only show orders you placed by hand in Kite.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

logger = logging.getLogger(__name__)
STALE_MS = 30_000


@dataclass(frozen=True)
class LiveTick:
    symbol: str
    token: int
    ltp: Decimal
    ts_ms: int


def normalize_order_update(raw: dict[str, Any]) -> dict[str, Any]:
    status = str(raw.get("status") or "").upper()
    filled = int(raw.get("filled_quantity") or 0)
    qty = int(raw.get("quantity") or 0)
    if status not in {"COMPLETE", "REJECTED", "CANCELLED", "OPEN", "TRIGGER PENDING", "UPDATE"}:
        status = status or "UNKNOWN"
    if status == "OPEN" and 0 < filled < qty:
        status = "PARTIAL"
    return {
        "order_id": str(raw.get("order_id") or ""),
        "symbol": str(raw.get("tradingsymbol") or ""),
        "status": status,
        "filled_qty": filled,
        "qty": qty,
        "avg_price": Decimal(str(raw.get("average_price") or 0)),
        "reason": raw.get("status_message"),
    }


class TickFeed:
    """Thread-safe latest-tick store fed by KiteTicker."""

    def __init__(self, api_key: str, access_token: str, *, ticker_factory: Callable[..., Any] | None = None,
                 clock: Callable[[], int] | None = None) -> None:
        if ticker_factory is None:
            from kiteconnect import KiteTicker  # streaming only: KiteTicker has no order methods

            ticker_factory = KiteTicker
        import time as _t

        self._clock = clock or (lambda: int(_t.time() * 1000))
        self._lock = threading.Lock()
        self._latest: dict[str, LiveTick] = {}
        self._tokens: dict[int, str] = {}
        self.order_updates: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.connected = False
        self.reconnects = 0
        self.kws = ticker_factory(api_key, access_token, reconnect=True, reconnect_max_tries=300,
                                  reconnect_max_delay=30)
        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_reconnect = self._on_reconnect
        self.kws.on_order_update = self._on_order_update

    # -- control -----------------------------------------------------------------
    def start(self) -> None:
        self.kws.connect(threaded=True)

    def stop(self) -> None:
        try:
            self.kws.close()
        except Exception:  # noqa: BLE001
            pass

    def subscribe(self, symbols_to_tokens: dict[str, int]) -> None:
        with self._lock:
            for sym, tok in symbols_to_tokens.items():
                self._tokens[int(tok)] = sym
            tokens = list(self._tokens)
        if self.connected and tokens:
            self.kws.subscribe(tokens)
            self.kws.set_mode(self.kws.MODE_LTP, tokens)

    def unsubscribe(self, symbols: list[str]) -> None:
        with self._lock:
            drop = [t for t, s in self._tokens.items() if s in symbols]
            for t in drop:
                self._tokens.pop(t, None)
            for s in symbols:
                self._latest.pop(s, None)
        if self.connected and drop:
            self.kws.unsubscribe(drop)

    # -- reads -----------------------------------------------------------------
    def latest(self, symbol: str) -> LiveTick | None:
        with self._lock:
            return self._latest.get(symbol)

    def is_stale(self, symbol: str, now_ms: int | None = None) -> bool:
        t = self.latest(symbol)
        now = self._clock() if now_ms is None else now_ms
        return t is None or now - t.ts_ms > STALE_MS

    def health(self, now_ms: int | None = None) -> dict[str, Any]:
        now = self._clock() if now_ms is None else now_ms
        with self._lock:
            subs = sorted(self._tokens.values())
            ages = {s: (now - self._latest[s].ts_ms) / 1000 for s in subs if s in self._latest}
        stale = [s for s in subs if s not in ages or ages[s] > STALE_MS / 1000]
        return {"connected": self.connected, "subscribed": subs, "stale_symbols": stale,
                "last_tick_age_s": min(ages.values()) if ages else None, "reconnects": self.reconnects}

    # -- KiteTicker callbacks ----------------------------------------------------
    def _on_connect(self, ws: Any, response: Any) -> None:
        self.connected = True
        with self._lock:
            tokens = list(self._tokens)
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_LTP, tokens)
        logger.info("kite ticker connected; %d instruments", len(tokens))

    def _on_close(self, ws: Any, code: Any, reason: Any) -> None:
        self.connected = False
        logger.warning("kite ticker closed: %s %s", code, reason)

    def _on_reconnect(self, ws: Any, attempts: int) -> None:
        self.reconnects += 1
        logger.warning("kite ticker reconnecting (attempt %s)", attempts)

    def _on_ticks(self, ws: Any, ticks: list[dict[str, Any]]) -> None:
        now = self._clock()
        with self._lock:
            for t in ticks:
                tok = int(t.get("instrument_token", 0))
                sym = self._tokens.get(tok)
                if sym is None or t.get("last_price") is None:
                    continue
                self._latest[sym] = LiveTick(sym, tok, Decimal(str(t["last_price"])), now)

    def _on_order_update(self, ws: Any, data: dict[str, Any]) -> None:
        self.order_updates.put(normalize_order_update(data))
