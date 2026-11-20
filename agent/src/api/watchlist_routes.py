"""Watchlist and quotes routes for the Kite-style home screen (under /api/desk/ so the hosted proxy forwards them).

    GET    /api/desk/watchlists                       the five lists
    POST   /api/desk/watchlists/{i}/items             {symbol}
    DELETE /api/desk/watchlists/{i}/items/{symbol}
    PATCH  /api/desk/watchlists/{i}                   {name?, items?}  rename or reorder
    GET    /api/desk/quotes?symbols=A,B&indices=1     live from Kite on the owner's desk, else end of day
    GET    /api/desk/symbols?q=REL                    symbol search
    GET    /api/desk/brief                            today's brief: indices, insight, top picks, news
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import Body, Depends, FastAPI, HTTPException, Query

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_watchlist_routes(app: FastAPI, require_auth: AuthDep) -> None:
    from src.alphadesk import watchlist as W

    def run(fn, *a):
        try:
            return {"lists": fn(*a)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/desk/watchlists", dependencies=[Depends(require_auth)])
    def lists() -> dict[str, Any]:
        return {"lists": W.load(), "max_items": W.MAX_ITEMS}

    @app.post("/api/desk/watchlists/{i}/items", dependencies=[Depends(require_auth)])
    def add(i: int, body: dict = Body(...)) -> dict[str, Any]:
        return run(W.add, i, str(body.get("symbol", "")))

    @app.delete("/api/desk/watchlists/{i}/items/{symbol}", dependencies=[Depends(require_auth)])
    def remove(i: int, symbol: str) -> dict[str, Any]:
        return run(W.remove, i, symbol)

    @app.patch("/api/desk/watchlists/{i}", dependencies=[Depends(require_auth)])
    def patch(i: int, body: dict = Body(...)) -> dict[str, Any]:
        out: dict[str, Any] = {"lists": W.load()}
        if "name" in body:
            out = run(W.rename, i, str(body["name"]))
        if "items" in body:
            if not isinstance(body["items"], list):
                raise HTTPException(status_code=400, detail="items must be a list")
            out = run(W.reorder, i, [str(s) for s in body["items"]])
        return out

    @app.get("/api/desk/quotes", dependencies=[Depends(require_auth)])
    def quotes(symbols: str = Query(""), indices: int = Query(1)) -> dict[str, Any]:
        syms = [s for s in symbols.split(",") if s.strip()]
        return W.quotes(syms, with_indices=bool(indices))

    @app.get("/api/desk/brief", dependencies=[Depends(require_auth)])
    def brief() -> dict[str, Any]:
        from src.alphadesk import market_brief

        watch = sorted({s for lst in W.load() for s in lst["items"]})
        return market_brief.brief(watch)

    @app.get("/api/desk/symbols", dependencies=[Depends(require_auth)])
    def symbols(q: str = Query("", max_length=20)) -> dict[str, Any]:
        return {"results": W.search(q)}
