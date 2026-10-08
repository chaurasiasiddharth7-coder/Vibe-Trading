"""India FY tax YTD tracker (estimate only — not CA advice).

FY runs Apr 1 → Mar 31. Records estimated STCG/LTCG from desk sells / outcomes.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.alphadesk.portfolio import LTCG_RATE, STCG_RATE, estimate_tax
from src.alphadesk.store import alphadesk_root


def india_fy_label(d: date | None = None) -> str:
    """e.g. 2026-04-01 → FY2026-27."""
    d = d or date.today()
    if d.month >= 4:
        return f"FY{d.year}-{str(d.year + 1)[-2:]}"
    return f"FY{d.year - 1}-{str(d.year)[-2:]}"


def _tax_dir() -> Path:
    d = alphadesk_root() / "tax"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fy_path(fy: str | None = None) -> Path:
    label = fy or india_fy_label()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    return _tax_dir() / f"{safe}.json"


def _default_state(fy: str) -> dict[str, Any]:
    return {
        "fy": fy,
        "stcg_gain_inr": 0.0,
        "ltcg_gain_inr": 0.0,
        "est_stcg_tax_inr": 0.0,
        "est_ltcg_tax_inr": 0.0,
        "est_tax_inr": 0.0,
        "realized_gross_inr": 0.0,
        "events": [],
        "updated_at": None,
    }


def load_fy(fy: str | None = None) -> dict[str, Any]:
    label = fy or india_fy_label()
    path = _fy_path(label)
    if not path.is_file():
        return _default_state(label)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("fy", label)
        return data
    except (json.JSONDecodeError, OSError):
        return _default_state(label)


def save_fy(state: dict[str, Any]) -> Path:
    state = {**state, "updated_at": datetime.now(timezone.utc).isoformat()}
    path = _fy_path(str(state.get("fy") or india_fy_label()))
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def record_sell_tax(
    *,
    symbol: str,
    gain_inr: float,
    long_term_frac: float = 0.0,
    gross_inr: float = 0.0,
    source: str = "desk",
    notes: str | None = None,
    fy: str | None = None,
) -> dict[str, Any]:
    """Accumulate estimated tax for a realized sell (gain can be negative → ignored)."""
    label = fy or india_fy_label()
    state = load_fy(label)
    lt = max(0.0, min(1.0, float(long_term_frac)))
    st = 1.0 - lt
    gain = float(gain_inr)
    taxable = max(0.0, gain)
    st_gain = taxable * st
    lt_gain = taxable * lt
    st_tax = st_gain * STCG_RATE
    lt_tax = lt_gain * LTCG_RATE
    total_tax = estimate_tax(gain, long_term_frac=lt)

    state["stcg_gain_inr"] = round(float(state.get("stcg_gain_inr") or 0) + st_gain, 2)
    state["ltcg_gain_inr"] = round(float(state.get("ltcg_gain_inr") or 0) + lt_gain, 2)
    state["est_stcg_tax_inr"] = round(float(state.get("est_stcg_tax_inr") or 0) + st_tax, 2)
    state["est_ltcg_tax_inr"] = round(float(state.get("est_ltcg_tax_inr") or 0) + lt_tax, 2)
    state["est_tax_inr"] = round(
        float(state.get("est_stcg_tax_inr") or 0) + float(state.get("est_ltcg_tax_inr") or 0),
        2,
    )
    state["realized_gross_inr"] = round(
        float(state.get("realized_gross_inr") or 0) + float(gross_inr or 0), 2
    )
    events = list(state.get("events") or [])
    events.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol.upper(),
            "gain_inr": round(gain, 2),
            "lt_frac": lt,
            "est_tax_inr": round(total_tax, 2),
            "source": source,
            "notes": notes,
        }
    )
    state["events"] = events[-500:]
    save_fy(state)
    return state


def summary(fy: str | None = None) -> dict[str, Any]:
    state = load_fy(fy)
    return {
        "fy": state.get("fy"),
        "stcg_gain_inr": state.get("stcg_gain_inr"),
        "ltcg_gain_inr": state.get("ltcg_gain_inr"),
        "est_stcg_tax_inr": state.get("est_stcg_tax_inr"),
        "est_ltcg_tax_inr": state.get("est_ltcg_tax_inr"),
        "est_tax_inr": state.get("est_tax_inr"),
        "realized_gross_inr": state.get("realized_gross_inr"),
        "event_count": len(state.get("events") or []),
        "updated_at": state.get("updated_at"),
        "rates": {"stcg": STCG_RATE, "ltcg": LTCG_RATE},
        "disclaimer": "Estimate only — not tax advice.",
    }
