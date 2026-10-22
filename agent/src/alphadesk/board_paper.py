"""Paper trades for signal-board rows, settled from daily bars.

A trade opens at the row's entry (the last close) with its ATR stop and
target. ``settle_open_trades`` walks the daily bars after the open date:

* stop and target touched in the same bar → stop (conservative)
* neither touched within ``MAX_BARS`` sessions → exit at that bar's close
* ``COST_PCT`` round-trip cost (brokerage, STT, slippage) is charged in R

Closed trades feed the ``signal_board`` survival engine, which is the
board's track record.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.alphadesk.signal_board import ENGINE_ID

logger = logging.getLogger(__name__)

MAX_BARS = 20
COST_PCT = 0.15
DEFAULT_RISK_INR = 1000.0

# Scans run in background threads; serialise ledger writes so a trade can't be
# opened twice or settled (and counted) twice.
_LEDGER_LOCK = threading.Lock()


def _dir() -> Path:
    from src.alphadesk.store import alphadesk_root

    d = alphadesk_root() / "board_paper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Exit styles. Every stop only ratchets in the trade's favour.
EXIT_PLANS: dict[str, dict[str, Any]] = {
    "fixed": {"trail_atr": None, "breakeven_r": None, "use_target": True},
    "breakeven": {"trail_atr": None, "breakeven_r": 1.0, "use_target": True},
    "trail": {"trail_atr": 1.5, "breakeven_r": None, "use_target": True},
    "trail_run": {"trail_atr": 2.0, "breakeven_r": None, "use_target": False},
}
DEFAULT_PLAN = "fixed"


def _plan_for(bar_time: str, plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Plan in effect for a bar: the latest switch made before that session."""
    current = EXIT_PLANS[DEFAULT_PLAN]
    for step in plans:
        since = step.get("from")
        if since is None or bar_time > str(since):
            current = EXIT_PLANS.get(step.get("plan") or DEFAULT_PLAN, current)
    return current


def walk_trade(
    bars: Iterable[dict[str, Any]],
    *,
    side: str,
    entry: float,
    stop: float,
    target: float,
    atr: float | None = None,
    plans: list[dict[str, Any]] | None = None,
    stop_moves: list[dict[str, Any]] | None = None,
    max_bars: int = MAX_BARS,
    cost_pct: float = COST_PCT,
) -> dict[str, Any]:
    """Walk bars after entry. Returns ``{exit, stop_now, best}``; ``exit`` is None while open.

    Each bar is checked against the stop set from *earlier* bars only, then
    the best price and trailing stop are updated for the next bar, so the
    simulation never uses intra-bar order it can't know.
    """
    from src.alphadesk.signal_board import STOP_ATR

    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("stop must differ from entry")
    long = side == "LONG"
    sign = 1 if long else -1
    atr = atr if atr and atr > 0 else risk / STOP_ATR
    plans = plans or [{"plan": DEFAULT_PLAN, "from": None}]
    best = entry
    live_stop = stop  # ratchets only toward the trade
    count = 0
    for bar in bars:
        count += 1
        plan = _plan_for(str(bar["time"]), plans)
        # Hand-set cut-losses apply from the session after they were set and,
        # like every other stop, only ever tighten.
        for move in stop_moves or []:
            if move.get("from") is None or str(bar["time"]) > str(move["from"]):
                price = float(move["price"])
                live_stop = max(live_stop, price) if long else min(live_stop, price)
        high, low = float(bar["high"]), float(bar["low"])
        open_ = float(bar.get("open") or 0) or None
        moved = (live_stop - stop) * sign > 1e-9
        stop_reason = "trail" if moved else "stop"
        hit_stop = low <= live_stop if long else high >= live_stop
        use_target = plan["use_target"]
        hit_target = use_target and (high >= target if long else low <= target)
        # A gap through a level fills at the open, not at the level itself.
        gap_stop = open_ is not None and (open_ <= live_stop if long else open_ >= live_stop)
        gap_target = use_target and open_ is not None and (open_ >= target if long else open_ <= target)
        exit_price: float | None = None
        if gap_stop:
            exit_price, reason = open_, stop_reason
        elif gap_target:
            exit_price, reason = open_, "target"
        elif hit_stop:
            exit_price, reason = live_stop, stop_reason
        elif hit_target:
            exit_price, reason = target, "target"
        elif count >= max_bars:
            exit_price, reason = float(bar["close"]), "time"
        if exit_price is not None:
            gross = (exit_price - entry) * sign
            r = (gross - entry * cost_pct / 100.0) / risk
            return {
                "exit": {
                    "exit_price": round(exit_price, 2),
                    "exit_reason": reason,
                    "exit_date": str(bar["time"]),
                    "bars_held": count,
                    "r_multiple": round(r, 4),
                },
                "stop_now": round(live_stop, 2),
                "best": round(best, 2),
            }
        # Update for the next bar using this (now closed) bar.
        best = max(best, high) if long else min(best, low)
        candidate = stop
        if plan["breakeven_r"] is not None and (best - entry) * sign >= plan["breakeven_r"] * risk:
            candidate = entry
        if plan["trail_atr"] is not None:
            trail = best - sign * plan["trail_atr"] * atr
            candidate = max(candidate, trail) if long else min(candidate, trail)
        live_stop = max(live_stop, candidate) if long else min(live_stop, candidate)
    return {"exit": None, "stop_now": round(live_stop, 2), "best": round(best, 2)}


