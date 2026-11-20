"""Scans show price ideas first, then add the AI forecast; the model uses only the CPUs the container may use."""

import time

from src.alphadesk import board_jobs as BJ
from src.alphadesk import kronos_service as K


def test_two_pass_scan_saves_prices_first_then_forecast(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls, saved = [], []

    def fake_build(members, with_kronos, universe_key, progress):
        calls.append(with_kronos)
        for i in range(len(members)):
            progress(i + 1, len(members))
        return {"rows": [], "pass": "forecast" if with_kronos else "prices", "track_record": None}

    monkeypatch.setattr("src.alphadesk.signal_board.build_board", fake_build)
    monkeypatch.setattr("src.alphadesk.index_lists.scan_list", lambda u, include_funds: [{"symbol": "A"}, {"symbol": "B"}])
    monkeypatch.setattr("src.alphadesk.board_paper.settle_open_trades", lambda: [])
    monkeypatch.setattr(BJ, "save_last", lambda u, b: saved.append(dict(b)))
    monkeypatch.setattr(BJ, "_forecast_available", lambda: True)
    jid = BJ.start_scan("nifty50", include_funds=False)
    for _ in range(100):
        if BJ.status(jid)["status"] != "running":
            break
        time.sleep(0.02)
    job = BJ.status(jid)
    assert job["status"] == "done" and job["phase"] == "forecast" and job["result"]["pass"] == "forecast"
    assert calls == [False, True]
    assert saved[0]["pass"] == "prices" and saved[0]["forecast_pending"] is True  # shown while the forecast runs
    assert saved[-1]["pass"] == "forecast" and "forecast_pending" not in saved[-1]


def test_no_forecast_model_means_one_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr("src.alphadesk.signal_board.build_board",
                        lambda members, with_kronos, universe_key, progress: calls.append(with_kronos) or {"rows": []})
    monkeypatch.setattr("src.alphadesk.index_lists.scan_list", lambda u, include_funds: [{"symbol": "A"}])
    monkeypatch.setattr("src.alphadesk.board_paper.settle_open_trades", lambda: [])
    monkeypatch.setattr(BJ, "save_last", lambda u, b: None)
    monkeypatch.setattr(BJ, "_forecast_available", lambda: False)
    jid = BJ.start_scan("core", include_funds=True)
    for _ in range(100):
        if BJ.status(jid)["status"] != "running":
            break
        time.sleep(0.02)
    assert BJ.status(jid)["status"] == "done" and calls == [False]


def test_cpu_threads_follow_the_container_quota(monkeypatch):
    monkeypatch.setenv("KRONOS_THREADS", "3")
    assert K.cpu_threads() == 3
    monkeypatch.delenv("KRONOS_THREADS")
    files = {"/sys/fs/cgroup/cpu.max": "150000 100000\n"}

    def fake_open(path, *a, **k):
        import io

        if path in files:
            return io.StringIO(files[path])
        raise OSError(path)

    monkeypatch.setattr("builtins.open", fake_open)
    assert K.cpu_threads() == 1  # cpus: 1.5 -> 1 thread
    files["/sys/fs/cgroup/cpu.max"] = "400000 100000\n"
    assert K.cpu_threads() == 4
    files["/sys/fs/cgroup/cpu.max"] = "max 100000\n"
    assert K.cpu_threads() >= 1  # no cap: the core count
