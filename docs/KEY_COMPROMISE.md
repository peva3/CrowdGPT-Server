# Key-compromise runbook

## Node key

1. Stop the node.
2. Start a new node with a fresh `orchestrator_node_key.pem`.
3. Register the new `NodeRecord`.
4. Revoke the old key:
   `POST /api/admin/node/revoke?secret=...` with `{node_id, pubkey, reason}`.
5. Re-issue the record chain: old rounds stay valid but are marked as signed by
   the revoked key; new rounds use the new key.

Rotation without compromise uses `/api/admin/node/rotate` with an overlap
window. Do not reuse an old key after expiry.

## Client key

The client key is only a receipt signer. Compromise lets an attacker sign
receipts for that device, not forge another client. Remove the device key from
the client (`checkpoints/client_key.pem`) and let it generate a new one; old
receipts remain attributable to the old key.

## Admin secret

Rotate `CROWDGPT_ADMIN_SECRET`, restart, and audit `/api/admin/*` calls in the
public event log.
