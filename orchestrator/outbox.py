"""Durable peer-announcement outbox with retry and restart safety.

Announcements are written to SQLite before any network call. A worker drains
due rows in id order, marks success, and reschedules failures with exponential
backoff. Restarting the process only resumes undelivered rows.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .state import StateStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS peer_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    next_attempt REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at REAL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON peer_outbox(delivered_at, next_attempt);
"""


@dataclass(frozen=True)
class OutboxRow:
    id: int
    kind: str
    payload: dict
    attempts: int
    next_attempt: float
    last_error: Optional[str]


class Outbox:
    def __init__(self, store: StateStore, *, max_backoff_s: float = 300.0):
        self.store = store
        self.max_backoff_s = float(max_backoff_s)
        # executescript commits implicitly, which conflicts with the store's
        # explicit transactions; run the DDL under the store lock instead.
        with self.store._lock:
            self.store._conn.executescript(SCHEMA)

    def enqueue(self, kind: str, payload: dict) -> int:
        now = time.time()
        with self.store.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO peer_outbox(kind,payload,created_at,next_attempt) VALUES(?,?,?,?)",
                (str(kind), json.dumps(payload, sort_keys=True, separators=(",", ":")), now, now),
            )
            return int(cur.lastrowid)

    def due(self, *, now: Optional[float] = None, limit: int = 100) -> list[OutboxRow]:
        at = time.time() if now is None else now
        rows = self.store.query(
            "SELECT * FROM peer_outbox WHERE delivered_at IS NULL AND next_attempt<=? "
            "ORDER BY id LIMIT ?", (at, int(limit)))
        return [OutboxRow(r["id"], r["kind"], json.loads(r["payload"] or "{}"),
                          int(r["attempts"]), float(r["next_attempt"]), r["last_error"])
                for r in rows]

    def mark_delivered(self, row_id: int, *, now: Optional[float] = None) -> None:
        with self.store.transaction() as conn:
            conn.execute("UPDATE peer_outbox SET delivered_at=?, last_error=NULL WHERE id=?",
                         (time.time() if now is None else now, int(row_id)))

    def mark_failed(self, row_id: int, error: str, *, now: Optional[float] = None) -> float:
        at = time.time() if now is None else now
        row = self.store.query_one("SELECT attempts FROM peer_outbox WHERE id=?", (int(row_id),))
        attempts = int(row["attempts"]) + 1 if row else 1
        delay = min(self.max_backoff_s, 2.0 ** min(attempts, 8))
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE peer_outbox SET attempts=?, next_attempt=?, last_error=? WHERE id=?",
                (attempts, at + delay, str(error)[:500], int(row_id)))
        return at + delay

    def drain(self, send: Callable[[str, dict], bool], *, now: Optional[float] = None,
              limit: int = 100) -> dict:
        delivered = 0
        retried = 0
        for row in self.due(now=now, limit=limit):
            try:
                ok = bool(send(row.kind, row.payload))
                err = "" if ok else "peer rejected announcement"
            except Exception as exc:  # network or peer error: retry later
                ok, err = False, f"{type(exc).__name__}: {exc}"
            if ok:
                self.mark_delivered(row.id, now=now)
                delivered += 1
            else:
                self.mark_failed(row.id, err, now=now)
                retried += 1
        return {"delivered": delivered, "retried": retried}

    def pending(self) -> int:
        row = self.store.query_one(
            "SELECT COUNT(*) AS n FROM peer_outbox WHERE delivered_at IS NULL")
        return int(row["n"]) if row else 0

    def purge_delivered(self, *, older_than_s: float = 7 * 86400) -> int:
        cutoff = time.time() - float(older_than_s)
        with self.store.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM peer_outbox WHERE delivered_at IS NOT NULL AND delivered_at<?",
                (cutoff,))
            return int(cur.rowcount)


__all__ = ["Outbox", "OutboxRow"]
