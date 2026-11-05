"""Indian intraday pack HTTP routes (read-only: nothing here places an order).

Mounted by ``agent/api_server.py`` next to the AlphaDesk routes.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import threading
from datetime import date as date_cls

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

from src.alphadesk import profiles

AuthDep = Callable[..., Awaitable[Any] | Any]


class TradeCheckBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: str = Field("long", pattern="^(long|short)$")
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    or_minutes: int = Field(15, ge=5, le=60)
    stop: str = Field("or-mid", max_length=20)
    target: str = Field("2R", max_length=20)
    min_vol_x: float | None = Field(None, gt=0, le=20)
    replays: int = Field(10_000, ge=500, le=50_000)
    seed: int = 19


class SettingsBody(BaseModel):
    universe: str | None = None
    sides: str | None = Field(None, pattern="^(long|short|both)$")
    max_trades: int | None = Field(None, ge=1, le=20)
    risk_inr: float | None = Field(None, ge=100, le=1_000_000)
    or_minutes: int | None = None
    stop: str | None = Field(None, max_length=20)
    target: str | None = Field(None, max_length=20)
    min_vol_x: float | None = Field(None, gt=0, le=20)
    market_filter: str | None = Field(None, pattern="^(off|with_nifty)$")
    max_candidates: int | None = Field(None, ge=5, le=300)
    live_refresh_minutes: int | None = Field(None, ge=0, le=60)
    open_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    close_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _parse_date(text: str) -> date_cls:
    try:
        return date_cls.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


def register_intraday_routes(app: FastAPI, require_auth: AuthDep) -> None:
    from src.alphadesk.intraday import scheduler

    scheduler.start_if_enabled()

    @app.post("/api/desk/intraday/trade-check", dependencies=[Depends(require_auth)])
    def trade_check(body: TradeCheckBody) -> dict[str, Any]:
        """Entry status, exit plan, 10,000 replays and real history for one symbol and day."""
        from src.alphadesk.intraday.pipeline import trade_check as run
        from src.alphadesk.symbols import normalize_india_symbol

        try:
            normalize_india_symbol(body.symbol)
            return run(body.symbol, body.side, body.date, body.or_minutes, body.stop,
                       body.target, body.min_vol_x, body.replays, body.seed)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/desk/intraday/watchlist", dependencies=[Depends(require_auth)])
    def watchlist() -> dict[str, Any]:
        """The last daily or opening-range scan."""
        from src.alphadesk.intraday.scanner import watchlist_path

        path = watchlist_path()
        if not path.is_file():
            return {"ok": True, "data": None}
        return {"ok": True, "data": json.loads(path.read_text(encoding="utf-8"))}

    @app.get("/api/desk/intraday/calibration", dependencies=[Depends(require_auth)])
    def calibration() -> dict[str, Any]:
        """The last calibration run (in-sample vs out-of-sample, selection bar, verdict)."""
        from src.alphadesk.intraday.bars import intraday_dir

        path = intraday_dir() / "calibration.json"
        if not path.is_file():
            return {"ok": True, "data": None}
        return {"ok": True, "data": json.loads(path.read_text(encoding="utf-8"))}

    @app.post("/api/desk/intraday/scan/daily", dependencies=[Depends(require_auth)])
    def scan_daily(source: str = Query("nifty200", max_length=20),
                   limit: int = Query(200, ge=1, le=600)) -> dict[str, Any]:
        """Pre-market scan on split/bonus-adjusted daily bars; saved as the watchlist."""
        from src.alphadesk.intraday import scanner

        try:
            res = scanner.scan_daily(scanner.universe(source, limit=limit))
        except (RuntimeError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        scanner.save_watchlist(res)
        return {"ok": True, "data": res}

    # --- the day log -------------------------------------------------------------------------------

    def _range(date_from: str | None, date_to: str | None) -> None:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=400, detail="The 'from' date is after the 'to' date.")

    @app.get("/api/desk/intraday/days", dependencies=[Depends(require_auth)])
    def list_days(limit: int = Query(120, ge=1, le=800),
                  date_from: str | None = Query(None, alias="from", pattern=DATE_PATTERN),
                  date_to: str | None = Query(None, alias="to", pattern=DATE_PATTERN)) -> dict[str, Any]:
        """One light row per filed day, newest first, optionally only days between two dates."""
        from src.alphadesk.intraday import days

        _range(date_from, date_to)
        return {"ok": True, "data": days.list_days(limit, date_from, date_to)}

    @app.get("/api/desk/intraday/search", dependencies=[Depends(require_auth)])
    def search_trades(
        date_from: str | None = Query(None, alias="from", pattern=DATE_PATTERN),
        date_to: str | None = Query(None, alias="to", pattern=DATE_PATTERN),
        symbol: str | None = Query(None, pattern=r"^[A-Za-z0-9&.\- ]{0,20}$"),
        side: str | None = Query(None, pattern="^(long|short)$"),
        outcome: str | None = Query(None, pattern="^(won|lost|flat|open)$"),
        kind: str | None = Query(None, pattern=r"^[A-Za-z0-9_ ]{0,20}$"),
        index: str | None = Query(None, pattern="^(nifty50|niftybank)$"),
        limit: int = Query(400, ge=1, le=800),
    ) -> dict[str, Any]:
        """Trades across all filed days, listed date by date (newest first). ``from``/``to`` are the
        dates the trades were taken. Every filter is optional and they combine."""
        from src.alphadesk.intraday import days

        _range(date_from, date_to)
        return {"ok": True, "data": days.search(date_from, date_to, symbol, side, outcome, kind, index, limit)}

    @app.get("/api/desk/intraday/day/{day}", dependencies=[Depends(require_auth)])
    def get_day(day: str = Path(pattern=DATE_PATTERN)) -> dict[str, Any]:
        """The full record of one day (plan, trades with paths, summary, day type) and whether a job is running."""
        from src.alphadesk.intraday import days

        d = _parse_date(day)
        return {"ok": True, "data": days.load_day(d), "running": days.is_running(d)}

    @app.post("/api/desk/intraday/day/{day}/run", dependencies=[Depends(require_auth)])
    def run_day(day: str = Path(pattern=DATE_PATTERN),
                kind: str = Query(pattern="^(open|refresh|close|replay)$")) -> dict[str, Any]:
        """Start a job for a day in the background: open (plan), refresh (live marks), close
        (settle), replay (research a past day). Poll GET /day/{day} until ``running`` is null."""
        from src.alphadesk.intraday import calendar as cal
        from src.alphadesk.intraday import days
        from src.alphadesk.intraday.bars import now_ist

        d = _parse_date(day)
        if d > now_ist().date():
            raise HTTPException(status_code=400, detail="That day has not happened yet.")
        if not cal.is_trading_day(d):
            raise HTTPException(status_code=400, detail=f"NSE is closed on {d.isoformat()} ({d.strftime('%A')}).")
        if kind == "replay" and d >= now_ist().date():
            raise HTTPException(status_code=400, detail="Replay is for past days; use Plan open or Close for today.")
        if days.is_running(d):
            raise HTTPException(status_code=409, detail=f"A {days.is_running(d)} job is already running for this day.")
        fn = {"open": days.run_open, "refresh": days.run_refresh, "close": days.run_close,
              "replay": days.run_replay}[kind]

        def work() -> None:
            try:
                fn(d, "manual")
            except Exception:  # noqa: BLE001 - recorded on the day's run log by the engine
                pass

        threading.Thread(target=profiles.carry(work), name=f"intraday-{kind}-{d}", daemon=True).start()
        return {"ok": True, "started": kind, "date": d.isoformat()}

    @app.post("/api/desk/intraday/backfill", dependencies=[Depends(require_auth)])
    def backfill(days_back: int = Query(30, ge=1, le=90, alias="days"), force: bool = False) -> dict[str, Any]:
        """File the last N trading days by replaying them from stored prices (background)."""
        from datetime import timedelta

        from src.alphadesk.intraday import days
        from src.alphadesk.intraday.bars import now_ist

        if days.is_running(now_ist().date()) == "backfill":
            raise HTTPException(status_code=409, detail="A backfill is already running.")
        end = now_ist().date() - timedelta(days=1)

        def work() -> None:
            flag = profiles.scoped_key(now_ist().date().isoformat())
            days._RUNNING[flag] = "backfill"
            try:
                days.backfill(end - timedelta(days=days_back), end, "backfill", force=force)
            finally:
                days._RUNNING.pop(flag, None)

        threading.Thread(target=profiles.carry(work), name="intraday-backfill", daemon=True).start()
        return {"ok": True, "started": "backfill", "days": days_back}

    @app.get("/api/desk/intraday/stats", dependencies=[Depends(require_auth)])
    def stats() -> dict[str, Any]:
        """Cumulative win ratio over settled days, and results by kind of day, side and index."""
        from src.alphadesk.intraday import days

        return {"ok": True, "data": days.stats()}

    @app.get("/api/desk/intraday/settings", dependencies=[Depends(require_auth)])
    def get_settings() -> dict[str, Any]:
        from src.alphadesk.intraday import days, universe

        return {"ok": True, "data": days.load_settings(), "universes": universe.KEYS}

    @app.put("/api/desk/intraday/settings", dependencies=[Depends(require_auth)])
    def put_settings(body: SettingsBody) -> dict[str, Any]:
        from src.alphadesk.intraday import days

        try:
            return {"ok": True, "data": days.save_settings(body.model_dump(exclude_none=True))}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/desk/intraday/schedule", dependencies=[Depends(require_auth)])
    def schedule() -> dict[str, Any]:
        """Whether the two daily jobs are on, when they last ran and when they run next."""
        from src.alphadesk.intraday import scheduler as sch

        return {"ok": True, "data": {**sch.status(), "cron": sch.cron_lines()}}
