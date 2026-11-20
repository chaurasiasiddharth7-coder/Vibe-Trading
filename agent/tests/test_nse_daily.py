"""The NSE store updater runs once per trading evening, only when a store exists and is behind."""

from datetime import date, datetime, timedelta, timezone

from src.alphadesk import nse_daily as N

IST = timezone(timedelta(hours=5, minutes=30))
weekday = lambda d: d.weekday() < 5  # noqa: E731


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_due_after_the_evening_bhavcopy():
    assert not N.due(at(2026, 11, 20, 18, 0), "2026-11-19", weekday)  # Friday before 19:15: Thursday is enough
    assert N.due(at(2026, 11, 20, 19, 20), "2026-11-19", weekday)  # Friday evening: Friday is owed
    assert not N.due(at(2026, 11, 20, 19, 20), "2026-11-20", weekday)


def test_weekend_and_monday_morning_want_friday():
    assert N.due(at(2026, 11, 22, 10, 0), "2026-11-19", weekday)  # Sunday: Friday missing
    assert not N.due(at(2026, 11, 23, 9, 0), "2026-11-20", weekday)  # Monday morning: Friday is the latest


def test_no_store_means_nothing_to_do():
    assert not N.due(at(2026, 11, 20, 20, 0), None, weekday)


def test_tick_runs_the_update_once_when_due(monkeypatch):
    days = iter(["2026-11-19", "2026-11-20"])
    monkeypatch.setattr(N, "store_last_day", lambda: next(days))
    calls = []
    msg = N.tick(at(2026, 11, 20, 19, 30), runner=lambda: (calls.append(1), (True, "ok"))[1])
    assert calls == [1] and "ok" in msg and "now 2026-11-20" in msg


def test_script_is_where_the_image_copies_it():
    assert N.script_path().name == "nse_pull.py" and N.script_path().parent.name == "scripts"
