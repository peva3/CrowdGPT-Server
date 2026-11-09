"""Compute bonds and reputation for permissionless orchestrators."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .nodes import Node, NodeRegistry
from .state import StateStore


@dataclass(frozen=True)
class Reputation:
    node_id: str
    certified_rounds: int
    uptime_s: float
    audits_passed: int
    audits_failed: int
    score: float


class ReputationService:
    def __init__(self, store: StateStore):
        self.store = store

    def get(self, node_id: str) -> Reputation:
        row = self.store.query_one("SELECT * FROM node_reputation WHERE node_id=?", (node_id,))
        if not row:
            return Reputation(node_id, 0, 0.0, 0, 0, 0.0)
        return Reputation(node_id, row["certified_rounds"], row["uptime_s"],
                          row["audits_passed"], row["audits_failed"], row["score"])

    def record_round(self, node_id: str, *, certified: bool, uptime_s: float = 0.0,
                     audit_ok: Optional[bool] = None) -> Reputation:
        cur = self.get(node_id)
        score = cur.score
        if certified: score += 1.0
        if audit_ok is True: score += 0.5
        if audit_ok is False: score -= 5.0
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO node_reputation(node_id,certified_rounds,uptime_s,audits_passed,"
                "audits_failed,score,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET certified_rounds=excluded.certified_rounds,"
                "uptime_s=excluded.uptime_s,audits_passed=excluded.audits_passed,"
                "audits_failed=excluded.audits_failed,score=excluded.score,"
                "updated_at=excluded.updated_at",
                (node_id, cur.certified_rounds + (1 if certified else 0),
                 cur.uptime_s + float(uptime_s),
                 cur.audits_passed + (1 if audit_ok is True else 0),
                 cur.audits_failed + (1 if audit_ok is False else 0),
                 score, time.time()))
        return self.get(node_id)

    def bond_satisfied(self, node: Node, *, required_rounds: int) -> bool:
        rep = self.get(node.node_id)
        return rep.certified_rounds >= int(required_rounds) and node.record.get("bond_rounds", 0) >= 0

    def slash(self, node: Node, reason: str) -> Reputation:
        now = time.time()
        with self.store.transaction() as conn:
            conn.execute("UPDATE nodes SET status='revoked', updated_at=? WHERE node_id=? AND pubkey=?",
                         (now, node.node_id, node.pubkey))
            conn.execute(
                "INSERT INTO node_reputation(node_id,certified_rounds,uptime_s,audits_passed,"
                "audits_failed,score,updated_at) VALUES(?,0,0,0,1,0,?) "
                "ON CONFLICT(node_id) DO UPDATE SET score=0, audits_failed=audits_failed+1, "
                "updated_at=excluded.updated_at", (node.node_id, now))
        return self.get(node.node_id)


__all__ = ["Reputation", "ReputationService"]
