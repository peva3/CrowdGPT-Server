"""Production peer service: leases, registry, tickets, rounds, submissions.

The transport is static-peer HTTP JSON.  Every peer request carries a timestamp
and a nonce; the receiver rejects stale requests and replayed nonces.  Signed
tickets are imported only after their signature verifies, and an existing
ticket for the same client is never overwritten.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict
from typing import Optional

from .admission import AdmissionService
from .domain import TicketView
from .identity import NodeIdentity
from .outbox import Outbox
from .records import (canonical_json, sign_record, signing_payload,
                      verify_certificate)
from .cluster import certify
from .replication import (HttpTransport, Peer, PeerConfig, ReplicatedLease,
                          ReplicatedRegistry)
from .state import StateStore

ENVELOPE_SCHEMA = "ng-peer-envelope-1"
MAX_SKEW_S = 120.0


class PeerAuth:
    """Timestamp + nonce replay protection for peer HTTP requests."""

    def __init__(self, store: StateStore, max_skew_s: float = MAX_SKEW_S):
        self.store = store
        self.max_skew_s = float(max_skew_s)

    def wrap(self, body: dict) -> dict:
        return {**body, "ts": time.time(), "nonce": secrets.token_hex(16)}

    def check(self, body: dict, *, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else float(now)
        try:
            ts = float(body.get("ts"))
            nonce = str(body.get("nonce") or "")
        except (TypeError, ValueError):
            return False
        if not nonce or len(nonce) < 8 or abs(now - ts) > self.max_skew_s:
            return False
        with self.store.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO peer_nonces(nonce, seen_at) VALUES(?,?) "
                "ON CONFLICT(nonce) DO NOTHING", (nonce, now))
            if cur.rowcount != 1:
                return False
            conn.execute("DELETE FROM peer_nonces WHERE seen_at<?", (now - 86400,))
        return True


class PeerService:
    def __init__(self, store: StateStore, identity: NodeIdentity, config: PeerConfig,
                 *, transport: Optional[HttpTransport] = None,
                 admission: Optional[AdmissionService] = None,
                 outbox: Optional[Outbox] = None):
        self.store = store
        self.identity = identity
        self.config = config
        self.transport = transport or HttpTransport()
        self.auth = PeerAuth(store)
        self.lease = ReplicatedLease(store, identity, config,
                                     transport=self.transport, path_prefix="/cluster",
                                     envelope=True)
        self.registry = ReplicatedRegistry(store, identity, config,
                                           transport=self.transport,
                                           path_prefix="/cluster", envelope=True)
        self.admission = admission or AdmissionService(store, identity=identity)
        self.outbox = outbox or Outbox(store)

    def _announce(self, path: str, body: dict) -> int:
        """Persist one row per peer, then try to drain immediately."""
        for peer in self.config.peers:
            self.outbox.enqueue("peer-announce", {
                "endpoint": peer.endpoint, "path": path,
                "body": self.auth.wrap(body)})
        result = self.drain()
        return int(result.get("delivered", 0))

    def drain(self, *, now=None) -> dict:
        def send(kind: str, payload: dict) -> bool:
            return bool(self.transport.post(payload["endpoint"], payload["path"], payload["body"]))
        return self.outbox.drain(send, now=now)

    def outbox_pending(self) -> int:
        return self.outbox.pending()

    # ---- health ---------------------------------------------------------
    def health(self) -> dict:
        return {"ok": True, "node": self.config.node_id,
                "endpoint": self.config.endpoint,
                "peers": [p.node_id for p in self.config.peers],
                "quorum": self.config.required}

    def publish_self(self, record: dict) -> bool:
        sig = self.identity.sign(canonical_json(record).encode("utf-8"))
        return self.registry.publish(record, sig) is not None

    # ---- leases ---------------------------------------------------------
    def acquire_lease(self, round_no: int, *, term: int = 1):
        return self.lease.acquire(round_no, term=term)

    def current_lease(self, round_no: int):
        return self.lease.current(round_no)

    def adopt_lease(self, payload: dict, requester_pubkey: str) -> bool:
        return self.lease.adopt(payload, requester_pubkey=requester_pubkey)

    # ---- registry -------------------------------------------------------
    def accept_registry(self, record: dict, signature: str):
        return self.registry.accept(record, signature)

    # ---- tickets and tasks ---------------------------------------------
    def announce_ticket(self, ticket: TicketView, task: Optional[dict] = None) -> int:
        return self._announce("/cluster/ticket/announce",
                              {"ticket": asdict(ticket), "task": task or {}})

    def accept_ticket(self, body: dict) -> dict:
        if not self.auth.check(body):
            return {"ok": False, "reason": "stale_or_replayed"}
        raw = body.get("ticket") or {}
        try:
            ticket = TicketView(**raw)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "ticket_invalid"}
        stored, created = self.admission.import_signed(ticket)
        if stored is None:
            return {"ok": False, "reason": "ticket_signature_or_conflict"}
        task = body.get("task") or {}
        if task and task.get("taskId"):
            self.save_task(task)
        return {"ok": True, "created": bool(created),
                "ticketId": stored.ticket_id, "taskId": stored.task_id}

    def save_task(self, task: dict) -> None:
        task_id = str(task.get("taskId") or "")
        if not task_id:
            raise ValueError("taskId required")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO peer_tasks(task_id,round,payload,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET round=excluded.round,"
                "payload=excluded.payload,updated_at=excluded.updated_at",
                (task_id, int(task.get("round") or 0), canonical_json(task), time.time()))

    def load_task(self, task_id: str) -> Optional[dict]:
        row = self.store.query_one("SELECT payload FROM peer_tasks WHERE task_id=?", (task_id,))
        return json.loads(row["payload"]) if row else None

    # ---- rounds ---------------------------------------------------------
    def announce_round(self, snapshot: dict) -> int:
        return self._announce("/cluster/round/announce", {"round": snapshot})

    def accept_round(self, body: dict) -> dict:
        if not self.auth.check(body):
            return {"ok": False, "reason": "stale_or_replayed"}
        snapshot = body.get("round") or {}
        round_no = snapshot.get("round")
        if round_no is None:
            return {"ok": False, "reason": "round_invalid"}
        state = str(snapshot.get("state") or "open")
        encoded = canonical_json(snapshot)
        row = self.store.query_one("SELECT state,payload FROM peer_rounds WHERE round=?",
                                   (int(round_no),))
        if row:
            current_state, current_payload = row["state"], row["payload"]
            if current_state != state:
                # A closed round is terminal.  Never reopen it from a stale peer.
                if current_state == "closed":
                    return {"ok": False, "reason": "round_conflict"}
                if state != "closed":
                    return {"ok": False, "reason": "round_conflict"}
            elif current_payload != encoded and encoded > current_payload:
                # Deterministic tie-break: the lexicographically smaller snapshot
                # wins, so two nodes converge whichever order they receive.
                return {"ok": False, "reason": "round_conflict"}
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO peer_rounds(round,state,payload,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(round) DO UPDATE SET state=excluded.state,"
                "payload=excluded.payload,updated_at=excluded.updated_at",
                (int(round_no), state, encoded, time.time()))
        return {"ok": True, "round": int(round_no)}

    def load_round(self, round_no: int) -> Optional[dict]:
        row = self.store.query_one("SELECT payload FROM peer_rounds WHERE round=?", (round_no,))
        return json.loads(row["payload"]) if row else None

    # ---- submissions ----------------------------------------------------
    def announce_submission(self, content_sha256: str, round_no: int,
                            client_key: str) -> int:
        return self._announce("/cluster/submission/announce",
                              {"contentSha256": content_sha256,
                               "round": int(round_no), "clientKey": client_key})

    def accept_submission(self, body: dict) -> dict:
        if not self.auth.check(body):
            return {"ok": False, "reason": "stale_or_replayed"}
        content = str(body.get("contentSha256") or "").lower()
        if len(content) != 64:
            return {"ok": False, "reason": "content_hash_invalid"}
        with self.store.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO peer_submissions(content_sha256,round,client_key,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(content_sha256) DO NOTHING",
                (content, int(body.get("round") or 0),
                 str(body.get("clientKey") or ""), time.time()))
            created = cur.rowcount == 1
        return {"ok": True, "created": bool(created)}

    # ---- witness --------------------------------------------------------
    def witness_sign(self, record: dict) -> dict:
        return {"node": self.config.node_id,
                "pubkey": self.identity.public_key_hex,
                "sig": self.identity.sign(
                    canonical_json(signing_payload(record)).encode("utf-8"))}

    def verify_peer_record(self, record: dict, cert: dict) -> bool:
        return verify_certificate(record, cert)

    def collect_witnesses(self, record: dict, *, required: int = 2) -> dict:
        """Ask peers to co-sign a leader-signed record and attach a quorum.

        A failed or non-quorum collection leaves the record with only its
        leader certificate; the publication path then reports it uncertified.
        """
        certs = list(record.get("certificate") or [])
        allowed = {c.get("node"): c.get("pubkey") for c in certs
                   if c.get("node") and c.get("pubkey")}
        for peer in self.config.peers:
            try:
                reply = self.transport.post(
                    peer.endpoint, "/cluster/witness/sign",
                    self.auth.wrap({"record": record}))
            except Exception:
                continue
            if not isinstance(reply, dict):
                continue
            node = reply.get("node")
            pubkey = reply.get("pubkey")
            if not node or not pubkey or not reply.get("sig"):
                continue
            cert = {"node": node, "pubkey": pubkey, "sig": reply["sig"]}
            if self.verify_peer_record(record, cert):
                certs.append(cert)
                allowed[node] = pubkey
        try:
            body = certify(record, certs, required=int(required), allowed=allowed or None)
        except ValueError:
            body = dict(record)
            body["certificate"] = certs
        return body


__all__ = ["PeerAuth", "PeerService"]
