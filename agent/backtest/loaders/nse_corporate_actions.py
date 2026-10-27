"""Price factors and dividends from NSE's corporate-action feed, confirmed against the bars.

Method adapted from abhij1306/NSE-noodles (MIT). A factor multiplies every price BEFORE its
ex-date and lies in (0, 1) (a consolidation is above 1). It is accepted only when the stock's
actual ex-date opening gap (open / previous close) confirms it, which filters the feed's known
errors (dividend amounts misread as splits, bonus debentures booked as equity bonuses, wrong
face values). Nothing is guessed: whatever does not parse or fails the gate goes to the review
list with a reason.

Covered: face-value splits, equity bonuses, rights issues (TERP), demergers (market-priced from
the ex-date gap, neutralised by an index when given) and cash dividends (kept apart: they feed a
total-return series, never the price series). All functions here are pure.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

import pandas as pd

GATE_TOLERANCE = 0.20  # NSE's widest price band; median miss on confirmed factors is ~2.6%
JUMP_THRESHOLD = 0.25
JUMP_MIN_PRICE = 2.0  # below this one 5-paise tick is a 2.5%+ move: not a corporate-action signal
DEMERGER_MIN_DROP = 0.10  # a demerger gap smaller than this is indistinguishable from news
DEMERGER_CHECK_TOL = 0.15  # |ln| tolerance of the close five sessions later vs the gap
DEMERGER_CHECK_SESSIONS = 5
DIVIDEND_MAX_FRACTION = 0.5  # cash above half the previous close goes to review

_SPLIT = re.compile(r"face\s*val\w*\s*split|split|sub-?\s*division")
_NON_EQUITY_BONUS = re.compile(
    r"bonus\W*(?:n?c?rps|ncds?\b|deb(?:entures?)?(?![a-z])|pref)"
    r"|bonus\b.*\b(?:debentures?|ncrps|preference)\b"
)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_RIGHTS_RATIO = re.compile(r"rights?\W*(?:issue\W*)?(\d+)\s*:\s*(\d+)")
_RIGHTS_PREMIUM = re.compile(r"prem(?:ium)?\.?\s*(?:of\s*)?(?:rs\.?|re\.?|inr)?\s*(\d+(?:\.\d+)?)")
_RIGHTS_PAR = re.compile(r"(?:@|\bat)\s*par\b")
_RIGHTS_PRICE = re.compile(r"@\s*(?:rs\.?|re\.?|inr)\s*(\d+(?:\.\d+)?)")
_RIGHTS_BLOCK = re.compile(r"warrant|convertible|debenture|ncd")
# an amount directly after the word "dividend" ("Dividend - Rs 2", "Special Dividend Re.0.25"),
# never a percentage and never the face value that follows "Per Share Of Rs 10"
_AMOUNT = re.compile(r"dividend\W*(?:rs\.?|re\.?|inr)?\W*?(\d+(?:\.\d+)?)(?!\d|\.\d)(?!\s*%)")
_DEMERGER = re.compile(r"de-?\s*merger|hive\W*off|spin\W*off|scheme of arrangement")


@dataclass(frozen=True)
class Factor:
    symbol: str
    ex_date: date
    kind: str  # split | bonus | rights | demerger
    factor: float
    text: str
    status: str = "confirmed"  # confirmed | price_unconfirmed | gap_estimate | raw_gap


@dataclass(frozen=True)
class Dividend:
    symbol: str
    ex_date: date
    amount: float
    text: str


# --- text parsers -------------------------------------------------------------------------------


def _split_factor(text: str) -> Fraction | None:
    nums = [Fraction(n) for n in _NUM.findall(text)]
    if len(nums) == 2 and all(n > 0 for n in nums):
        return nums[1] / nums[0]  # new face value / old
    return None


def _bonus_factor(text: str) -> Fraction | None:
    at = text.find("bonus")
    found = re.search(r"(\d+)\s*:\s*(\d+)", text[at:]) if at >= 0 else None
    if not found:
        return None
    x, y = int(found[1]), int(found[2])  # x new for y held
    return Fraction(y, x + y) if x > 0 and y > 0 else None


def parse_purpose(purpose: str) -> list[tuple[str, Fraction]]:
    """Legs (kind, factor) of a split/bonus purpose text; ``[]`` when not cleanly readable."""
    lowered = re.sub(r"\bsplt\b", "split", purpose.lower())
    if "rights" in lowered:
        return []
    cut = _SPLIT.search(lowered)
    legs: list[tuple[str, Fraction]] = []
    head = lowered[: cut.start()] if cut else lowered
    if cut:
        split = _split_factor(lowered[cut.start():])
        if split is None or split == 1:
            return []
        legs.append(("split", split))
    if "bonus" in head and not _NON_EQUITY_BONUS.search(head):
        bonus = _bonus_factor(head)
        if bonus is None:
            return []
        legs.append(("bonus", bonus))
    return legs


def parse_rights(purpose: str, face_value: float | None) -> tuple[int, int, float] | None:
    """(new shares M, held shares N, issue price S) of a rights text; None if not exact.

    S = face value + premium, or the stated all-in price. Warrants, convertibles and debentures
    alongside make the entitlement unpriceable here, so they return None (review).
    """
    low = purpose.lower()
    if _RIGHTS_BLOCK.search(low):
        return None
    ratio = _RIGHTS_RATIO.search(low)
    if not ratio or int(ratio[1]) <= 0 or int(ratio[2]) <= 0:
        return None
    premium, price = _RIGHTS_PREMIUM.search(low), _RIGHTS_PRICE.search(low)
    if premium and face_value and face_value > 0:
        s = face_value + float(premium[1])
    elif _RIGHTS_PAR.search(low) and face_value and face_value > 0:
        s = face_value
    elif price:
        s = float(price[1])
    else:
        return None
    return int(ratio[1]), int(ratio[2]), s


def rights_factor(m: int, n: int, issue_price: float, cum_price: float) -> float | None:
    """TERP / P = (N·P + M·S) / ((N+M)·P); None (neutral) when the issue is not below the market."""
    if issue_price >= cum_price or cum_price <= 0:
        return None
    return (n * cum_price + m * issue_price) / ((n + m) * cum_price)


def parse_dividend(purpose: str) -> float | None:
    """One cash amount per share, or None (percentage-of-face, several amounts, none)."""
    low = purpose.lower()
    if "dividend" not in low or any(w in low for w in ("bonus", "split", "rights")):
        return None
    amounts = [float(a) for a in _AMOUNT.findall(low)]
    # "Dividend Rs 2 / Special Dividend Rs 1" are two cash payments on one ex-date: they add up
    return sum(amounts) if amounts and all(a > 0 for a in amounts) else None


def is_demerger(purpose: str) -> bool:
    return _DEMERGER.search(purpose.lower()) is not None


def parse_ex_date(raw: object) -> date | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    return None


# --- the price gate -----------------------------------------------------------------------------


def ex_gap(bars: pd.DataFrame, ex_date: date) -> tuple[int, float, float] | None:
    """(position of the ex-date bar, open / previous close, previous close) or None."""
    ts = pd.Timestamp(ex_date)
    pos = int(bars.index.searchsorted(ts))
    if pos <= 0 or pos >= len(bars) or bars.index[pos] - ts > pd.Timedelta(days=5):
        return None
    prev = float(bars["close"].iloc[pos - 1])
    return pos, float(bars["open"].iloc[pos]) / prev, prev


def gate_status(gap: float, expected: float) -> str:
    """confirmed | ambiguous | off_factor (NSE-noodles rules 20-22)."""
    if gap <= 0 or abs(gap / expected - 1) > GATE_TOLERANCE:
        return "off_factor"
    if 2 * abs(math.log(gap / expected)) <= abs(math.log(gap)):
        return "confirmed"
    return "ambiguous"


def gate(bars: pd.DataFrame, ex_date: date, expected: float) -> bool:
    """True when the ex-date gap confirms ``expected``."""
    found = ex_gap(bars, ex_date)
    return found is not None and gate_status(found[1], expected) == "confirmed"


def demerger_factor(
    bars: pd.DataFrame, ex_date: date, others: float = 1.0, index: pd.DataFrame | None = None
) -> tuple[float, str] | None:
    """Market-priced demerger factor and its status, or None when unconfirmed.

    The ex-date gap, divided by other factors that day and by the index's overnight move so the
    market is not read as the action. Accepted only for a drop of at least 10% whose neutralised
    close five sessions later still agrees (log distance within 15%).
    """
    found = ex_gap(bars, ex_date)
    if found is None:
        return None
    pos, gap, prev = found
    idx_open = idx_close = idx_prev = None
    if index is not None and not index.empty:
        ts = bars.index[pos]
        if ts in index.index and index.index.get_loc(ts) > 0:
            at = index.index.get_loc(ts)
            idx_open, idx_close = float(index["open"].iloc[at]), float(index["close"].iloc[at])
            idx_prev = float(index["close"].iloc[at - 1])
    neutral = (idx_open / idx_prev) if idx_open and idx_prev else 1.0
    factor = gap / neutral / others
    if not 0 < factor <= 1 - DEMERGER_MIN_DROP:
        return None
    later = pos + DEMERGER_CHECK_SESSIONS
    if later >= len(bars):
        return None  # not yet confirmable
    later_close = float(bars["close"].iloc[later])
    idx_neutral = 1.0
    if idx_prev and index is not None:
        later_ts = bars.index[later]
        if later_ts not in index.index:
            return None
        idx_neutral = float(index.loc[later_ts, "close"]) / idx_prev
    check = later_close / prev / idx_neutral / others
    if abs(math.log(check / factor)) > DEMERGER_CHECK_TOL:
        return None
    return factor, ("gap_estimate" if idx_prev else "raw_gap")


# --- building the factor set --------------------------------------------------------------------


def build_factors(
    rows: list[dict],
    bars_by_symbol: dict[str, pd.DataFrame],
    index: pd.DataFrame | None = None,
) -> tuple[list[Factor], list[Dividend], list[dict]]:
    """Accepted price factors, dividends, and the review list from feed rows.

    ``bars_by_symbol`` must hold RAW (unadjusted) bars; ``index`` is an optional DataFrame
    (open, close) indexed by date, used to neutralise demerger gaps.
    """
    accepted: list[Factor] = []
    dividends: list[Dividend] = []
    review: list[dict] = []
    seen: set[tuple] = set()
    groups: dict[tuple[str, date], list[dict]] = {}

    def flag(symbol: str, ex: date, text: str, why: str) -> None:
        review.append({"symbol": symbol, "ex_date": ex, "text": text, "why": why})

    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        text = str(row.get("subject") or row.get("purpose") or "")
        ex = parse_ex_date(row.get("exDate"))
        key = (symbol, ex, text)
        if symbol not in bars_by_symbol or ex is None or key in seen:
            continue
        seen.add(key)
        groups.setdefault((symbol, ex), []).append(row)

    for (symbol, ex), members in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        bars = bars_by_symbol[symbol]
        found = ex_gap(bars, ex)
        legs: list[tuple[str, float, str]] = []  # kind, factor, text
        demergers: list[str] = []
        div_total, div_texts = 0.0, []
        for row in members:
            text = str(row.get("subject") or row.get("purpose") or "")
            low = text.lower()
            if is_demerger(low):
                demergers.append(text)
            elif "rights" in low:
                face = _to_float(row.get("faceVal"))
                parsed = parse_rights(text, face)
                if parsed is None:
                    flag(symbol, ex, text, "rights terms not exact (review)")
                elif found is None:
                    flag(symbol, ex, text, _no_bars_reason(bars, ex))
                else:
                    f = rights_factor(*parsed, cum_price=found[2])
                    if f is not None:  # None: issue price at/above market, neutral
                        legs.append(("rights", f, text))
            elif "split" in low or "sub-div" in low or "subdiv" in low or "bonus" in low:
                parsed_legs = parse_purpose(text)
                if parsed_legs:
                    legs.extend((k, float(f), text) for k, f in parsed_legs)
                elif "bonus" in low and _NON_EQUITY_BONUS.search(low):
                    continue  # debentures / preference shares: no equity issued, never a factor
                else:
                    flag(symbol, ex, text, "unparsed split/bonus text")
            elif "dividend" in low:
                amount = parse_dividend(text)
                if amount is None:
                    flag(symbol, ex, text, "dividend amount not a single cash figure")
                else:
                    div_total += amount
                    div_texts.append(text)

        others = 1.0
        if legs:
            expected = math.prod(f for _, f, _ in legs)
            if found is None:
                for _, _, text in legs:
                    flag(symbol, ex, text, _no_bars_reason(bars, ex))
            else:
                status = gate_status(found[1], expected)
                has_rights = any(k == "rights" for k, _, _ in legs)
                if status == "confirmed" or (status == "ambiguous" and has_rights):
                    label = "confirmed" if status == "confirmed" else "price_unconfirmed"
                    accepted.extend(Factor(symbol, ex, k, f, t, label) for k, f, t in legs)
                    others = expected
                else:
                    for _, _, text in legs:
                        flag(symbol, ex, text, f"price gate {status} (expected {expected:.4f},"
                                               f" gap {found[1]:.4f})")
        for text in demergers:
            result = demerger_factor(bars, ex, others, index)
            if result is None:
                flag(symbol, ex, text, "demerger not confirmed by the bars (drop <10%, "
                                       "no later close, or close disagrees)")
            else:
                accepted.append(Factor(symbol, ex, "demerger", result[0], text, result[1]))
        if div_total > 0:
            if found is not None and div_total > DIVIDEND_MAX_FRACTION * found[2]:
                flag(symbol, ex, "; ".join(div_texts), "cash above half the previous close")
            else:
                dividends.append(Dividend(symbol, ex, div_total, "; ".join(div_texts)))
    return accepted, dividends, review


def _no_bars_reason(bars: pd.DataFrame, ex: date) -> str:
    if len(bars) and pd.Timestamp(ex) > bars.index[-1]:
        return "pending: ex-date after the store's last session"
    return "no bars around the ex-date"


def _to_float(raw: object) -> float | None:
    try:
        return float(str(raw).replace(",", "").strip())
    except ValueError:
        return None


# --- applying -----------------------------------------------------------------------------------


def apply_factors(
    bars: pd.DataFrame,
    factors: list[Factor],
    dividends: list[Dividend] | None = None,
    basis: str = "price",
) -> pd.DataFrame:
    """Adjust RAW bars. basis: ``raw`` (untouched) | ``price`` (factors; volume by the inverse) |
    ``total_return`` (price × each dividend's 1 - D / previous raw close)."""
    if basis == "raw" or bars.empty:
        return bars
    if basis not in ("price", "total_return"):
        raise ValueError(f"unknown price basis {basis!r}")
    mult = pd.Series(1.0, index=bars.index)
    vol_mult = pd.Series(1.0, index=bars.index)
    for f in factors:
        before = bars.index < pd.Timestamp(f.ex_date)
        mult[before] *= f.factor
        vol_mult[before] *= f.factor
    if basis == "total_return":
        raw_close = bars["close"]
        for d in dividends or []:
            ts = pd.Timestamp(d.ex_date)
            prior = raw_close[raw_close.index < ts]
            if prior.empty:
                continue
            mult[bars.index < ts] *= max(1.0 - d.amount / float(prior.iloc[-1]), 1e-6)
    if (mult == 1.0).all():
        return bars
    out = bars.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * mult
    if "volume" in out:
        out["volume"] = out["volume"] / vol_mult
    return out


def find_jumps(
    bars: pd.DataFrame, threshold: float = JUMP_THRESHOLD, min_price: float = JUMP_MIN_PRICE
) -> list[tuple[pd.Timestamp, float]]:
    """Sessions whose close moves more than ``threshold`` from the previous close.

    On a correctly adjusted series each is a genuine move or an unresolved corporate action
    (rights with unclear terms, a demerger, a symbol reuse). Sessions whose previous close is
    under ``min_price`` are skipped: sub-rupee stocks swing 25% on a single tick. Works on bars
    from any source.
    """
    if len(bars) < 2:
        return []
    close = bars["close"]
    ret = close.pct_change()
    keep = (ret.abs() > threshold) & (close.shift(1) >= min_price)
    return [(ts, float(r)) for ts, r in ret[keep].items()]
