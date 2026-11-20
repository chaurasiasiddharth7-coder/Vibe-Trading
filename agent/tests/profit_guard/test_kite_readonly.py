"""K1 safety + feed tests. The first group is the guarantee: Kite is DATA ONLY."""

from __future__ import annotations

import os
import re
import stat
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from src.live.profit_guard import session
from src.live.profit_guard.feeds import candle_cache, kite_auth
from src.live.profit_guard.feeds.kite_client import (
    FORBIDDEN_METHODS,
    READ_METHODS,
    KiteReadOnlyError,
    ReadOnlyKite,
)
from src.live.profit_guard.feeds.kite_ticker import TickFeed, normalize_order_update

AGENT = Path(__file__).resolve().parents[2]
FEEDS = AGENT / "src" / "live" / "profit_guard" / "feeds"


class FakeKite:
    def __init__(self, api_key):
        self.api_key, self.token, self.calls = api_key, None, []

    def set_access_token(self, t):
        self.token = t

    def generate_session(self, request_token, api_secret):
        return {"access_token": "tok123", "user_id": "AB1234"}

    def ltp(self, *a):
        self.calls.append("ltp")
        return {"NSE:NIFTY 50": {"last_price": 24512.3}}

    def place_order(self, **kw):  # must never be reachable through the wrapper
        raise AssertionError("place_order reached the real client")


# ----------------------------------------------------------------- the guarantee
@pytest.mark.parametrize("name", sorted(FORBIDDEN_METHODS))
def test_every_write_method_is_blocked(name):
    k = ReadOnlyKite("key", factory=FakeKite)
    with pytest.raises(KiteReadOnlyError, match="market data only"):
        getattr(k, name)


def test_unknown_methods_do_not_exist_and_reads_work():
    k = ReadOnlyKite("key", factory=FakeKite)
    with pytest.raises(AttributeError):
        k.anything_else
    assert k.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"] == 24512.3
    assert not (set(dir(k)) & FORBIDDEN_METHODS)
    assert not (READ_METHODS & FORBIDDEN_METHODS)
    with pytest.raises(KiteReadOnlyError):
        k.some_attr = 1


def test_only_the_feeds_import_kiteconnect():
    pat = re.compile(r"^\s*(from\s+kiteconnect\b|import\s+kiteconnect\b)", re.M)
    offenders = []
    for p in AGENT.rglob("*.py"):
        if "__pycache__" in p.parts or "tests" in p.parts or ".venv" in p.parts:
            continue
        if pat.search(p.read_text(encoding="utf-8", errors="ignore")) and p.parent != FEEDS:
            offenders.append(str(p.relative_to(AGENT)))
    assert offenders == [], f"kiteconnect imported outside the read-only feeds: {offenders}"
    ticker_src = (FEEDS / "kite_ticker.py").read_text()
    assert re.findall(r"from kiteconnect import (\w+)", ticker_src) == ["KiteTicker"]


def test_feeds_never_call_a_write_method():
    for p in FEEDS.glob("*.py"):
        src = p.read_text()
        for name in FORBIDDEN_METHODS:
            assert not re.search(r"\.\s*" + name + r"\s*\(", src), f"{p.name} calls {name}()"


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    with pytest.raises(KiteReadOnlyError, match="KITE_API_KEY"):
        ReadOnlyKite(factory=FakeKite)


# ----------------------------------------------------------------- daily login
def test_token_freshness_and_expiry():
    d = date(2026, 11, 20)
    t10 = session.ist_ms(d, time(10, 0))
    exp = kite_auth.next_expiry_ms(t10)
    assert session.to_ist(exp).date() == date(2026, 11, 21) and session.to_ist(exp).hour == 7 and session.to_ist(exp).minute == 30
    rec = {"access_token": "x", "expires_at_ms": exp}
    assert kite_auth.token_is_fresh(rec, t10)
    assert not kite_auth.token_is_fresh(rec, exp)
    assert not kite_auth.token_is_fresh(None, t10)
    assert not kite_auth.token_is_fresh({"access_token": "x", "expires_at_ms": "bad"}, t10)


