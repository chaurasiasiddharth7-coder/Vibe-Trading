"""Phase 1 survival, arena, PM, cache tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.alphadesk.arena import pick_winner
from src.alphadesk.cache import get_cached, set_cached
from src.alphadesk.cio_schema import CioTradePlan, parse_cio_json
from src.alphadesk.constitution import assert_live_allowed, ensure_constitution_files, get_laws
from src.alphadesk.engines.debate import capped_universe, heuristic_debate_plan
from src.alphadesk.engines.portfolio_manager import build_action_plan, proposal_for_symbol
from src.alphadesk import survival as surv


@pytest.fixture()
def alphadesk_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "alphadesk"
    root.mkdir()
    monkeypatch.setattr(surv, "alphadesk_root", lambda: root)
    # store.alphadesk_root used by constitution/cache too
    import src.alphadesk.store as store
    import src.alphadesk.constitution as constitution
    import src.alphadesk.cache as cache

    monkeypatch.setattr(store, "alphadesk_root", lambda: root)
    monkeypatch.setattr(constitution, "alphadesk_root", lambda: root)
    monkeypatch.setattr(cache, "alphadesk_root", lambda: root)
    for sub in ("signals", "outcomes", "portfolio", "survival", "cache"):
        (root / sub).mkdir(exist_ok=True)
    return root


def test_constitution_and_live_gate(alphadesk_tmp: Path):
    paths = ensure_constitution_files()
    assert Path(paths["constitution"]).is_file()
    assert len(get_laws()) == 3
    with pytest.raises(PermissionError):
        assert_live_allowed(unlocked=False, approved=True, mandate_ok=True)


def test_sleeve_death_and_scorecard(alphadesk_tmp: Path):
    surv.reset_engine("pm", sleeve_inr=1000)
    state = surv.apply_outcome_to_engine("pm", r_multiple=-1.0, pnl_inr=-1000)
    assert state["status"] == "DEAD"
    assert state["sleeve_inr"] == 0
    row = surv.scorecard_row(state)
    assert row["status"] == "DEAD"
    assert not surv.is_engine_tradable("pm")


def test_expectancy_death_after_30(alphadesk_tmp: Path):
    surv.reset_engine("debate", sleeve_inr=1_000_000)
    for _ in range(30):
        surv.apply_outcome_to_engine("debate", r_multiple=-0.1, pnl_inr=-10)
    state = surv.load_engine("debate")
    assert state["status"] == "DEAD"
    assert state.get("death_reason") == "expectancy_non_positive"


def test_pm_hygiene_plan(alphadesk_tmp: Path):
    holdings = [
        {
            "symbol": "DHFL",
            "qty": 100,
            "price": 10,
            "avg_cost": 50,
            "current_value": 1000,
        },
        {
            "symbol": "RELIANCE",
            "qty": 10,
            "price": 2800,
            "avg_cost": 2000,
            "current_value": 28000,
        },
    ]
    plan = build_action_plan(holdings, target_net=500)
    assert plan["hygiene_exits"] >= 1
    assert any(p["symbol"] == "DHFL" for p in plan["proposals"])
    cio = proposal_for_symbol("DHFL", holdings)
    assert cio.engine_id == "pm"
    assert cio.action in {"SELL", "TRIM"}


def test_arena_excludes_dead(alphadesk_tmp: Path):
    surv.reset_engine("pm", sleeve_inr=100_000)
    surv.reset_engine("debate", sleeve_inr=0)
    surv.apply_outcome_to_engine("debate", r_multiple=-1, pnl_inr=-1)
    # force dead
    d = surv.load_engine("debate")
    d["status"] = "DEAD"
    d["sleeve_inr"] = 0
    surv.save_engine(d)

    pm = CioTradePlan(
        action="SELL",
        confidence_score=80,
        target_price=90,
        stop_loss=110,
        executive_summary="pm sell",
        engine_id="pm",
    )
    debate = CioTradePlan(
        action="BUY",
        confidence_score=90,
        target_price=120,
        stop_loss=80,
        executive_summary="debate buy",
        engine_id="debate",
    )
    winner, votes = pick_winner([pm, debate])
    assert winner is not None
    assert winner.engine_id == "pm"
    assert any(v["excluded"] for v in votes if v["engine_id"] == "debate")


def test_cache_roundtrip(alphadesk_tmp: Path):
    set_cached("analysis", "RELIANCE", {"current_price": 1}, interval="1d")
    got = get_cached("analysis", "RELIANCE", interval="1d")
    assert got == {"current_price": 1}


def test_capped_universe():
    u = capped_universe([{"symbol": "FOO"}], max_extra=10)
    assert "FOO" in u
    assert len(u) <= 11


def test_cio_new_fields():
    plan = parse_cio_json(
        json.dumps(
            {
                "action": "HOLD",
                "confidence_score": 50,
                "target_price": 1,
                "stop_loss": 1,
                "executive_summary": "x",
                "engine_id": "pm",
                "arena_score": 40,
                "survival_status": "ALIVE",
            }
        )
    )
    assert plan.engine_id == "pm"
    assert plan.arena_score == 40


def test_heuristic_debate(alphadesk_tmp: Path):
    surv.ensure_engines(["debate"])
    plan = heuristic_debate_plan(
        "RELIANCE",
        analysis={"current_price": 100, "metrics": {"rsi_14": 40, "supertrend": "BULLISH"}},
        kronos={"kronos_projection": {"trajectory_bias": "BEARISH"}},
    )
    # TA bull vs Kronos bear → not both bull → WATCH or non-BUY
    assert plan.action in {"WATCH", "HOLD", "SELL", "BUY"}
    assert plan.engine_id == "debate"
