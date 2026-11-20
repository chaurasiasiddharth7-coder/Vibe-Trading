"""Portfolio groups, asset tabs, F&O helpers, suggestions, and the owner-only rule for Kite data."""

from datetime import date

import pytest

from src.alphadesk import profiles, users, wealth as W


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    users.bind_user(None)
    profiles.bind(None)
    yield tmp_path
    users.bind_user(None)
    profiles.bind(None)


def _rows():
    return [
        W._row("RELIANCE", "NSE", 150, 2400, 2500, source="kite", close=2490),
        W._row("GOLDBEES", "NSE", 100, 50, 60, source="kite"),
        W._row("TCS", "NSE", 10, 3000, 2900, source="paper", side="SHORT"),
    ]


@pytest.mark.parametrize("sym,ex,kind", [
    ("RELIANCE", "NSE", "stock"), ("GOLDBEES", "NSE", "gold"), ("SGBMAR29", "NSE", "gold"),
    ("SILVERBEES", "NSE", "gold"), ("NIFTYBEES", "NSE", "equity_etf"), ("LIQUIDBEES", "NSE", "debt_etf"),
    ("NIFTY24DEC24000CE", "NFO", "fno"), ("BANKNIFTY25JANFUT", "NFO", "fno"), ("INF209K01VA3", "MF", "mf"),
])
def test_each_holding_lands_in_one_tab(sym, ex, kind):
    assert W.classify(sym, ex) == kind


def test_split_holding_sums_back_and_cannot_over_allocate(home):
    rows = _rows()
    held = {r["id"]: r for r in rows}
    rid = rows[0]["id"]
    ret = W.create_group({"name": "Retirement", "asset_types": ["stock", "gold"]})
    swing = W.create_group({"name": "Swing", "asset_types": ["stock"]})
    W.set_allocation(ret["id"], rid, 100, held)
    W.set_allocation(swing["id"], rid, 50, held)
    with pytest.raises(W.GroupError, match="Only 50 of RELIANCE"):
        W.set_allocation(swing["id"], rid, 51, held)
    with pytest.raises(W.GroupError, match="doesn't hold gold"):
        W.set_allocation(swing["id"], rows[1]["id"], 10, held)
    with pytest.raises(W.GroupError, match="Short"):
        W.set_allocation(ret["id"], rows[2]["id"], 1, held)
    view = W.groups_view(rows)
    by = {g["name"]: g for g in view["groups"]}
    total_alloc_value = by["Retirement"]["value"] + by["Swing"]["value"]
    assert total_alloc_value == pytest.approx(rows[0]["value"])
    assert by["Retirement"]["pnl"] + by["Swing"]["pnl"] == pytest.approx(rows[0]["pnl"])
    assert [u["symbol"] for u in view["ungrouped"]] == ["GOLDBEES"]


def test_sell_trims_newest_first(home):
    rows = _rows()
    held = {r["id"]: r for r in rows}
    rid = rows[0]["id"]
    a = W.create_group({"name": "A"})
    b = W.create_group({"name": "B"})
    W.set_allocation(a["id"], rid, 100, held)
    W.set_allocation(b["id"], rid, 50, held)
    rows[0] = W._row("RELIANCE", "NSE", 120, 2400, 2500, source="kite")  # sold 30
    view = W.groups_view(rows)
    by = {g["name"]: g for g in view["groups"]}
    assert by["A"]["lines"][0]["qty"] == 100 and by["B"]["lines"][0]["qty"] == 20
    assert view["notes"]


def test_groups_are_private_per_user(home):
    W.create_group({"name": "Owner group"})
    profiles.create("ayushi", "A")
    with profiles.use("ayushi"):
        assert W.groups_view([])["groups"] == []


def test_kite_data_only_for_owner(home, monkeypatch):
    monkeypatch.setattr(W, "kite_state", lambda: {"connected": True})
    called = []
    monkeypatch.setattr(W, "kite_holdings", lambda: called.append(1) or [])
    monkeypatch.setattr(W, "kite_mf", lambda: [])
    monkeypatch.setattr(W, "paper_holdings", lambda: [])
    W.holdings()
    assert called == [1]
    u, _ = users.create_user("ayushi", "A", "trader")
    users.bind_user(u)
    profiles.bind(u.profile_id)
    out = W.holdings()
    assert called == [1] and "only shown to the owner" in out["note"]
    assert W.kite_client() is None
    assert W.guard_status()["available"] is False


def test_max_pain_and_xirr():
    rows = [{"strike": 100, "ce": {"oi": 10}, "pe": {"oi": 100}},
            {"strike": 110, "ce": {"oi": 50}, "pe": {"oi": 50}},
            {"strike": 120, "ce": {"oi": 100}, "pe": {"oi": 10}}]
    assert W.max_pain(rows) == 110
    r = W.xirr([(date(2024, 1, 1), -1000), (date(2025, 1, 1), 1100)])
    assert r == pytest.approx(10.0, abs=0.05)
    assert W.xirr([(date(2024, 1, 1), 100)]) is None


def test_suggestions_have_track_lines(home):
    import json

    from src.alphadesk.store import alphadesk_root

    d = alphadesk_root() / "board_last"
    d.mkdir(parents=True, exist_ok=True)
    (d / "core.json").write_text(json.dumps({"universe": "core", "as_of": "2026-11-19", "rows": [
        {"symbol": "INFY", "side": "LONG", "score": 3, "reasons": ["Trend up"], "cautions": [],
         "history": {"symbol_side": {"trades": 12, "win_rate": 0.58, "avg_r": 0.31}}},
        {"symbol": "ITC", "side": "STEER_CLEAR", "score": -1, "reasons": ["Mixed"], "cautions": ["Thin volume"]},
        {"symbol": "HDFC", "side": "NO_EDGE", "score": 0},
    ]}))
    cards = W.suggestions()["cards"]
    assert [c["symbol"] for c in cards][:2] == ["INFY", "ITC"]
    assert all(c["track"] for c in cards)
    assert "12 trades" in cards[0]["track"] and "untested" in cards[1]["track"]
