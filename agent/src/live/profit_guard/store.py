"""Crash-safe state for Profit Guard: SQLite (WAL, synchronous=FULL) + append-only JSONL audit.

Write-ahead contract: the executor records an *intent* (status ``pending``) and the
store commits it BEFORE any broker call. After a crash, ``pending_intents()`` tells
reconcile exactly what may or may not have reached the broker.

Single writer: an exclusive ``fcntl`` lock on ``<db>.lock`` makes a second process
fail fast instead of corrupting state.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.live.profit_guard.models import PortfolioState, PositionPlan, State

SCHEMA_VERSION = 1
INTENT_STATUSES = {"pending", "sent", "confirmed", "failed"}


class StoreLockedError(RuntimeError):
    pass


def default_paths() -> tuple[Path, Path]:
    from src.live.paths import live_root

    root = live_root()
    return root / "profit_guard.db", root / "profit_guard_audit.jsonl"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, strategy TEXT NOT NULL, symbol TEXT NOT NULL, mode TEXT NOT NULL,
    state TEXT NOT NULL, json TEXT NOT NULL, updated_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio (
    trade_date TEXT NOT NULL, scope TEXT NOT NULL, json TEXT NOT NULL, updated_ms INTEGER NOT NULL,
    PRIMARY KEY (trade_date, scope));
CREATE TABLE IF NOT EXISTS intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
    status TEXT NOT NULL, broker_order_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS exits (
    plan_id TEXT NOT NULL, qty INTEGER NOT NULL, reason TEXT NOT NULL, price TEXT,
    broker_order_id TEXT, ts_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS plans_state ON plans(state, mode);
CREATE INDEX IF NOT EXISTS intents_status ON intents(status);
"""


