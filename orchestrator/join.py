"""Signed join tokens for adding an orchestrator to a static peer set.

A token is self-describing but not self-trusting: the joining operator must
supply the issuer's expected public key out of band.  The token never carries a
private key.
"""
from __future__ import annotations

import time
import uuid
from typing import Iterable, Optional

from .identity import NodeIdentity
from .records import canonical_json
from .replication import Peer

SCHEMA = "ng-join-1"


def create_invite(identity: NodeIdentity, *, node_id: str, endpoint: str,
                  roles: Iterable[str] = ("leader", "witness", "validator"),
                  ttl_s: float = 3600.0, network: str = "ng-1",
                  at: Optional[float] = None) -> dict:
    now = time.time() if at is None else float(at)
    body = {
        "schema": SCHEMA, "network": str(network), "nodeId": str(node_id),
        "endpoint": str(endpoint).rstrip("/"), "pubkey": identity.public_key_hex,
        "roles": list(roles), "issuedAt": now,
        "expiresAt": now + max(30.0, float(ttl_s)),
        "nonce": uuid.uuid4().hex,
    }
    body["signature"] = identity.sign(canonical_json(body).encode("utf-8"))
    return body


def verify_invite(token: dict, *, trusted_pubkeys: Iterable[str],
                  at: Optional[float] = None) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    now = time.time() if at is None else float(at)
    body = {k: v for k, v in token.items() if k != "signature"}
    if body.get("schema") != SCHEMA:
        raise ValueError("unsupported join token schema")
    pub = str(body.get("pubkey") or "").lower()
    trusted = {str(k).lower() for k in trusted_pubkeys}
    if not trusted:
        raise ValueError("at least one trusted public key is required")
    if pub not in trusted:
        raise ValueError("join token issuer is not trusted")
    if float(body.get("expiresAt") or 0) < now:
        raise ValueError("join token has expired")
    if not body.get("endpoint") or not body.get("nodeId"):
        raise ValueError("join token is missing endpoint or node id")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
            bytes.fromhex(str(token.get("signature") or "")),
            canonical_json(body).encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"join token signature invalid: {exc}") from exc
    return body


def invite_peer(token: dict) -> Peer:
    return Peer(str(token["nodeId"]), str(token["endpoint"]), str(token["pubkey"]))


__all__ = ["SCHEMA", "create_invite", "invite_peer", "verify_invite"]
