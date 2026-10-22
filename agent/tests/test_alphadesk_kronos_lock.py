"""Forecasts must run one at a time: the shared predictor isn't safe to call concurrently."""

from __future__ import annotations

import threading
import time

from src.alphadesk import kronos_service


def test_predictions_never_overlap():
    active = 0
    overlap = False
    guard = threading.Lock()

    class Fake:
        def predict(self, **_kw):
            nonlocal active, overlap
            with guard:
                active += 1
                overlap = overlap or active > 1
            time.sleep(0.02)
            with guard:
                active -= 1
            return "ok"

    fake = Fake()
    threads = [threading.Thread(target=kronos_service._locked_predict, args=(fake,)) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert overlap is False
