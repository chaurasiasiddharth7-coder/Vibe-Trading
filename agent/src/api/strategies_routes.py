"""Strategies page routes (under /api/desk/ so the hosted proxy forwards them).

    GET   /api/desk/strategies                     catalogue (owner: every profile; others: their own)
    POST  /api/desk/strategies                     {name, rules, notes?, source_url?} -> new lab strategy
    GET   /api/desk/strategies/{id}                detail: rules, tests, paper trades
    PATCH /api/desk/strategies/{id}                {name?, notes?, rules?, archived?}
    PUT   /api/desk/strategies/{id}/paper          {on: bool}
    PUT   /api/desk/strategies/{id}/live           always 409: trading is paper only
    POST  /api/desk/strategies/{id}/test           {years?, cost_pct?} -> job id
    POST  /api/desk/strategies/test                {rules, years?, cost_pct?} -> job id (try rules before saving)
    GET   /api/desk/strategies/jobs/{job_id}
    POST  /api/desk/strategies/{id}/scan-now       run today's paper scan for a lab strategy
    POST  /api/desk/strategies/import              {text?, url?} -> drafted rules to review (owner only: uses the AI model)

``?profile=<id>`` lets the owner act on another profile's strategy.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

AuthDep = Callable[..., Awaitable[Any] | Any]


def _is_owner() -> bool:
    from src.alphadesk import profiles, users

    user = users.current_user()
    if user is not None:
        return user.role == "owner" and not user.must_change
    return profiles.current_id() is None


def _author() -> str:
    from src.alphadesk import profiles, users

    user = users.current_user()
    if user is not None:
        return user.username
    return profiles.current_id() or "owner"


def register_strategies_routes(app: FastAPI, require_auth: AuthDep) -> None:
    from src.alphadesk import profiles
    from src.alphadesk import strategies as S

    def bad(exc: Exception, code: int = 400) -> HTTPException:
        return HTTPException(status_code=code, detail=str(exc))

    def scoped(profile: str | None):
        """Run as another profile only for the owner; everyone else stays in their own."""
        if profile in (None, "", "owner"):
            if _is_owner():
                return profiles.use(None)
            return profiles.use(profiles.current_id())
        if not _is_owner():
            raise HTTPException(status_code=403, detail="Only the owner can change other people's strategies.")
        if profiles.get(profile) is None:
            raise HTTPException(status_code=404, detail="No such profile.")
        return profiles.use(profile)

    @app.get("/api/desk/strategies", dependencies=[Depends(require_auth)])
    def list_strategies() -> dict[str, Any]:
        return {"ok": True, "data": S.catalogue(include_all_profiles=_is_owner())}

    @app.post("/api/desk/strategies", dependencies=[Depends(require_auth)])
    def create(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "data": S.create_custom(body, author=_author())}
        except S.StrategyError as exc:
            raise bad(exc) from exc

    @app.post("/api/desk/strategies/test", dependencies=[Depends(require_auth)])
    def try_rules(body: dict[str, Any]) -> dict[str, Any]:
        try:
            jid = S.start_test(None, body.get("rules"), int(body.get("years") or 3),
                               float(body.get("cost_pct") if body.get("cost_pct") is not None else S.DEFAULT_COST_PCT))
        except (S.StrategyError, TypeError, ValueError) as exc:
            raise bad(exc) from exc
        return {"ok": True, "job_id": jid}

    @app.post("/api/desk/strategies/import", dependencies=[Depends(require_auth)])
    def import_strategy(body: dict[str, Any]) -> dict[str, Any]:
        if not _is_owner():
            raise HTTPException(status_code=403, detail="Importing with the AI model is for the owner. Build the rules by hand.")
        try:
            return {"ok": True, "data": S.draft_from_text(str(body.get("text") or ""),
                                                           url=(str(body.get("url")).strip() or None) if body.get("url") else None)}
        except S.StrategyError as exc:
            raise bad(exc) from exc

    @app.get("/api/desk/strategies/jobs/{job_id}", dependencies=[Depends(require_auth)])
    def get_job(job_id: str) -> dict[str, Any]:
        j = S.job(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail="Test not found (the server may have restarted).")
        return {"ok": True, "data": j}

    @app.get("/api/desk/strategies/{sid}", dependencies=[Depends(require_auth)])
    def get_one(sid: str, profile: str | None = Query(None)) -> dict[str, Any]:
        with scoped(profile):
            return {"ok": True, "data": S.detail(sid)}

    @app.patch("/api/desk/strategies/{sid}", dependencies=[Depends(require_auth)])
    def edit(sid: str, body: dict[str, Any], profile: str | None = Query(None)) -> dict[str, Any]:
        with scoped(profile):
            try:
                return {"ok": True, "data": S.update_custom(sid, body)}
            except S.StrategyError as exc:
                raise bad(exc) from exc

    @app.put("/api/desk/strategies/{sid}/paper", dependencies=[Depends(require_auth)])
    def paper(sid: str, body: dict[str, Any], profile: str | None = Query(None)) -> dict[str, Any]:
        with scoped(profile):
            try:
                return {"ok": True, "data": S.set_paper(sid, bool(body.get("on")))}
            except S.StrategyError as exc:
                raise bad(exc) from exc

    @app.put("/api/desk/strategies/{sid}/live", dependencies=[Depends(require_auth)])
    def live(sid: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            S.set_live(sid, bool(body.get("on")))
        except S.StrategyError as exc:
            raise bad(exc, 409) from exc
        raise HTTPException(status_code=409, detail=S.LIVE_LOCKED_REASON)  # pragma: no cover

    @app.post("/api/desk/strategies/{sid}/test", dependencies=[Depends(require_auth)])
    def test(sid: str, body: dict[str, Any], profile: str | None = Query(None)) -> dict[str, Any]:
        with scoped(profile):
            rec = (S.detail(sid) or {}).get("strategy")
            if rec is None:
                raise HTTPException(status_code=404, detail="Only lab strategies can be re-tested here.")
            try:
                jid = S.start_test(sid, rec["rules"], int(body.get("years") or 3),
                                   float(body.get("cost_pct") if body.get("cost_pct") is not None else S.DEFAULT_COST_PCT))
            except (S.StrategyError, TypeError, ValueError) as exc:
                raise bad(exc) from exc
        return {"ok": True, "job_id": jid}

    @app.post("/api/desk/strategies/{sid}/scan-now", dependencies=[Depends(require_auth)])
    def scan_now(sid: str, profile: str | None = Query(None)) -> dict[str, Any]:
        with scoped(profile):
            try:
                return {"ok": True, "data": S.paper_scan(sid)}
            except S.StrategyError as exc:
                raise bad(exc) from exc

    if os.environ.get("STRATEGY_SCHEDULER", os.environ.get("INTRADAY_SCHEDULER", "")) == "1":
        S.start_scheduler()