class GuardStore:
    def __init__(self, db_path: Path | str | None = None, audit_path: Path | str | None = None):
        d, a = default_paths() if db_path is None else (Path(db_path), None)
        self.db_path = Path(d)
        self.audit_path = Path(audit_path) if audit_path else (a or self.db_path.with_suffix(".audit.jsonl"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fh = open(self.db_path.with_suffix(".lock"), "w")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_fh.close()
            raise StoreLockedError(f"profit guard store already open by another process: {self.db_path}") from exc
        try:
            self.conn = sqlite3.connect(self.db_path, isolation_level=None)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.executescript(_SCHEMA)
            self._migrate()
        except Exception:
            if getattr(self, "conn", None) is not None:
                self.conn.close()
            fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
            self._lock_fh.close()
            raise

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            try:
                fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
            finally:
                self._lock_fh.close()

    def __enter__(self) -> "GuardStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _migrate(self) -> None:
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
            return
        found = int(row[0])
        if found > SCHEMA_VERSION:
            raise RuntimeError(f"store schema {found} is newer than this code ({SCHEMA_VERSION}); refusing to start")
        # future migrations: if found < 2: ...

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # -- audit ---------------------------------------------------------------
    def audit(self, event: str, ts_ms: int, **data: Any) -> None:
        line = json.dumps({"ts_ms": ts_ms, "event": event, **data}, default=str, sort_keys=True)
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read_audit(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        return [json.loads(l) for l in self.audit_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    # -- plans ---------------------------------------------------------------
    @staticmethod
    def _write_plan(c: sqlite3.Connection, plan: PositionPlan, now_ms: int) -> None:
        c.execute(
            "INSERT INTO plans(id, strategy, symbol, mode, state, json, updated_ms) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET state=excluded.state, json=excluded.json, updated_ms=excluded.updated_ms",
            (plan.id, plan.strategy, plan.symbol, plan.mode, plan.state.value,
             json.dumps(plan.to_dict()), now_ms),
        )

    def save_plan(self, plan: PositionPlan, now_ms: int) -> None:
        with self._tx() as c:
            self._write_plan(c, plan, now_ms)

    # -- meta (small persistent counters, e.g. how far the entry inbox has been read) ----
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, value))

    def get_plan(self, plan_id: str) -> PositionPlan | None:
        row = self.conn.execute("SELECT json FROM plans WHERE id=?", (plan_id,)).fetchone()
        return PositionPlan.from_dict(json.loads(row[0])) if row else None

    def open_plans(self, mode: str | None = None) -> list[PositionPlan]:
        q, args = "SELECT json FROM plans WHERE state != ?", [State.EXITED.value]
        if mode:
            q += " AND mode = ?"
            args.append(mode)
        return [PositionPlan.from_dict(json.loads(r[0])) for r in self.conn.execute(q + " ORDER BY id", args)]

    def all_plans(self) -> list[PositionPlan]:
        return [PositionPlan.from_dict(json.loads(r[0])) for r in self.conn.execute("SELECT json FROM plans ORDER BY id")]

    # -- portfolio -----------------------------------------------------------
    def save_portfolio(self, st: PortfolioState, now_ms: int) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO portfolio(trade_date, scope, json, updated_ms) VALUES(?,?,?,?) "
                "ON CONFLICT(trade_date, scope) DO UPDATE SET json=excluded.json, updated_ms=excluded.updated_ms",
                (st.trade_date, st.scope, json.dumps(st.to_dict()), now_ms),
            )

    def get_portfolio(self, trade_date: str, scope: str = "account") -> PortfolioState | None:
        row = self.conn.execute(
            "SELECT json FROM portfolio WHERE trade_date=? AND scope=?", (trade_date, scope)
        ).fetchone()
        return PortfolioState.from_dict(json.loads(row[0])) if row else None

    def last_portfolio_before(self, trade_date: str, scope: str = "account") -> PortfolioState | None:
        row = self.conn.execute(
            "SELECT json FROM portfolio WHERE trade_date < ? AND scope=? ORDER BY trade_date DESC LIMIT 1",
            (trade_date, scope),
        ).fetchone()
        return PortfolioState.from_dict(json.loads(row[0])) if row else None

    # -- intents (write-ahead) -----------------------------------------------
    def record_intent(self, plan_id: str | None, kind: str, payload: dict[str, Any], now_ms: int,
                      plan: PositionPlan | None = None) -> int:
        """Write-ahead record of an order about to be sent. ``plan`` (the plan as the rules just left it) is saved in
        the same transaction, so a crash can never leave a plan marked exited with no exit order on record."""
        with self._tx() as c:
            if plan is not None:
                self._write_plan(c, plan, now_ms)
            cur = c.execute(
                "INSERT INTO intents(plan_id, kind, payload_json, status, created_ms, updated_ms) "
                "VALUES(?,?,?,?,?,?)",
                (plan_id, kind, json.dumps(payload, default=str), "pending", now_ms, now_ms),
            )
            intent_id = int(cur.lastrowid)
        self.audit("intent", now_ms, intent_id=intent_id, plan_id=plan_id, kind=kind, payload=payload)
        return intent_id

    def mark_intent(self, intent_id: int, status: str, now_ms: int, broker_order_id: str | None = None,
                    error: str | None = None) -> None:
        if status not in INTENT_STATUSES:
            raise ValueError(f"bad intent status {status}")
        with self._tx() as c:
            c.execute(
                "UPDATE intents SET status=?, broker_order_id=COALESCE(?, broker_order_id), "
                "attempts=attempts + (CASE WHEN ?='sent' THEN 1 ELSE 0 END), updated_ms=? WHERE id=?",
                (status, broker_order_id, status, now_ms, intent_id),
            )
        self.audit("intent_status", now_ms, intent_id=intent_id, status=status,
                   broker_order_id=broker_order_id, error=error)

    def intents(self, status: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT id, plan_id, kind, payload_json, status, broker_order_id, attempts, created_ms, updated_ms FROM intents", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        out = []
        for r in self.conn.execute(q + " ORDER BY id", args):
            out.append({"id": r[0], "plan_id": r[1], "kind": r[2], "payload": json.loads(r[3]), "status": r[4],
                        "broker_order_id": r[5], "attempts": r[6], "created_ms": r[7], "updated_ms": r[8]})
        return out

    def pending_intents(self) -> list[dict[str, Any]]:
        return [i for i in self.intents() if i["status"] in ("pending", "sent")]

    # -- exits ---------------------------------------------------------------
    def record_exit(self, plan_id: str, qty: int, reason: str, price: Any, broker_order_id: str | None,
                    ts_ms: int) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO exits(plan_id, qty, reason, price, broker_order_id, ts_ms) VALUES(?,?,?,?,?,?)",
                      (plan_id, qty, reason, None if price is None else str(price), broker_order_id, ts_ms))
        self.audit("exit", ts_ms, plan_id=plan_id, qty=qty, reason=reason, price=price,
                   broker_order_id=broker_order_id)

    def history(self, start_ms: int = 0, end_ms: int = 2**62) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT plan_id, qty, reason, price, broker_order_id, ts_ms FROM exits WHERE ts_ms BETWEEN ? AND ? "
            "ORDER BY ts_ms", (start_ms, end_ms))
        return [{"plan_id": r[0], "qty": r[1], "reason": r[2], "price": r[3], "broker_order_id": r[4],
                 "ts_ms": r[5]} for r in rows]
