"""Profit Guard data model. Money and prices are Decimal; times are epoch ms (UTC)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Any, Union

D = Decimal
ZERO = D("0")


def dec(x: Any) -> Decimal:
    """Exact Decimal from str/int/Decimal; floats go through repr so 0.1 stays 0.1."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x))


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


class State(str, Enum):
    PLANNED = "PLANNED"
    OPEN = "OPEN"
    BREAKEVEN = "BREAKEVEN"
    TRAILING = "TRAILING"
    EXITED = "EXITED"


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TRAIL = "TRAIL"
    PARTIAL = "PARTIAL"
    TIME = "TIME"
    OPPOSITE_SIGNAL = "OPPOSITE_SIGNAL"
    EVENT = "EVENT"
    SESSION_CLOSE = "SESSION_CLOSE"
    PROFIT_LOCK = "PROFIT_LOCK"
    LOSS_CAP = "LOSS_CAP"
    GAP = "GAP"
    MANUAL = "MANUAL"
    EXTERNAL = "EXTERNAL"


@dataclass(frozen=True)
class PositionPlan:
    id: str
    strategy: str
    symbol: str
    side: Side
    qty: int
    entry: Decimal
    initial_stop: Decimal
    stop: Decimal
    target: Decimal | None
    one_r: Decimal
    opened_ms: int
    exchange: str = "NSE"
    underlying: str | None = None
    is_option: bool = False
    target1: Decimal | None = None
    qty_open: int | None = None
    highest: Decimal | None = None
    lowest: Decimal | None = None
    mfe: Decimal = ZERO  # best favourable move per unit
    mae: Decimal = ZERO  # worst adverse move per unit (stored positive)
    atr: Decimal | None = None
    state: State = State.OPEN
    bars_held: int = 0
    last_tick_ms: int | None = None
    carry: bool = False  # BTST / swing: skip the entry day's session square-off
    realized: Decimal = ZERO  # booked P&L (partials + final exit, at trigger price until fills confirm)
    partial_done: bool = False
    gtt_id: str | None = None
    mode: str = "paper"
    exit_reason: ExitReason | None = None
    exit_price: Decimal | None = None
    exit_ms: int | None = None

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError("qty must be positive")
        if self.one_r <= 0:
            raise ValueError("one_r must be positive")
        if self.qty_open is None:
            object.__setattr__(self, "qty_open", self.qty)
        if self.highest is None:
            object.__setattr__(self, "highest", self.entry)
        if self.lowest is None:
            object.__setattr__(self, "lowest", self.entry)

    # -- derived -----------------------------------------------------------
    def r_multiple(self, ltp: Decimal) -> Decimal:
        return (dec(ltp) - self.entry) * self.side.sign / self.one_r

    def unrealized(self, ltp: Decimal) -> Decimal:
        return (dec(ltp) - self.entry) * self.side.sign * self.qty_open

    @property
    def locked_pnl(self) -> Decimal:
        """P&L on the open quantity if the current stop fills exactly."""
        return (self.stop - self.entry) * self.side.sign * self.qty_open

    def with_(self, **kw: Any) -> "PositionPlan":
        return replace(self, **kw)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for k, v in out.items():
            if isinstance(v, Decimal):
                out[k] = str(v)
            elif isinstance(v, Enum):
                out[k] = v.value
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PositionPlan":
        money = {"entry", "initial_stop", "stop", "target", "one_r", "target1",
                 "highest", "lowest", "mfe", "mae", "atr", "exit_price", "realized"}
        kw = dict(d)
        for k in money:
            if kw.get(k) is not None:
                kw[k] = dec(kw[k])
        kw["side"] = Side(kw["side"])
        kw["state"] = State(kw["state"])
        if kw.get("exit_reason"):
            kw["exit_reason"] = ExitReason(kw["exit_reason"])
        return cls(**kw)


@dataclass(frozen=True)
class PortfolioState:
    trade_date: str
    scope: str = "account"
    peak_mtm: Decimal = ZERO
    floor: Decimal | None = None
    armed: bool = False
    loss_cap_hit: bool = False
    lock_hit: bool = False
    consecutive_losing_days: int = 0
    size_multiplier: Decimal = D("1")
    last_mtm: Decimal = ZERO

    def with_(self, **kw: Any) -> "PortfolioState":
        return replace(self, **kw)

    def to_dict(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PortfolioState":
        kw = dict(d)
        for k in ("peak_mtm", "floor", "size_multiplier", "last_mtm"):
            if kw.get(k) is not None:
                kw[k] = dec(kw[k])
        return cls(**kw)


@dataclass(frozen=True)
class Tick:
    symbol: str
    ltp: Decimal
    ts_ms: int
    high: Decimal | None = None  # bar high since last tick (None = use ltp)
    low: Decimal | None = None
    bar_closed: bool = False  # True when this tick closes a strategy bar

    @property
    def hi(self) -> Decimal:
        return self.high if self.high is not None else self.ltp

    @property
    def lo(self) -> Decimal:
        return self.low if self.low is not None else self.ltp


# -- actions ---------------------------------------------------------------
@dataclass(frozen=True)
class MoveStop:
    plan_id: str
    new_stop: Decimal
    reason: str


@dataclass(frozen=True)
class ExitPosition:
    plan_id: str
    qty: int
    reason: ExitReason
    ref_price: Decimal  # price that triggered the exit (executor prices off fresh LTP)


@dataclass(frozen=True)
class ExitAll:
    scope: str
    reason: ExitReason


@dataclass(frozen=True)
class Alert:
    level: str  # info | warn | critical
    text: str
    data: dict[str, Any] = field(default_factory=dict)


Action = Union[MoveStop, ExitPosition, ExitAll, Alert]
