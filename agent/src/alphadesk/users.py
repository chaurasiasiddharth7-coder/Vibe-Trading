"""Desk users: people who sign in to the dashboard with a username and password.

Built on desk profiles (``profiles.py``): every non-owner user is linked to a profile, so their practice
trades, groups and preferences stay private. The owner role uses the owner's own desk.

Security rules (see the execution plan, Phase 5):

* Only an Argon2id hash of a password is stored, never the password. Nothing here logs one.
* The owner sets a one-time password; the user must change it at first sign-in. Minimum 12 characters.
* 5 wrong tries lock the account for 15 minutes. Every sign-in, failure, reset and change is audited.
* Sessions are random 32-byte tokens in an HttpOnly cookie, 12 hours; only their SHA-256 is stored.
* Disabling a user or resetting their password ends all their sessions at once.
* Password sign-in reaches only the desk routes (``profiles.ALLOWED_PREFIXES``), never live orders, settings,
  the agent or the shell, whatever the role. The owner key keeps working for scripts.

    python -m src.alphadesk.users create-owner sid --name Sid     # prints a one-time password once
    python -m src.alphadesk.users list
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import re
import secrets
import sqlite3
import string
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # a server built without argon2-cffi must still start: only password sign-in is then off
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

    ARGON2_OK = True
except ImportError:  # pragma: no cover - exercised only on a broken install
    ARGON2_OK = False
    PasswordHasher = None  # type: ignore[assignment,misc]

    class InvalidHashError(Exception):  # type: ignore[no-redef]
        pass

    VerificationError = VerifyMismatchError = InvalidHashError  # type: ignore[misc]

ROLES = ("owner", "trader", "viewer")
# Pages a user can be given. "users" is the owner's admin page and is never granted to other roles.
PAGES = ("stocks", "intraday", "btst", "fno", "wealth", "portfolio", "suggestions", "strategies")
OWNER_PAGES = (*PAGES, "users")
DEFAULT_PAGES = ("stocks", "portfolio", "suggestions")

MIN_PASSWORD = 12
MAX_PASSWORD = 256
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
SESSION_SECONDS = 12 * 3600
COOKIE = "vt_session"

_USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")
_LOCK = threading.Lock()
_HASHER = PasswordHasher() if ARGON2_OK else None  # argon2id with the library's current recommended cost
# Verified when the username is unknown, so a miss takes as long as a wrong password.
_DUMMY_HASH = _HASHER.hash("not-a-real-password-" + secrets.token_hex(8)) if _HASHER else ""
NO_ARGON2 = "Password sign-in is off: the server is missing the argon2-cffi package. Use the connect key."

_current: contextvars.ContextVar["User | None"] = contextvars.ContextVar("desk_user", default=None)


class AuthError(Exception):
    """A sign-in or account action was refused. ``str(exc)`` is safe to show the person."""


@dataclass
class User:
    id: str
    username: str
    name: str
    role: str
    pages: list[str] = field(default_factory=list)
    must_change: bool = False
    disabled: bool = False
    locked_until: float = 0.0
    failed: int = 0
    created_at: float = 0.0
    last_login_at: float | None = None

    @property
    def profile_id(self) -> str | None:
        """The desk profile whose data this user sees: the owner's own desk for the owner role."""
        return None if self.role == "owner" else self.id

    def allowed_pages(self) -> list[str]:
        return list(OWNER_PAGES) if self.role == "owner" else [p for p in self.pages if p in PAGES]

    def public(self) -> dict[str, Any]:
        """Everything the UI may see. Never includes the hash."""
        now = time.time()
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "role": self.role,
            "pages": self.allowed_pages(),
            "must_change": self.must_change,
            "disabled": self.disabled,
            "locked": self.locked_until > now,
            "locked_until": self.locked_until if self.locked_until > now else None,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


# --- storage ---------------------------------------------------------------------------------------


