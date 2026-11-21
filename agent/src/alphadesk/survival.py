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
        "replay": state.get("replay"),
        "death_reason": state.get("death_reason"),
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
    replay: bool = False,
) -> dict[str, Any]:
    """Update sleeve + stats; mark DEAD if the sleeve is gone, or after 30+ trades the average R is below
    zero with 95% confidence (the desk's one kill-switch rule, see ``health.judge``).

    A DEAD engine keeps counting: its record must stay true even while it is switched off (until
    21 Nov 2026 a dead engine stopped updating, so the signal board's record froze at 30 trades)."""
    from src.alphadesk.health import judge

    state = load_engine(engine_id)
    if replay:
        # Backtest replays are tests, not a track record: kept beside the live stats, never mixed in.
        rp = state.setdefault("replay", {"trades": 0, "sum_r": 0.0, "pnl_inr": 0.0})
        rp["trades"] = int(rp["trades"]) + 1
        rp["sum_r"] = round(float(rp["sum_r"]) + float(r_multiple), 4)
        rp["pnl_inr"] = round(float(rp["pnl_inr"]) + float(pnl_inr), 2)
        save_engine(state)
        return state
    was_dead = state.get("status") == "DEAD"
    state.setdefault("r_list", [])
    state["r_list"] = [*state["r_list"], round(float(r_multiple), 4)][-500:]

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
    verdict = judge(state["r_list"], MIN_TRADES_FOR_DEATH)
    if was_dead:
        pass  # only a person revives an engine (reset_engine); the stats above still update
    elif state["sleeve_inr"] <= 0:
        state["status"] = "DEAD"
        state["death_reason"] = "sleeve_depleted"
    elif verdict["status"] == "pause":
        state["status"] = "DEAD"
        state["death_reason"] = "losing_with_95pct_confidence"
        state["death_note"] = verdict["reason"]
    elif n >= 10 and exp is not None and exp <= 0:
        state["status"] = "PROBATION"
    elif state.get("status") == "PROBATION" and exp is not None and exp > 0:
        state["status"] = "ALIVE"

    save_engine(state)
    return state


def rebuild_engine(engine_id: str, outcomes: list[tuple[float, float]]) -> dict[str, Any]:
    """Recompute an engine from its full ledger ``[(r, pnl_inr), ...]`` under the current rule."""
    state = default_engine_state(engine_id)
    save_engine(state)
    for r, pnl in outcomes:
        apply_outcome_to_engine(engine_id, r_multiple=r, pnl_inr=pnl)
    return load_engine(engine_id)


REPLAY_SOURCES = {"backtest_paper", "intraday_backtest_oos"}


def split_replays_from_outcomes() -> list[str]:
    """One-off repair: engines whose recorded outcomes came from backtest replays get those moved
    to their ``replay`` block, and their live stats rebuilt from live outcomes only."""
    from src.alphadesk.store import alphadesk_root

    by_engine: dict[str, list[dict[str, Any]]] = {}
    for p in (alphadesk_root() / "outcomes").glob("*.json"):
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if o.get("engine_id") and o.get("r_multiple") is not None:
            by_engine.setdefault(str(o["engine_id"]), []).append(o)
    fixed = []
    for eid, outs in by_engine.items():
        if eid == "signal_board" or not any(o.get("source") in REPLAY_SOURCES for o in outs):
            continue
        outs.sort(key=lambda o: str(o.get("recorded_at") or o.get("created_at") or ""))
        status = load_engine(eid).get("status")
        live = [(float(o["r_multiple"]), float(o.get("pnl_inr") or 0)) for o in outs if o.get("source") not in REPLAY_SOURCES]
        state = rebuild_engine(eid, live)
        for o in outs:
            if o.get("source") in REPLAY_SOURCES:
                state = apply_outcome_to_engine(eid, r_multiple=float(o["r_multiple"]),
                                                pnl_inr=float(o.get("pnl_inr") or 0), replay=True)
        if status == "DEAD" and state.get("status") != "DEAD":
            state["status"], state["death_reason"] = "DEAD", "revived only by a person"
            save_engine(state)
        fixed.append(f"{eid}: {len(live)} live, {len(outs) - len(live)} replay")
    return fixed


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