def simulate_exit(
    bars: Iterable[dict[str, Any]],
    *,
    side: str,
    entry: float,
    stop: float,
    target: float,
    max_bars: int = MAX_BARS,
    cost_pct: float = COST_PCT,
    atr: float | None = None,
    plan: str = DEFAULT_PLAN,
) -> dict[str, Any] | None:
    """Return the exit for a trade given the bars after entry, or None if still open."""
    return walk_trade(
        bars,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        atr=atr,
        plans=[{"plan": plan, "from": None}],
        max_bars=max_bars,
        cost_pct=cost_pct,
    )["exit"]


def _trade_path(trade_id: str) -> Path:
    return _dir() / f"{trade_id}.json"


def _save(trade: dict[str, Any]) -> None:
    """Write via a temp file so a crash mid-write can't leave a half-written trade."""
    path = _trade_path(trade["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(trade, indent=2), encoding="utf-8")
    os.replace(tmp, path)


BACKUPS_KEPT = 14


def backup_ledger(keep: int = BACKUPS_KEPT) -> Path | None:
    """Copy every trade file into a dated folder, once a day, keeping the last ``keep`` days."""
    root = _dir() / "_backups"
    today = root / datetime.now(timezone.utc).date().isoformat()
    made: Path | None = None
    files = list(_dir().glob("*.json"))
    if files and not today.exists():
        today.mkdir(parents=True)
        for f in files:
            shutil.copy2(f, today / f.name)
        made = today
    if root.is_dir():
        for old in sorted(p for p in root.iterdir() if p.is_dir())[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
    return made


SUSPECT_MOVE_PCT = 15.0


def suspect_move(bars: list[dict[str, Any]], *, after: str | None, until: str | None = None) -> dict[str, Any] | None:
    """Largest one-day close-to-close jump over ``SUSPECT_MOVE_PCT`` inside the trade's window.

    NSE circuit limits keep most stocks under this, so a bigger jump is more likely a
    split, bonus or bad data point than a real move.
    """
    worst: dict[str, Any] | None = None
    for prev, bar in zip(bars, bars[1:]):
        t = str(bar["time"])
        if after is not None and t <= str(after):
            continue
        if until is not None and t > str(until):
            continue
        base = float(prev["close"])
        if base <= 0:
            continue
        change = (float(bar["close"]) / base - 1) * 100
        if abs(change) >= SUSPECT_MOVE_PCT and (worst is None or abs(change) > abs(worst["change_pct"])):
            worst = {"date": t, "change_pct": round(change, 1)}
    return worst


def list_trades() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in _dir().glob("*.json"):
        if p.name.startswith("."):  # macOS "._x.json" resource-fork files from tar/rsync copies
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            # One unreadable file must not take the whole dashboard down.
            logger.warning("skipping unreadable trade file %s", p.name, exc_info=True)
    out.sort(key=lambda t: t.get("opened_at") or "", reverse=True)
    return out


def open_trade(
    row: dict[str, Any], *, risk_inr: float = DEFAULT_RISK_INR, plan: str = DEFAULT_PLAN
) -> dict[str, Any]:
    """Open a paper trade from a board row. Size so a stop-out loses ``risk_inr``."""
    if plan not in EXIT_PLANS:
        raise ValueError(f"unknown exit plan {plan!r}")
    with _LEDGER_LOCK:
        return _open_trade(row, risk_inr=risk_inr, plan=plan)


def _open_trade(row: dict[str, Any], *, risk_inr: float, plan: str) -> dict[str, Any]:
    side = str(row.get("side") or "")
    if side not in {"LONG", "SHORT"}:
        raise ValueError("only LONG or SHORT rows can be paper traded")
    entry, stop, target = row.get("entry"), row.get("stop"), row.get("target")
    if not entry or not stop or not target:
        raise ValueError("row has no entry / stop / target")
    symbol = str(row["symbol"])
    if any(t["symbol"] == symbol and t["status"] == "OPEN" for t in list_trades()):
        raise ValueError(f"{symbol} already has an open paper trade")
    risk_per_share = abs(float(entry) - float(stop))
    qty = max(1, math.floor(float(risk_inr) / risk_per_share))
    trade = {
        "id": str(uuid.uuid4()),
        "engine_id": ENGINE_ID,
        "symbol": symbol,
        "side": side,
        "entry": float(entry),
        "stop": float(stop),
        "target": float(target),
        "qty": qty,
        "risk_inr": round(qty * risk_per_share, 2),
        "opened_at": _now(),
        "opened_bar": row.get("as_of"),
        "votes": row.get("votes"),
        "status": "OPEN",
        "atr": row.get("atr"),
        "plans": [{"plan": plan, "from": None}],
        # Snapshot of why the board picked it, so the reasoning survives
        # after the board changes its mind.
        "decision": {
            "name": row.get("name"),
            "industry": row.get("industry"),
            "score": row.get("score"),
            "reasons": row.get("reasons") or [],
            "cautions": row.get("cautions") or [],
            "rsi": row.get("rsi"),
            "atr": row.get("atr"),
            "kronos_change_pct": row.get("kronos_change_pct"),
            "risk_pct": row.get("risk_pct"),
            "history": (row.get("history") or {}).get("symbol_side"),
            "side_history": (row.get("history") or {}).get("side"),
        },
    }
    _save(trade)
    return trade


def close_trade(trade: dict[str, Any], exit_: dict[str, Any]) -> dict[str, Any]:
    from src.alphadesk.store import record_outcome
    from src.alphadesk.survival import apply_outcome_to_engine

    risk_per_share = abs(trade["entry"] - trade["stop"])
    pnl = round(exit_["r_multiple"] * risk_per_share * trade["qty"], 2)
    trade = {**trade, **exit_, "pnl_inr": pnl, "status": "CLOSED", "closed_at": _now()}
    _save(trade)
    record_outcome(
        trade["id"],
        {
            "engine_id": ENGINE_ID,
            "r_multiple": exit_["r_multiple"],
            "pnl_inr": pnl,
            "symbol": trade["symbol"],
            "source": "board_paper",
            "notes": f"{trade['side']} exit={exit_['exit_reason']}",
        },
    )
    apply_outcome_to_engine(ENGINE_ID, r_multiple=exit_["r_multiple"], pnl_inr=pnl)
    return trade


def settle_open_trades(fetch_bars=None) -> list[dict[str, Any]]:
    """Close any open trade whose stop / target / time limit was reached."""
    with _LEDGER_LOCK:
        try:
            backup_ledger()
        except OSError:
            logger.warning("ledger backup failed", exc_info=True)
        return _settle_open_trades(fetch_bars)


def _settle_open_trades(fetch_bars=None) -> list[dict[str, Any]]:
    if fetch_bars is None:
        from src.alphadesk.analysis import analyze_symbol

        def fetch_bars(sym: str) -> list[dict[str, Any]]:
            return analyze_symbol(sym, period="3mo", interval="1d")["chart_data"]

    closed: list[dict[str, Any]] = []
    for trade in list_trades():
        if trade.get("status") != "OPEN":
            continue
        bars = [b for b in fetch_bars(trade["symbol"]) if str(b["time"]) > str(trade["opened_bar"])]
        exit_ = _walk(trade, bars)["exit"]
        if exit_ is not None:
            closed.append(close_trade(trade, exit_))
    return closed


def _walk(trade: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    return walk_trade(
        bars,
        side=trade["side"],
        entry=trade["entry"],
        stop=trade["stop"],
        target=trade["target"],
        atr=trade.get("atr"),
        plans=trade.get("plans"),
        stop_moves=trade.get("stop_moves"),
    )


def current_plan(trade: dict[str, Any]) -> str:
    plans = trade.get("plans") or []
    return str(plans[-1]["plan"]) if plans else DEFAULT_PLAN


def set_plan(trade_id: str, plan: str) -> dict[str, Any]:
    """Switch an open trade's exit style from the next session onward."""
    from src.alphadesk.signal_board import expected_session

    if plan not in EXIT_PLANS:
        raise ValueError(f"unknown exit plan {plan!r}")
    try:
        trade_id = str(uuid.UUID(trade_id))
    except ValueError as exc:
        raise FileNotFoundError(trade_id) from exc
    with _LEDGER_LOCK:
        path = _trade_path(trade_id)
        if not path.is_file():
            raise FileNotFoundError(trade_id)
        trade = json.loads(path.read_text(encoding="utf-8"))
        if trade.get("status") != "OPEN":
            raise ValueError("only open trades can change exit style")
        if current_plan(trade) == plan:
            return trade
        plans = list(trade.get("plans") or [{"plan": DEFAULT_PLAN, "from": None}])
        plans.append({"plan": plan, "from": expected_session(), "set_at": _now()})
        trade = {**trade, "plans": plans}
        _save(trade)
        return trade


def _load_open(trade_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        trade_id = str(uuid.UUID(trade_id))
    except ValueError as exc:
        raise FileNotFoundError(trade_id) from exc
    path = _trade_path(trade_id)
    if not path.is_file():
        raise FileNotFoundError(trade_id)
    trade = json.loads(path.read_text(encoding="utf-8"))
    if trade.get("status") != "OPEN":
        raise ValueError("only open trades can be changed")
    return path, trade


def set_stop(trade_id: str, price: float, load_bars=None) -> dict[str, Any]:
    """Tighten an open trade's cut-loss by hand, from the next session onward."""
    from src.alphadesk.signal_board import expected_session

    with _LEDGER_LOCK:
        _, trade = _load_open(trade_id)
        live = with_live_prices([trade], load_bars)[0]
        now, stop_now = live.get("current_price"), live.get("stop_now")
        if now is None or stop_now is None:
            raise ValueError("no price available to check the new cut-loss against")
        long = trade["side"] == "LONG"
        price = round(float(price), 2)
        if long and not stop_now < price < now:
            raise ValueError(f"for a buy the cut-loss must be above {stop_now:.2f} and below the price now ({now:.2f})")
        if not long and not now < price < stop_now:
            raise ValueError(f"for a short the cut-loss must be below {stop_now:.2f} and above the price now ({now:.2f})")
        moves = list(trade.get("stop_moves") or [])
        moves.append({"price": price, "from": expected_session(), "set_at": _now()})
        trade = {**trade, "stop_moves": moves}
        _save(trade)
        return trade


def exit_now(trade_id: str, load_bars=None) -> dict[str, Any]:
    """Close an open trade at the latest close (or at its stop, if that was already hit)."""
    with _LEDGER_LOCK:
        _, trade = _load_open(trade_id)
        bars = [b for b in _bars(trade["symbol"], load_bars) if str(b["time"]) > str(trade.get("opened_bar"))]
        if not bars:
            raise ValueError("no price after the trade opened yet")
        exit_ = _walk(trade, bars)["exit"]
        if exit_ is None:
            last = bars[-1]
            sign = 1 if trade["side"] == "LONG" else -1
            risk = abs(trade["entry"] - trade["stop"])
            price = float(last["close"])
            r = ((price - trade["entry"]) * sign - trade["entry"] * COST_PCT / 100.0) / risk
            exit_ = {
                "exit_price": round(price, 2),
                "exit_reason": "manual",
                "exit_date": str(last["time"]),
                "bars_held": len(bars),
                "r_multiple": round(r, 4),
            }
        return close_trade(trade, exit_)


def _bars(symbol: str, load_bars=None) -> list[dict[str, Any]]:
    if load_bars is None:
        from src.alphadesk.signal_board import _load_analysis

        return _load_analysis(symbol).get("chart_data") or []
    return load_bars(symbol)


def review_closed(trade: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Plain-English post-mortem: how far it ran, and what each exit style would have done."""
    after = [b for b in bars if str(b["time"]) > str(trade.get("opened_bar"))]
    exit_date = trade.get("exit_date")
    if exit_date:
        after = [b for b in after if str(b["time"]) <= str(exit_date)]
    risk = abs(trade["entry"] - trade["stop"])
    if not after or risk <= 0:
        return None
    sign = 1 if trade["side"] == "LONG" else -1
    entry = trade["entry"]
    fav = max(((b["high"] if sign == 1 else -b["low"]) - (entry if sign == 1 else -entry)) / risk for b in after)
    adv = max(((entry - b["low"]) if sign == 1 else (b["high"] - entry)) / risk for b in after)
    best_r = max(0.0, fav)
    gap = None
    first = after[0]
    open0 = float(first.get("open") or 0) or None
    if open0 is not None and ((open0 - entry) * sign) / risk <= -0.8:
        gap = open0
    full = after
    # Replay on every bar up to the exit so each style is judged on the same days.
    alt: dict[str, float | None] = {}
    for key in EXIT_PLANS:
        res = walk_trade(
            full, side=trade["side"], entry=entry, stop=trade["stop"], target=trade["target"],
            atr=trade.get("atr"), plans=[{"plan": key, "from": None}],
        )["exit"]
        alt[key] = res["r_multiple"] if res else None
    actual = trade.get("r_multiple") or 0.0
    reason = trade.get("exit_reason")
    best_alt = max((v for v in alt.values() if v is not None), default=actual)
    if reason in {"stop", "trail"} and gap is not None:
        what = f"Price opened {abs(gap - entry) / entry * 100:.1f}% against you the next morning, past your cut-loss, so it filled at the open."
        lesson = "Nothing in the exit rules could have helped. Overnight gaps skip the cut-loss. The lesson is about entry timing: avoid opening right before a stock is already stretched or news is due."
    elif reason in {"stop", "trail"} and best_r >= 0.5:
        what = f"It moved your way by {best_r:.1f}× the amount you risked, then reversed all the way to the cut-loss."
        better = alt.get("trail")
        lesson = (
            f"A trailing cut-loss would have turned this into {better:+.2f}R instead of {actual:+.2f}R."
            if better is not None and better > actual + 0.1
            else "A moving cut-loss would not have changed the result here."
        ) + " The idea was right for a day or two, the exit plan gave the gain back."
    elif reason in {"stop", "trail"}:
        what = f"It never really worked. The best it got was {best_r:.1f}× the amount you risked in your favour, and the cut-loss was hit on trading day {trade.get('bars_held')}."
        lesson = "No exit style fixes a trade that goes the wrong way from the start. The lesson is in the entry: the signals agreed, but price didn't follow."
    elif reason == "target":
        extra = best_r - (abs(trade["target"] - entry) / risk)
        what = "It reached take-profit."
        if extra >= 1.0:
            lesson = f"Price kept going to about {best_r:.1f}R. Take-profit left roughly {extra:.1f}R on the table. A trailing style without a take-profit would have kept more, with more give-back risk."
        else:
            lesson = "This worked as planned."
    else:
        what = f"It went nowhere for 20 trading days and was closed at the end ({actual:+.2f}R)."
        lesson = "Slow trades tie up money. If most trades end like this, the signal is weak."
    return {
        "best_move_r": round(best_r, 2),
        "worst_move_r": round(adv, 2),
        "what_happened": what,
        "lesson": lesson,
        "if_exit_style": {k: (round(v, 2) if v is not None else None) for k, v in alt.items()},
        "best_style": max(alt, key=lambda k: alt[k] if alt[k] is not None else -9) if best_alt != actual else None,
    }


def suggest_exit(trade: dict[str, Any], price: float, stop_now: float, best: float) -> dict[str, Any]:
    """One plain suggestion for an open trade. Informational only: nothing is changed."""
    sign = 1 if trade["side"] == "LONG" else -1
    risk = abs(trade["entry"] - trade["stop"])
    if risk <= 0:
        return {"action": "hold", "text": "No suggestion."}
    r_now = (price - trade["entry"]) * sign / risk
    r_best = (best - trade["entry"]) * sign / risk
    plan = trade.get("plan") or DEFAULT_PLAN
    entry = trade["entry"]
    if r_best >= 1.0 and r_now <= r_best - 0.6:
        return {
            "action": "exit",
            "text": f"It was up {r_best:.1f}× your risk and has given back most of it (now {r_now:+.1f}×). Consider taking what is left.",
        }
    if r_now >= 1.0 and plan == "fixed":
        return {
            "action": "trail",
            "text": f"Up {r_now:.1f}× your risk with a fixed cut-loss. Switch to a trailing cut-loss, or move it to {entry:.2f} (your entry) so this can't become a loss.",
            "stop": round(entry, 2),
        }
    if r_now >= 0.5 and plan == "fixed" and (stop_now - entry) * sign < 0:
        return {
            "action": "breakeven",
            "text": f"Up {r_now:.1f}× your risk. Protect at break-even once it reaches 1×.",
        }
    if r_now <= -0.7:
        return {
            "action": "hold",
            "text": f"Close to the cut-loss ({r_now:+.1f}×). The plan is to let the cut-loss do its job. Exit by hand only if you think the reason for the trade is gone.",
        }
    return {"action": "hold", "text": f"Nothing to change ({r_now:+.1f}× your risk). Let the plan run."}


def with_live_prices(trades: list[dict[str, Any]], load_bars=None) -> list[dict[str, Any]]:
    """Add price now, running result, and the live (possibly trailed) stop to open trades."""
    if load_bars is None:
        from src.alphadesk.signal_board import _load_analysis

        def load_bars(sym: str) -> list[dict[str, Any]]:
            return _load_analysis(sym).get("chart_data") or []

    out: list[dict[str, Any]] = []
    for t in trades:
        enriched = {**t, "plan": current_plan(t)}
        if t.get("status") != "OPEN":
            try:
                closed_bars = load_bars(t["symbol"])
                enriched["review"] = review_closed(t, closed_bars)
                enriched["data_warning"] = suspect_move(closed_bars, after=t.get("opened_bar"), until=t.get("exit_date"))
            except Exception:
                pass
            out.append(enriched)
            continue
        try:
            bars = load_bars(t["symbol"])
        except Exception:
            out.append(enriched)
            continue
        if not bars:
            out.append(enriched)
            continue
        price = float(bars[-1]["close"])
        walk = _walk(t, [b for b in bars if str(b["time"]) > str(t.get("opened_bar"))])
        move = (price - t["entry"]) if t["side"] == "LONG" else (t["entry"] - price)
        suggestion = suggest_exit(enriched, price, walk["stop_now"], walk["best"])
        enriched["data_warning"] = suspect_move(bars, after=t.get("opened_bar"))
        out.append(
            {
                **enriched,
                "suggestion": suggestion,
                "current_price": round(price, 2),
                "price_as_of": bars[-1].get("time"),
                "unrealized_inr": round(move * t["qty"], 2),
                "stop_now": walk["stop_now"],
                "best_price": walk["best"],
                "locked_in_inr": round(
                    ((walk["stop_now"] - t["entry"]) if t["side"] == "LONG" else (t["entry"] - walk["stop_now"]))
                    * t["qty"],
                    2,
                ),
            }
        )
    return out
