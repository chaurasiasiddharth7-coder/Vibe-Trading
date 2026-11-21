"""Bridge: hands the desk's strategies to the Profit Guard, so it has real (paper) positions to guard.

Sources, all paper:
  * **Overnight (BTST) pick.** The 15:20 scan writes today's pick to the BTST day file. The guard buys it at the
    live price as a carry position with the pick's stop. The +1% target is NOT used (the P7 backtest showed the
    +1% / -2% exit loses money); the guard's tiered profit lock manages the exit, and a wide target only sets the
    overnight GTT backstop.
  * **Signal board practice trades** (the Vibe-Trading strategy, "Today's ideas"): every OPEN trade in the
    practice ledger is mirrored once, with its current stop and its target, and then managed on live prices.
    Read from this Mac's ledger, and from the hosted desk too when DESK_API_KEY is set in agent/.env.

Every hand-over goes through the guard's inbox (the runner stays the store's only writer) and is remembered in the
store, so a source item is never entered twice, even across restarts. Nothing here can place a real order.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": True,
    "btst": True,
    "signal_board": True,
    "risk_per_trade_pct": 0.5,   # a stop-out loses at most this % of capital
    "max_position_pct": 20.0,    # and no position is bigger than this % of capital
    "btst_backstop_target_pct": 5.0,
    "poll_seconds": 60,
}
_META_KEY = "bridge_done"


def settings(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(raw_cfg.get("bridge") or {})}


def size(capital: Decimal, entry: float, stop: float, s: dict[str, Any]) -> int:
    """Shares so a stop-out costs at most risk_per_trade_pct of capital, capped at max_position_pct of capital."""
    risk_ps = abs(entry - stop)
    if entry <= 0 or risk_ps <= 0:
        return 0
    by_risk = math.floor(float(capital) * s["risk_per_trade_pct"] / 100 / risk_ps)
    by_value = math.floor(float(capital) * s["max_position_pct"] / 100 / entry)
    return max(0, min(by_risk, by_value))


# --- sources: each returns a list of (key, entry request) -----------------------------------------------


def btst_requests(today: str, capital: Decimal, s: dict[str, Any],
                  load_day: Callable[[date], dict | None] | None = None) -> list[tuple[str, dict[str, Any]]]:
    if load_day is None:
        from src.alphadesk.btst import days

        def load_day(d: date) -> dict | None:
            return days.load_day(d, "live")

    rec = load_day(date.fromisoformat(today))
    pick = (rec or {}).get("pick")
    if not rec or rec.get("status") != "picked" or not pick:
        return []
    entry, stop = float(pick.get("entry") or 0), float(pick.get("stop") or 0)
    if not entry or not stop or stop >= entry:
        return []
    qty = size(capital, entry, stop, s)
    if qty < 1:
        return []
    return [(f"btst:{today}:{pick['symbol']}", {
        "strategy": "btst", "symbol": pick["symbol"], "side": "LONG", "qty": qty, "carry": True,
        "stop": stop, "target_pct": s["btst_backstop_target_pct"], "source": "overnight pick",
    })]


def _ledger_rows(fetch_remote: Callable[[], list[dict]] | None = None,
                 read_local: Callable[[], list[dict]] | None = None) -> list[dict]:
    rows: list[dict] = []
    if read_local is None:
        def read_local() -> list[dict]:
            from src.alphadesk.board_paper import list_trades

            return list_trades()
    try:
        rows += [{**t, "_where": "local"} for t in read_local()]
    except Exception as exc:  # noqa: BLE001 - one source failing must not stop the other
        logger.info("bridge: local ledger unreadable: %s", exc)
    if fetch_remote is None:
        fetch_remote = remote_ledger
    try:
        rows += [{**t, "_where": "hosted"} for t in fetch_remote()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("bridge: hosted ledger unreadable: %s", exc)
    return rows


def remote_ledger() -> list[dict]:
    """The hosted desk's practice trades (owner key from agent/.env). Empty when no key is set."""
    key = os.environ.get("DESK_API_KEY", "").strip()
    if not key:
        return []
    import httpx

    url = os.environ.get("DESK_API_URL", "https://desk.fairscoreguide.com").rstrip("/") + "/api/desk/board/paper"
    r = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    r.raise_for_status()
    body = r.json()
    trades = body.get("trades")
    if trades is None and isinstance(body.get("data"), dict):
        trades = body["data"].get("trades")
    return trades if isinstance(trades, list) else []


