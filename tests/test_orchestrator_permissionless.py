import time

from orchestrator.admission_policy import AdmissionPolicy
from orchestrator.identity import NodeIdentity
from orchestrator.nodes import NodeRegistry, local_node_record
from orchestrator.reputation import ReputationService
from orchestrator.state import StateStore


def _node(reg, key):
    rec = local_node_record(key, endpoints=["https://n.example"], roles=["leader"],
                            software_version="3.2", started_at="2026-11-09T00:00:00Z")
    return reg.register(rec)


def test_policy_switch_without_deploy(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    reg = NodeRegistry(store); rep = ReputationService(store)
    node = _node(reg, NodeIdentity.generate())
    assert AdmissionPolicy("open").allowed(node, rep)[0] is True
    ok, why = AdmissionPolicy("bonded", bond_rounds=3).allowed(node, rep)
    assert ok is False and why == "bond_not_satisfied"
    for _ in range(3):
        rep.record_round(node.node_id, certified=True)
    assert AdmissionPolicy("bonded", bond_rounds=3).allowed(node, rep)[0] is True
    assert AdmissionPolicy("vetted").allowed(node, rep)[0] is False
    assert AdmissionPolicy("vetted").allowed(node, rep, vouched=True)[0] is True
    store.close()


def test_incompatible_version_rejected(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    reg = NodeRegistry(store); rep = ReputationService(store)
    rec = local_node_record(NodeIdentity.generate(), endpoints=["https://n.example"],
                            roles=["leader"], software_version="2.9",
                            started_at="2026-11-09T00:00:00Z")
    node = reg.register(rec)
    ok, why = AdmissionPolicy("open", min_version="3.2").allowed(node, rep)
    assert ok is False and why == "incompatible_version"
    store.close()


def test_bond_slash_on_fraud(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    reg = NodeRegistry(store); rep = ReputationService(store)
    node = _node(reg, NodeIdentity.generate())
    rep.record_round(node.node_id, certified=True)
    assert rep.get(node.node_id).score > 0
    slashed = rep.slash(node, "forged loss receipt")
    assert slashed.score == 0 and slashed.audits_failed == 1
    assert reg.resolve(node.node_id, node.pubkey) is None or \
        reg.resolve(node.node_id, node.pubkey).status == "revoked"
    store.close()
