"""BTST (buy today, sell tomorrow) routes. Read-only: nothing here places an order.

Mounted by ``agent/api_server.py`` next to the intraday routes.
"""

from __future__ import annotations

import logging
import threading
from datetime import date as date_cls
from datetime import timedelta
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Path, Query

AuthDep = Callable[..., Awaitable[Any] | Any]
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
logger = logging.getLogger(__name__)
JOB: dict[str, Any] = {}  # the background job the page can poll: {name, started, done, total, error, result}


def _parse(text: str) -> date_cls:
    try:
        return date_cls.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


def register_btst_routes(app: FastAPI, require_auth: AuthDep) -> None:
    from src.alphadesk.btst import days, scheduler
    from src.alphadesk.btst import rules as R

    scheduler.start_if_enabled()

    def _start(name: str, fn) -> None:
        if JOB.get("name") and not JOB.get("finished"):
            raise HTTPException(status_code=409, detail=f"{JOB['name']} is already running")
        JOB.clear()
        JOB.update({"name": name, "finished": False, "done": 0, "total": 0, "error": None, "result": None})

        def work() -> None:
            try:
                with days.guard(name):
                    JOB["result"] = fn()
            except Exception as exc:  # noqa: BLE001 - shown to the page in plain words
                logger.warning("btst %s failed: %s", name, exc)
                JOB["error"] = str(exc)
            finally:
                JOB["finished"] = True

        threading.Thread(target=work, name=f"btst-{name}", daemon=True).start()

    def _progress(i: int, n: int) -> None:
        JOB["done"], JOB["total"] = i, n

    @app.get("/api/desk/btst/today", dependencies=[Depends(require_auth)])
    def today() -> dict[str, Any]:
        """The latest live record (today's, or the most recent one) and the schedule."""
        latest = days.list_days("live", limit=1)
        return {"ok": True, "data": latest[0] if latest else None, "schedule": scheduler.status(),
                "job": dict(JOB) if JOB else None, "store_last": _iso(days.store_last_date())}

    @app.get("/api/desk/btst/day/{day}", dependencies=[Depends(require_auth)])
    def day(day: str = Path(pattern=DATE_PATTERN), kind: str = Query("live", pattern="^(live|replay)$")) -> dict[str, Any]:
        return {"ok": True, "data": days.load_day(_parse(day), kind)}

    @app.get("/api/desk/btst/days", dependencies=[Depends(require_auth)])
    def list_days(kind: str = Query("live", pattern="^(live|replay)$"), limit: int = Query(120, ge=1, le=2000)) -> dict[str, Any]:
        return {"ok": True, "data": [days.row_of(r) for r in days.list_days(kind, limit)]}

    @app.get("/api/desk/btst/stats", dependencies=[Depends(require_auth)])
    def stats(since: str | None = Query(None, pattern=DATE_PATTERN)) -> dict[str, Any]:
        """Live and replayed track records, side by side and never blended."""
        out = {}
        for kind in days.KINDS:
            recs = days.list_days(kind)
            if since:
                recs = [r for r in recs if r["date"] >= since]
            out[kind] = days.stats(kind, recs)
        return {"ok": True, "data": out}

    @app.get("/api/desk/btst/rules", dependencies=[Depends(require_auth)])
    def rules() -> dict[str, Any]:
        return {"ok": True, "data": {"version": R.RULES_VERSION, "rules": R.DEFAULT_RULES.as_dict(),
                                     "warnings": [{"key": k, "label": v[0], "rule": v[1], "big_loss_flagged_pct": v[2],
                                                   "big_loss_clean_pct": v[3]} for k, v in R.WARNINGS.items()]}}

    @app.get("/api/desk/btst/job", dependencies=[Depends(require_auth)])
    def job() -> dict[str, Any]:
        return {"ok": True, "data": dict(JOB) if JOB else None}

    @app.post("/api/desk/btst/scan", dependencies=[Depends(require_auth)])
    def scan() -> dict[str, Any]:
        from src.alphadesk.intraday import bars as ibars
        from src.alphadesk.intraday import calendar as cal

        now = ibars.now_ist()
        if not cal.is_trading_day(now.date()):
            raise HTTPException(status_code=400, detail="The market is closed today.")
        if not ("09:15" <= now.strftime("%H:%M") < "15:30"):
            raise HTTPException(status_code=400, detail="A live scan needs the market open (09:15-15:30 IST). The rules expect about 15:20.")
        _start("scan", lambda: _summary(days.run_scan(now.date(), "manual", now=now)))
        return {"ok": True, "started": "scan"}

    @app.post("/api/desk/btst/settle", dependencies=[Depends(require_auth)])
    def settle(day: str = Query(..., pattern=DATE_PATTERN)) -> dict[str, Any]:
        d = _parse(day)
        if days.load_day(d, "live") is None:
            raise HTTPException(status_code=404, detail=f"No live record for {day}.")
        try:
            rec = days.run_settle(d, "manual")
        except days.NotReady as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "data": rec}

    @app.post("/api/desk/btst/replay", dependencies=[Depends(require_auth)])
    def replay(years: float = Query(3.0, gt=0, le=15)) -> dict[str, Any]:
        last = days.store_last_date()
        if last is None:
            raise HTTPException(status_code=400, detail="No NSE store on this server: run scripts/nse_pull.py first.")
        end = last - timedelta(days=1)
        start = end - timedelta(days=int(years * 365))
        _start("replay", lambda: days.replay(start, end, progress=_progress))
        return {"ok": True, "started": "replay", "from": start.isoformat(), "to": end.isoformat()}


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _summary(rec: dict) -> dict:
    p = rec.get("pick")
    return {"date": rec["date"], "pick": p["symbol"] if p else None, "candidates": len(rec.get("candidates") or [])}