def db_path() -> Path:
    return Path.home() / ".vibe-trading" / "desk_users.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
            pages TEXT NOT NULL, pw_hash TEXT NOT NULL, must_change INTEGER NOT NULL DEFAULT 1,
            failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
            disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, last_login_at REAL);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL NOT NULL,
            expires_at REAL NOT NULL, ip TEXT);
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
        CREATE TABLE IF NOT EXISTS auth_audit (
            ts REAL NOT NULL, event TEXT NOT NULL, user_id TEXT, username TEXT, ip TEXT, detail TEXT);
        """
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return con


def _row_user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    try:
        pages = [p for p in json.loads(row["pages"]) if isinstance(p, str)]
    except (TypeError, ValueError):
        pages = []
    return User(
        id=row["id"], username=row["username"], name=row["name"], role=row["role"], pages=pages,
        must_change=bool(row["must_change"]), disabled=bool(row["disabled"]),
        locked_until=float(row["locked_until"] or 0), failed=int(row["failed"] or 0),
        created_at=float(row["created_at"]), last_login_at=row["last_login_at"],
    )


def audit(event: str, user: User | None = None, *, username: str | None = None, ip: str | None = None,
          detail: str = "") -> None:
    """Record an account event. ``detail`` must never hold a password."""
    with _connect() as con:
        con.execute(
            "INSERT INTO auth_audit VALUES (?,?,?,?,?,?)",
            (time.time(), event, user.id if user else None, user.username if user else username, ip, detail[:200]),
        )


def audit_log(limit: int = 200) -> list[dict[str, Any]]:
    with _connect() as con:
        rows = con.execute("SELECT * FROM auth_audit ORDER BY ts DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
    return [dict(r) for r in rows]


# --- passwords ---------------------------------------------------------------------------------------


def generate_password(length: int = 16) -> str:
    """A one-time password: letters and digits, no look-alikes (0 O 1 l I)."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    return "".join(secrets.choice(alphabet) for _ in range(max(MIN_PASSWORD, length)))


def check_password_rules(password: str, *, username: str = "") -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AuthError("That password is too long.")
    if username and username.lower() in password.lower():
        raise AuthError("Don't put your username in your password.")
    if len(set(password)) < 5:
        raise AuthError("Use more different characters.")


def _hash_password(password: str) -> str:
    if _HASHER is None:
        raise AuthError(NO_ARGON2)
    return _HASHER.hash(password)


