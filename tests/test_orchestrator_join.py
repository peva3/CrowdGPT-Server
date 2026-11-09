import time

import pytest

from orchestrator.identity import NodeIdentity
from orchestrator.join import create_invite, invite_peer, verify_invite
from orchestrator.nodes import node_id_for


def test_invite_verifies_and_exports_peer():
    identity = NodeIdentity.generate()
    token = create_invite(identity, node_id=node_id_for(identity),
                          endpoint="https://node.example/", roles=["leader"],
                          ttl_s=60)
    body = verify_invite(token, trusted_pubkeys=[identity.public_key_hex])
    assert body["endpoint"] == "https://node.example"
    assert invite_peer(body).pubkey == identity.public_key_hex


def test_invite_requires_trust_and_rejects_tamper():
    identity = NodeIdentity.generate()
    token = create_invite(identity, node_id="node-a", endpoint="https://a.example")
    with pytest.raises(ValueError):
        verify_invite(token, trusted_pubkeys=[])
    with pytest.raises(ValueError):
        verify_invite(token, trusted_pubkeys=["0" * 64])
    token["endpoint"] = "https://evil.example"
    with pytest.raises(ValueError):
        verify_invite(token, trusted_pubkeys=[identity.public_key_hex])


def test_expired_invite_is_rejected():
    identity = NodeIdentity.generate()
    token = create_invite(identity, node_id="node-a", endpoint="https://a.example",
                          ttl_s=30, at=time.time() - 3600)
    with pytest.raises(ValueError):
        verify_invite(token, trusted_pubkeys=[identity.public_key_hex])
