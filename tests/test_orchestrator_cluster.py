import time

import pytest

from orchestrator.cluster import ClusterView, LeaseManager, certify
from orchestrator.identity import NodeIdentity
from orchestrator.nodes import local_node_record, node_id_for
from orchestrator.records import build_round_record, sign_record
from orchestrator.state import StateStore


def _record():
    return build_round_record(
        round_no=3, opened_at="2026-11-09T00:00:00Z", closed_at="2026-11-09T01:00:00Z",
        base_weights="a" * 64, aggregate_weights="b" * 64, leader="node-aaaaaaaaaaaaaaaa",
        witnesses=(), tickets_root="1" * 64, submissions_root="2" * 64,
        deltas_root="3" * 64, prev_record_hash=None, eval=None,
        bytes_down=1, bytes_up=2, code_versions=("3.2",))


def test_five_node_gossip_converges(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    a, b = ClusterView(store), ClusterView(store)
    now = time.time()
    for i in range(5):
        a.heartbeat(f"n{i}", generation=1, at=now)
    # bounded fan-out: each node hears 2 peers, two rounds reach all 5
    for seed in range(2):
        for node in a.gossip_sample(2, seed=seed):
            b.heartbeat(node, at=now)
    assert b.healthy(at=now + 1) == ["n0", "n1", "n2", "n3", "n4"]
    store.close()


def test_stale_node_expires(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3"); view = ClusterView(store)
    now = time.time(); view.heartbeat("old", at=now - 1000); view.heartbeat("new", at=now)
    assert view.healthy(timeout_s=60, at=now) == ["new"]
    store.close()


def test_leader_election_changes_after_crash():
    healthy = ["n0", "n1", "n2"]
    leader = LeaseManager.leader_for(10, "a" * 64, healthy)
    assert leader in healthy
    assert LeaseManager.leader_for(10, "a" * 64, [n for n in healthy if n != leader]) != leader


def test_lease_acquire_expire_and_takeover(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    lm = LeaseManager(store, ttl_s=2)
    now = time.time()
    assert lm.acquire(4, "n0", at=now).leader == "n0"
    assert lm.acquire(4, "n1", at=now + 1) is None
    assert lm.current(4, at=now + 1).leader == "n0"
    assert lm.current(4, at=now + 3) is None
    assert lm.acquire(4, "n1", at=now + 3).leader == "n1"
    store.close()


def test_two_two_partition_has_no_certificate():
    rec = _record()
    keys = [NodeIdentity.generate() for _ in range(2)]
    certs = [sign_record(rec, k, f"node-{i}")["certificate"][0] for i, k in enumerate(keys)]
    with pytest.raises(ValueError):
        certify(rec, certs, required=3)
    assert len(certify(rec, certs, required=2)["certificate"]) == 2


def test_bad_witness_signature_is_ignored():
    rec = _record(); keys = [NodeIdentity.generate() for _ in range(3)]
    certs = [sign_record(rec, keys[0], "node-0")["certificate"][0],
             sign_record(rec, keys[1], "node-1")["certificate"][0]]
    certs[1] = dict(certs[1]); certs[1]["sig"] = "0" * 128
    with pytest.raises(ValueError):
        certify(rec, certs, required=2)
    assert len(certify(rec, certs, required=1)["certificate"]) == 1


def test_signed_node_registry_ignores_bad_signature(tmp_path):
    from orchestrator.nodes import NodeRegistry, local_node_record
    from orchestrator.records import canonical_json
    store = StateStore(tmp_path / "state.sqlite3"); reg = NodeRegistry(store)
    key = NodeIdentity.generate(); rec = local_node_record(
        key, endpoints=["https://n.example"], roles=["leader"], software_version="3.2",
        started_at="2026-11-09T00:00:00Z")
    sig = key.sign(canonical_json(rec).encode())
    assert reg.register_signed(rec, sig) is not None
    assert [n.pubkey for n in reg.active(signed_only=True)] == [key.public_key_hex]
    bad = dict(rec); bad["node_id"] = "node-ffffffffffffffff"
    assert reg.register_signed(bad, sig) is None
    store.close()
