from __future__ import annotations

from datetime import date, time
from decimal import Decimal

import pytest

from src.live.profit_guard import session
from src.live.profit_guard.config import load_config
from src.live.profit_guard.models import PositionPlan, Side, Tick

# A plain Wednesday trading day, and the next day.
DAY = date(2026, 11, 18)
NEXT = date(2026, 11, 19)


def at(hh: int, mm: int, ss: int = 0, d: date = DAY) -> int:
    return session.ist_ms(d, time(hh, mm, ss))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """HALT sentinel, audit and store all live under a throwaway ~/.vibe-trading."""
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


@pytest.fixture
def cfg():
    return load_config()


def long_plan(**kw) -> PositionPlan:
    base = dict(
        id="A", strategy="swing", symbol="STOCKA", side=Side.LONG, qty=500,
        entry=Decimal("500"), initial_stop=Decimal("495"), stop=Decimal("495"),
        target=Decimal("510"), one_r=Decimal("5"), opened_ms=at(10, 0), atr=Decimal("1"),
        target1=None,
    )
    base.update(kw)
    return PositionPlan(**base)


def short_opt(**kw) -> PositionPlan:
    base = dict(
        id="B", strategy="intraday_opt_buy", symbol="NIFTY24500CE", side=Side.SHORT, qty=75,
        entry=Decimal("120"), initial_stop=Decimal("156"), stop=Decimal("156"),
        target=Decimal("60"), one_r=Decimal("36"), opened_ms=at(10, 0), is_option=True,
    )
    base.update(kw)
    return PositionPlan(**base)


def tk(symbol: str, ltp, ts: int, hi=None, lo=None, bar=False) -> Tick:
    return Tick(symbol, Decimal(str(ltp)), ts,
                None if hi is None else Decimal(str(hi)),
                None if lo is None else Decimal(str(lo)), bar)