def _verify(pw_hash: str, password: str) -> bool:
    if _HASHER is None:
        raise AuthError(NO_ARGON2)
    try:
        return _HASHER.verify(pw_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# --- users ---------------------------------------------------------------------------------------------


def _clean_pages(role: str, pages: Any) -> list[str]:
    if role == "owner":
        return list(OWNER_PAGES)
    raw = pages if isinstance(pages, list) else list(DEFAULT_PAGES)
    out = [p for p in PAGES if p in raw]
    return out or ["stocks"]


def _check_username(username: str) -> str:
    u = (username or "").strip().lower()
    if not _USERNAME_RE.match(u):
        raise AuthError("Username: 2-32 characters, lowercase letters, digits, dot, dash or underscore, starting with a letter.")
    return u


def get_user(user_id: str) -> User | None:
    with _connect() as con:
        return _row_user(con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def get_by_username(username: str) -> User | None:
    with _connect() as con:
        return _row_user(con.execute("SELECT * FROM users WHERE username=?", ((username or "").strip().lower(),)).fetchone())


def list_users() -> list[User]:
    with _connect() as con:
        return [u for u in (_row_user(r) for r in con.execute("SELECT * FROM users ORDER BY created_at")) if u]


def create_user(username: str, name: str, role: str = "trader", pages: Any = None, *,
                actor: str = "owner-key") -> tuple[User, str]:
    """Add a user and return it with a one-time password. The password is shown once and never stored."""
    u = _check_username(username)
    if role not in ROLES:
        raise AuthError("Role must be owner, trader or viewer.")
    clean_pages = _clean_pages(role, pages)
    otp = generate_password()
    uid = u.replace(".", "_")
    with _LOCK:
        if get_by_username(u) is not None or get_user(uid) is not None:
            raise AuthError("That username is taken.")
        if role != "owner":
            from src.alphadesk import profiles as _p

            if _p.get(uid) is not None:
                raise AuthError("A desk with that name already exists (made with a connect key). Pick another username.")
            _ensure_profile(uid, name or u)
        with _connect() as con:
            con.execute(
                "INSERT INTO users (id, username, name, role, pages, pw_hash, must_change, created_at) VALUES (?,?,?,?,?,?,1,?)",
                (uid, u, (name or u).strip()[:60], role, json.dumps(clean_pages), _hash_password(otp), time.time()),
            )
    user = get_user(uid)
    assert user is not None
    audit("user_created", user, detail=f"role={role} by={actor}")
    return user, otp


def _ensure_profile(profile_id: str, name: str) -> None:
    """Every non-owner user gets a desk profile, so background jobs (settling trades) cover them too."""
    from src.alphadesk import profiles

    if profiles.get(profile_id) is None:
        try:
            profiles.create(profile_id, name, for_desk_user=True)  # key not shown; the owner can rotate one for scripts
        except ValueError as exc:
            raise AuthError(f"Could not make a desk for this user: {exc}") from exc


def update_user(user_id: str, *, name: str | None = None, role: str | None = None, pages: Any = None,
                actor: str = "owner") -> User:
    user = get_user(user_id)
    if user is None:
        raise AuthError("No such user.")
    new_role = role or user.role
    if new_role not in ROLES:
        raise AuthError("Role must be owner, trader or viewer.")
    if user.role == "owner" and new_role != "owner" and _owner_count() <= 1:
        raise AuthError("Keep at least one owner.")
    if user.role == "owner" and new_role != "owner":
        _ensure_profile(user.id, user.name)
    new_pages = _clean_pages(new_role, pages if pages is not None else user.pages)
    with _connect() as con:
        con.execute(
            "UPDATE users SET name=?, role=?, pages=? WHERE id=?",
            ((name or user.name).strip()[:60], new_role, json.dumps(new_pages), user_id),
        )
    if new_role != user.role:
        end_sessions(user_id)  # a role change takes effect at once
    out = get_user(user_id)
    assert out is not None
    audit("user_updated", out, detail=f"role={new_role} pages={','.join(new_pages)} by={actor}")
    return out


def _owner_count() -> int:
    with _connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM users WHERE role='owner' AND disabled=0").fetchone()[0])


def reset_password(user_id: str, *, actor: str = "owner") -> str:
    """New one-time password; the user must change it. Ends their sessions and clears a lock."""
    user = get_user(user_id)
    if user is None:
        raise AuthError("No such user.")
    otp = generate_password()
    with _connect() as con:
        con.execute("UPDATE users SET pw_hash=?, must_change=1, failed=0, locked_until=0 WHERE id=?",
                    (_hash_password(otp), user_id))
    end_sessions(user_id)
    audit("password_reset", user, detail=f"by={actor}")
    return otp


def set_disabled(user_id: str, disabled: bool, *, actor: str = "owner") -> User:
    user = get_user(user_id)
    if user is None:
        raise AuthError("No such user.")
    if disabled and user.role == "owner" and _owner_count() <= 1:
        raise AuthError("Keep at least one owner who can sign in.")
    with _connect() as con:
        con.execute("UPDATE users SET disabled=? WHERE id=?", (1 if disabled else 0, user_id))
    if disabled:
        end_sessions(user_id)
    audit("user_disabled" if disabled else "user_enabled", user, detail=f"by={actor}")
    out = get_user(user_id)
    assert out is not None
    return out


def unlock(user_id: str, *, actor: str = "owner") -> None:
    user = get_user(user_id)
    if user is None:
        raise AuthError("No such user.")
    with _connect() as con:
        con.execute("UPDATE users SET failed=0, locked_until=0 WHERE id=?", (user_id,))
    audit("user_unlocked", user, detail=f"by={actor}")


# --- sign-in and sessions --------------------------------------------------------------------------------

GENERIC_FAIL = "Wrong username or password."


_GHOSTS: dict[str, dict[str, float]] = {}  # unknown usernames: {"failed", "locked_until"}, exactly like a real account
_GHOST_LOCK = threading.Lock()


def _locked_msg(until: float, now: float) -> str:
    mins = max(1, int((until - now + 59) // 60))
    return f"Too many wrong tries. Try again in {mins} minute{'s' if mins != 1 else ''}, or ask the owner to unlock you."


def _ghost_try(name: str, now: float) -> None:
    """Unknown usernames lock after 5 tries too, so a lock message never tells real names from made-up ones."""
    with _GHOST_LOCK:
        if len(_GHOSTS) > 10_000:
            _GHOSTS.clear()
        g = _GHOSTS.setdefault(name, {"failed": 0, "locked_until": 0.0})
        if g["locked_until"] > now:  # same as a real lock: no new count, the countdown runs from the lock time
            raise AuthError(_locked_msg(g["locked_until"], now))
        if g["locked_until"]:
            g["failed"], g["locked_until"] = 0, 0.0  # the old lock ran out: start a fresh count
        g["failed"] += 1
        if g["failed"] >= MAX_FAILS:
            g["locked_until"] = now + LOCK_SECONDS
            raise AuthError("Too many wrong tries. This account is locked for 15 minutes.")


def _claim_attempt(user_id: str, now: float) -> tuple[int, float]:
    """Count this try BEFORE checking the password, in one transaction, so parallel tries can't skip the lock.

    Returns (tries so far including this one, locked_until). At most MAX_FAILS passwords are ever checked
    per lock window, however many requests arrive at once."""
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT failed, locked_until FROM users WHERE id=?", (user_id,)).fetchone()
        failed, until = int(row["failed"] or 0), float(row["locked_until"] or 0)
        if until > now:
            con.execute("COMMIT")
            return failed, until
        if until and until <= now:
            failed = 0  # the old lock ran out: start a fresh count
        failed += 1
        until = now + LOCK_SECONDS if failed > MAX_FAILS else 0.0
        con.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?", (failed, until, user_id))
        con.execute("COMMIT")
        return failed, until
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def login(username: str, password: str, *, ip: str | None = None) -> tuple[User, str]:
    """Check a password and open a session. Returns the user and the raw session token (cookie value)."""
    now = time.time()
    if not isinstance(password, str) or len(password) > MAX_PASSWORD:
        password = ""
    name = (username or "").strip().lower()[:64]
    user = get_by_username(name)
    if user is None:
        _verify(_DUMMY_HASH, password)  # same cost as a real check
        audit("login_failed", username=name[:40], ip=ip, detail="unknown user")
        _ghost_try(name, now)
        raise AuthError(GENERIC_FAIL)
    failed, until = _claim_attempt(user.id, now)
    if until > now:
        audit("login_locked", user, ip=ip)
        raise AuthError(_locked_msg(until, now))
    with _connect() as con:
        pw_hash = con.execute("SELECT pw_hash FROM users WHERE id=?", (user.id,)).fetchone()["pw_hash"]
    ok = _verify(pw_hash, password)
    if not ok or user.disabled:
        # A turned-off account answers exactly like a wrong password, so it can't confirm a guess.
        if failed >= MAX_FAILS:
            with _connect() as con:
                con.execute("UPDATE users SET locked_until=? WHERE id=?", (now + LOCK_SECONDS, user.id))
        audit("login_disabled" if ok else "login_failed", user, ip=ip,
              detail=f"try {failed}" + (" -> locked 15 min" if failed >= MAX_FAILS else ""))
        if failed >= MAX_FAILS:
            raise AuthError("Too many wrong tries. This account is locked for 15 minutes.")
        raise AuthError(GENERIC_FAIL)
    if _HASHER is not None and _HASHER.check_needs_rehash(pw_hash):
        with _connect() as con:
            con.execute("UPDATE users SET pw_hash=? WHERE id=?", (_hash_password(password), user.id))
    token = secrets.token_urlsafe(32)
    with _connect() as con:
        con.execute("UPDATE users SET failed=0, locked_until=0, last_login_at=? WHERE id=?", (now, user.id))
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                    (_token_hash(token), user.id, now, now + SESSION_SECONDS, ip))
    audit("login_ok", user, ip=ip)
    fresh = get_user(user.id)
    assert fresh is not None
    return fresh, token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_user(token: str | None) -> User | None:
    """The signed-in user for a session cookie, or ``None`` (missing, expired, ended, or user turned off)."""
    if not token or len(token) > 200:
        return None
    with _connect() as con:
        row = con.execute("SELECT user_id, expires_at FROM sessions WHERE token_hash=?", (_token_hash(token),)).fetchone()
    if row is None or float(row["expires_at"]) < time.time():
        return None
    user = get_user(row["user_id"])
    if user is None or user.disabled:
        return None
    return user


def logout(token: str | None) -> None:
    if not token:
        return
    user = session_user(token)
    with _connect() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    if user:
        audit("logout", user)


def end_sessions(user_id: str) -> int:
    with _connect() as con:
        cur = con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return cur.rowcount


def change_password(user: User, old: str, new: str, *, keep_token: str | None = None, ip: str | None = None) -> None:
    """Change one's own password. Ends every other session of this user."""
    with _connect() as con:
        pw_hash = con.execute("SELECT pw_hash FROM users WHERE id=?", (user.id,)).fetchone()["pw_hash"]
    failed, until = _claim_attempt(user.id, time.time())
    if until > time.time():
        end_sessions(user.id)
        audit("password_change_locked", user, ip=ip)
        raise AuthError("Too many wrong tries. You've been signed out for 15 minutes.")
    if _verify(pw_hash, old or ""):
        # The right current password: forget earlier misses, so a weak new password can never lock you out.
        with _connect() as con:
            con.execute("UPDATE users SET failed=0, locked_until=0 WHERE id=?", (user.id,))
    else:
        audit("password_change_failed", user, ip=ip, detail=f"try {failed}")
        if failed >= MAX_FAILS:
            with _connect() as con:
                con.execute("UPDATE users SET locked_until=? WHERE id=?", (time.time() + LOCK_SECONDS, user.id))
            end_sessions(user.id)
            raise AuthError("Too many wrong tries. You've been signed out for 15 minutes.")
        raise AuthError("Your current password is wrong.")
    check_password_rules(new, username=user.username)
    if _verify(pw_hash, new):
        raise AuthError("Pick a new password, not the old one.")
    with _connect() as con:
        con.execute("UPDATE users SET pw_hash=?, must_change=0, failed=0, locked_until=0 WHERE id=?",
                    (_hash_password(new), user.id))
        if keep_token:
            con.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user.id, _token_hash(keep_token)))
        else:
            con.execute("DELETE FROM sessions WHERE user_id=?", (user.id,))
    audit("password_changed", user, ip=ip)


def counts() -> dict[str, int]:
    """For the Users page tiles."""
    now = time.time()
    day_start = now - (now + 19800) % 86400  # midnight India time
    users = list_users()
    return {
        "active": sum(1 for u in users if not u.disabled),
        "signed_in_today": sum(1 for u in users if (u.last_login_at or 0) >= day_start),
        "locked": sum(1 for u in users if u.locked_until > now),
    }


# --- who is asking, and what they may reach ---------------------------------------------------------------


def current_user() -> User | None:
    """The password-signed-in user for this request, or ``None`` when the request used a key or loopback."""
    return _current.get()


def bind_user(user: User | None) -> None:
    _current.set(user)


# Desk routes by the page they serve. Anything else under the allowed prefixes belongs to "stocks".
_PAGE_ROUTES: tuple[tuple[str, str], ...] = (
    ("/api/desk/users", "users"),
    ("/api/desk/intraday", "intraday"),
    ("/api/desk/btst", "btst"),
    ("/api/desk/fno", "fno"),
    ("/api/desk/wealth", "wealth"),
    ("/api/desk/groups", "portfolio"),
    ("/api/desk/holdings", "portfolio"),
    ("/api/desk/suggestions", "suggestions"),
    ("/api/desk/strategies", "strategies"),
)
# Reachable by every signed-in user whatever their pages: sign-in, who-am-I, their own layout, guard health.
# The watchlist home screen is everyone's: each person's own lists, and only the owner gets Kite prices.
_ALWAYS = ("/api/desk/auth/", "/api/desk/me", "/api/desk/guard/status", "/api/desk/universes",
           "/api/desk/watchlists", "/api/desk/quotes", "/api/desk/symbols", "/api/desk/brief")
# Before the one-time password is replaced, only these: sign-in actions and who-am-I.
_BEFORE_CHANGE = ("/api/desk/auth/", "/api/desk/me")  # documented; checked inline in route_allowed
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Desk tools that spend the owner's AI-model budget: the owner only, whatever pages someone has.
_OWNER_ONLY_ROUTES = ("/api/desk/morning", "/api/desk/arena", "/api/desk/pm/")


def page_for_path(path: str) -> str:
    for prefix, page in _PAGE_ROUTES:
        if path == prefix or path.startswith(prefix + "/"):
            return page
    return "stocks"


def route_allowed(user: User, path: str, method: str) -> tuple[bool, str]:
    """May this password user call this route? Returns (allowed, reason)."""
    from src.alphadesk import profiles

    if not profiles.path_allowed(path):
        return False, "Password sign-in can't reach this part of the app."
    if user.must_change:
        if path == "/api/desk/me" or path.startswith("/api/desk/auth/"):
            return True, ""
        return False, "Change your password first."
    if any(path == p.rstrip("/") or path.startswith(p) for p in _ALWAYS):
        return True, ""
    if user.role != "owner" and any(path == r.rstrip("/") or path.startswith(r) for r in _OWNER_ONLY_ROUTES):
        return False, "This desk tool uses the owner's AI budget, so it's for the owner only."
    page = page_for_path(path)
    if page not in user.allowed_pages():
        return False, "You don't have access to this page."
    if user.role == "viewer" and method.upper() not in _SAFE_METHODS:
        return False, "Viewers can look but not change anything."
    return True, ""


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m src.alphadesk.users")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-owner", help="add an owner user and print a one-time password once")
    c.add_argument("username")
    c.add_argument("--name", default="")
    r = sub.add_parser("reset", help="print a new one-time password for a user")
    r.add_argument("username")
    sub.add_parser("list")
    args = p.parse_args(argv)
    try:
        if args.cmd == "list":
            for u in list_users():
                d = u.public()
                print(f"{d['username']:<20} {d['role']:<7} {'off' if d['disabled'] else 'on':<3} pages={','.join(d['pages'])}")
            return 0
        if args.cmd == "create-owner":
            _user, otp = create_user(args.username, args.name or args.username.title(), "owner", actor="cli")
        else:
            user = get_by_username(args.username)
            if user is None:
                print("error: no such user")
                return 1
            otp = reset_password(user.id, actor="cli")
    except AuthError as exc:
        print(f"error: {exc}")
        return 1
    print(f"One-time password (shown once, change it at first sign-in): {otp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
