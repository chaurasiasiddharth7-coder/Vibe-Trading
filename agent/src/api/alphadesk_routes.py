"""AlphaDesk HTTP routes: analysis, Kronos, Desk, survival, Arena, PM.

Mounted by ``agent/api_server.py`` via ``register_alphadesk_routes``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from backtest.loaders.base import NoAvailableSourceError
from src.alphadesk.analysis import analyze_symbol
from src.alphadesk.symbols import normalize_india_symbol

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_alphadesk_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount AlphaDesk analysis and forecast routes."""
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError(
                "register_alphadesk_routes: api_server not in sys.modules; "
                "pass require_auth explicitly"
            )
        require_auth = host.require_auth

    @app.get("/api/analysis/{symbol}", dependencies=[Depends(require_auth)])
    def get_analysis(
        symbol: str,
        interval: str = Query("1d", max_length=8),
        period: str = Query("6mo", max_length=8),
        bypass_cache: bool = Query(False),
    ) -> dict[str, Any]:
        """Structured TA + candlestick payload for an NSE/BSE symbol."""
        from src.alphadesk.cache import PRICE_MAX_AGE_S, get_cached, set_cached

        try:
            normalize_india_symbol(symbol)
            if not bypass_cache:
                cached = get_cached(
                    "analysis",
                    symbol,
                    max_age_s=PRICE_MAX_AGE_S,
                    interval=interval,
                    period=period,
                )
                if cached is not None:
                    return {"ok": True, "data": cached, "cached": True}
            payload = analyze_symbol(symbol, period=period, interval=interval)
            set_cached("analysis", symbol, payload, interval=interval, period=period)
            return {"ok": True, "data": payload, "cached": False}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except NoAvailableSourceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("analysis failed for %s", symbol)
            raise HTTPException(
                status_code=500, detail="internal error; see server logs"
            ) from exc

    @app.get("/api/kronos/forecast/{symbol}", dependencies=[Depends(require_auth)])
    def get_kronos_forecast(
        symbol: str,
        lookback: int = Query(400, ge=50, le=2048),
        pred_len: int = Query(15, ge=1, le=120),
        interval: str = Query("1d", max_length=8),
        period: str = Query("2y", max_length=8),
        bypass_cache: bool = Query(False),
    ) -> dict[str, Any]:
        """Kronos K-line forecast — fail-closed if model unavailable."""
        from src.alphadesk.cache import get_cached, set_cached

        try:
            from src.alphadesk.kronos_service import forecast_symbol

            params = {
                "lookback": lookback,
                "pred_len": pred_len,
                "interval": interval,
                "period": period,
            }
            if not bypass_cache:
                cached = get_cached("kronos", symbol, **params)
                if cached is not None:
                    return {"ok": True, "data": cached, "cached": True}
            payload = forecast_symbol(
                symbol,
                lookback=lookback,
                pred_len=pred_len,
                interval=interval,
                period=period,
            )
            set_cached("kronos", symbol, payload, **params)
            return {"ok": True, "data": payload, "cached": False}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("kronos forecast failed for %s", symbol)
            raise HTTPException(
                status_code=500, detail="internal error; see server logs"
            ) from exc

    @app.post("/api/desk/signal", dependencies=[Depends(require_auth)])
    async def post_desk_signal(body: dict[str, Any]) -> dict[str, Any]:
        """Validate CIO JSON, apply edge gate, persist PENDING signal.

        When ``raw`` is not parseable JSON and ``fallback_heuristic`` is true,
        emit a fail-soft debate heuristic from analysis/kronos (Committee DSML
        / tool-hallucination recovery).
        """
        from src.alphadesk.cio_schema import parse_cio_json
        from src.alphadesk.engines.debate import heuristic_debate_plan
        from src.alphadesk.store import save_signal

        symbol = str(body.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")
        try:
            if isinstance(body.get("plan"), dict):
                plan = parse_cio_json(json.dumps(body["plan"]))
            else:
                try:
                    plan = parse_cio_json(str(body.get("raw") or ""))
                except Exception as parse_exc:
                    if not body.get("fallback_heuristic"):
                        raise
                    analysis = body.get("analysis") if isinstance(body.get("analysis"), dict) else None
                    kronos = body.get("kronos") if isinstance(body.get("kronos"), dict) else None
                    plan = heuristic_debate_plan(
                        symbol, analysis=analysis, kronos=kronos
                    )
                    plan = plan.model_copy(
                        update={
                            "executive_summary": (
                                f"[Committee JSON missing — heuristic] {plan.executive_summary} "
                                f"(parse: {parse_exc})"
                            ),
                            "disconfirming_evidence": list(plan.disconfirming_evidence)
                            + ["committee_cio_json_missing"],
                        }
                    )
            signal_id = save_signal(symbol, plan, extras=body.get("extras") or {})
            return {"ok": True, "signal_id": signal_id, "plan": plan.model_dump()}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desk/paper-order", dependencies=[Depends(require_auth)])
    async def post_paper_order(body: dict[str, Any]) -> dict[str, Any]:
        """Place a paper order via dhan-paper-trade; read-after-write verify.

        Optional close_outcome ``{r_multiple, pnl_inr}`` records a closed-trade
        outcome so sleeve N / expectancy can grow (sell or explicit close).
        """
        from src.alphadesk.store import load_signal, record_outcome, update_signal_status
        from src.alphadesk.survival import apply_outcome_to_engine, scorecard_row
        from src.trading import service as trading_service
        from src.trading.profiles import load_selected_profile_id, profile_by_id

        symbol = str(body.get("symbol") or "").strip().upper()
        side = str(body.get("side") or "").strip().lower()
        quantity = body.get("quantity")
        signal_id = body.get("signal_id")
        if not symbol or side not in {"buy", "sell"}:
            raise HTTPException(status_code=400, detail="symbol and side=buy|sell required")
        if quantity is None:
            raise HTTPException(status_code=400, detail="quantity required")

        profile_id = str(
            body.get("profile_id") or load_selected_profile_id() or "dhan-paper-trade"
        )
        try:
            profile = profile_by_id(profile_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if profile.environment != "paper":
            raise HTTPException(
                status_code=403,
                detail="AlphaDesk desk orders are paper-only in v1",
            )

        order_symbol = symbol.replace(".NS", "").replace(".BO", "")
        result = trading_service.place_order(
            order_symbol,
            profile_id,
            side=side,
            quantity=float(quantity),
            order_type=str(body.get("order_type") or "market"),
        )
        ok = bool(result.get("ok", True))
        # Read-after-write: if signal_id given, reload; missing → failed
        verified = False
        signal: dict[str, Any] | None = None
        if signal_id:
            try:
                update_signal_status(
                    str(signal_id),
                    "EXECUTED" if ok else "REJECTED",
                    order_result=result,
                )
                signal = load_signal(str(signal_id))
                verified = True
            except FileNotFoundError:
                ok = False
                verified = False
                result = {
                    **(result if isinstance(result, dict) else {"raw": result}),
                    "verify_error": "signal missing after write",
                }

        outcome_row: dict[str, Any] | None = None
        close = body.get("close_outcome")
        record_close = bool(body.get("record_close")) or isinstance(close, dict)
        if ok and signal_id and record_close and side == "sell":
            close = close if isinstance(close, dict) else {}
            r_multiple = float(close.get("r_multiple") or body.get("r_multiple") or 0)
            pnl_inr = float(close.get("pnl_inr") or body.get("pnl_inr") or 0)
            engine_id = str(
                close.get("engine_id")
                or body.get("engine_id")
                or ((signal or {}).get("plan") or {}).get("engine_id")
                or "pm"
            )
            path = record_outcome(
                str(signal_id),
                {
                    "engine_id": engine_id,
                    "r_multiple": r_multiple,
                    "pnl_inr": pnl_inr,
                    "symbol": symbol,
                    "source": "paper_close",
                    "notes": body.get("notes"),
                },
            )
            state = apply_outcome_to_engine(
                engine_id, r_multiple=r_multiple, pnl_inr=pnl_inr
            )
            outcome_row = {
                "path": str(path),
                "engine": scorecard_row(state),
            }

        return {
            "ok": ok,
            "verified": verified,
            "profile_id": profile_id,
            "result": result,
            "outcome": outcome_row,
        }

    @app.post("/api/desk/outcome", dependencies=[Depends(require_auth)])
    async def post_outcome(body: dict[str, Any]) -> dict[str, Any]:
        """Record closed trade outcome and update engine sleeve / survival."""
        from src.alphadesk.store import load_signal, record_outcome
        from src.alphadesk.survival import apply_outcome_to_engine, scorecard_row

        signal_id = str(body.get("signal_id") or "").strip()
        if not signal_id:
            raise HTTPException(status_code=400, detail="signal_id required")
        try:
            signal = load_signal(signal_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="signal not found") from exc

        engine_id = str(
            body.get("engine_id")
            or (signal.get("plan") or {}).get("engine_id")
            or "pm"
        )
        r_multiple = float(body.get("r_multiple") or 0)
        pnl_inr = float(body.get("pnl_inr") or 0)
        path = record_outcome(
            signal_id,
            {
                "engine_id": engine_id,
                "r_multiple": r_multiple,
                "pnl_inr": pnl_inr,
                "notes": body.get("notes"),
                "symbol": signal.get("symbol"),
            },
        )
        state = apply_outcome_to_engine(
            engine_id, r_multiple=r_multiple, pnl_inr=pnl_inr
        )
        tax_state = None
        if body.get("gain_inr") is not None or body.get("est_tax_inr") is not None:
            from src.alphadesk.tax_ytd import record_sell_tax, summary as tax_summary

            tax_state = record_sell_tax(
                symbol=str(signal.get("symbol") or "UNKNOWN"),
                gain_inr=float(body.get("gain_inr") or body.get("pnl_inr") or 0),
                long_term_frac=float(body.get("lt_frac") or 0),
                gross_inr=float(body.get("gross_inr") or 0),
                source="outcome",
                notes=body.get("notes"),
            )
            tax_state = tax_summary(str(tax_state.get("fy")))
        return {
            "ok": True,
            "path": str(path),
            "engine": scorecard_row(state),
            "tax_ytd": tax_state,
        }

    @app.get("/api/desk/scorecard", dependencies=[Depends(require_auth)])
    async def get_scorecard() -> dict[str, Any]:
        from src.alphadesk.store import list_outcomes
        from src.alphadesk.survival import ensure_engines, scorecard_row

        engines = ensure_engines(
            [
                "pm",
                "debate",
                "br_pdh_pdl",
                "br_order_block",
                "br_orb",
                "sentiment",
                "alpha_forge",
            ]
        )
        return {
            "ok": True,
            "engines": [scorecard_row(e) for e in engines],
            "recent_outcomes": list_outcomes(limit=20),
        }

    @app.get("/api/desk/survival", dependencies=[Depends(require_auth)])
    async def get_survival() -> dict[str, Any]:
        from src.alphadesk.survival import heartbeat

        return {"ok": True, "data": heartbeat()}

    @app.post("/api/desk/survival/heartbeat", dependencies=[Depends(require_auth)])
    async def post_heartbeat() -> dict[str, Any]:
        from src.alphadesk.survival import heartbeat

        return {"ok": True, "data": heartbeat()}

    @app.post("/api/desk/survival/book", dependencies=[Depends(require_auth)])
    async def post_book_equity(body: dict[str, Any]) -> dict[str, Any]:
        from src.alphadesk.survival import update_book_equity

        equity = body.get("equity")
        if equity is None:
            raise HTTPException(status_code=400, detail="equity required")
        book = update_book_equity(float(equity))
        return {"ok": True, "book": book}

    @app.post("/api/desk/survival/reset", dependencies=[Depends(require_auth)])
    async def post_reset_engine(body: dict[str, Any]) -> dict[str, Any]:
        """Human-only revive of a DEAD engine (no auto top-up)."""
        from src.alphadesk.survival import DEFAULT_SLEEVE_INR, reset_engine, scorecard_row

        engine_id = str(body.get("engine_id") or "").strip()
        if not engine_id:
            raise HTTPException(status_code=400, detail="engine_id required")
        sleeve = float(body.get("sleeve_inr") or DEFAULT_SLEEVE_INR)
        state = reset_engine(engine_id, sleeve_inr=sleeve)
        return {"ok": True, "engine": scorecard_row(state)}

    @app.post("/api/desk/portfolio", dependencies=[Depends(require_auth)])
    async def post_portfolio(body: dict[str, Any]) -> dict[str, Any]:
        """Save holdings snapshot for portfolio-aware CIO / sell ladder."""
        from src.alphadesk.store import save_portfolio_snapshot

        holdings = body.get("holdings")
        if not isinstance(holdings, list):
            raise HTTPException(status_code=400, detail="holdings list required")
        path = save_portfolio_snapshot(holdings)
        return {"ok": True, "path": str(path), "count": len(holdings)}

    @app.get("/api/desk/portfolio", dependencies=[Depends(require_auth)])
    async def get_portfolio() -> dict[str, Any]:
        from src.alphadesk.store import load_portfolio_snapshot

        data = load_portfolio_snapshot()
        return {"ok": True, "data": data}

    @app.post("/api/desk/pm/plan", dependencies=[Depends(require_auth)])
    def post_pm_plan(body: dict[str, Any]) -> dict[str, Any]:
        """Hygiene plan + mentor opportunity scan (horizon buckets).

        Works with empty holdings — still returns where to put fresh capital
        across 1w / 1m / 3m / 6m+ horizons.
        """
        from src.alphadesk.engines.portfolio_manager import build_action_plan
        from src.alphadesk.opportunity_scan import scan_opportunities
        from src.alphadesk.store import load_portfolio_snapshot

        holdings = body.get("holdings")
        if not isinstance(holdings, list):
            snap = load_portfolio_snapshot()
            holdings = (snap or {}).get("holdings") or []
        target_net = float(body.get("target_net") or 0)
        max_names = int(body.get("max_names") or 18)
        if holdings:
            plan = build_action_plan(
                holdings, target_net=target_net, max_names=max_names
            )
        else:
            plan = {
                "engine_id": "pm",
                "total_value": 0,
                "name_count": 0,
                "overflow_names": 0,
                "core_count": 0,
                "noncore_count": 0,
                "hygiene_exits": 0,
                "ladder": [],
                "proposals": [],
                "add_candidates": [],
                "max_names": max_names,
                "raised_net": 0,
                "note": "no holdings on file — mentor scan only",
            }

        symbols = body.get("symbols")
        if not isinstance(symbols, list):
            symbols = None
        try:
            opportunities = scan_opportunities(
                symbols=symbols,
                capital_inr=target_net,
                max_per_bucket=int(body.get("max_per_bucket") or 5),
                include_etfs=bool(body.get("include_etfs", True)),
                full_universe=bool(body.get("full_universe", True)),
            )
        except Exception as exc:
            logger.exception("opportunity scan failed")
            opportunities = {"error": str(exc), "buckets": {}, "top_buys": []}

        plan = {**plan, "opportunities": opportunities}
        return {"ok": True, "plan": plan}

    @app.post("/api/desk/board", dependencies=[Depends(require_auth)])
    def post_signal_board(body: dict[str, Any]) -> dict[str, Any]:
        """Ranked long / short / steer-clear board with evidence and track record."""
        from src.alphadesk.signal_board import build_board

        from src.alphadesk.board_paper import settle_open_trades

        symbols = body.get("symbols")
        if not isinstance(symbols, list):
            symbols = None
        try:
            settled = settle_open_trades()
        except Exception:
            logger.exception("board paper settle failed")
            settled = []
        board = build_board(symbols, with_kronos=bool(body.get("with_kronos", True)))
        return {"ok": True, "data": {**board, "just_closed": settled}}

    @app.get("/api/desk/board/paper", dependencies=[Depends(require_auth)])
    def get_board_paper() -> dict[str, Any]:
        from src.alphadesk.board_paper import list_trades, with_live_prices
        from src.alphadesk.signal_board import track_record

        return {
            "ok": True,
            "trades": with_live_prices(list_trades()),
            "track_record": track_record(),
        }

    @app.post("/api/desk/board/paper", dependencies=[Depends(require_auth)])
    def post_board_paper(body: dict[str, Any]) -> dict[str, Any]:
        """Open a paper trade for one symbol using the board's own fresh levels."""
        from src.alphadesk.board_paper import DEFAULT_RISK_INR, open_trade
        from src.alphadesk.signal_board import build_board

        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")
        risk_inr = float(body.get("risk_inr") or DEFAULT_RISK_INR)
        if not 0 < risk_inr <= 100000:
            raise HTTPException(status_code=400, detail="risk_inr must be between 0 and 100000")
        members = [{"symbol": symbol, "name": body.get("name") or symbol, "industry": body.get("industry") or ""}]
        rows = build_board(members=members, universe_key=str(body.get("universe") or "core")).get("rows") or []
        if not rows:
            raise HTTPException(status_code=404, detail=f"no data for {symbol}")
        try:
            trade = open_trade(rows[0], risk_inr=risk_inr, plan=str(body.get("plan") or "fixed"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "trade": trade}

    @app.post("/api/desk/board/paper/{trade_id}/plan", dependencies=[Depends(require_auth)])
    def post_board_paper_plan(trade_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Change an open practice trade's exit style (applies from the next session)."""
        from src.alphadesk.board_paper import set_plan, with_live_prices

        try:
            trade = set_plan(trade_id, str(body.get("plan") or ""))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trade not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "trade": with_live_prices([trade])[0]}

    @app.post("/api/desk/board/paper/{trade_id}/stop", dependencies=[Depends(require_auth)])
    def post_board_paper_stop(trade_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Tighten an open practice trade's cut-loss by hand."""
        from src.alphadesk.board_paper import set_stop, with_live_prices

        try:
            price = float(body.get("price"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="price required") from exc
        try:
            trade = set_stop(trade_id, price)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trade not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "trade": with_live_prices([trade])[0]}

    @app.post("/api/desk/board/paper/{trade_id}/exit", dependencies=[Depends(require_auth)])
    def post_board_paper_exit(trade_id: str) -> dict[str, Any]:
        """Close an open practice trade now at the latest price."""
        from src.alphadesk.board_paper import exit_now, with_live_prices
        from src.alphadesk.signal_board import track_record

        try:
            trade = exit_now(trade_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trade not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "trade": with_live_prices([trade])[0], "track_record": track_record()}

    @app.post("/api/desk/board/settle", dependencies=[Depends(require_auth)])
    def post_board_settle() -> dict[str, Any]:
        from src.alphadesk.board_paper import settle_open_trades
        from src.alphadesk.signal_board import track_record

        closed = settle_open_trades()
        return {"ok": True, "closed": closed, "track_record": track_record()}

    @app.get("/api/desk/universes", dependencies=[Depends(require_auth)])
    def get_universes() -> dict[str, Any]:
        from src.alphadesk.index_lists import catalogue

        return {"ok": True, "data": catalogue()}

    def _universe(value: Any) -> str:
        from src.alphadesk.index_lists import UNIVERSES

        key = str(value or "core")
        if key not in UNIVERSES:
            raise HTTPException(status_code=400, detail=f"unknown universe {key!r}")
        return key

    @app.post("/api/desk/board/scan", dependencies=[Depends(require_auth)])
    def post_board_scan(body: dict[str, Any]) -> dict[str, Any]:
        """Start a background scan of a universe; poll /api/desk/board/jobs/{id}."""
        from src.alphadesk.board_jobs import start_scan

        key = _universe(body.get("universe"))
        job_id = start_scan(key, include_funds=bool(body.get("include_funds", True)))
        return {"ok": True, "job_id": job_id}

    @app.get("/api/desk/board/jobs/{job_id}", dependencies=[Depends(require_auth)])
    def get_board_job(job_id: str) -> dict[str, Any]:
        from src.alphadesk.board_jobs import status

        job = status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found (API restarted?)")
        return {"ok": True, "job": job}

    @app.get("/api/desk/board/last", dependencies=[Depends(require_auth)])
    def get_board_last(universe: str = Query("core", max_length=32)) -> dict[str, Any]:
        from src.alphadesk.board_jobs import load_last

        return {"ok": True, "data": load_last(_universe(universe))}

    @app.get("/api/desk/board/replay", dependencies=[Depends(require_auth)])
    def get_board_replay(universe: str = Query("core", max_length=32)) -> dict[str, Any]:
        from src.alphadesk.board_replay import load_replay

        return {"ok": True, "data": load_replay(_universe(universe))}

    @app.post("/api/desk/board/replay", dependencies=[Depends(require_auth)])
    def post_board_replay(body: dict[str, Any]) -> dict[str, Any]:
        """Start a background past test for a universe."""
        from src.alphadesk.board_jobs import start_replay

        return {"ok": True, "job_id": start_replay(_universe(body.get("universe")))}

    @app.post("/api/desk/quick-signal", dependencies=[Depends(require_auth)])
    async def post_quick_signal(body: dict[str, Any]) -> dict[str, Any]:
        """Auto BUY/WATCH call with entry + target + stop (no swarm)."""
        from src.alphadesk.cache import get_cached
        from src.alphadesk.quick_signal import build_quick_signal

        symbol = str(body.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")
        analysis = body.get("analysis") if isinstance(body.get("analysis"), dict) else None
        kronos = body.get("kronos") if isinstance(body.get("kronos"), dict) else None
        if analysis is None:
            analysis = get_cached("analysis", symbol, interval="1d", period="6mo")
        if kronos is None:
            kronos = get_cached(
                "kronos",
                symbol,
                lookback=400,
                pred_len=15,
                interval="1d",
                period="2y",
            )
        try:
            plan, signal_id = build_quick_signal(
                symbol,
                analysis=analysis,
                kronos=kronos,
                persist=bool(body.get("persist", True)),
            )
            return {
                "ok": True,
                "signal_id": signal_id,
                "plan": plan.model_dump(),
            }
        except Exception as exc:
            logger.exception("quick-signal failed for %s", symbol)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/desk/sell-ladder", dependencies=[Depends(require_auth)])
    async def post_sell_ladder(body: dict[str, Any]) -> dict[str, Any]:
        """Tax-aware sell ladder against posted or saved holdings."""
        from src.alphadesk.portfolio import prioritize_exits
        from src.alphadesk.store import load_portfolio_snapshot
        from src.alphadesk.tax_ytd import record_sell_tax, summary as tax_summary

        target = float(body.get("target_net") or 0)
        if target <= 0:
            raise HTTPException(status_code=400, detail="target_net must be > 0")
        holdings = body.get("holdings")
        if not isinstance(holdings, list):
            snap = load_portfolio_snapshot()
            holdings = (snap or {}).get("holdings") or []
        if not holdings:
            raise HTTPException(status_code=400, detail="no holdings available")
        plan = prioritize_exits(holdings, target_net=target)
        raised = sum(float(r.get("net") or 0) for r in plan)
        # Accumulate YTD estimates only when operator explicitly commits
        if body.get("record_tax") is True:
            for row in plan:
                record_sell_tax(
                    symbol=str(row.get("symbol") or "UNKNOWN"),
                    gain_inr=float(row.get("gain") or 0),
                    long_term_frac=float(row.get("lt_frac") or 0),
                    gross_inr=float(row.get("gross") or 0),
                    source="sell_ladder",
                )
        return {
            "ok": True,
            "raised_net": round(raised, 2),
            "orders": plan,
            "tax_ytd": tax_summary(),
        }

    @app.post("/api/desk/arena", dependencies=[Depends(require_auth)])
    async def post_arena(body: dict[str, Any]) -> dict[str, Any]:
        """Run PM vs Debate vs gated break-retest Arena; persist winning signal."""
        from src.alphadesk.analysis import fetch_india_ohlcv
        from src.alphadesk.arena import pick_winner
        from src.alphadesk.engines.br_engine import proposals_from_frame
        from src.alphadesk.engines.debate import heuristic_debate_plan
        from src.alphadesk.engines.portfolio_manager import proposal_for_symbol
        from src.alphadesk.idle import mark_run, should_sleep
        from src.alphadesk.store import load_portfolio_snapshot, save_signal
        from src.alphadesk.survival import is_engine_tradable
        from src.alphadesk.token_budget import record_spend

        symbol = str(body.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")

        idle = should_sleep("arena", force=bool(body.get("force")))
        if idle.get("sleep"):
            raise HTTPException(
                status_code=429,
                detail=f"arena anti-thrash: retry in {idle.get('seconds_left')}s",
            )

        # Heuristic arena ≈ cheap; reserve budget headroom for swarm later
        try:
            record_spend("arena", int(body.get("est_tokens") or 2_000), note=f"arena:{symbol}")
        except PermissionError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

        holdings = body.get("holdings")
        if not isinstance(holdings, list):
            snap = load_portfolio_snapshot()
            holdings = (snap or {}).get("holdings") or []

        proposals = []
        br_meta: dict[str, Any] = {}
        if is_engine_tradable("pm"):
            proposals.append(
                proposal_for_symbol(
                    symbol,
                    holdings,
                    target_net=float(body.get("target_net") or 0),
                )
            )

        analysis = body.get("analysis") if isinstance(body.get("analysis"), dict) else None
        kronos = body.get("kronos") if isinstance(body.get("kronos"), dict) else None
        if body.get("debate_raw"):
            from src.alphadesk.engines.debate import plan_from_cio_text

            try:
                proposals.append(
                    plan_from_cio_text(str(body["debate_raw"]), symbol=symbol)
                )
            except Exception as exc:
                logger.warning("debate_raw parse failed: %s", exc)
        elif is_engine_tradable("debate"):
            if analysis is None:
                try:
                    from src.alphadesk.cache import get_cached

                    analysis = get_cached(
                        "analysis", symbol, interval="1d", period="6mo"
                    )
                except Exception:
                    analysis = None
            if kronos is None:
                try:
                    from src.alphadesk.cache import get_cached

                    kronos = get_cached(
                        "kronos",
                        symbol,
                        lookback=400,
                        pred_len=15,
                        interval="1d",
                        period="2y",
                    )
                except Exception:
                    kronos = None
            proposals.append(
                heuristic_debate_plan(symbol, analysis=analysis, kronos=kronos)
            )

        # Break-retest engines (PDH/PDL primary; OB; ORB if intraday)
        include_br = body.get("include_break_retest", True)
        ohlcv_df = None
        if include_br or body.get("include_sentiment", True) or body.get(
            "include_alpha_forge", True
        ):
            try:
                _ticker, ohlcv_df = fetch_india_ohlcv(symbol, period="1y", interval="1d")
            except Exception as exc:
                logger.warning("ohlcv fetch for arena extras failed: %s", exc)

        if include_br and ohlcv_df is not None:
            try:
                br_plans, br_meta = proposals_from_frame(ohlcv_df)
                proposals.extend(br_plans)
            except Exception as exc:
                logger.warning("break-retest skipped for %s: %s", symbol, exc)
                br_meta = {"error": str(exc)}

        if body.get("include_sentiment", True) and is_engine_tradable("sentiment"):
            try:
                from src.alphadesk.engines.sentiment_engine import (
                    proposal_for_symbol as sentiment_proposal,
                )

                px = float((analysis or {}).get("current_price") or 0)
                proposals.append(sentiment_proposal(symbol, price=px))
            except Exception as exc:
                logger.warning("sentiment skipped: %s", exc)

        if body.get("include_alpha_forge", True) and is_engine_tradable("alpha_forge"):
            if ohlcv_df is not None:
                try:
                    from src.alphadesk.engines.alpha_forge import (
                        proposal_for_symbol as alpha_proposal,
                    )

                    px = float((analysis or {}).get("current_price") or 0)
                    proposals.append(
                        alpha_proposal(symbol, ohlcv_df, price=px)
                    )
                except Exception as exc:
                    logger.warning("alpha_forge skipped: %s", exc)

        winner, votes = pick_winner(proposals)
        if winner is None:
            raise HTTPException(
                status_code=409,
                detail="no tradable engines (all DEAD or empty proposals)",
            )
        signal_id = save_signal(
            symbol,
            winner,
            extras={
                "arena_votes": votes,
                "mode": "arena_pm_debate_br",
                "break_retest": br_meta,
            },
        )
        mark_run("arena", meta={"symbol": symbol, "signal_id": signal_id})
        return {
            "ok": True,
            "signal_id": signal_id,
            "plan": winner.model_dump(),
            "votes": votes,
            "levels": br_meta.get("levels") or [],
            "gates": br_meta.get("gates") or {},
        }

    @app.get("/api/desk/break-retest/{symbol}", dependencies=[Depends(require_auth)])
    def get_break_retest(
        symbol: str,
        period: str = Query("1y", max_length=8),
        interval: str = Query("1d", max_length=8),
    ) -> dict[str, Any]:
        """Detect PDH/PDL / order-block / ORB levels + backtest gate status."""
        from src.alphadesk.analysis import fetch_india_ohlcv
        from src.alphadesk.engines.br_engine import proposals_from_frame

        try:
            _ticker, df = fetch_india_ohlcv(symbol, period=period, interval=interval)
            plans, meta = proposals_from_frame(df)
            return {
                "ok": True,
                "data": {
                    "plans": [p.model_dump() for p in plans],
                    "levels": meta.get("levels") or [],
                    "gates": meta.get("gates") or {},
                },
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("break-retest failed for %s", symbol)
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/desk/sentiment/{symbol}", dependencies=[Depends(require_auth)])
    def get_sentiment(symbol: str) -> dict[str, Any]:
        from src.alphadesk.engines.sentiment_engine import diagnose, proposal_for_symbol

        try:
            diag = diagnose(symbol)
            plan = proposal_for_symbol(symbol)
            return {"ok": True, "data": {"diagnose": diag, "plan": plan.model_dump()}}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/desk/alpha-forge/{symbol}", dependencies=[Depends(require_auth)])
    def get_alpha_forge(
        symbol: str,
        period: str = Query("1y", max_length=8),
    ) -> dict[str, Any]:
        from src.alphadesk.analysis import fetch_india_ohlcv
        from src.alphadesk.engines.alpha_forge import diagnose, proposal_for_symbol

        try:
            _t, df = fetch_india_ohlcv(symbol, period=period, interval="1d")
            return {
                "ok": True,
                "data": {
                    "diagnose": diagnose(df, symbol),
                    "plan": proposal_for_symbol(symbol, df).model_dump(),
                },
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/desk/universe", dependencies=[Depends(require_auth)])
    async def get_universe() -> dict[str, Any]:
        """Capped Debate universe: holdings + 10 watchlist."""
        from src.alphadesk.engines.debate import capped_universe
        from src.alphadesk.store import load_portfolio_snapshot

        snap = load_portfolio_snapshot()
        holdings = (snap or {}).get("holdings") or []
        return {"ok": True, "symbols": capped_universe(holdings)}

    # --- Phase 4: manual tiny live ---

    @app.get("/api/desk/live/status", dependencies=[Depends(require_auth)])
    async def get_live_status(
        broker: str = Query("dhan", max_length=32),
    ) -> dict[str, Any]:
        from src.alphadesk.live_unlock import load_live_sync, list_tickets, status_all

        return {
            "ok": True,
            "data": status_all(broker=broker),
            "sync": load_live_sync(),
            "tickets": list_tickets(limit=10),
        }

    @app.post("/api/desk/live/unlock", dependencies=[Depends(require_auth)])
    async def post_live_unlock(body: dict[str, Any]) -> dict[str, Any]:
        """Human grant after expectancy gate. action=grant|revoke."""
        from src.alphadesk.live_unlock import grant_unlock, revoke_unlock

        engine_id = str(body.get("engine_id") or "").strip()
        if not engine_id:
            raise HTTPException(status_code=400, detail="engine_id required")
        action = str(body.get("action") or "grant").strip().lower()
        approved_by = str(body.get("approved_by") or "operator")
        try:
            if action == "revoke":
                rec = revoke_unlock(engine_id)
            else:
                rec = grant_unlock(engine_id, approved_by=approved_by)
            return {"ok": True, "unlock": rec}
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/api/desk/signal/{signal_id}/approve", dependencies=[Depends(require_auth)])
    async def post_signal_approve(
        signal_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from src.alphadesk.live_unlock import approve_signal

        body = body or {}
        try:
            signal = approve_signal(
                signal_id,
                approved_by=str(body.get("approved_by") or "operator"),
            )
            return {"ok": True, "signal": signal}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="signal not found") from exc

    @app.post("/api/desk/live/sync", dependencies=[Depends(require_auth)])
    async def post_live_sync(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Pull live-readonly account/positions (P&L truth). No order placement."""
        from src.alphadesk.live_unlock import LIVE_READONLY_PROFILE, sync_live_readonly

        body = body or {}
        profile_id = str(body.get("profile_id") or LIVE_READONLY_PROFILE)
        try:
            payload = sync_live_readonly(profile_id=profile_id)
            return {"ok": True, "data": payload}
        except Exception as exc:
            logger.exception("live-readonly sync failed")
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/desk/live/ticket", dependencies=[Depends(require_auth)])
    async def post_live_ticket(body: dict[str, Any]) -> dict[str, Any]:
        """Issue a gated manual ticket (copyable order). No broker API place."""
        from src.alphadesk.live_unlock import issue_manual_ticket
        from src.alphadesk.store import load_signal

        signal_id = str(body.get("signal_id") or "").strip()
        if not signal_id:
            raise HTTPException(status_code=400, detail="signal_id required")
        try:
            signal = load_signal(signal_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="signal not found") from exc

        engine_id = str(
            body.get("engine_id")
            or (signal.get("plan") or {}).get("engine_id")
            or "br_pdh_pdl"
        )
        symbol = str(body.get("symbol") or signal.get("symbol") or "").strip()
        side = str(body.get("side") or "buy").strip().lower()
        quantity = float(body.get("quantity") or 1)
        limit_price = body.get("limit_price")
        notional = body.get("notional_inr")
        if notional is None and limit_price is not None:
            notional = float(limit_price) * quantity
        try:
            ticket = issue_manual_ticket(
                engine_id=engine_id,
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                notional_inr=float(notional) if notional is not None else None,
                order_type=str(body.get("order_type") or "limit"),
                limit_price=float(limit_price) if limit_price is not None else None,
                product=str(body.get("product") or "CNC"),
                broker=str(body.get("broker") or "dhan"),
                notes=body.get("notes"),
            )
            return {"ok": True, "ticket": ticket}
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/desk/live/ticket/{ticket_id}/fill",
        dependencies=[Depends(require_auth)],
    )
    async def post_live_ticket_fill(
        ticket_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Operator confirms they placed the order manually in the broker app."""
        from src.alphadesk.live_unlock import mark_ticket_filled

        body = body or {}
        try:
            ticket = mark_ticket_filled(
                ticket_id,
                fill_price=(
                    float(body["fill_price"]) if body.get("fill_price") is not None else None
                ),
                fill_qty=(
                    float(body["fill_qty"]) if body.get("fill_qty") is not None else None
                ),
                broker_order_id=body.get("broker_order_id"),
                notes=body.get("notes"),
            )
            return {"ok": True, "ticket": ticket}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="ticket not found") from exc

    @app.get("/api/desk/live/tickets", dependencies=[Depends(require_auth)])
    async def get_live_tickets(
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        from src.alphadesk.live_unlock import list_tickets

        return {"ok": True, "tickets": list_tickets(limit=limit)}

    @app.post("/api/desk/live/replay-br-pdh-pdl", dependencies=[Depends(require_auth)])
    def post_replay_br_pdh_pdl(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Farm paper expectancy for br_pdh_pdl from OHLCV backtest sim (option C)."""
        from src.alphadesk.analysis import fetch_india_ohlcv
        from src.alphadesk.paper_replay import replay_br_pdh_pdl

        body = body or {}
        symbol = str(body.get("symbol") or "RELIANCE").strip()
        period = str(body.get("period") or "2y")
        max_trades = int(body.get("max_trades") or 40)
        risk_inr = float(body.get("risk_inr") or 500)
        skip_if = body.get("skip_if_n_ge")
        try:
            _t, df = fetch_india_ohlcv(symbol, period=period, interval="1d")
            result = replay_br_pdh_pdl(
                df,
                symbol=symbol,
                max_trades=max_trades,
                risk_inr=risk_inr,
                skip_if_n_ge=int(skip_if) if skip_if is not None else None,
            )
            return {"ok": bool(result.get("ok")), "data": result}
        except Exception as exc:
            logger.exception("br_pdh_pdl replay failed")
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # --- Phase 5: hardening ---

    @app.post("/api/desk/morning", dependencies=[Depends(require_auth)])
    async def post_morning(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Morning desk: heartbeat + PM on capped universe + report (cron-safe)."""
        from src.alphadesk.morning_scan import run_morning_desk

        body = body or {}
        result = run_morning_desk(
            target_net=float(body.get("target_net") or 0),
            force=bool(body.get("force")),
        )
        return {"ok": True, "data": result}

    @app.get("/api/desk/tax-ytd", dependencies=[Depends(require_auth)])
    async def get_tax_ytd(fy: str | None = Query(None, max_length=16)) -> dict[str, Any]:
        from src.alphadesk.tax_ytd import summary

        return {"ok": True, "data": summary(fy)}

    @app.post("/api/desk/tax-ytd", dependencies=[Depends(require_auth)])
    async def post_tax_ytd(body: dict[str, Any]) -> dict[str, Any]:
        """Manually record a realized sell tax estimate."""
        from src.alphadesk.tax_ytd import record_sell_tax, summary

        symbol = str(body.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")
        state = record_sell_tax(
            symbol=symbol,
            gain_inr=float(body.get("gain_inr") or 0),
            long_term_frac=float(body.get("lt_frac") or 0),
            gross_inr=float(body.get("gross_inr") or 0),
            source=str(body.get("source") or "manual"),
            notes=body.get("notes"),
        )
        return {"ok": True, "state": state, "summary": summary(str(state.get("fy")))}

    @app.get("/api/desk/token-budget", dependencies=[Depends(require_auth)])
    async def get_token_budget() -> dict[str, Any]:
        from src.alphadesk.token_budget import status

        return {"ok": True, "data": status()}

    @app.post("/api/desk/audit/export", dependencies=[Depends(require_auth)])
    async def post_audit_export(body: dict[str, Any] | None = None) -> dict[str, Any]:
        from src.alphadesk.audit_export import export_audit

        body = body or {}
        result = export_audit(as_zip=bool(body.get("as_zip", True)))
        return {"ok": True, "data": result}
