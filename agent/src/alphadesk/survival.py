"""Paper sleeves, engine tiers, scorecard, book HWM / daily DD → HALT."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.alphadesk.store import alphadesk_root

SurvivalStatus = Literal["ALIVE", "PROBATION", "DEAD"]

DEFAULT_SLEEVE_INR = 100_000.0
MIN_TRADES_FOR_DEATH = 30
DAILY_DD_HALT = 0.02
HWM_DD_HALT = 0.15


def _survival_dir() -> Path:
    d = alphadesk_root() / "survival"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _engine_path(engine_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in engine_id)
    return _survival_dir() / f"{safe}.json"


def _book_path() -> Path:
    return _survival_dir() / "book.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_engine_state(engine_id: str) -> dict[str, Any]:
    return {
        "engine_id": engine_id,
        "status": "ALIVE",
        "sleeve_inr": DEFAULT_SLEEVE_INR,
        "initial_sleeve_inr": DEFAULT_SLEEVE_INR,
        "closed_trades": 0,
        "sum_r": 0.0,
        "wins": 0,
        "losses": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "updated_at": _utc_now(),
    }


def load_engine(engine_id: str) -> dict[str, Any]:
    path = _engine_path(engine_id)
    if not path.is_file():
        state = default_engine_state(engine_id)
        save_engine(state)
        return state
    return json.loads(path.read_text(encoding="utf-8"))


def save_engine(state: dict[str, Any]) -> Path:
    path = _engine_path(str(state["engine_id"]))
    state = {**state, "updated_at": _utc_now()}
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def list_engines() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_survival_dir().glob("*.json")):
        if path.name == "book.json":
            continue
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def ensure_engines(engine_ids: list[str]) -> list[dict[str, Any]]:
    return [load_engine(eid) for eid in engine_ids]


def expectancy_r(state: dict[str, Any]) -> float | None:
    n = int(state.get("closed_trades") or 0)
    if n <= 0:
        return None
    return float(state.get("sum_r") or 0) / n


def profit_factor(state: dict[str, Any]) -> float | None:
    gp = float(state.get("gross_profit") or 0)
    gl = abs(float(state.get("gross_loss") or 0))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return gp / gl


def scorecard_row(state: dict[str, Any]) -> dict[str, Any]:
    n = int(state.get("closed_trades") or 0)
    wins = int(state.get("wins") or 0)
    exp = expectancy_r(state)
    pf = profit_factor(state)
    return {
        "engine_id": state.get("engine_id"),
        "status": state.get("status"),
        "sleeve_inr": round(float(state.get("sleeve_inr") or 0), 2),
        "closed_trades": n,
        "win_rate": round(wins / n, 4) if n else None,
        "expectancy_r": round(exp, 4) if exp is not None else None,
        "profit_factor": (
            None
            if pf is None
            else (round(pf, 4) if pf != float("inf") else None)
        ),
        "profit_factor_inf": pf == float("inf"),
        "live_unlock_eligible": bool(
            n >= MIN_TRADES_FOR_DEATH
            and exp is not None
            and exp > 0
            and state.get("status") == "ALIVE"
        ),
    }


def apply_outcome_to_engine(
    engine_id: str,
    *,
    r_multiple: float,
    pnl_inr: float,
) -> dict[str, Any]:
    """Update sleeve + stats; mark DEAD if sleeve depleted or expectancy ≤ 0 at N≥30."""
    state = load_engine(engine_id)
    if state.get("status") == "DEAD":
        return state

    state["closed_trades"] = int(state.get("closed_trades") or 0) + 1
    state["sum_r"] = float(state.get("sum_r") or 0) + float(r_multiple)
    sleeve = float(state.get("sleeve_inr") or 0) + float(pnl_inr)
    state["sleeve_inr"] = max(0.0, sleeve)

    if pnl_inr >= 0:
        state["wins"] = int(state.get("wins") or 0) + 1
        state["gross_profit"] = float(state.get("gross_profit") or 0) + pnl_inr
    else:
        state["losses"] = int(state.get("losses") or 0) + 1
        state["gross_loss"] = float(state.get("gross_loss") or 0) + pnl_inr

    n = int(state["closed_trades"])
    exp = expectancy_r(state)
    if state["sleeve_inr"] <= 0:
        state["status"] = "DEAD"
        state["death_reason"] = "sleeve_depleted"
    elif n >= MIN_TRADES_FOR_DEATH and exp is not None and exp <= 0:
        state["status"] = "DEAD"
        state["death_reason"] = "expectancy_non_positive"
    elif n >= 10 and exp is not None and exp <= 0:
        state["status"] = "PROBATION"
    elif state.get("status") == "PROBATION" and exp is not None and exp > 0:
        state["status"] = "ALIVE"

    save_engine(state)
    return state


def reset_engine(engine_id: str, *, sleeve_inr: float = DEFAULT_SLEEVE_INR) -> dict[str, Any]:
    """Human-only revive: new sleeve, clear death (Constitution: no auto top-up)."""
    state = default_engine_state(engine_id)
    state["sleeve_inr"] = float(sleeve_inr)
    state["initial_sleeve_inr"] = float(sleeve_inr)
    save_engine(state)
    return state


def load_book() -> dict[str, Any]:
    path = _book_path()
    if not path.is_file():
        book = {
            "equity": 0.0,
            "hwm": 0.0,
            "day_start_equity": 0.0,
            "day_key": "",
            "updated_at": _utc_now(),
        }
        path.write_text(json.dumps(book, indent=2), encoding="utf-8")
        return book
    return json.loads(path.read_text(encoding="utf-8"))


def save_book(book: dict[str, Any]) -> Path:
    path = _book_path()
    book = {**book, "updated_at": _utc_now()}
    path.write_text(json.dumps(book, indent=2), encoding="utf-8")
    return path


def update_book_equity(equity: float) -> dict[str, Any]:
    """Update HWM / day baseline; trip HALT on DD breaches."""
    book = load_book()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if book.get("day_key") != today:
        book["day_key"] = today
        book["day_start_equity"] = float(equity)
    book["equity"] = float(equity)
    hwm = max(float(book.get("hwm") or 0), float(equity))
    book["hwm"] = hwm
    save_book(book)

    day_start = float(book.get("day_start_equity") or equity)
    halt_reason: str | None = None
    if day_start > 0 and (day_start - equity) / day_start >= DAILY_DD_HALT:
        halt_reason = f"daily_dd>={DAILY_DD_HALT:.0%} from day start"
    if hwm > 0 and (hwm - equity) / hwm >= HWM_DD_HALT:
        halt_reason = f"hwm_dd>={HWM_DD_HALT:.0%} from high-water mark"

    if halt_reason:
        try:
            from src.live.halt import trip_halt

            trip_halt("frontend", f"AlphaDesk survival: {halt_reason}")
        except Exception:
            # Fail-closed: still record intent even if live module unavailable
            flag = _survival_dir() / "HALT_REQUESTED.json"
            flag.write_text(
                json.dumps({"reason": halt_reason, "at": _utc_now()}, indent=2),
                encoding="utf-8",
            )
        book["halt_reason"] = halt_reason
        save_book(book)
    return book


def heartbeat() -> dict[str, Any]:
    """Scorecard refresh + ensure default engines + book snapshot."""
    from src.alphadesk.constitution import ensure_constitution_files

    paths = ensure_constitution_files()
    engines = ensure_engines(
        [
            "pm",
            "debate",
            "br_pdh_pdl",
            "br_order_block",
            "br_orb",
            "sentiment",
            "alpha_forge",
        ]
    )
    book = load_book()
    return {
        "constitution": paths,
        "engines": [scorecard_row(e) for e in engines],
        "book": book,
        "halted": (_survival_dir() / "HALT_REQUESTED.json").is_file(),
    }


def is_engine_tradable(engine_id: str) -> bool:
    state = load_engine(engine_id)
    return state.get("status") != "DEAD" and float(state.get("sleeve_inr") or 0) > 0
