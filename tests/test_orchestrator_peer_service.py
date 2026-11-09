from __future__ import annotations

import time

from orchestrator.identity import NodeIdentity
from orchestrator.nodes import local_node_record, node_id_for
from orchestrator.peer_service import PeerService
from orchestrator.replication import HttpTransport, Peer, PeerConfig
from orchestrator.records import canonical_json
from orchestrator.state import StateStore


class RoutedTransport(HttpTransport):
    def __init__(self, peers: dict[str, PeerService]):
        self.peers = peers
        self.calls: list[tuple[str, str]] = []

    def post(self, endpoint, path, body):
        self.calls.append((endpoint, path))
        peer = self.peers.get(endpoint)
        if peer is None:
            return None
        if path == "/cluster/ticket/announce":
            return peer.accept_ticket(body)
        if path == "/cluster/round/announce":
            return peer.accept_round(body)
        if path == "/cluster/submission/announce":
            return peer.accept_submission(body)
        if path == "/cluster/lease/ack":
            lease = body["lease"]
            if not peer.adopt_lease(dict(lease, signature=body["signature"]),
                                    body["pubkey"]):
                return {"ack": False}
            return {"ack": True, "node": peer.config.node_id,
                    "pubkey": peer.identity.public_key_hex,
                    "sig": peer.identity.sign(canonical_json(lease).encode())}
        if path == "/cluster/registry/announce":
            node = peer.accept_registry(body["record"], body["signature"])
            return {"ok": node is not None}
        return None


def _pair(tmp_path):
    a_id = NodeIdentity.generate()
    b_id = NodeIdentity.generate()
    a_store = StateStore(tmp_path / "a.sqlite3")
    b_store = StateStore(tmp_path / "b.sqlite3")
    a_node, b_node = node_id_for(a_id), node_id_for(b_id)
    b_peer = Peer(b_node, "http://b.test", b_id.public_key_hex)
    a_peer = Peer(a_node, "http://a.test", a_id.public_key_hex)
    b = PeerService(b_store, b_id, PeerConfig(b_node, "http://b.test", ()))
    transport = RoutedTransport({"http://b.test": b})
    a = PeerService(a_store, a_id, PeerConfig(a_node, "http://a.test", (b_peer,),
                                              quorum=2), transport=transport)
    return a, b, a_store, b_store


def test_ticket_and_task_replicate_and_duplicate_is_idempotent(tmp_path):
    a, b, a_store, b_store = _pair(tmp_path)
    task = {"taskId": "round_7_p1", "round": 7, "poolId": "p1",
            "targetSeed": 123, "targetLocalSteps": 500,
            "trainMode": "dense", "loraRank": 0, "trainedParams": ["base"]}
    ticket = a.admission.issue("ip:a", "round_7_p1", 7, pool_id="p1",
                               base_weights="step:6")
    assert a.announce_ticket(ticket, task) == 1
    assert b.admission.get("ip:a").ticket_id == ticket.ticket_id
    assert b.load_task("round_7_p1")["targetSeed"] == 123
    # B can use the A-issued ticket (the handoff path) and then replaying the
    # announcement must not create a second ticket or reset it.
    assert b.admission.consume("ip:a", "round_7_p1") is True
    # A new envelope with the same ticket must not create a second row.
    before = len(b_store.query("SELECT * FROM tickets"))
    a.announce_ticket(ticket, task)
    assert len(b_store.query("SELECT * FROM tickets")) == before
    a_store.close(); b_store.close()


def test_unknown_or_replayed_envelope_is_rejected(tmp_path):
    a, b, a_store, b_store = _pair(tmp_path)
    ticket = a.admission.issue("ip:a", "t1", 1)
    body = a.auth.wrap({"ticket": ticket.__dict__, "task": {}})
    first = b.accept_ticket(body)
    second = b.accept_ticket(body)
    assert first["ok"] is True and first["created"] is True
    assert second == {"ok": False, "reason": "stale_or_replayed"}
    stale = a.auth.wrap({"ticket": ticket.__dict__, "task": {}})
    stale["ts"] = time.time() - 3600
    assert b.accept_ticket(stale)["reason"] == "stale_or_replayed"
    a_store.close(); b_store.close()


def test_ticket_conflict_does_not_overwrite_local(tmp_path):
    a, b, a_store, b_store = _pair(tmp_path)
    local = b.admission.issue("ip:a", "local-task", 1)
    other = a.admission.issue("ip:a", "remote-task", 1)
    body = a.auth.wrap({"ticket": other.__dict__, "task": {}})
    result = b.accept_ticket(body)
    assert result["ok"] is False
    assert b.admission.get("ip:a").ticket_id == local.ticket_id
    a_store.close(); b_store.close()


def test_submission_hash_is_recorded_once(tmp_path):
    a, b, a_store, b_store = _pair(tmp_path)
    content = "a" * 64
    assert a.announce_submission(content, 3, "ip:a") == 1
    assert b.store.query_one("SELECT * FROM peer_submissions WHERE content_sha256=?",
                             (content,)) is not None
    before = len(b.store.query("SELECT * FROM peer_submissions"))
    assert a.announce_submission(content, 3, "ip:a") == 1
    assert len(b.store.query("SELECT * FROM peer_submissions")) == before
    a_store.close(); b_store.close()


def test_lease_quorum_uses_peer_ack(tmp_path):
    a, b, a_store, b_store = _pair(tmp_path)
    lease = a.acquire_lease(4)
    assert lease is not None and lease.leader == a.config.node_id
    assert b.current_lease(4).leader == a.config.node_id
    row = a.store.query_one("SELECT quorum, acks FROM leases WHERE round=4")
    assert row["quorum"] == 2 and "node-" in row["acks"]
    a_store.close(); b_store.close()


def test_registry_announcement_reaches_peer(tmp_path):
    a, b, a_store, b_store = _pair(tmp_path)
    record = local_node_record(a.identity, endpoints=["http://a.test"],
                               roles=["leader"], software_version="3.2",
                               started_at="2026-11-09T00:00:00Z")
    assert a.publish_self(record) is True
    assert a.config.node_id in [n.record["node_id"] for n in b.registry.active()]
    a_store.close(); b_store.close()
