"""Daily per-symbol cache for analysis / Kronos (cost is edge)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.alphadesk.store import alphadesk_root


# Daily bars change all session (and Yahoo finalises the last bar late), so
# price-derived payloads are reused for at most this long.
PRICE_MAX_AGE_S = 30 * 60


def _cache_dir() -> Path:
    d = alphadesk_root() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _key_path(kind: str, symbol: str, **params: Any) -> Path:
    raw = f"{kind}|{symbol.upper()}|{json.dumps(params, sort_keys=True)}|{_day_key()}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return _cache_dir() / f"{digest}.json"


def get_cached(
    kind: str, symbol: str, *, max_age_s: float | None = None, **params: Any
) -> dict[str, Any] | None:
    path = _key_path(kind, symbol, **params)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("day") != _day_key():
            return None
        if max_age_s is not None:
            cached_at = datetime.fromisoformat(str(data.get("cached_at")))
            if (datetime.now(timezone.utc) - cached_at).total_seconds() > max_age_s:
                return None
        return data.get("payload")
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def set_cached(kind: str, symbol: str, payload: dict[str, Any], **params: Any) -> Path:
    path = _key_path(kind, symbol, **params)
    blob = {
        "day": _day_key(),
        "kind": kind,
        "symbol": symbol.upper(),
        "params": params,
        "payload": payload,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    return path
