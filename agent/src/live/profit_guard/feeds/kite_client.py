"""Read-only Kite Connect client. DATA ONLY: this module can never place, change or cancel an order.

Decision (Sid, 19 Nov 2026): the Kite API key is used for real-time and historical data only;
every trade is paper. So this wrapper exposes an explicit allowlist of read methods and nothing
else. The underlying ``KiteConnect`` object is private and is never handed out.

``tests/profit_guard/test_kite_readonly.py`` fails the build if:
  * any write method (orders, GTTs, MF orders/SIPs, position conversion) becomes reachable here;
  * any other file in the repo imports ``kiteconnect``.
"""

from __future__ import annotations

import os
from typing import Any, Callable

#: Every read the guard needs. Anything not listed here does not exist on this client.
READ_METHODS: frozenset[str] = frozenset({
    "login_url",
    "profile",
    "margins",
    "holdings",
    "positions",
    "mf_holdings",
    "mf_sips",
    "orders",
    "trades",
    "quote",
    "ltp",
    "ohlc",
    "historical_data",
    "instruments",
})

#: Kite write methods. Listed so the test can prove none of them is reachable.
FORBIDDEN_METHODS: frozenset[str] = frozenset({
    "place_order", "modify_order", "cancel_order", "exit_order", "place_autoslice_order",
    "place_gtt", "modify_gtt", "delete_gtt", "convert_position",
    "place_mf_order", "cancel_mf_order", "place_mf_sip", "modify_mf_sip", "cancel_mf_sip",
})


class KiteReadOnlyError(RuntimeError):
    pass


class ReadOnlyKite:
    """Allowlisted, read-only facade over ``kiteconnect.KiteConnect``."""

    __slots__ = ("_k", "api_key")

    def __init__(self, api_key: str | None = None, access_token: str | None = None,
                 factory: Callable[..., Any] | None = None) -> None:
        api_key = api_key or os.environ.get("KITE_API_KEY", "").strip()
        if not api_key:
            raise KiteReadOnlyError("KITE_API_KEY is not set (put it in agent/.env)")
        if factory is None:
            from kiteconnect import KiteConnect  # the ONLY kiteconnect import for REST in the repo

            factory = KiteConnect
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "_k", factory(api_key=api_key))
        if access_token:
            object.__getattribute__(self, "_k").set_access_token(access_token)

    # -- session (needs the secret; returns only what we store) ------------------
    def exchange_request_token(self, request_token: str, api_secret: str | None = None) -> dict[str, Any]:
        secret = api_secret or os.environ.get("KITE_API_SECRET", "").strip()
        if not secret:
            raise KiteReadOnlyError("KITE_API_SECRET is not set (put it in agent/.env)")
        k = object.__getattribute__(self, "_k")
        data = k.generate_session(request_token, api_secret=secret)
        k.set_access_token(data["access_token"])
        return {"access_token": data["access_token"], "user_id": data.get("user_id"),
                "login_time": str(data.get("login_time") or "")}

    # -- reads ---------------------------------------------------------------------
    def __getattribute__(self, name: str) -> Any:
        # The raw KiteConnect object is never reachable through the public object (rk._k.place_order is blocked).
        if name == "_k" or (name.startswith("_") and not name.startswith("__")):
            raise KiteReadOnlyError("the underlying Kite client is private: only read methods are available")
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        if name in FORBIDDEN_METHODS:
            raise KiteReadOnlyError(f"'{name}' is blocked: the Kite key is for market data only; trading is paper")
        if name not in READ_METHODS:
            raise AttributeError(f"ReadOnlyKite has no '{name}' (only {sorted(READ_METHODS)})")
        return getattr(object.__getattribute__(self, "_k"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise KiteReadOnlyError("ReadOnlyKite is immutable")

    def __dir__(self) -> list[str]:
        return sorted(READ_METHODS | {"exchange_request_token", "api_key"})
