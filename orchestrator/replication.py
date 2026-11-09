"""Cross-node replication for leases and the signed node registry.

The single-node orchestrator keeps its state in SQLite. A real quorum needs the
same decisions to be visible on every node, so this module adds a small,
explicit replication layer:

* a lease is written locally and is valid only after a majority of the static
  peer set acknowledges the same ``(round, leader, term, expires_at)`` payload;
* a node record is written locally and announced to peers, each of which
  verifies the record signature before storing it.

Transport is a tiny HTTP JSON protocol. ``sim_node.py`` exposes it for the
on-machine cluster drills; a production deployment can replace the peer client
with retrying/authenticated transport without changing the quorum rules.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .cluster import Lease, LeaseManager
from .identity import NodeIdentity
from .nodes import Node, NodeRegistry
from .records import canonical_json, verify_client_receipt
from .state import StateStore

LEASE_SCHEMA = "ng-lease-1"
ACK_SCHEMA = "ng-lease-ack-1"


@dataclass(frozen=True)
class Peer:
    node_id: str
    endpoint: str
    pubkey: str


@dataclass(frozen=True)
class PeerConfig:
    node_id: str
    endpoint: str
    peers: tuple[Peer, ...] = ()
    quorum: Optional[int] = None

    @property
    def required(self) -> int:
        if self.quorum is not None:
            return max(1, int(self.quorum))
        return (len(self.peers) + 1) // 2 + 1

    def peer(self, node_id: str) -> Optional[Peer]:
        return next((p for p in self.peers if p.node_id == node_id), None)


class HttpTransport:
    """POST one JSON object to a peer and return its JSON reply."""

    def __init__(self, timeout_s: float = 2.0):
        self.timeout_s = float(timeout_s)

    def post(self, endpoint: str, path: str, body: dict) -> Optional[dict]:
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(endpoint.rstrip("/") + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None

    def get(self, endpoint: str, path: str) -> Optional[dict]:
        try:
            with urllib.request.urlopen(endpoint.rstrip("/") + path,
                                        timeout=self.timeout_s) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None


def lease_payload(round_no: int, leader: str, term: int, expires_at: float) -> dict:
    return {"schema": LEASE_SCHEMA, "round": int(round_no), "leader": leader,
            "term": int(term), "expiresAt": round(float(expires_at), 6)}


def sign_lease(identity: NodeIdentity, payload: dict) -> str:
    return identity.sign(canonical_json(payload).encode("utf-8"))


class ReplicatedLease:
    """Lease acquisition that requires a majority acknowledgement."""

    def __init__(self, store: StateStore, identity: NodeIdentity, config: PeerConfig,
                 transport: Optional[HttpTransport] = None, ttl_s: float = 60.0):
        self.store = store
        self.identity = identity
        self.config = config
        self.transport = transport or HttpTransport()
        self.local = LeaseManager(store, ttl_s=ttl_s)
        self.ttl_s = float(ttl_s)

    def acquire(self, round_no: int, *, term: int = 1) -> Optional[Lease]:
        lease = self.local.acquire(round_no, self.config.node_id, term=term)
        if lease is None:
            return None
        if self.config.required <= 1:
            self._save_quorum(lease, 1, [])
            return lease
        payload = lease_payload(lease.round, lease.leader, lease.term, lease.expires_at)
        sig = sign_lease(self.identity, payload)
        acks = []
        for peer in self.config.peers:
            reply = self.transport.post(peer.endpoint, "/sim/lease/ack", {
                "lease": payload, "signature": sig,
                "pubkey": self.identity.public_key_hex,
                "requester": self.config.node_id,
            })
            if (reply and reply.get("ack") and reply.get("node") == peer.node_id
                    and verify_client_receipt(payload, peer.pubkey,
                                              reply.get("sig", ""))):
                acks.append(reply)
        if 1 + len(acks) < self.config.required:
            self.local.release(round_no)
            return None
        self._save_quorum(lease, 1 + len(acks), [a.get("node") for a in acks])
        return lease

    def _save_quorum(self, lease: Lease, acks: int, nodes: list[str]) -> None:
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE leases SET quorum=?, acks=? WHERE round=?",
                (int(acks), json.dumps(sorted(nodes)), int(lease.round)))

    def current(self, round_no: int) -> Optional[Lease]:
        return self.local.current(round_no)

    def adopt(self, lease: dict, *, requester_pubkey: str) -> bool:
        """Accept a leader's lease after verifying the leader's signature."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        payload = {k: lease[k] for k in ("schema", "round", "leader", "term", "expiresAt")}
        try:
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(requester_pubkey))
            pub.verify(bytes.fromhex(lease.get("signature", "")),
                       canonical_json(payload).encode("utf-8"))
        except Exception:
            return False
        existing = self.local.current(int(payload["round"]))
        if existing and existing.leader != payload["leader"]:
            return False
        self.local.adopt(Lease(int(payload["round"]), payload["leader"],
                               int(payload["term"]), float(payload["expiresAt"])))
        return True


class ReplicatedRegistry:
    """Signed node registration announced to peers."""

    def __init__(self, store: StateStore, identity: NodeIdentity, config: PeerConfig,
                 transport: Optional[HttpTransport] = None):
        self.store = store
        self.identity = identity
        self.config = config
        self.transport = transport or HttpTransport()
        self.registry = NodeRegistry(store)

    def publish(self, record: dict, signature: str) -> Optional[Node]:
        node = self.registry.register_signed(record, signature)
        if node is None:
            return None
        for peer in self.config.peers:
            self.transport.post(peer.endpoint, "/sim/registry/announce", {
                "record": record, "signature": signature})
        return node

    def accept(self, record: dict, signature: str) -> Optional[Node]:
        return self.registry.register_signed(record, signature)

    def active(self) -> list[Node]:
        return self.registry.active(signed_only=True)

    def seen_ids(self) -> list[str]:
        return sorted(n.record.get("node_id", "") for n in self.active())


def peers_as_dict(peers: list[Peer]) -> list[dict]:
    return [{"node_id": p.node_id, "endpoint": p.endpoint, "pubkey": p.pubkey}
            for p in peers]


__all__ = [
    "ACK_SCHEMA", "LEASE_SCHEMA", "HttpTransport", "Peer", "PeerConfig",
    "ReplicatedLease", "ReplicatedRegistry", "lease_payload", "peers_as_dict",
    "sign_lease",
]
