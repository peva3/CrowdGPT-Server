from __future__ import annotations

import time

import pytest

from orchestrator.cluster import Lease
from orchestrator.identity import NodeIdentity
from orchestrator.nodes import local_node_record, node_id_for
from orchestrator.records import canonical_json
from orchestrator.replication import (HttpTransport, Peer, PeerConfig, ReplicatedLease,
                                      ReplicatedRegistry)
from orchestrator.state import StateStore


class FakeTransport(HttpTransport):
    """Routes replication calls to in-process peer objects."""

    def __init__(self, peers: dict[str, object]):
        self.peers = peers
        self.calls = []

    def post(self, endpoint, path, body):
        self.calls.append((endpoint, path))
        peer = self.peers.get(endpoint)
        if peer is None:
            return None
        if path == "/sim/lease/ack":
            return peer.ack(body)
        if path == "/sim/registry/announce":
            node = peer.registry.accept(body["record"], body["signature"])
            return {"ok": bool(node)} if node else None
        return None


class Harness:
    def __init__(self):
        self.identity = NodeIdentity.generate()
        self.node_id = node_id_for(self.identity)
        self.store = StateStore(":memory:")
        self.lease = ReplicatedLease(self.store, self.identity,
                                     PeerConfig(self.node_id, "http://self", ()), ttl_s=30)
        self.registry = ReplicatedRegistry(self.store, self.identity,
                                           PeerConfig(self.node_id, "http://self", ()))

    def ack(self, body):
        lease = body["lease"]
        if not self.lease.adopt(dict(lease, signature=body["signature"]),
                                requester_pubkey=body["pubkey"]):
            return {"ack": False}
        sig = self.identity.sign(canonical_json(lease).encode())
        return {"ack": True, "node": self.node_id,
                "pubkey": self.identity.public_key_hex, "sig": sig}


def _cluster(quorum, peers_count):
    leader = Harness()
    peers = [Harness() for _ in range(peers_count)]
    peer_objs = [Peer(p.node_id, f"http://{p.node_id}.test",
                      p.identity.public_key_hex) for p in peers]
    transport = FakeTransport({p.endpoint: h for p, h in zip(peer_objs, peers)})
    leader.lease = ReplicatedLease(leader.store, leader.identity,
                                   PeerConfig(leader.node_id, "http://leader",
                                              tuple(peer_objs), quorum=quorum),
                                   transport=transport, ttl_s=30)
    leader.registry = ReplicatedRegistry(leader.store, leader.identity,
                                         leader.lease.config, transport=transport)
    return leader, peers, peer_objs


def test_lease_requires_majority_acks():
    leader, peers, _ = _cluster(quorum=2, peers_count=2)
    lease = leader.lease.acquire(1)
    assert lease is not None and lease.leader == leader.lease.config.node_id
    assert peers[0].lease.current(1).leader == leader.lease.config.node_id
    row = leader.store.query_one("SELECT quorum, acks FROM leases WHERE round=1")
    assert row["quorum"] == 3


def test_lease_fails_and_is_released_without_majority():
    leader, peers, peer_objs = _cluster(quorum=3, peers_count=2)
    # Both peers ack, so 3 total -> succeeds; now make one peer unreachable.
    lease = leader.lease.acquire(2)
    assert lease is not None
    leader.lease.local.release(2)
    leader.lease.transport.peers.pop(peer_objs[1].endpoint, None)
    assert leader.lease.acquire(2) is None
    assert leader.lease.current(2) is None


def test_conflicting_live_lease_is_rejected():
    a, _, _ = _cluster(quorum=1, peers_count=0)
    b, _, _ = _cluster(quorum=1, peers_count=0)
    a.lease.local.adopt(Lease(1, a.node_id, 1, time.time() + 60))
    body = {"schema": "ng-lease-1", "round": 1, "leader": b.node_id,
            "term": 1, "expiresAt": time.time() + 60}
    sig = b.identity.sign(canonical_json(body).encode())
    assert a.lease.adopt(dict(body, signature=sig),
                         requester_pubkey=b.identity.public_key_hex) is False


def test_registry_announces_signed_record():
    leader, peers, peer_objs = _cluster(quorum=1, peers_count=2)
    record = local_node_record(leader.identity, endpoints=["http://leader"],
                               roles=["leader"], software_version="sim-1",
                               started_at="2026-11-09T00:00:00Z")
    sig = leader.identity.sign(canonical_json(record).encode())
    node = leader.registry.publish(record, sig)
    assert node is not None
    assert all(leader.node_id in p.registry.seen_ids() for p in peers)
    assert leader.registry.seen_ids() == [leader.node_id]


def test_registry_rejects_bad_signature():
    leader, _, _ = _cluster(quorum=1, peers_count=0)
    record = local_node_record(leader.identity, endpoints=["http://leader"],
                               roles=["leader"], software_version="sim-1",
                               started_at="2026-11-09T00:00:00Z")
    assert leader.registry.publish(record, "0" * 128) is None
