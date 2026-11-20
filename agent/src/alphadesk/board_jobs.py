"""Background jobs for wide board scans and past tests.

A Nifty 500 scan takes minutes (Yahoo is rate-limited), too long for one
HTTP request. Jobs run in a thread; the UI polls progress. The last finished
board per universe is saved so the page opens instantly next time.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from src.alphadesk import profiles

logger = logging.getLogger(__name__)

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_MAX_JOBS = 20


def _last_path(universe: str):
    from src.alphadesk.store import alphadesk_root

    d = alphadesk_root() / "board_last"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{universe}.json"


def save_last(universe: str, board: dict[str, Any]) -> None:
    _last_path(universe).write_text(json.dumps(board, default=str), encoding="utf-8")


def load_last(universe: str) -> dict[str, Any] | None:
    path = _last_path(universe)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _start(kind: str, key: str, work: Callable[[Callable[[int, int], None]], Any]) -> str:
    with _LOCK:
        for job in _JOBS.values():
            if job["kind"] == kind and job["key"] == key and job["status"] == "running" and job.get("profile") == profiles.current_id():
                return job["id"]
        if len(_JOBS) >= _MAX_JOBS:
            for old in [j for j in _JOBS.values() if j["status"] != "running"][: len(_JOBS) - _MAX_JOBS + 1]:
                _JOBS.pop(old["id"], None)
        job_id = str(uuid.uuid4())
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "key": key,
            "profile": profiles.current_id(),
            "status": "running",
            "done": 0,
            "total": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None,
        }

    def progress(done: int, total: int, **extra: Any) -> None:
        _JOBS[job_id].update(done=done, total=total, **extra)

    def run() -> None:
        try:
            result = work(progress)
            _JOBS[job_id].update(status="done", result=result)
        except Exception as exc:
            logger.exception("board job %s failed", job_id)
            _JOBS[job_id].update(status="failed", error=str(exc))

    threading.Thread(target=profiles.carry(run), name=f"board-{kind}-{key}", daemon=True).start()
    return job_id


def _forecast_available() -> bool:
    from src.alphadesk.signal_board import _kronos_available

    try:
        return _kronos_available()
    except Exception:  # noqa: BLE001
        return False


def start_scan(universe: str, *, include_funds: bool) -> str:
    from src.alphadesk.board_paper import settle_open_trades
    from src.alphadesk.index_lists import scan_list
    from src.alphadesk.signal_board import build_board

    def work(progress):
        try:
            settled = settle_open_trades()
        except Exception:
            logger.exception("board paper settle failed")
            settled = []
        members = scan_list(universe, include_funds=include_funds)
        if not members:
            raise RuntimeError(f"no members for {universe} (NSE list unavailable)")
        # Pass 1: every stock from prices (seconds with the NSE store). The page shows these ideas at once.
        board = build_board(members=members, with_kronos=False, universe_key=universe,
                            progress=lambda d, t: progress(d, t, phase="prices"))
        board = {**board, "just_closed": settled, "include_funds": include_funds}
        if not _forecast_available():
            save_last(universe, board)
            return board
        save_last(universe, {**board, "forecast_pending": True})
        progress(0, len(members), phase="forecast")  # the page now loads the saved price-only board
        # Pass 2: the AI forecast for the stocks whose price votes already lean one way (prices are cached now).
        full = build_board(members=members, with_kronos=True, universe_key=universe,
                           progress=lambda d, t: progress(d, t, phase="forecast"))
        full = {**full, "just_closed": settled, "include_funds": include_funds}
        save_last(universe, full)
        return full

    return _start("scan", f"{universe}:{int(include_funds)}", work)


def start_replay(universe: str) -> str:
    from src.alphadesk.board_replay import run_replay
    from src.alphadesk.index_lists import scan_list

    def work(progress):
        syms = [m["symbol"] for m in scan_list(universe, include_funds=True)]
        return run_replay(syms, universe_key=universe, progress=progress)

    return _start("replay", universe, work)


def status(job_id: str) -> dict[str, Any] | None:
    job = _JOBS.get(job_id)
    if not job or job.get("profile") != profiles.current_id():
        return None  # another profile's job looks like no job
    return {k: v for k, v in job.items() if k != "profile"}
