"""Daily token-budget caps for AlphaDesk expensive LLM work."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.alphadesk.store import alphadesk_root

# Soft daily ceilings (estimated tokens). Debate swarm is the expensive path.
DEFAULT_DAILY_CAPS: dict[str, int] = {
    "debate_swarm": 80_000,
    "arena": 40_000,
    "alpha_forge": 20_000,
    "total": 150_000,
}


def _budget_path() -> Path:
    d = alphadesk_root() / "budget"
    d.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return d / f"{day}.json"


def _default() -> dict[str, Any]:
    return {
        "day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "used": {k: 0 for k in DEFAULT_DAILY_CAPS},
        "caps": dict(DEFAULT_DAILY_CAPS),
        "events": [],
    }


def load_budget() -> dict[str, Any]:
    path = _budget_path()
    if not path.is_file():
        return _default()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("caps", dict(DEFAULT_DAILY_CAPS))
        data.setdefault("used", {})
        return data
    except (json.JSONDecodeError, OSError):
        return _default()


def save_budget(state: dict[str, Any]) -> Path:
    path = _budget_path()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def remaining(bucket: str = "total") -> int:
    state = load_budget()
    caps = state.get("caps") or DEFAULT_DAILY_CAPS
    used = state.get("used") or {}
    cap = int(caps.get(bucket, DEFAULT_DAILY_CAPS.get(bucket, 0)))
    return max(0, cap - int(used.get(bucket, 0)))


def can_spend(bucket: str, tokens: int) -> bool:
    if tokens <= 0:
        return True
    if remaining(bucket) < tokens:
        return False
    if remaining("total") < tokens:
        return False
    return True


def record_spend(
    bucket: str,
    tokens: int,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Record spend. Raises PermissionError if over cap."""
    tokens = max(0, int(tokens))
    if not can_spend(bucket, tokens):
        raise PermissionError(
            f"token budget exceeded for {bucket!r} "
            f"(need {tokens}, remaining {remaining(bucket)} / total {remaining('total')})"
        )
    state = load_budget()
    used = dict(state.get("used") or {})
    used[bucket] = int(used.get(bucket, 0)) + tokens
    used["total"] = int(used.get("total", 0)) + tokens
    state["used"] = used
    events = list(state.get("events") or [])
    events.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "bucket": bucket,
            "tokens": tokens,
            "note": note,
        }
    )
    state["events"] = events[-200:]
    save_budget(state)
    return state


def status() -> dict[str, Any]:
    state = load_budget()
    caps = state.get("caps") or DEFAULT_DAILY_CAPS
    used = state.get("used") or {}
    return {
        "day": state.get("day"),
        "caps": caps,
        "used": used,
        "remaining": {k: max(0, int(caps.get(k, 0)) - int(used.get(k, 0))) for k in caps},
    }
