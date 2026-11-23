"""Lazy Kronos foundation-model forecast wrapper (CPU-first, fail-closed)."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any

import pandas as pd

from src.alphadesk.analysis import fetch_india_ohlcv
from src.alphadesk.symbols import normalize_india_symbol

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# The shared predictor keeps per-call state, so two forecasts at once (a board scan
# plus a click) corrupt each other. One prediction at a time.
_PREDICT_LOCK = threading.Lock()
_PREDICTOR: Any = None
_LOAD_ERROR: str | None = None

# Repo-relative Kronos checkout (vendor)
_KRONOS_ROOT = (
    Path(__file__).resolve().parents[3] / "third_party" / "Kronos"
)

# Mac CPU default — small param count, longer context
DEFAULT_MODEL_ID = "NeoQuasar/Kronos-mini"
DEFAULT_TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-2k"
DEFAULT_MAX_CONTEXT = 2048


def _ensure_kronos_on_path() -> None:
    root = str(_KRONOS_ROOT)
    if not _KRONOS_ROOT.is_dir():
        raise RuntimeError(
            f"Kronos repo not found at {_KRONOS_ROOT}. "
            "Clone https://github.com/shiyu-coder/Kronos into third_party/Kronos."
        )
    if root not in sys.path:
        sys.path.insert(0, root)


def _locked_predict(predictor: Any, **kwargs: Any) -> Any:
    with _PREDICT_LOCK:
        return predictor.predict(**kwargs)


def cpu_threads() -> int:
    """Threads for CPU inference: KRONOS_THREADS, else the container's CPU quota (cgroup), else the core count."""
    import math
    import os

    env = os.environ.get("KRONOS_THREADS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    for path, parse in (("/sys/fs/cgroup/cpu.max", lambda t: t.split()),  # cgroup v2: "150000 100000"
                        ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", lambda t: [t.strip(), None])):  # v1
        try:
            parts = parse(open(path).read())
            if path.endswith("cpu.max") and parts[0] != "max":
                return max(1, math.floor(int(parts[0]) / int(parts[1])))
            if parts[1] is None and int(parts[0]) > 0:
                period = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read().strip())
                return max(1, math.floor(int(parts[0]) / period))
        except (OSError, ValueError, IndexError, ZeroDivisionError):
            continue
    return max(1, os.cpu_count() or 1)


def get_predictor() -> Any:
    """Lazy-load KronosPredictor once per process (thread-safe)."""
    global _PREDICTOR, _LOAD_ERROR
    with _LOCK:
        if _PREDICTOR is not None:
            return _PREDICTOR
        if _LOAD_ERROR is not None:
            raise RuntimeError(_LOAD_ERROR)
        try:
            import torch

            _ensure_kronos_on_path()
            from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
            if device == "cpu":
                # torch starts one thread per HOST core; under a container CPU cap (cpus: 1.5) they fight for
                # the quota and every forecast slows down a lot. Use only the CPUs we may really use.
                torch.set_num_threads(cpu_threads())
            logger.info("Loading Kronos (%s) on %s …", DEFAULT_MODEL_ID, device)
            tokenizer = KronosTokenizer.from_pretrained(DEFAULT_TOKENIZER_ID)
            model = Kronos.from_pretrained(DEFAULT_MODEL_ID)
            model.to(device)
            _PREDICTOR = KronosPredictor(
                model, tokenizer, max_context=DEFAULT_MAX_CONTEXT, device=device
            )
            return _PREDICTOR
        except Exception as exc:
            _LOAD_ERROR = f"Kronos unavailable: {exc}"
            logger.exception("Kronos load failed")
            raise RuntimeError(_LOAD_ERROR) from exc


def forecast_symbol(
    symbol: str,
    *,
    lookback: int = 400,
    pred_len: int = 15,
    interval: str = "1d",
    period: str = "2y",
    temperature: float = 0.8,
    top_p: float = 0.9,
    sample_count: int = 5,
    as_of: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """Run Kronos autoregressive forecast for an India ticker.

    ``as_of`` (YYYY-MM-DD) forecasts from the bars up to that day only, as Kronos would have that day.
    Daily forecasts are saved to ``kronos_log`` so the chart can show what Kronos predicted."""
    ticker = normalize_india_symbol(symbol)
    lookback = min(lookback, DEFAULT_MAX_CONTEXT)
    _, df = fetch_india_ohlcv(ticker, period=period, interval=interval)
    if as_of is not None:
        df = df[pd.to_datetime(df.index).strftime("%Y-%m-%d") <= as_of]
    if len(df) < lookback:
        raise LookupError(
            f"insufficient history for {ticker}: need {lookback} bars, got {len(df)}"
        )

    # Normalize columns
    lower = {str(c).lower(): c for c in df.columns}
    def col(name: str) -> pd.Series:
        return df[lower[name]].astype(float)

    frame = pd.DataFrame(
        {
            "open": col("open"),
            "high": col("high"),
            "low": col("low"),
            "close": col("close"),
            "volume": col("volume"),
        }
    )
    frame["amount"] = frame["close"] * frame["volume"]
    frame = frame.reset_index(drop=True)

    timestamps = pd.to_datetime(df.index)
    x_df = frame.iloc[-lookback:].reset_index(drop=True)
    x_timestamp = pd.Series(timestamps[-lookback:]).reset_index(drop=True)

    last_ts = pd.Timestamp(x_timestamp.iloc[-1])
    if interval.lower() in {"1d", "1w"}:
        y_timestamp = pd.Series(
            pd.bdate_range(start=last_ts + pd.Timedelta(days=1), periods=pred_len)
        )
    else:
        # Intraday: step by interval minutes when possible
        freq = "5min" if interval.lower() == "5m" else "1h"
        y_timestamp = pd.Series(
            pd.date_range(start=last_ts, periods=pred_len + 1, freq=freq)[1:]
        )

    predictor = get_predictor()
    forecast_df = _locked_predict(
        predictor,
        df=x_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=pred_len,
        T=temperature,
        top_p=top_p,
        sample_count=sample_count,
        verbose=False,
    )

    def _candle_rows(ts: pd.Series, data: pd.DataFrame) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for i in range(len(data)):
            t = pd.Timestamp(ts.iloc[i])
            row = data.iloc[i]
            rows.append(
                {
                    "time": t.strftime("%Y-%m-%d"),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                }
            )
        return rows

    historical = _candle_rows(x_timestamp, x_df)
    projected = _candle_rows(y_timestamp, forecast_df)
    current_price = historical[-1]["close"]
    forecast_end = projected[-1]["close"]
    expected_change = round(
        ((forecast_end - current_price) / current_price) * 100, 2
    )
    projected_high = max(c["high"] for c in projected)
    projected_low = min(c["low"] for c in projected)

    out = {
        "symbol": ticker.replace(".NS", "").replace(".BO", ""),
        "ticker": ticker,
        "current_price": current_price,
        "kronos_projection": {
            "expected_change_percent": expected_change,
            "projected_high": round(projected_high, 2),
            "projected_low": round(projected_low, 2),
            "trajectory_bias": "BULLISH" if expected_change > 0 else "BEARISH",
            "model": DEFAULT_MODEL_ID,
            "pred_len": pred_len,
            "lookback": lookback,
        },
        "historical_data": historical[-120:],
        "forecast_data": projected,
    }
    if record and interval.lower() == "1d" and as_of is None:
        try:
            from src.alphadesk import kronos_log

            kronos_log.record(out["symbol"], out)
        except Exception:  # noqa: BLE001 - saving the forecast must never break it
            logger.warning("kronos forecast for %s not saved", ticker, exc_info=True)
    return out
