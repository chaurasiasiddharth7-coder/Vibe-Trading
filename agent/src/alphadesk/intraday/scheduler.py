"""The two daily jobs, in-process: opening scan at 09:15 IST and closing settle at 15:40 IST.

Enable with ``INTRADAY_SCHEDULER=1`` on whatever process serves the API (the VPS container keeps
it running all day). A tiny thread wakes every 30 seconds, asks :func:`due_jobs` what is owed and
runs it once per trading day. State lives in ``schedule.json`` so a restart never repeats a job,
and a restart mid-day catches up what was missed. If the machine was off for a whole day the
closing job replays it from stored prices, so no day is left without a record.

No scheduler thread? Use the same jobs from the system cron: ``python -m src.alphadesk.intraday
cron`` prints the two lines.
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

from src.alphadesk import profiles
from src.alphadesk.intraday import bars as ibars
from src.alphadesk.intraday import calendar as cal
from src.alphadesk.intraday import days
from src.alphadesk.intraday.rules import CLOSE_MIN, OPEN_MIN, hhmm

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
CATCH_UP_DAYS = 3
_thread: threading.Thread | None = None


def _state_path() -> Path:
    return ibars.intraday_dir() / "schedule.json"


def load_state() -> dict:
    path = _state_path()
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=1))


def due_jobs(now: datetime, state: dict, settings: dict) -> list[str]:
    """Which of ``open`` / ``refresh`` / ``close`` are owed right now (pure)."""
    today = now.date().isoformat()
    if not cal.is_trading_day(now.date()):
        return []
    minute = now.hour * 60 + now.minute
    out = []
    if hhmm(settings["open_time"]) <= minute < CLOSE_MIN and (state.get("open") or {}).get("date") != today:
        out.append("open")
    every = settings["live_refresh_minutes"]
    if every and OPEN_MIN + 15 <= minute < CLOSE_MIN and (state.get("open") or {}).get("date") == today:
        last = (state.get("refresh") or {})
        if last.get("date") != today or minute - int(last.get("minute", -999)) >= every:
            out.append("refresh")
    if minute >= hhmm(settings["close_time"]) and (state.get("close") or {}).get("date") != today:
        out.append("close")
    return out


def _recent_unsettled(now: datetime, n: int = CATCH_UP_DAYS) -> list[date]:
    out, d = [], now.date() - timedelta(days=1)
    while len(out) < n and (now.date() - d).days <= 10:
        if cal.is_trading_day(d):
            rec = days.load_day(d)
            if not rec or rec.get("status") != "settled":
                out.append(d)
            else:
                break  # everything before a settled day was handled
        d -= timedelta(days=1)
    return sorted(out)


def run_job(kind: str, now: datetime | None = None, source: str = "scheduler") -> dict:
    now = now or ibars.now_ist()
    d = now.date()
    return {"open": days.run_open, "close": days.run_close}[kind](d, source) if kind in ("open", "close") \
        else days.run_refresh(d, source, now)


def tick(now: datetime | None = None, runner=run_job) -> list[str]:
    """Run whatever is owed. Returns the jobs attempted (``close`` for a past day is ``replay:DATE``)."""
    now = now or ibars.now_ist()
    settings = days.load_settings()
    state = load_state()
    done = []
    for kind in due_jobs(now, state, settings):
        try:
            runner(kind, now)
            ok, note = True, ""
        except days.MarketClosed:
            ok, note = True, "market closed today (holiday): nothing filed"
        except Exception as exc:  # noqa: BLE001 - recorded, and the loop must live on
            logger.warning("intraday %s job failed: %s", kind, exc)
            ok, note = False, f"{type(exc).__name__}: {exc}"
        entry = {"date": now.date().isoformat(), "at": now.isoformat(timespec="seconds"), "ok": ok, "note": note}
        if kind == "refresh":
            entry["minute"] = now.hour * 60 + now.minute
        state[kind] = entry
        save_state(state)
        done.append(kind)
    if not done and now.hour * 60 + now.minute >= hhmm(settings["open_time"]) - 30:
        tried = state.setdefault("catchup", {})
        today = now.date().isoformat()
        for d in _recent_unsettled(now):
            if tried.get(d.isoformat()) == today:
                continue  # one attempt per missed day per day: a failing day must not hammer the data source
            tried[d.isoformat()] = today
            save_state(state)
            try:
                days.run_replay(d, "scheduler-catch-up")
                done.append(f"replay:{d.isoformat()}")
            except days.MarketClosed:
                pass  # a holiday: nothing to file, and it is not retried (tried[] already says so)
            except Exception as exc:  # noqa: BLE001
                logger.warning("catch-up of %s failed: %s", d, exc)
        state["catchup"] = {k: v for k, v in tried.items() if v == today}
        save_state(state)
    return done


def tick_all(now: datetime | None = None) -> None:
    """One tick for the owner and for every profile, each on its own settings, state and folder."""
    for pid in profiles.all_ids():
        try:
            with profiles.use(pid):
                tick(now)
        except Exception:  # noqa: BLE001 - one person's failure must not stop the others
            logger.exception("intraday scheduler tick failed for %s", pid or "owner")


def _loop() -> None:
    while True:
        tick_all()
        time.sleep(TICK_SECONDS)


def enabled() -> bool:
    return os.environ.get("INTRADAY_SCHEDULER", "").strip().lower() in ("1", "true", "yes", "on")


def start_if_enabled() -> bool:
    """Start the scheduler thread once, if ``INTRADAY_SCHEDULER`` is on. Safe to call repeatedly."""
    global _thread
    if not enabled() or (_thread is not None and _thread.is_alive()):
        return False
    _thread = threading.Thread(target=_loop, name="intraday-scheduler", daemon=True)
    _thread.start()
    logger.info("intraday scheduler started: opening scan + closing settle on NSE trading days")
    return True


def status(now: datetime | None = None) -> dict:
    now = now or ibars.now_ist()
    settings = days.load_settings()
    state = load_state()
    nxt = None
    d = now.date()
    for _ in range(10):
        if cal.is_trading_day(d):
            for kind, hhmm_text in (("open", settings["open_time"]), ("close", settings["close_time"])):
                at = datetime.combine(d, datetime.min.time()).replace(hour=hhmm(hhmm_text) // 60, minute=hhmm(hhmm_text) % 60)
                if at > now and (nxt is None or at < nxt[1]):
                    nxt = (kind, at)
        if nxt:
            break
        d += timedelta(days=1)
    return {"enabled": enabled(), "thread_alive": bool(_thread and _thread.is_alive()),
            "open_time": settings["open_time"], "close_time": settings["close_time"],
            "live_refresh_minutes": settings["live_refresh_minutes"], "timezone": "Asia/Kolkata",
            "market": cal.market_state(now), "last": {k: state.get(k) for k in ("open", "refresh", "close")},
            "next": {"kind": nxt[0], "at": nxt[1].isoformat(timespec="minutes")} if nxt else None,
            "how_to_enable": None if enabled() else "Set INTRADAY_SCHEDULER=1 on the server, or install the cron lines below."}


def cron_lines(agent_dir: Path | None = None, python: str | None = None) -> str:
    agent_dir = agent_dir or Path(__file__).resolve().parents[3]
    python = python or sys.executable
    log = "$HOME/.vibe-trading/alphadesk/intraday/cron.log"
    settings = days.load_settings()
    (oh, om), (ch, cm) = (divmod(hhmm(settings["open_time"]), 60)), (divmod(hhmm(settings["close_time"]), 60))
    base = f'cd {agent_dir} && {python} -m src.alphadesk.intraday day'
    return (f"CRON_TZ=Asia/Kolkata\n"
            f"{om} {oh} * * 1-5 {base} open >> {log} 2>&1\n"
            f"{cm} {ch} * * 1-5 {base} close >> {log} 2>&1\n")
