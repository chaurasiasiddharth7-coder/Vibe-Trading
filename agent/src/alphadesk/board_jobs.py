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
            if job["kind"] == kind and job["key"] == key and job["status"] == "running":
                return job["id"]
        if len(_JOBS) >= _MAX_JOBS:
            for old in [j for j in _JOBS.values() if j["status"] != "running"][: len(_JOBS) - _MAX_JOBS + 1]:
                _JOBS.pop(old["id"], None)
        job_id = str(uuid.uuid4())
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "key": key,
            "status": "running",
            "done": 0,
            "total": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None,
        }

    def progress(done: int, total: int) -> None:
        _JOBS[job_id].update(done=done, total=total)

    def run() -> None:
        try:
            result = work(progress)
            _JOBS[job_id].update(status="done", result=result)
        except Exception as exc:
            logger.exception("board job %s failed", job_id)
            _JOBS[job_id].update(status="failed", error=str(exc))

    threading.Thread(target=run, name=f"board-{kind}-{key}", daemon=True).start()
    return job_id


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
        board = build_board(members=members, progress=progress, universe_key=universe)
        board = {**board, "just_closed": settled, "include_funds": include_funds}
        save_last(universe, board)
        return board

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
    return dict(job) if job else None
