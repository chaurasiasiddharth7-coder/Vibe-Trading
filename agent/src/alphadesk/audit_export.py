"""Audit export — pack AlphaDesk state for review / backup."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.alphadesk.store import alphadesk_root
from src.alphadesk.survival import ensure_engines, load_book, scorecard_row
from src.alphadesk.tax_ytd import summary as tax_summary
from src.alphadesk.token_budget import status as budget_status


def _collect() -> dict[str, Any]:
    root = alphadesk_root()
    engines = ensure_engines(
        [
            "pm",
            "debate",
            "br_pdh_pdl",
            "br_order_block",
            "br_orb",
            "sentiment",
            "alpha_forge",
        ]
    )

    def _read_glob(sub: str, limit: int = 200) -> list[dict[str, Any]]:
        d = root / sub
        if not d.is_dir():
            return []
        out: list[dict[str, Any]] = []
        paths = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths[:limit]:
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return out

    live = root / "live"
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "scorecard": [scorecard_row(e) for e in engines],
        "book": load_book(),
        "tax_ytd": tax_summary(),
        "token_budget": budget_status(),
        "outcomes": _read_glob("outcomes", 300),
        "signals": _read_glob("signals", 100),
        "tickets": _read_glob("live/tickets", 50) if (live / "tickets").is_dir() else [],
        "unlocks": _read_glob("live/unlocks", 20) if (live / "unlocks").is_dir() else [],
        "portfolio": _read_glob("portfolio", 5),
    }


def export_audit(*, as_zip: bool = True) -> dict[str, Any]:
    """Write audit JSON (and optional zip) under alphadesk/audit/."""
    payload = _collect()
    audit_dir = alphadesk_root() / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = audit_dir / f"audit_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    zip_path: Path | None = None
    if as_zip:
        zip_path = audit_dir / f"audit_{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(json_path.name, json_path.read_text(encoding="utf-8"))
            # Include constitution if present
            const = alphadesk_root() / "CONSTITUTION.md"
            if const.is_file():
                zf.write(const, arcname="CONSTITUTION.md")
    return {
        "ok": True,
        "json_path": str(json_path),
        "zip_path": str(zip_path) if zip_path else None,
        "counts": {
            "outcomes": len(payload.get("outcomes") or []),
            "signals": len(payload.get("signals") or []),
            "tickets": len(payload.get("tickets") or []),
            "engines": len(payload.get("scorecard") or []),
        },
        "tax_ytd": payload.get("tax_ytd"),
        "token_budget": payload.get("token_budget"),
    }
