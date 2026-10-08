"""Anti-thrash idle sleep — skip expensive desk work if recently done."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.alphadesk.store import alphadesk_root

# Default cool-downs (seconds)
DEFAULT_COOLDOWNS: dict[str, int] = {
    "morning_desk": 6 * 3600,  # once per morning window
    "arena": 120,
    "debate_swarm": 300,
    "kronos": 60,
    "live_sync": 60,
}


def _idle_dir() -> Path:
    d = alphadesk_root() / "idle"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp_path(job: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job)
    return _idle_dir() / f"{safe}.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def last_run(job: str) -> dict[str, Any] | None:
    path = _stamp_path(job)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def mark_run(job: str, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "job": job,
        "ran_at": _utc_now().isoformat(),
        "day": _utc_now().strftime("%Y-%m-%d"),
        "meta": meta or {},
    }
    _stamp_path(job).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def should_sleep(
    job: str,
    *,
    cooldown_sec: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Return ``{sleep: bool, seconds_left, last}``. force=True never sleeps."""
    if force:
        return {"sleep": False, "seconds_left": 0, "last": last_run(job)}
    cd = int(cooldown_sec if cooldown_sec is not None else DEFAULT_COOLDOWNS.get(job, 120))
    prev = last_run(job)
    if not prev or not prev.get("ran_at"):
        return {"sleep": False, "seconds_left": 0, "last": None}
    try:
        ran = datetime.fromisoformat(str(prev["ran_at"]).replace("Z", "+00:00"))
    except ValueError:
        return {"sleep": False, "seconds_left": 0, "last": prev}
    elapsed = (_utc_now() - ran).total_seconds()
    left = max(0, cd - elapsed)
    return {
        "sleep": left > 0,
        "seconds_left": int(left),
        "last": prev,
        "cooldown_sec": cd,
    }
