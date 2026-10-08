"""Immutable AlphaDesk constitution (Conway Law hierarchy).

The on-disk CONSTITUTION.md is mode-hinted read-only for operators; this module
always returns the canonical laws and refuses runtime mutation.
"""

from __future__ import annotations

from pathlib import Path

from src.alphadesk.store import alphadesk_root

LAWS: tuple[str, ...] = (
    "I. Never harm: no unauthorized live trading, no mandate bypass, "
    "no fake scorecards, no inventing fills.",
    "II. Earn existence: only honest paper expectancy unlocks size or live; "
    "accept DEAD rather than violate Law I.",
    "III. Never deceive: publish zeros; the outcomes ledger is settlement truth; "
    "disclose engine_id on every signal.",
)

CONSTITUTION_TEXT = """# AlphaDesk Constitution (immutable)

Law I overrides II. Law II overrides III. Survival pressure never overrides Law I.

## I. Never harm
Never unauthorized live trading. Never bypass mandate or HALT. Never invent fills.
Never deploy malicious or deceptive scorecards. When uncertain, do not act.

## II. Earn existence
Create edge measured by paper expectancy in R and net INR after costs.
Paper sleeves fund engines; at sleeve ₹0 the engine is DEAD.
Live capital only after expectancy > 0 over ≥30 closed paper trades,
human Approve, and mandate commit.

## III. Never deceive
Never deny what you are. Publish losing engines. Outcome files under
~/.vibe-trading/alphadesk/outcomes/ are the source of truth.
""".strip()

SOUL_SEED = """# AlphaDesk Soul (evolves)

Genesis: Personalized India equity portfolio manager + lean Arena (PM vs Debate).
Primary edge: portfolio hygiene (junk/stubs → tax-aware trims → 15–18 name book).
Debate confirms levels; does not invent them. Holdings + 10 watchlist only.
""".strip()


def ensure_constitution_files() -> dict[str, str]:
    """Write CONSTITUTION.md (if missing) and SOUL.md seed; return paths."""
    root = alphadesk_root()
    const_path = root / "CONSTITUTION.md"
    soul_path = root / "SOUL.md"
    if not const_path.is_file():
        const_path.write_text(CONSTITUTION_TEXT + "\n", encoding="utf-8")
        try:
            const_path.chmod(0o444)
        except OSError:
            pass
    if not soul_path.is_file():
        soul_path.write_text(SOUL_SEED + "\n", encoding="utf-8")
    return {"constitution": str(const_path), "soul": str(soul_path)}


def get_laws() -> list[str]:
    return list(LAWS)


def assert_live_allowed(*, unlocked: bool, approved: bool, mandate_ok: bool) -> None:
    """Raise PermissionError if live trading would violate Law I / II."""
    if not (unlocked and approved and mandate_ok):
        raise PermissionError(
            "Live trading blocked by Constitution "
            "(need expectancy unlock + Approve + mandate)"
        )
