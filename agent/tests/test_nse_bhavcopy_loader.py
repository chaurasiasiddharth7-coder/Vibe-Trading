"""NSE corporate actions (split, bonus, rights, demerger, dividend), lineage, point-in-time, loader."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from backtest.loaders import nse_store
from backtest.loaders.nse_corporate_actions import (
    Dividend, Factor, apply_factors, build_factors, find_jumps, gate_status, parse_dividend,
    parse_purpose, parse_rights, rights_factor,
)


def _bars(closes, opens=None, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"open": opens or closes, "high": closes, "low": closes,
                         "close": closes, "volume": [100.0] * len(closes)}, index=idx)


def _row(symbol, subject, bars, pos, **extra):
    return {"symbol": symbol, "subject": subject,
            "exDate": bars.index[pos].strftime("%d-%b-%Y"), **extra}


# --- parsers ------------------------------------------------------------------------------------


def test_parse_split_bonus_debenture_dividend():
    split = parse_purpose("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share")
    assert split[0][1] == pytest.approx(0.2)
    assert parse_purpose("Bonus 1:1")[0] == ("bonus", pytest.approx(0.5))
    both = parse_purpose("Bonus 1:1/Face Value Split From Rs 10/- To Rs 5/-")
    assert sorted(k for k, _ in both) == ["bonus", "split"]
    assert parse_purpose("Bonus Debentures 1:1") == []
    assert parse_purpose("Dividend - Rs 5 Per Share") == []
    assert parse_dividend("Interim Dividend - Rs 2.5 Per Share") == 2.5
    assert parse_dividend("Final Dividend - 150%") is None
    assert parse_dividend("Dividend Rs 2 / Special Dividend Rs 3") == 5.0


def test_rights_terms_and_terp():
    assert parse_rights("Rights 1:11 @ Premium Rs 4/-", face_value=10) == (1, 11, 14.0)
    assert parse_rights("Rights 1:2 @ Rs 100 Per Share", face_value=10) == (1, 2, 100.0)
    assert parse_rights("Rights 1:2 Warrants @ Premium Rs 4/-", face_value=10) is None
    assert rights_factor(1, 1, 50.0, 100.0) == pytest.approx(0.75)
    assert rights_factor(1, 1, 120.0, 100.0) is None  # issue at/above market is neutral


def test_gate_status_bands():
    assert gate_status(0.5, 0.5) == "confirmed"
    assert gate_status(1.0, 0.5) == "off_factor"
    assert gate_status(0.97, 0.99) == "ambiguous"


# --- factors ------------------------------------------------------------------------------------


def test_gate_accepts_real_bonus_rejects_phantom_and_debenture():
    bars = _bars([100.0, 100.0, 50.0, 50.0])
    rows = [_row("ABC", "Bonus 1:1", bars, 2)]
    ok, divs, review = build_factors(rows, {"ABC": bars})
    assert [f.factor for f in ok] == [0.5] and not review
    ok, _, review = build_factors(rows, {"ABC": _bars([100.0] * 4)})
    assert not ok and "price gate" in review[0]["why"]
    ok, _, review = build_factors([_row("ABC", "Bonus Debentures 1:1", bars, 2)], {"ABC": bars})
    assert not ok and not review  # never a price factor, not even a review item


def test_rights_factor_priced_and_ambiguous_accepted():
    # 1:1 rights at Rs 50 on a Rs 100 stock: TERP factor 0.75, confirmed by a 25% gap
    bars = _bars([100.0, 100.0, 75.0, 75.0])
    ok, _, review = build_factors([_row("ABC", "Rights 1:1 @ Rs 50", bars, 2, faceVal="10")],
                                  {"ABC": bars})
    assert ok[0].kind == "rights" and ok[0].factor == pytest.approx(0.75) and not review
    # a tiny rights factor hidden in a normal move is kept, tagged price_unconfirmed
    bars = _bars([100.0, 100.0, 100.0, 100.0])
    ok, _, _ = build_factors([_row("ABC", "Rights 1:20 @ Rs 80", bars, 2, faceVal="10")],
                             {"ABC": bars})
    assert ok and ok[0].status == "price_unconfirmed"


def test_demerger_gap_neutralised_by_index_and_checked_later():
    closes = [100.0, 100.0, 70.0, 70.0, 70.0, 70.0, 70.0, 70.0, 70.0]
    bars = _bars(closes)
    rows = [_row("ABC", "Demerger", bars, 2)]
    ok, _, _ = build_factors(rows, {"ABC": bars})
    assert ok[0].kind == "demerger" and ok[0].factor == pytest.approx(0.7)
    assert ok[0].status == "raw_gap"
    # the whole market fell 5% overnight: only the remainder is the demerger
    idx = pd.DataFrame({"open": [1000.0, 1000.0, 950.0] + [950.0] * 6,
                        "close": [1000.0, 1000.0, 950.0] + [950.0] * 6}, index=bars.index)
    ok, _, _ = build_factors(rows, {"ABC": bars}, idx)
    assert ok[0].factor == pytest.approx(0.7 / 0.95) and ok[0].status == "gap_estimate"
    # a small drop is news, not a demerger
    small = _bars([100.0, 100.0, 95.0] + [95.0] * 6)
    ok, _, review = build_factors([_row("ABC", "Demerger", small, 2)], {"ABC": small})
    assert not ok and review
    # a gap that reverts is not confirmed by the later close
    revert = _bars([100.0, 100.0, 70.0, 80.0, 90.0, 100.0, 100.0, 100.0, 100.0])
    ok, _, review = build_factors([_row("ABC", "Demerger", revert, 2)], {"ABC": revert})
    assert not ok and review


def test_dividend_collected_and_guarded():
    bars = _bars([100.0] * 5)
    rows = [_row("ABC", "Interim Dividend - Rs 2 Per Share", bars, 2),
            _row("ABC", "Interim Dividend - Rs 2 Per Share", bars, 2),  # duplicate row counts once
            _row("ABC", "Final Dividend - 150%", bars, 3),
            _row("ABC", "Special Dividend - Rs 80 Per Share", bars, 4)]
    _, divs, review = build_factors(rows, {"ABC": bars})
    assert [(d.amount) for d in divs] == [2.0]
    assert {r["why"] for r in review} >= {"dividend amount not a single cash figure",
                                          "cash above half the previous close"}


# --- applying -----------------------------------------------------------------------------------


def test_adjustment_bases():
    bars = _bars([100.0, 100.0, 50.0, 50.0])
    f = [Factor("ABC", bars.index[2].date(), "bonus", 0.5, "Bonus 1:1")]
    d = [Dividend("ABC", bars.index[3].date(), 5.0, "div")]
    price = apply_factors(bars, f, d, "price")
    assert price["close"].tolist() == [50.0] * 4 and price["volume"].tolist() == [200, 200, 100, 100]
    assert find_jumps(bars) and not find_jumps(price)
    tr = apply_factors(bars, f, d, "total_return")
    # dividend of 5 on a raw previous close of 50: earlier bars scale by 0.9 more
    assert tr["close"].tolist() == pytest.approx([50 * 0.9] * 3 + [50.0])
    assert apply_factors(bars, f, d, "raw") is bars
    with pytest.raises(ValueError):
        apply_factors(bars, f, d, "nope")


# --- lineage ------------------------------------------------------------------------------------


def _seg(isin, symbol, closes, start, prev_first=None):
    idx = pd.bdate_range(start, periods=len(closes))
    prevs = [prev_first if prev_first is not None else closes[0]] + closes[:-1]
    return pd.DataFrame({"date": idx, "isin": isin, "symbol": symbol, "open": closes,
                         "high": closes, "low": closes, "close": closes,
                         "prev_close": prevs, "volume": 1.0})


def test_isin_lineage_links_continuations_not_reused_symbols():
    old = _seg("INE000A01010", "ABC", [100.0] * 5, "2024-01-01")
    new = _seg("INE000A01028", "ABC", [100.0] * 5, "2024-01-08")  # reissue: same price
    linked, notes = nse_store.link_isins(pd.concat([old, new]))
    assert linked["isin"].nunique() == 2 and not notes
    other = _seg("INE999Z01010", "ABC", [333.0] * 5, "2024-01-08")  # a different company
    linked, notes = nse_store.link_isins(pd.concat([old, other]))
    assert linked["isin"].unique().tolist() == ["INE999Z01010"] and notes
    late = _seg("INE000A01028", "ABC", [100.0] * 5, "2024-03-04")  # months later: not a successor
    linked, _ = nse_store.link_isins(pd.concat([old, late]))
    assert linked["isin"].unique().tolist() == ["INE000A01028"]


def test_isin_lineage_follows_a_split_only_with_an_action_nearby():
    old = _seg("INE000A01010", "ABC", [100.0] * 5, "2024-01-01")
    new = _seg("INE000A01028", "ABC", [20.0] * 5, "2024-01-08", prev_first=100.0)  # NSE-style raw prev
    assert nse_store.link_isins(pd.concat([old, new]))[0]["isin"].nunique() == 1
    linked, _ = nse_store.link_isins(pd.concat([old, new]), [date(2024, 1, 8)])
    assert linked["isin"].nunique() == 2


def test_multi_part_dividends_and_par_rights():
    assert parse_dividend("Dividend - Rs 2 Per Share & Special Dividend Re 0.75 Per Share") == 2.75
    assert parse_dividend("Annual General Meeting/Dividend Rs. 2.50/- Per Equity Share Of Rs.10/- Each") == 2.5
    assert parse_dividend("Dividend 20% (Rs 2 per share)") is None
    assert parse_rights("Rights 2:3@ Par Rs 10 Per Share", face_value=10) == (2, 3, 10.0)
    assert parse_rights("Rights 1:2@Prem Rs 40", face_value=10) == (1, 2, 50.0)


def test_jumps_ignore_sub_rupee_ticks():
    assert find_jumps(_bars([0.40, 0.30, 0.40])) == []
    assert find_jumps(_bars([40.0, 30.0, 40.0]))


# --- store + loader -----------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NSE_DATA_DIR", str(tmp_path))
    closes = [100.0, 100.0, 50.0, 50.0, 50.0, 50.0]
    bars = _bars(closes)
    df = bars.reset_index(names="date").assign(isin="INE000A01010", symbol="ABC", prev_close=1.0)
    df.to_parquet(tmp_path / "bars.parquet", index=False)
    (tmp_path / "corporate_actions.json").write_text(json.dumps([
        _row("ABC", "Bonus 1:1", bars, 2),
        _row("ABC", "Interim Dividend - Rs 1 Per Share", bars, 4)]))
    return tmp_path, bars


def test_build_all_ledger_is_append_only_and_point_in_time(store):
    root, bars = store
    summary = nse_store.build_all(root)
    assert summary["factors"] == 1 and summary["dividends"] == 1 and summary["new_versions"] == 2
    first = (root / "factor_versions.jsonl").read_text()
    assert nse_store.build_all(root)["new_versions"] == 0  # idempotent
    assert (root / "factor_versions.jsonl").read_text() == first
    ex = bars.index[2].date()
    before = nse_store.load_factors(root, "ABC", asof=(ex - pd.Timedelta(days=1)).isoformat())
    after = nse_store.load_factors(root, "ABC", asof=ex.isoformat())
    assert before == ([], []) and len(after[0]) == 1


def test_loader_end_to_end(store, monkeypatch):
    root, bars = store
    from backtest.loaders.nse_bhavcopy_loader import DataLoader
    nse_store.build_all(root)
    loader = DataLoader()
    assert loader.is_available()
    out = loader.fetch(["ABC.NS"], "2024-01-01", "2024-02-01")["ABC.NS"]
    assert out["close"].tolist() == [50.0] * 6
    raw = DataLoader(basis="raw").fetch(["ABC.NS"], "2024-01-01", "2024-02-01")["ABC.NS"]
    assert raw["close"].tolist() == [100.0, 100.0, 50.0, 50.0, 50.0, 50.0]
    tr = DataLoader(basis="total_return").fetch(["ABC.NS"], "2024-01-01", "2024-02-01")["ABC.NS"]
    assert tr["close"].iloc[0] < out["close"].iloc[0]
    ex = bars.index[2].date().isoformat()
    early = DataLoader(asof=bars.index[1].date().isoformat()).fetch(
        ["ABC.NS"], "2024-01-01", "2024-02-01")["ABC.NS"]
    assert len(early) == 2 and early["close"].tolist() == [100.0, 100.0]  # no future factor, no future bars
    assert ex  # silence unused
    assert loader.fetch(["ABC.NS", "ZZZ.NS"], "2024-01-01", "2024-02-01") == {}
    assert loader.fetch(["ABC.BO"], "2024-01-01", "2024-02-01") == {}  # BSE is not served
    assert loader.fetch(["ABC.NS"], "2024-01-01", "2024-02-01", interval="1H") == {}


# --- Yahoo as a second witness ------------------------------------------------------------------


def test_witness_finds_a_missing_split_and_ignores_blips():
    from backtest.loaders.nse_witness import find_steps, witness_symbol
    raw = _bars([1000.0] * 12 + [100.0] * 12)  # a 10:1 split the feed never recorded
    yahoo = raw["close"].copy()
    yahoo[:12] = 100.0  # Yahoo adjusted it
    steps = find_steps(raw["close"], yahoo)
    assert len(steps) == 1 and steps[0][1] == pytest.approx(0.1)
    found = witness_symbol(raw, yahoo, accepted=[])
    assert found and found[0]["factor"] == pytest.approx(0.1) and found[0]["gate"] == "confirmed"
    # already explained by an accepted factor: nothing new
    done = [Factor("ABC", raw.index[12].date(), "split", 0.1, "Split")]
    assert witness_symbol(raw, yahoo, accepted=done) == []
    # a one-day disagreement that reverts is not an adjustment
    calm = _bars([100.0] * 24)["close"]
    blip = calm.copy()
    blip.iloc[10] *= 1.06
    assert find_steps(calm, blip) == []


def test_witness_requires_the_bars_to_agree():
    from backtest.loaders.nse_witness import witness_symbol
    flat = _bars([100.0] * 24)  # Yahoo claims a 0.5 adjustment but the price never moved
    yahoo = flat["close"].copy()
    yahoo[:12] = 50.0
    assert witness_symbol(flat, yahoo, accepted=[]) == []


def test_build_all_merges_witnessed_factors(store):
    root, bars = store
    ex = date(2024, 2, 1)  # well away from the bonus the feed recorded
    (root / "witness.json").write_text(json.dumps({"ABC": {"found": [
        {"ex_date": ex.isoformat(), "factor": 0.9, "gap": 0.9, "gate": "ambiguous"}]}}))
    summary = nse_store.build_all(root)
    assert summary["factors"] == 2  # the bonus plus the witnessed one
    kinds = {f.kind for f in nse_store.load_factors(root, "ABC")[0]}
    assert "witnessed" in kinds


def test_witness_asks_yahoo_for_daily_bars_not_range_max(monkeypatch):
    """range=max returns monthly bars; the witness must pass an explicit daily window."""
    from backtest.loaders import nse_witness, yahoo_client
    seen = {}

    def fake_chart(symbol, **kw):
        seen.update(kw)
        return [{"trade_date": 1_700_000_000, "close": 10.0}]

    monkeypatch.setattr(yahoo_client, "get_chart", fake_chart)
    nse_witness.yahoo_daily("ABC")
    assert seen["interval"] == "1d" and "range_" not in seen and seen["period1"] < seen["period2"]


def test_stale_store_defers_when_current_prices_are_wanted(store):
    from datetime import timedelta
    from backtest.loaders.nse_bhavcopy_loader import DataLoader
    root, _ = store
    nse_store.build_all(root)
    today = date.today()
    # the fixture's bars are from Jan 2024: a request ending today must fall through, a historical one must not
    assert DataLoader().fetch(["ABC.NS"], "2024-01-01", today.isoformat()) == {}
    assert DataLoader().fetch(["ABC.NS"], "2024-01-01", "2024-02-01") != {}