def test_parse_request_token():
    url = "https://desk.fairscoreguide.com/zerodha/callback?action=login&type=login&status=success&request_token=Abc123XYZ"
    assert kite_auth.parse_request_token(url) == "Abc123XYZ"
    assert kite_auth.parse_request_token("Abc123XYZ") == "Abc123XYZ"
    with pytest.raises(ValueError):
        kite_auth.parse_request_token(url.replace("status=success", "status=error"))
    with pytest.raises(ValueError):
        kite_auth.parse_request_token("not a token!")


def test_exchange_and_save_is_private(tmp_path):
    k = ReadOnlyKite("key", factory=FakeKite)
    p = tmp_path / "tok.json"
    rec = kite_auth.save_token(k.exchange_request_token("Abc123", api_secret="s"), 1_000, path=p)
    assert rec["access_token"] == "tok123" and rec["user_id"] == "AB1234"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert kite_auth.load_token(p)["access_token"] == "tok123"


# ----------------------------------------------------------------- ticker
class FakeTicker:
    MODE_LTP = "ltp"

    def __init__(self, api_key, access_token, **kw):
        self.kw, self.subs, self.modes = kw, [], []

    def subscribe(self, t):
        self.subs += t

    def set_mode(self, mode, t):
        self.modes.append((mode, list(t)))

    def connect(self, threaded=False):
        self.on_connect(self, {})


def test_tick_feed_latest_and_stale():
    now = [1_000_000]
    f = TickFeed("k", "t", ticker_factory=FakeTicker, clock=lambda: now[0])
    assert f.kws.kw["reconnect_max_delay"] == 30
    f.subscribe({"NIFTY 50": 256265})
    f.start()
    assert f.kws.subs == [256265]
    f._on_ticks(f.kws, [{"instrument_token": 256265, "last_price": 24512.3}])
    assert f.latest("NIFTY 50").ltp == Decimal("24512.3")
    assert not f.is_stale("NIFTY 50")
    now[0] += 30_001
    assert f.is_stale("NIFTY 50")
    assert f.health()["stale_symbols"] == ["NIFTY 50"]


def test_order_update_normalizer():
    base = {"order_id": "1", "tradingsymbol": "SBIN", "quantity": 10, "average_price": 812.3}
    assert normalize_order_update({**base, "status": "COMPLETE", "filled_quantity": 10})["status"] == "COMPLETE"
    assert normalize_order_update({**base, "status": "REJECTED"})["status"] == "REJECTED"
    assert normalize_order_update({**base, "status": "CANCELLED"})["status"] == "CANCELLED"
    assert normalize_order_update({**base, "status": "OPEN", "filled_quantity": 4})["status"] == "PARTIAL"


# ----------------------------------------------------------------- candle cache
class CandleKite:
    def __init__(self):
        self.calls = 0

    def historical_data(self, token, start, end, interval):
        self.calls += 1
        from datetime import datetime, timedelta

        out, d = [], start.date()
        while d <= end.date():
            out.append({"date": datetime.combine(d, time(15, 29)), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10})
            d += timedelta(days=1)
        return out


def test_candle_cache_reuses_complete_months():
    k = CandleKite()
    rows = candle_cache.get_candles(k, "SBIN", 779521, "day", date(2026, 9, 1), date(2026, 10, 31), today=date(2026, 11, 19))
    assert len(rows) == 61 and k.calls == 2
    again = candle_cache.get_candles(k, "SBIN", 779521, "day", date(2026, 9, 1), date(2026, 10, 31), today=date(2026, 11, 19))
    assert len(again) == 61 and k.calls == 2  # zero new API calls


def test_windows_respect_kite_caps():
    w = candle_cache.windows(date(2026, 1, 1), date(2026, 6, 30), "minute")
    assert all((b - a).days + 1 <= 60 for a, b in w) and w[0][0] == date(2026, 1, 1) and w[-1][1] == date(2026, 6, 30)
