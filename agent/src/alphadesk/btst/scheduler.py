"""The two BTST jobs, in-process: the scan at 15:20 IST and the settle of the previous day at 15:40.

Enable with ``BTST_SCHEDULER=1`` on the process that serves the API. It runs for the owner only:
the market is the same for every profile, and the records are shared. State lives in
``btst/schedule.json`` so a restart never repeats a job. No thread? ``python -m src.alphadesk.btst
cron`` prints system-cron lines that run the same jobs.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from src.alphadesk.btst import days

logger = logging.getLogger(__name__)

SCAN_TIME = os.environ.get("BTST_SCAN_TIME", "15:20")
SETTLE_TIME = os.environ.get("BTST_SETTLE_TIME", "15:40")
TICK_SECONDS = 30
_thread: threading.Thread | None = None


def _hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _state_path() -> Path:
    return days.root() / "schedule.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text()) if _state_path().exists() else {}
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=1))


def _prev_session(d: date) -> date | None:
    from src.alphadesk.intraday import calendar as cal

    p = d - timedelta(days=1)
    for _ in range(10):
        if cal.is_trading_day(p):
            return p
        p -= timedelta(days=1)
    return None


def due_jobs(now: datetime, state: dict) -> list[str]:
    """Which of ``scan`` / ``settle`` are owed right now (pure apart from the holiday list)."""
    from src.alphadesk.intraday import calendar as cal

    if not cal.is_trading_day(now.date()):
        return []
    today, minute, out = now.date().isoformat(), now.hour * 60 + now.minute, []
    if _hm(SCAN_TIME) <= minute < _hm("15:30") and (state.get("scan") or {}).get("date") != today:
        out.append("scan")
    if minute >= _hm(SETTLE_TIME) and (state.get("settle") or {}).get("date") != today:
        out.append("settle")
    return out


def run_job(kind: str, now: datetime) -> dict:
    if kind == "scan":
        with days.guard("scan"):
            return days.run_scan(now.date(), "scheduler", now=now)
    prev = _prev_session(now.date())
    if prev is None or days.load_day(prev, "live") is None:
        return {"note": "nothing to settle"}
    with days.guard("settle"):
        return days.run_settle(prev, "scheduler", now=now)


def _paper_on(strategy_id: str) -> bool:
    try:
        from src.alphadesk.strategies import paper_on

        return paper_on(strategy_id)
    except Exception:  # noqa: BLE001 - a broken switch file must not stop the bot
        return True


def tick(now: datetime | None = None, runner=run_job) -> list[str]:
    from src.alphadesk.intraday import bars as ibars

    now = now or ibars.now_ist()
    state, done = load_state(), []
    for kind in due_jobs(now, state):
        if kind == "scan" and not _paper_on("btst"):
            # Paper switch off on the Strategies page: no new pick; yesterday's pick still settles.
            state[kind] = {"date": now.date().isoformat(), "at": now.isoformat(timespec="seconds"), "ok": True,
                           "note": "paper trading switched off on the Strategies page"}
            save_state(state)
            done.append("scan:skipped")
            continue
        try:
            runner(kind, now)
            ok, note = True, ""
        except Exception as exc:  # noqa: BLE001 - recorded; the loop must live on
            logger.warning("btst %s job failed: %s", kind, exc)
            ok, note = False, f"{type(exc).__name__}: {exc}"
        state[kind] = {"date": now.date().isoformat(), "at": now.isoformat(timespec="seconds"), "ok": ok, "note": note}
        save_state(state)
        done.append(kind)
    return done


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001
            logger.exception("btst scheduler tick failed")
        time.sleep(TICK_SECONDS)


def enabled() -> bool:
    return os.environ.get("BTST_SCHEDULER", "").strip().lower() in ("1", "true", "yes", "on")


def start_if_enabled() -> bool:
    global _thread
    if not enabled() or (_thread is not None and _thread.is_alive()):
        return False
    _thread = threading.Thread(target=_loop, name="btst-scheduler", daemon=True)
    _thread.start()
    logger.info("btst scheduler started: scan %s, settle %s IST", SCAN_TIME, SETTLE_TIME)
    return True


def status() -> dict:
    state = load_state()
    return {"enabled": enabled(), "thread_alive": bool(_thread and _thread.is_alive()),
            "scan_time": SCAN_TIME, "settle_time": SETTLE_TIME, "timezone": "Asia/Kolkata",
            "last": {k: state.get(k) for k in ("scan", "settle")},
            "how_to_enable": None if enabled() else "Set BTST_SCHEDULER=1 on the server, or install the cron lines.",
            "cron": cron_lines()}


def cron_lines(agent_dir: Path | None = None, python: str | None = None) -> str:
    agent_dir = agent_dir or Path(__file__).resolve().parents[3]
    python = python or sys.executable
    log = "$HOME/.vibe-trading/alphadesk/btst/cron.log"
    (sh, sm), (th, tm) = divmod(_hm(SCAN_TIME), 60), divmod(_hm(SETTLE_TIME), 60)
    base = f"cd {agent_dir} && {python} -m src.alphadesk.btst"
    return (f"CRON_TZ=Asia/Kolkata\n"
            f"{sm} {sh} * * 1-5 {base} scan >> {log} 2>&1\n"
            f"{tm} {th} * * 1-5 {base} settle >> {log} 2>&1\n")
