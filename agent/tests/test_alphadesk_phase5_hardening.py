"""Phase 5: morning desk, tax YTD, token budget, idle, audit export."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.alphadesk import idle
from src.alphadesk import tax_ytd
from src.alphadesk import token_budget as tb
from src.alphadesk.audit_export import export_audit
from src.alphadesk.morning_scan import run_morning_desk
from src.alphadesk import survival as surv


@pytest.fixture()
def alphadesk_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "alphadesk"
    root.mkdir()
    import src.alphadesk.store as store
    import src.alphadesk.constitution as constitution
    import src.alphadesk.live_unlock as lu
    import src.alphadesk.audit_export as audit_mod

    for mod in (surv, store, constitution, idle, tax_ytd, tb, lu, audit_mod):
        monkeypatch.setattr(mod, "alphadesk_root", lambda: root)
    for sub in (
        "signals",
        "outcomes",
        "portfolio",
        "survival",
        "cache",
        "live",
        "idle",
        "tax",
        "budget",
        "audit",
        "reports",
    ):
        (root / sub).mkdir(exist_ok=True)
    return root


def test_india_fy_label():
    from datetime import date

    assert tax_ytd.india_fy_label(date(2026, 4, 1)).startswith("FY2026")
    assert tax_ytd.india_fy_label(date(2026, 3, 31)).startswith("FY2025")


def test_tax_ytd_accumulate(alphadesk_tmp: Path):
    s1 = tax_ytd.record_sell_tax(symbol="RELIANCE", gain_inr=10_000, long_term_frac=0.0)
    assert s1["est_tax_inr"] == pytest.approx(2_000.0)  # 20% STCG
    s2 = tax_ytd.record_sell_tax(symbol="TCS", gain_inr=8_000, long_term_frac=1.0)
    assert s2["est_ltcg_tax_inr"] == pytest.approx(1_000.0)  # 12.5%
    summary = tax_ytd.summary()
    assert summary["event_count"] == 2
    assert summary["est_tax_inr"] == pytest.approx(3_000.0)


def test_token_budget_cap(alphadesk_tmp: Path):
    tb.record_spend("arena", 1_000, note="t1")
    assert tb.remaining("arena") < tb.DEFAULT_DAILY_CAPS["arena"]
    # Force over total
    with pytest.raises(PermissionError):
        tb.record_spend("total", tb.DEFAULT_DAILY_CAPS["total"] + 1)


def test_idle_anti_thrash(alphadesk_tmp: Path):
    assert idle.should_sleep("morning_desk")["sleep"] is False
    idle.mark_run("morning_desk")
    assert idle.should_sleep("morning_desk", cooldown_sec=3600)["sleep"] is True
    assert idle.should_sleep("morning_desk", force=True)["sleep"] is False


def test_morning_desk_and_audit(alphadesk_tmp: Path):
    from src.alphadesk.store import save_portfolio_snapshot

    save_portfolio_snapshot(
        [
            {
                "symbol": "RELIANCE",
                "qty": 10,
                "price": 2800,
                "avg_cost": 2000,
                "current_value": 28000,
                "bucket": "core",
            }
        ]
    )
    result = run_morning_desk(force=True)
    assert result["ok"] is True
    assert result["skipped"] is False
    assert Path(result["path"]).is_file()

    # Second call without force should anti-thrash
    again = run_morning_desk(force=False)
    assert again["skipped"] is True

    audit = export_audit(as_zip=True)
    assert audit["ok"]
    assert Path(audit["json_path"]).is_file()
    assert audit["zip_path"] and Path(audit["zip_path"]).is_file()
