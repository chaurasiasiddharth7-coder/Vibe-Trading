from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from decimal import Decimal
from pathlib import Path

import pytest

from src.live.profit_guard.models import PortfolioState, State
from src.live.profit_guard.store import GuardStore, StoreLockedError
from tests.profit_guard.conftest import long_plan

AGENT_DIR = Path(__file__).resolve().parents[2]


def test_plan_and_portfolio_round_trip(tmp_path):
    with GuardStore(tmp_path / "g.db") as s:
        p = long_plan(stop=Decimal("503.40"), state=State.TRAILING)
        s.save_plan(p, 1)
        assert s.get_plan("A") == p
        assert [x.id for x in s.open_plans()] == ["A"]
        s.save_portfolio(PortfolioState("2026-11-18", peak_mtm=Decimal("10670.25"), armed=True), 2)
        assert s.get_portfolio("2026-11-18").peak_mtm == Decimal("10670.25")
        s.save_plan(p.with_(state=State.EXITED), 3)
        assert s.open_plans() == []


def test_crash_mid_loop_keeps_state(tmp_path):
    db = tmp_path / "crash.db"
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(AGENT_DIR)!r})
        from decimal import Decimal
        from src.live.profit_guard.store import GuardStore
        from src.live.profit_guard.models import PortfolioState, State
        from tests.profit_guard.conftest import long_plan
        s = GuardStore({str(db)!r})
        s.save_plan(long_plan(stop=Decimal("503.40"), state=State.TRAILING), 1)
        s.save_portfolio(PortfolioState("2026-11-18", peak_mtm=Decimal("10670.25"), armed=True), 1)
        s.record_intent("A", "exit", {{"qty": 500}}, 2)
        print("ready", flush=True)
        i = 0
        while True:
            i += 1
            s.audit("tick", i)
            time.sleep(0.001)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "HOME": str(tmp_path)})
    assert proc.stdout.readline().strip() == "ready"
    time.sleep(0.2)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    with GuardStore(db) as s:
        p = s.get_plan("A")
        assert p.stop == Decimal("503.40") and p.state is State.TRAILING
        assert s.get_portfolio("2026-11-18").peak_mtm == Decimal("10670.25")
        pend = s.pending_intents()
        assert len(pend) == 1 and pend[0]["status"] == "pending"


def test_second_writer_fails_fast(tmp_path):
    with GuardStore(tmp_path / "g.db"):
        with pytest.raises(StoreLockedError):
            GuardStore(tmp_path / "g.db")


def test_intent_lifecycle_and_audit_only_grows(tmp_path):
    with GuardStore(tmp_path / "g.db") as s:
        iid = s.record_intent("A", "exit", {"qty": 1}, 1)
        assert s.pending_intents()[0]["id"] == iid
        s.mark_intent(iid, "sent", 2, broker_order_id="PG-1")
        s.mark_intent(iid, "confirmed", 3)
        assert s.pending_intents() == []
        n = len(s.read_audit())
        s.audit("x", 4)
        assert len(s.read_audit()) == n + 1
        assert not hasattr(s, "truncate_audit")
        with pytest.raises(ValueError):
            s.mark_intent(iid, "weird", 5)


def test_decimal_exact(tmp_path):
    with GuardStore(tmp_path / "g.db") as s:
        p = long_plan(stop=Decimal("0.1") + Decimal("0.2"))
        s.save_plan(p, 1)
        assert s.get_plan("A").stop == Decimal("0.3")


def test_newer_schema_refused(tmp_path):
    db = tmp_path / "g.db"
    with GuardStore(db) as s:
        s.conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(RuntimeError, match="newer"):
        GuardStore(db)
