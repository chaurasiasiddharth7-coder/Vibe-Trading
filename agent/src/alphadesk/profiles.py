"""Desk profiles: a named person (a test user, a client) with a private copy of the desk.

The owner is the *default profile*: no id, data in ``~/.vibe-trading/alphadesk`` exactly as before.
Every other profile gets its own folder, ``~/.vibe-trading/desk_profiles/<id>/alphadesk``, so practice
trades, filed intraday days, settings, caches and preferences are never shared.

A profile signs in with its own key. Only a hash is stored (``~/.vibe-trading/desk_profiles.json``).
The API layer calls :func:`profile_for_token` and :func:`bind`; everything below that asks
:func:`current_id` through ``alphadesk_root()``. Work started on another thread must be wrapped
with :func:`carry` so it keeps writing into the right profile.

    python -m src.alphadesk.profiles create ayushi --name Ayushi
    python -m src.alphadesk.profiles list
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

KEY_PREFIX = "dk_"
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_LOCK = threading.Lock()
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("desk_profile", default=None)

# What a profile key may reach. Everything else (agent runs, settings, shell, live trading) needs the owner key.
ALLOWED_PREFIXES = ("/api/desk/", "/api/analysis/", "/api/kronos/")
DENIED_PREFIXES = ("/api/desk/live", "/api/desk/audit", "/api/desk/token-budget", "/api/desk/tax-ytd")


def _base() -> Path:
    return Path.home() / ".vibe-trading"


def registry_path() -> Path:
    return _base() / "desk_profiles.json"


def _load() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(users: dict[str, dict[str, Any]]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- who is asking ---------------------------------------------------------------------------------


def current_id() -> str | None:
    """The active profile id, or ``None`` for the owner's own desk."""
    return _current.get()


def scoped_key(key: str) -> str:
    """``key`` for the owner, ``<id>:key`` for a profile: for in-memory maps shared by every profile."""
    pid = _current.get()
    return f"{pid}:{key}" if pid else key


@contextlib.contextmanager
def use(profile_id: str | None) -> Iterator[None]:
    token = _current.set(profile_id)
    try:
        yield
    finally:
        _current.reset(token)


def bind(profile_id: str | None) -> None:
    """Make ``profile_id`` the active profile for the rest of this request (called once by auth)."""
    _current.set(profile_id)


def carry(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``fn`` so a new thread runs under the profile that was active when ``carry`` was called."""
    pid = _current.get()

    def run(*args: Any, **kwargs: Any) -> Any:
        with use(pid):
            return fn(*args, **kwargs)

    return run


def root_for(profile_id: str | None) -> Path:
    if profile_id is None:
        return _base() / "alphadesk"
    if not _ID_RE.match(profile_id):
        raise ValueError(f"bad profile id {profile_id!r}")
    return _base() / "desk_profiles" / profile_id / "alphadesk"


def path_allowed(path: str) -> bool:
    """May a profile key call this route?"""
    return path.startswith(ALLOWED_PREFIXES) and not path.startswith(DENIED_PREFIXES) and "/approve" not in path


# --- registry ---------------------------------------------------------------------------------------


def profile_for_token(token: str) -> str | None:
    """The profile id this key belongs to, else ``None``. Constant-time per candidate."""
    if not token:
        return None
    digest = _hash(token)
    found = None
    for pid, rec in _load().items():
        if hmac.compare_digest(str(rec.get("key_hash", "")), digest):
            found = pid
    return found


def get(profile_id: str) -> dict[str, Any] | None:
    rec = _load().get(profile_id)
    return None if rec is None else {k: v for k, v in rec.items() if k != "key_hash"}


def list_profiles() -> list[dict[str, Any]]:
    return [{"id": pid, **{k: v for k, v in rec.items() if k != "key_hash"}} for pid, rec in sorted(_load().items())]


def create(profile_id: str, name: str) -> str:
    """Add a profile and return its key. The key is shown once; only its hash is kept."""
    if not _ID_RE.match(profile_id):
        raise ValueError("id must be 2-32 chars: lowercase letters, digits, - or _, starting with a letter")
    with _LOCK:
        users = _load()
        if profile_id in users:
            raise ValueError(f"profile {profile_id!r} already exists")
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        users[profile_id] = {
            "name": name.strip() or profile_id,
            "key_hash": _hash(key),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _save(users)
    root_for(profile_id).mkdir(parents=True, exist_ok=True)
    return key


def rotate_key(profile_id: str) -> str:
    with _LOCK:
        users = _load()
        if profile_id not in users:
            raise ValueError(f"no profile {profile_id!r}")
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        users[profile_id]["key_hash"] = _hash(key)
        _save(users)
    return key


def all_ids() -> list[str | None]:
    """The owner plus every profile: what a background job loops over."""
    return [None, *sorted(_load())]


# --- preferences (how this person wants the dashboard) -----------------------------------------------

PREF_LAYOUTS = ("tabs", "stacked")
PREF_DENSITY = ("comfortable", "compact")
PREF_TILE_STYLES = ("cards", "strip")
_ITEM_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_ITEMS = 60


def default_prefs() -> dict[str, Any]:
    """``tiles`` is the ordered list of tiles to show (``None`` = the app's default set). The ``hidden_*`` lists
    name what to hide, so anything added to the app later shows up on its own."""
    return {"version": 1, "layout": "tabs", "density": "comfortable", "tile_style": "cards",
            "tiles": None, "hidden_tabs": [], "hidden_filters": [], "hidden_rows": []}


def _ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for v in raw:
        if isinstance(v, str) and _ITEM_RE.match(v) and v not in out:
            out.append(v)
        if len(out) >= MAX_ITEMS:
            break
    return out


def clean_prefs(raw: Any) -> dict[str, Any]:
    """Keep only known keys with safe values. Item ids are checked for shape here, for meaning in the UI."""
    base = default_prefs()
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    if raw.get("layout") in PREF_LAYOUTS:
        out["layout"] = raw["layout"]
    if raw.get("density") in PREF_DENSITY:
        out["density"] = raw["density"]
    if raw.get("tile_style") in PREF_TILE_STYLES:
        out["tile_style"] = raw["tile_style"]
    out["tiles"] = _ids(raw["tiles"]) if isinstance(raw.get("tiles"), list) else None
    for k in ("hidden_tabs", "hidden_filters", "hidden_rows"):
        out[k] = _ids(raw.get(k))
    return out


def prefs_path() -> Path:
    from src.alphadesk.store import alphadesk_root

    return alphadesk_root() / "prefs.json"


def load_prefs() -> dict[str, Any]:
    try:
        return clean_prefs(json.loads(prefs_path().read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return default_prefs()


def save_prefs(raw: Any) -> dict[str, Any]:
    prefs = clean_prefs(raw)
    path = prefs_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    tmp.replace(path)
    return prefs


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m src.alphadesk.profiles")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="add a profile and print its connect key once")
    c.add_argument("id")
    c.add_argument("--name", default="")
    r = sub.add_parser("rotate", help="issue a new key for a profile")
    r.add_argument("id")
    sub.add_parser("list")
    args = p.parse_args(argv)
    if args.cmd == "list":
        for rec in list_profiles():
            print(f"{rec['id']:<16} {rec.get('name', '')}  created {rec.get('created_at', '')}")
        return 0
    try:
        key = create(args.id, args.name or args.id.title()) if args.cmd == "create" else rotate_key(args.id)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
