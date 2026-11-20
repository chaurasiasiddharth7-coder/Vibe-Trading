"""Load + validate Profit Guard config (profit_guard.yaml)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from src.live.profit_guard.models import dec

DEFAULT_PATH = Path(__file__).with_name("profit_guard.yaml")
MODES = {"tiered", "lock_fixed", "lock_and_trail", "off"}
TRAILS = {"chandelier", "percent", "none"}


def _hhmm(s: str) -> time:
    h, m = str(s).split(":")
    return time(int(h), int(m))


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass(frozen=True)
class GuardConfig:
    raw: dict[str, Any]

    # -- accessors -----------------------------------------------------------
    @property
    def capital(self) -> Decimal:
        return dec(self.raw["capital_inr"])

    @property
    def l1(self) -> dict[str, Any]:
        return self.raw["layer1"]

    @property
    def l2(self) -> dict[str, Any]:
        return self.raw["layer2"]

    @property
    def l3(self) -> dict[str, Any]:
        return self.raw["layer3"]

    @property
    def ex(self) -> dict[str, Any]:
        return self.raw["executor"]

    def pct(self, section: str, key: str) -> Decimal:
        return dec(self.raw[section][key]) / 100

    def time(self, key: str) -> time:
        return _hhmm(self.l3[key])

    def for_strategy(self, name: str) -> "GuardConfig":
        over = (self.raw.get("strategies") or {}).get(name)
        if not over:
            return self
        merged = _deep_merge(self.raw, over)
        validate(merged)
        return GuardConfig(merged)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw)


def validate(raw: dict[str, Any]) -> None:
    def need(cond: bool, field: str, msg: str) -> None:
        if not cond:
            raise ValueError(f"{field}: {msg}")

    for sec in ("layer1", "layer2", "layer3", "executor"):
        need(isinstance(raw.get(sec), dict), sec, "section missing")
    need(dec(raw.get("capital_inr", 0)) > 0, "capital_inr", "must be > 0")

    l1, l2, l3, ex = raw["layer1"], raw["layer2"], raw["layer3"], raw["executor"]
    for k in ("stop_atr_k", "min_stop_atr", "breakeven_at_r", "chandelier_atr_k"):
        need(dec(l1[k]) > 0, f"layer1.{k}", "must be > 0")
    need(0 <= dec(l1["scale_out_frac"]) < 1, "layer1.scale_out_frac", "must be in [0, 1)")
    need(l1["trail"] in TRAILS, "layer1.trail", f"one of {sorted(TRAILS)}")
    need(0 < dec(l1["trail_pct"]) < 100, "layer1.trail_pct", "must be in (0, 100)")
    need(int(l1["time_stop_bars"]) >= 0, "layer1.time_stop_bars", "must be >= 0")
    if l1.get("target_r") is not None:
        need(dec(l1["target_r"]) > 0, "layer1.target_r", "must be > 0 or null")

    need(l2["mode"] in MODES, "layer2.mode", f"one of {sorted(MODES)}")
    need(l2["scope"] in {"per_strategy", "account"}, "layer2.scope", "per_strategy | account")
    need(dec(l2["loss_cap_pct"]) > 0, "layer2.loss_cap_pct", "must be > 0")
    need(dec(l2["arm_pct"]) > 0, "layer2.arm_pct", "must be > 0")
    tiers = l2["tiers"]
    need(isinstance(tiers, list) and tiers, "layer2.tiers", "non-empty list")
    prev_hi, prev_keep = None, Decimal("-1")
    for i, t in enumerate(tiers):
        need(len(t) == 3, f"layer2.tiers[{i}]", "[from_pct, to_pct|null, keep_pct]")
        lo, hi, keep = t
        need(0 <= dec(keep) <= 100, f"layer2.tiers[{i}].keep_pct", "must be 0..100")
        need(dec(keep) >= prev_keep, f"layer2.tiers[{i}].keep_pct", "must not decrease")
        if prev_hi is not None:
            need(dec(lo) == dec(prev_hi), f"layer2.tiers[{i}].from_pct", "must equal previous to_pct")
        if hi is not None:
            need(dec(hi) > dec(lo), f"layer2.tiers[{i}].to_pct", "must be > from_pct")
        else:
            need(i == len(tiers) - 1, f"layer2.tiers[{i}].to_pct", "only the last tier may be open")
        prev_hi, prev_keep = hi, dec(keep)
    for sub in ("lock_fixed", "lock_and_trail"):
        s = l2[sub]
        need(dec(s["lock_pct"]) <= dec(s["at_pct"]), f"layer2.{sub}.lock_pct", "must be <= at_pct")
    need(dec(l2["lock_and_trail"]["every_pct"]) > 0, "layer2.lock_and_trail.every_pct", "must be > 0")

    for k in ("no_new_entry", "square_off", "expiry_exit"):
        try:
            _hhmm(l3[k])
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"layer3.{k}: expected HH:MM") from exc
    wd = l3.get("weekly_expiry_weekday")
    need(wd is None or 0 <= int(wd) <= 4, "layer3.weekly_expiry_weekday", "0..4 or null")

    need(0 < int(ex["max_orders_per_sec"]) <= 10, "executor.max_orders_per_sec", "1..10 (exchange cap)")
    need(dec(ex["limit_through_ltp_pct"]) > 0, "executor.limit_through_ltp_pct", "must be > 0")


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> GuardConfig:
    raw = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    if overrides:
        raw = _deep_merge(raw, overrides)
    validate(raw)
    return GuardConfig(raw)


def dump_config(cfg: GuardConfig) -> str:
    return yaml.safe_dump(cfg.to_dict(), sort_keys=False)
