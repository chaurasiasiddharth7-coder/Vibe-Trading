"""Registers the routes behind the Kite-style desk pages in one call (keeps api_server.py a thin assembler)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import FastAPI


def register_desk_pages_routes(app: FastAPI, require_auth: Callable[..., Awaitable[Any] | Any]) -> None:
    from src.api.strategies_routes import register_strategies_routes
    from src.api.users_routes import register_users_routes
    from src.api.watchlist_routes import register_watchlist_routes
    from src.api.wealth_routes import register_wealth_routes

    register_users_routes(app, require_auth)
    register_wealth_routes(app, require_auth)
    register_strategies_routes(app, require_auth)
    register_watchlist_routes(app, require_auth)

    import os

    if os.environ.get("INTRADAY_SCHEDULER", "") == "1":  # the hosted server: keep the NSE price store current
        from src.alphadesk import nse_daily

        nse_daily.start()
