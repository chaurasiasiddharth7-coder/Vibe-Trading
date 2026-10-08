"""Persist AlphaDesk signals and outcomes under ~/.vibe-trading/alphadesk/."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.alphadesk.cio_schema import CioTradePlan


def _is_valid_uuid(uuid_string: str) -> bool:
    """Validate that a string is a valid UUID (version 4)."""
    try:
        val = uuid.UUID(uuid_string, version=4)
        return str(val) == uuid_string
    except ValueError:
        return False


def _signal_path(signal_id: str) -> Path:
    """Build and validate path for a signal file."""
    if not _is_valid_uuid(signal_id):
        raise FileNotFoundError(signal_id)
    return alphadesk_root() / "signals" / f"{signal_id}.json"


def _outcome_path(signal_id: str) -> Path:
    """Build and validate path for an outcome file."""
    if not _is_valid_uuid(signal_id):
        raise FileNotFoundError(signal_id)
    return alphadesk_root() / "outcomes" / f"{signal_id}.json"


def alphadesk_root() -> Path:
    root = Path.home() / ".vibe-trading" / "alphadesk"
    root.mkdir(parents=True, exist_ok=True)
    (root / "signals").mkdir(exist_ok=True)
    (root / "outcomes").mkdir(exist_ok=True)
    (root / "portfolio").mkdir(exist_ok=True)
    (root / "survival").mkdir(exist_ok=True)
    (root / "cache").mkdir(exist_ok=True)
    return root


def load_signal(signal_id: str) -> dict[str, Any]:
    path = _signal_path(signal_id)
    if not path.is_file():
        raise FileNotFoundError(signal_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_outcomes(limit: int = 100) -> list[dict[str, Any]]:
    paths = sorted(
        (alphadesk_root() / "outcomes").glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for path in paths[:limit]:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def save_signal(symbol: str, plan: CioTradePlan, *, extras: dict[str, Any] | None = None) -> str:
    """Write a PENDING signal JSON; return signal id."""
    signal_id = str(uuid.uuid4())
    path = alphadesk_root() / "signals" / f"{signal_id}.json"
    payload = {
        "id": signal_id,
        "symbol": symbol.upper(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "PENDING",
        "plan": plan.model_dump(),
        "extras": extras or {},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return signal_id


def update_signal_status(signal_id: str, status: str, **fields: Any) -> dict[str, Any]:
    path = _signal_path(signal_id)
    if not path.is_file():
        raise FileNotFoundError(signal_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = status
    data.update(fields)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def record_outcome(signal_id: str, outcome: dict[str, Any]) -> Path:
    path = _outcome_path(signal_id)
    payload = {
        "signal_id": signal_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **outcome,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def save_portfolio_snapshot(rows: list[dict[str, Any]]) -> Path:
    path = alphadesk_root() / "portfolio" / "latest.json"
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "holdings": rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_portfolio_snapshot() -> dict[str, Any] | None:
    path = alphadesk_root() / "portfolio" / "latest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
