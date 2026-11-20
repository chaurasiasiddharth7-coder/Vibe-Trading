from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from src.live.profit_guard import session
from src.live.profit_guard.config import dump_config, load_config
from src.live.runtime.triggers import market_is_open_at
from tests.profit_guard.conftest import DAY, at, long_plan, short_opt


# ---------------------------------------------------------------- P1 session clock
@pytest.mark.parametrize(
    "hh,mm,ss,expected",
    [(9, 14, 59, False), (9, 15, 0, True), (15, 29, 59, True), (15, 30, 0, False)],
)
def test_india_equity_trigger_bounds(hh, mm, ss, expected):
    assert market_is_open_at("india_equity", at(hh, mm, ss)) is expected


def test_india_equity_closed_on_weekend():
    sat = date(2026, 11, 21)
    assert market_is_open_at("india_equity", session.ist_ms(sat, time(11, 0))) is False


def test_existing_markets_untouched():
    assert market_is_open_at("crypto", at(3, 0)) is True


@pytest.mark.parametrize(
    "hh,mm,phase",
    [(8, 59, "closed"), (9, 5, "preopen"), (9, 15, "open"), (14, 44, "open"), (14, 50, "no_new_entry"),
     (15, 16, "square_off"), (15, 30, "closed")],
)
def test_session_phase(hh, mm, phase):
    assert session.session_phase(at(hh, mm)) == phase


def test_session_phase_holiday_closed():
    assert session.session_phase(at(11, 0), holidays={DAY}) == "closed"


def test_ist_mapping_is_fixed_offset():
    # IST has no DST: 05:45 UTC is always 11:15 IST
    for month in (1, 7):
        ts = int(datetime(2026, month, 14, 5, 45, tzinfo=timezone.utc).timestamp() * 1000)
        assert session.to_ist(ts).time() == time(11, 15)


def test_expiry_and_minutes_to_close():
    assert session.is_expiry_day(at(11, 0), DAY.weekday()) is True
    assert session.is_expiry_day(at(11, 0), None) is False
    assert session.minutes_to_close(at(15, 0)) == 30


# ---------------------------------------------------------------- P2 models + config
def test_config_round_trip(tmp_path):
    cfg = load_config()
    p = tmp_path / "c.yaml"
    p.write_text(dump_config(cfg))
    assert load_config(p).to_dict() == cfg.to_dict()


@pytest.mark.parametrize(
    "override,field",
    [
        ({"layer2": {"tiers": [[0, 1, 50], [1, None, 120]]}}, "keep_pct"),
        ({"layer2": {"tiers": [[0, 1, 70], [1, None, 50]]}}, "must not decrease"),
        ({"layer2": {"loss_cap_pct": -1}}, "loss_cap_pct"),
        ({"layer2": {"mode": "yolo"}}, "layer2.mode"),
        ({"executor": {"max_orders_per_sec": 25}}, "max_orders_per_sec"),
        ({"layer1": {"scale_out_frac": 1.5}}, "scale_out_frac"),
        ({"layer3": {"square_off": "3pm"}}, "square_off"),
    ],
)
def test_invalid_config_rejected(override, field):
    with pytest.raises(ValueError, match=field):
        load_config(overrides=override)


def test_strategy_override_applies_only_to_that_strategy():
    cfg = load_config()
    assert cfg.for_strategy("btst").l1["target_r"] is None
    assert cfg.l1["target_r"] == 2.0
    assert cfg.for_strategy("unknown") is cfg


def test_locked_pnl_and_r_multiple():
    long = long_plan(stop=Decimal("503.40"))
    assert long.locked_pnl == Decimal("1700.00")
    assert round(long.r_multiple(Decimal("506.20")), 2) == Decimal("1.24")
    short = short_opt()
    assert short.locked_pnl == Decimal("-2700")
    assert round(short.r_multiple(Decimal("96.50")), 2) == Decimal("0.65")


def test_plan_serialization_round_trip():
    p = long_plan(stop=Decimal("503.40"))
    from src.live.profit_guard.models import PositionPlan

    assert PositionPlan.from_dict(p.to_dict()) == p
