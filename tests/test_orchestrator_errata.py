from orchestrator.errata import (build_errata, errata_id, sign_errata,
                                 verify_errata)
from orchestrator.identity import NodeIdentity
from orchestrator.service import Orchestrator


def _build(key, node="node-test"):
    return build_errata(
        round_no=337, original_record_hash="a" * 64,
        reason="reported loss was not from the fixed held-out set",
        replacement_record_hash="b" * 64, issuer=node)


def test_signed_errata_verifies_and_tamper_fails():
    key = NodeIdentity.generate()
    rec = sign_errata(_build(key), key, "node-test")
    assert verify_errata(rec) is True
    assert verify_errata(rec, expected_pubkey=key.public_key_hex) is True
    bad = dict(rec); bad["reason"] = "a different reason"
    assert verify_errata(bad) is False
    wrong = NodeIdentity.generate()
    assert verify_errata(rec, expected_pubkey=wrong.public_key_hex) is False
    assert len(errata_id(rec)) == 64


def test_errata_is_stored_without_touching_original(tmp_path):
    orch = Orchestrator.open(tmp_path / "state.sqlite3", identity_path=tmp_path / "node.pem")
    try:
        rec = sign_errata(_build(orch.identity), orch.identity, "node-test")
        stored = orch.errata.record(rec)
        assert stored["errata_id"] == errata_id(rec)
        assert orch.errata.for_round(337)[0]["replacement_record_hash"] == "b" * 64
        assert orch.errata.get(stored["errata_id"])["original_record_hash"] == "a" * 64
        # record is idempotent
        orch.errata.record(rec)
        assert len(orch.errata.all()) == 1
    finally:
        orch.close()


def test_unsigned_errata_is_rejected(tmp_path):
    orch = Orchestrator.open(tmp_path / "state.sqlite3", identity_path=tmp_path / "node.pem")
    try:
        rec = _build(orch.identity)
        try:
            orch.errata.record(rec)
        except ValueError as exc:
            assert "invalid errata" in str(exc)
        else:
            raise AssertionError("unsigned errata accepted")
        assert orch.errata.all() == []
    finally:
        orch.close()
