"""The kill switch, live-vs-replay records, the Kronos split, the audit pauses and the weekly audit."""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.alphadesk import board_paper as bp
from src.alphadesk import health as H
from src.alphadesk import strategies as S


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".vibe-trading" / "alphadesk"
    root.mkdir(parents=True)
    monkeypatch.setattr("src.alphadesk.store.alphadesk_root", lambda: root)
    monkeypatch.setattr(bp, "_dir", lambda: (root / "board_paper").mkdir(exist_ok=True) or root / "board_paper")
    monkeypatch.setattr("src.alphadesk.intraday.days._records", lambda newest_first=True: [])
    monkeypatch.setattr("src.alphadesk.btst.days.list_days", lambda kind="live", limit=None: [])
    return root


def _trade(i, r, side="LONG", forecast=1, reason="target"):
    return {"id": f"00000000-0000-0000-0000-{i:012d}", "symbol": f"S{i}", "side": side, "entry": 100.0, "stop": 97.0,
            "target": 106.0, "qty": 10, "status": "CLOSED", "r_multiple": r, "pnl_inr": r * 30,
            "exit_reason": reason, "exit_date": f"2026-10-{i % 28 + 1:02d}", "closed_at": f"2026-10-{i % 28 + 1:02d}",
            "votes": {"trend": 1, "structure": 1, "momentum": 1, "forecast": forecast * (1 if side == "LONG" else -1)}}


def test_judge_needs_enough_trades_and_proof():
    assert H.judge([-1.0] * 29)["status"] == "collecting"
    assert H.judge([-0.5, -0.3] * 15)["status"] == "pause"
    noisy = [2.0 if i % 3 == 0 else -1.05 for i in range(30)]  # slightly negative, but not proven
    assert H.judge(noisy)["status"] == "ok"
    assert H.first_trip([-0.5, -0.3] * 30) == 30


def test_kill_switch_pauses_a_proven_loser_and_restart_needs_a_new_passing_test(home):
    rec = S.create_custom({"name": "Loser", "rules": {"side": "LONG", "entry": [{"kind": "day_up", "pct": 2}],
                                                     "exit": {"stop_pct": 3}}}, author="sid")
    sid = rec["id"]
    S.record_test(sid, {"summary": {}, "gate": {"passed": True}}, 3)
    S.set_paper(sid, True)
    data = S._load()
    for t in data["tests"][sid]:
        t["ran_at"] = "2026-01-01T00:00:00+05:30"  # an old test doesn't count after a pause
    S._save(data)
    for i in range(40):
        bp._save({**_trade(i, -0.4 if i % 2 else -0.2), "strategy_id": sid})
    report = {r["id"]: r for r in H.check()}
    assert report[sid]["action"] == "paused" and not S.paper_on(sid)
    with pytest.raises(S.StrategyError, match="Paused"):
        S.set_paper(sid, True)
    data = S._load()
    data["switches"][sid]["auto_paused"]["at"] = "2026-01-02T00:00:00+05:30"
    S._save(data)
    S.record_test(sid, {"summary": {}, "gate": {"passed": False}}, 3)
    with pytest.raises(S.StrategyError, match="Paused"):
        S.set_paper(sid, True)  # a failing test doesn't restart it
    S.record_test(sid, {"summary": {}, "gate": {"passed": True}}, 3)
    assert S.set_paper(sid, True)["paper"] and "auto_paused" not in S._load()["switches"][sid]


def test_audit_pauses_apply_once(home):
    assert S.paper_on("intraday") and S.paper_on("btst")
    done = H.apply_audit_pauses()
    assert "paused intraday" in done and "paused btst" in done
    assert not S.paper_on("intraday") and not S.paper_on("btst") and S.paper_on("signal_board")
    with pytest.raises(S.StrategyError, match="Paused"):
        S.set_paper("intraday", True)
    assert H.apply_audit_pauses() == []


def test_kronos_split_is_relative_to_the_trade_side(home):
    for i in range(6):
        bp._save(_trade(i, 1.0, "LONG", forecast=1))
    for i in range(6, 9):
        bp._save(_trade(i, -1.0, "SHORT", forecast=1))  # Kronos agrees with the short
    for i in range(9, 11):
        bp._save(_trade(i, -0.5, "LONG", forecast=0))
    split = {r["vote"]: r for r in H.kronos_split()}
    assert split["agrees"]["trades"] == 9 and split["agrees"]["by_side"]["LONG"]["total"] == 6.0
    assert split["agrees"]["by_side"]["SHORT"]["total"] == -3.0 and split["neutral"]["trades"] == 2
    h = H.strategy_health("signal_board")
    assert h["live"]["trades"] == 11 and h["replay"]["trades"] == 0


def test_hand_closed_trades_are_tagged(home):
    bp._save(_trade(1, 0.8, reason="manual"))
    assert H.records("signal_board")["live"][0]["source"] == "hand-closed"


def test_intraday_live_and_replay_kept_apart_and_combined_only_to_prove_a_loss(monkeypatch, home):
    def day(d, src, rs):
        return {"date": d, "status": "settled", "runs": [{"source": src}],
                "trades": [{"r_net": r, "pnl_inr": r * 1000, "entry": 100, "qty": 10, "side": "long"} for r in rs]}
    recs = [day(f"2026-08-{i + 1:02d}", "cli", [-0.3, -0.2, -0.25]) for i in range(25)] + \
           [day(f"2026-11-{i + 1:02d}", "scheduler", [-0.5, 0.6, -0.4]) for i in range(10)]
    monkeypatch.setattr("src.alphadesk.intraday.days._records", lambda newest_first=True: recs)
    h = H.strategy_health("intraday")
    assert h["live"]["trades"] == 30 and h["replay"]["trades"] == 75
    assert h["verdict"]["status"] == "pause" and h["verdict"]["basis"] == "live+replay"


def test_weekly_report_is_saved(home):
    for i in range(3):
        bp._save(_trade(i, 1.0))
    rep = H.weekly(date(2026, 11, 21))
    assert rep["week"] == ["2026-11-16", "2026-11-20"] and {r["id"] for r in rep["strategies"]} >= {"signal_board"}
    assert H.last_weekly()["week"] == rep["week"]
    assert json.loads((home / "reports" / "weekly" / "2026-11-20.json").read_text())["flags"] == rep["flags"]
