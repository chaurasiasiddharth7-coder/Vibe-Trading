"""Kite live prices (owner only) and the watchlist home screen."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest

from src.live.profit_guard.feeds import live_quotes as LQ
from src.alphadesk import watchlist as W

IST = LQ.IST
NOW = datetime(2026, 11, 20, 11, 0, tzinfo=IST)


def _q(ltp=105.0, o=100.0, h=106.0, l=99.0, prev=98.0, day=NOW):
    return {"last_price": ltp, "ohlc": {"open": o, "high": h, "low": l, "close": prev},
            "volume": 1234, "last_trade_time": day.replace(tzinfo=None)}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    LQ._CACHE.clear()
    monkeypatch.setattr(LQ, "_LAST_CALL", [0.0])
    monkeypatch.setattr(LQ.time, "sleep", lambda s: None)


def test_today_bar_from_quote():
    bar = LQ.today_bar(_q(), NOW)
    assert bar == {"open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0, "volume": 1234.0}


def test_today_bar_ignores_yesterdays_quote():
    from datetime import timedelta

    assert LQ.today_bar(_q(day=NOW - timedelta(days=1)), NOW) is None


def test_overlay_appends_today():
    idx = pd.to_datetime(["2026-11-18", "2026-11-19"])
    df = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2], "volume": [5, 6]}, index=idx)
    out, used = LQ.overlay(df, "RELIANCE", fetch=lambda keys: {k: _q() for k in keys}, now=NOW)
    assert used and len(out) == 3
    assert out.iloc[-1]["close"] == 105.0 and out.index[-1] == pd.Timestamp("2026-11-20")


def test_overlay_replaces_todays_row():
    idx = pd.to_datetime(["2026-11-19", "2026-11-20"])
    df = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2]}, index=idx)
    out, used = LQ.overlay(df, "LT", fetch=lambda keys: {k: _q(ltp=110, h=111) for k in keys}, now=NOW)
    assert used and len(out) == 2 and out.iloc[-1]["close"] == 110 and out.iloc[-1]["high"] == 111


def test_prime_batches_in_one_call_per_450():
    calls = []

    def fetch(keys):
        calls.append(len(keys))
        return {k: _q() for k in keys}

    n = LQ.prime([f"S{i}" for i in range(600)], fetch=fetch)
    assert n == 600 and calls == [450, 150]


def test_not_active_for_another_profile(monkeypatch):
    from src.alphadesk import profiles

    monkeypatch.setattr(LQ, "_client", lambda: object())
    with profiles.use("friend"):
        assert LQ.active() is False
    assert LQ.active() is True


def test_watchlist_crud(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "_path", lambda: tmp_path / "w.json")
    assert len(W.load()) == 5
    W.add(0, "rba")
    W.add(0, "NSE:LT")
    lists = W.add(0, "LT")
    assert lists[0]["items"] == ["RBA", "LT"]
    assert W.rename(1, "banks")[1]["name"] == "banks"
    assert W.reorder(0, ["LT", "RBA"])[0]["items"] == ["LT", "RBA"]
    assert W.remove(0, "LT")[0]["items"] == ["RBA"]
    with pytest.raises(ValueError):
        W.add(0, "bad symbol!")
    with pytest.raises(ValueError):
        W.add(9, "LT")


def test_quotes_eod_for_others(monkeypatch):
    monkeypatch.setattr(LQ, "active", lambda: False)
    monkeypatch.setattr(W, "_eod_tables", lambda: (
        {"LT": {"ltp": 4209.1, "prev": 4211.0, "as_of": "2026-11-19"}},
        {"NIFTY 50": {"ltp": 25800.6, "prev": 25514.3, "as_of": "2026-11-19"}}))
    out = W.quotes(["LT"])
    assert out["live"] is False and out["source"] == "nse_eod"
    assert out["quotes"]["LT"]["change"] == -1.9
    assert out["indices"][0]["symbol"] == "NIFTY 50" and out["indices"][0]["change_pct"] == 1.12


def test_quotes_live_for_owner(monkeypatch):
    monkeypatch.setattr(LQ, "active", lambda: True)
    monkeypatch.setattr(LQ, "_client", lambda: type("K", (), {"quote": staticmethod(lambda keys: {k: _q() for k in keys})})())
    out = W.quotes(["RBA"])
    assert out["live"] is True and out["source"] == "kite"
    assert out["quotes"]["RBA"]["ltp"] == 105.0 and out["quotes"]["RBA"]["change"] == 7.0
    assert {i["symbol"] for i in out["indices"]} == {"NIFTY 50", "NIFTY BANK"}


def test_token_import(tmp_path):
    from src.live.profit_guard.feeds import kite_auth

    p = tmp_path / "t.json"
    rec = {"access_token": "x", "user_id": "AB1", "created_at_ms": 1, "expires_at_ms": 10**13, "api_key": "k", "secret": "no"}
    assert kite_auth.import_token(json.dumps(rec), path=p, now_ms=5) == 0
    saved = json.loads(p.read_text())
    assert "secret" not in saved and saved["api_key"] == "k"
    assert kite_auth.import_token(json.dumps({**rec, "expires_at_ms": 2}), path=p, now_ms=5) == 1