def ledger_requests(capital: Decimal, s: dict[str, Any], rows: list[dict] | None = None) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for t in rows if rows is not None else _ledger_rows():
        if t.get("status") != "OPEN" or t.get("side") not in ("LONG", "SHORT"):
            continue
        stop = float(t.get("stop_now") or t.get("stop") or 0)
        target = float(t.get("target") or 0) or None
        entry = float(t.get("current_price") or t.get("entry") or 0)
        if not stop or not entry:
            continue
        qty = int(t.get("qty") or 0) or size(capital, entry, stop, s)
        cap = math.floor(float(capital) * s["max_position_pct"] / 100 / entry) if entry else 0
        qty = min(qty, cap)
        if qty < 1:
            continue
        strategy = "lab" if t.get("strategy_id") else "signal_board"
        if strategy == "signal_board" and t.get("side") != "LONG":
            continue  # signal board is longs only since the 21 Nov 2026 audit (shorts lost -326R over 5 years)
        out.append((f"ledger:{t.get('_where', 'local')}:{t['id']}", {
            "strategy": strategy, "symbol": t["symbol"], "side": t["side"], "qty": qty, "carry": True,
            "stop": stop, "target": target, "source": t.get("strategy_name") or "signal board",
        }))
    return out


# --- the bridge, called by the runner ---------------------------------------------------------------------


@dataclass
class Bridge:
    raw_cfg: dict[str, Any]
    get_meta: Callable[[str], str | None]
    set_meta: Callable[[str, str], None]
    queue: Callable[[dict[str, Any]], None]
    held: Callable[[], set[str]]
    btst_source: Callable[..., list] = btst_requests
    ledger_source: Callable[..., list] = ledger_requests
    _last_ms: int = 0
    log: list[str] = field(default_factory=list)

    def _done(self) -> dict[str, str]:
        try:
            v = json.loads(self.get_meta(_META_KEY) or "{}")
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def poll(self, now_ms: int, phase: str, today: str) -> list[str]:
        """Queue anything new. Only in market phases, at most once per poll_seconds."""
        s = settings(self.raw_cfg)
        if not s["enabled"] or phase not in ("open", "no_new_entry", "square_off"):
            return []
        if now_ms - self._last_ms < s["poll_seconds"] * 1000:
            return []
        self._last_ms = now_ms
        capital = Decimal(str(self.raw_cfg.get("capital_inr", 0)))
        found: list[tuple[str, dict[str, Any]]] = []
        if s["btst"]:
            try:
                found += self.btst_source(today, capital, s)
            except Exception as exc:  # noqa: BLE001
                logger.warning("bridge: BTST source failed: %s", exc)
        if s["signal_board"] and phase == "open":  # swing ideas go in during normal hours only
            try:
                found += self.ledger_source(capital, s)
            except Exception as exc:  # noqa: BLE001
                logger.warning("bridge: ledger source failed: %s", exc)
        done = self._done()
        held = self.held()
        queued = []
        for key, req in found:
            if key in done:
                continue
            if req["symbol"].upper() in held:
                done[key] = f"skipped {today}: already held"
                continue
            self.queue(req)
            done[key] = f"queued {today}"
            held.add(req["symbol"].upper())
            queued.append(f"{req['source']}: {req['side']} {req['qty']} {req['symbol']}")
        if queued or len(done) != len(self._done()):
            if len(done) > 2000:  # keep the newest
                done = dict(list(done.items())[-1500:])
            self.set_meta(_META_KEY, json.dumps(done))
        self.log += queued
        return queued
