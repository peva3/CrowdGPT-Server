"""Registry health, lease election, and witness quorum."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .identity import NodeIdentity
from .nodes import Node, NodeRegistry
from .records import canonical_json, sign_record, verify_certificate, verify_client_receipt
from .state import StateStore


@dataclass(frozen=True)
class Lease:
    round: int
    leader: str
    term: int
    expires_at: float


class ClusterView:
    def __init__(self, store: StateStore):
        self.store = store

    def heartbeat(self, node_id: str, *, generation: int = 0,
                  endpoint: Optional[str] = None, at: Optional[float] = None) -> None:
        now = time.time() if at is None else at
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO node_health(node_id,generation,last_seen,endpoint) VALUES(?,?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET generation=excluded.generation, "
                "last_seen=excluded.last_seen, endpoint=excluded.endpoint",
                (node_id, int(generation), now, endpoint))

    def healthy(self, *, timeout_s: float = 90.0, at: Optional[float] = None) -> list[str]:
        now = time.time() if at is None else at
        rows = self.store.query(
            "SELECT node_id FROM node_health WHERE last_seen>=? ORDER BY node_id",
            (now - float(timeout_s),))
        return [r["node_id"] for r in rows]

    def merge(self, view: dict[str, float]) -> None:
        for node_id, seen in view.items():
            self.heartbeat(node_id, at=float(seen))

    def gossip_sample(self, k: int = 2, *, seed: int = 0) -> list[str]:
        nodes = self.healthy()
        if not nodes: return []
        k = min(int(k), len(nodes))
        start = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16) % len(nodes)
        return [nodes[(start + i) % len(nodes)] for i in range(k)]


class LeaseManager:
    def __init__(self, store: StateStore, ttl_s: float = 60.0):
        self.store = store
        self.ttl_s = float(ttl_s)

    @staticmethod
    def leader_for(round_no: int, prev_cert: str, healthy: Sequence[str]) -> Optional[str]:
        nodes = sorted(set(healthy))
        if not nodes: return None
        digest = hashlib.sha256(f"{prev_cert}:{round_no}".encode()).hexdigest()
        return nodes[int(digest, 16) % len(nodes)]

    def acquire(self, round_no: int, node_id: str, *, term: int = 1,
                at: Optional[float] = None) -> Optional[Lease]:
        now = time.time() if at is None else at
        with self.store.transaction() as conn:
            cur = conn.execute("SELECT * FROM leases WHERE round=?", (int(round_no),)).fetchone()
            if cur and cur["expires_at"] > now and cur["leader"] != node_id:
                return None
            conn.execute(
                "INSERT INTO leases(round,leader,term,expires_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(round) DO UPDATE SET leader=excluded.leader, term=excluded.term, "
                "expires_at=excluded.expires_at, updated_at=excluded.updated_at",
                (int(round_no), node_id, int(term), now + self.ttl_s, now))
        return Lease(int(round_no), node_id, int(term), now + self.ttl_s)

    def adopt(self, lease: Lease, at: Optional[float] = None) -> Lease:
        """Store a leader's lease locally (used by a peer that acknowledged it)."""
        now = time.time() if at is None else at
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO leases(round,leader,term,expires_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(round) DO UPDATE SET leader=excluded.leader, term=excluded.term, "
                "expires_at=excluded.expires_at, updated_at=excluded.updated_at",
                (int(lease.round), lease.leader, int(lease.term),
                 float(lease.expires_at), now))
        return lease

    def current(self, round_no: int, at: Optional[float] = None) -> Optional[Lease]:
        now = time.time() if at is None else at
        row = self.store.query_one("SELECT * FROM leases WHERE round=?", (int(round_no),))
        if not row or row["expires_at"] < now: return None
        return Lease(row["round"], row["leader"], row["term"], row["expires_at"])

    def release(self, round_no: int) -> None:
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM leases WHERE round=?", (int(round_no),))


def certify(record: dict, signatures: Sequence[dict], *, required: int,
            allowed: Optional[dict[str, str]] = None) -> dict:
    """Attach certificates only when at least ``required`` distinct nodes verify."""
    good = []
    seen = set()
    for cert in signatures:
        node = cert.get("node")
        if not node or node in seen: continue
        if allowed is not None and allowed.get(node) != cert.get("pubkey"): continue
        if verify_certificate(record, cert):
            good.append(cert); seen.add(node)
    if len(good) < int(required):
        raise ValueError(f"quorum not met: {len(good)}/{required}")
    body = dict(record)
    body["certificate"] = good
    return body


__all__ = ["ClusterView", "Lease", "LeaseManager", "certify"]
