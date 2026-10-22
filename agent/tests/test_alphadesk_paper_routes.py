"""Route tests for practice-trade actions: bad input is a 4xx, never a 500."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.alphadesk import board_paper
from src.api import alphadesk_routes
from src.api.security import require_auth


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(board_paper, "_dir", lambda: tmp_path)
    monkeypatch.setattr("src.alphadesk.store.record_outcome", lambda *a, **k: None)
    monkeypatch.setattr("src.alphadesk.survival.apply_outcome_to_engine", lambda *a, **k: None)
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: None
    sig = alphadesk_routes.register_alphadesk_routes.__code__.co_varnames  # noqa: F841
    alphadesk_routes.register_alphadesk_routes(app, require_auth=require_auth)
    return TestClient(app)


def test_unknown_or_malformed_trade_id_is_404(client):
    for path in ("stop", "exit"):
        r = client.post(f"/api/desk/board/paper/not-a-uuid/{path}", json={"price": 1})
        assert r.status_code == 404
    r = client.post("/api/desk/board/paper/00000000-0000-0000-0000-000000000000/exit", json={})
    assert r.status_code == 404


def test_stop_requires_numeric_price(client):
    r = client.post("/api/desk/board/paper/00000000-0000-0000-0000-000000000000/stop", json={"price": "abc"})
    assert r.status_code == 400
    r = client.post("/api/desk/board/paper/00000000-0000-0000-0000-000000000000/stop", json={})
    assert r.status_code == 400


def _open_with_bars(monkeypatch, bars):
    monkeypatch.setattr("src.alphadesk.signal_board.expected_session", lambda *a, **k: "d1")
    monkeypatch.setattr("src.alphadesk.signal_board._load_analysis", lambda sym: {"chart_data": bars})
    return board_paper.open_trade(
        {"symbol": "T", "side": "LONG", "entry": 100.0, "stop": 97.0, "target": 106.0, "as_of": "d0"}, risk_inr=300
    )


BARS = [{"time": "d1", "open": 101, "high": 104, "low": 100, "close": 103.0}]


def test_move_stop_then_exit_end_to_end(client, monkeypatch):
    trade = _open_with_bars(monkeypatch, BARS)
    bad = client.post(f"/api/desk/board/paper/{trade['id']}/stop", json={"price": 90})
    assert bad.status_code == 400 and "above" in bad.json()["detail"]
    ok = client.post(f"/api/desk/board/paper/{trade['id']}/stop", json={"price": 100.5})
    assert ok.status_code == 200 and ok.json()["trade"]["stop_moves"][0]["price"] == 100.5
    out = client.post(f"/api/desk/board/paper/{trade['id']}/exit", json={})
    body = out.json()
    assert out.status_code == 200 and body["trade"]["status"] == "CLOSED"
    assert body["trade"]["exit_reason"] == "manual" and body["trade"]["exit_price"] == 103.0
    again = client.post(f"/api/desk/board/paper/{trade['id']}/exit", json={})
    assert again.status_code == 400


def test_listing_flags_suspect_price_jump(client, monkeypatch):
    bars = BARS + [{"time": "d2", "open": 80, "high": 82, "low": 79, "close": 80.0}]
    _open_with_bars(monkeypatch, bars)
    trades = client.get("/api/desk/board/paper").json()["trades"]
    assert trades[0]["data_warning"]["date"] == "d2"
