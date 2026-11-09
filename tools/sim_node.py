#!/usr/bin/env python3
"""One lightweight orchestrator process for the on-machine cluster drills.

This is a control-plane-only node: it owns a real SQLite state store and a real
Ed25519 key, exposes the replication endpoints from ``orchestrator.replication``,
and can register submissions idempotently. It deliberately does not load the
658M model or `krum_stream`; the drills here test lease/registry/quorum and
submission idempotency, not training throughput.

    python tools/sim_node.py --node-id node-a --port 7101 --dir /tmp/ng-sim/a \
        --peers /tmp/ng-sim/peers.json --quorum 2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import Orchestrator  # noqa: E402
from orchestrator.cluster import Lease  # noqa: E402
from orchestrator.nodes import local_node_record, node_id_for  # noqa: E402
from orchestrator.records import canonical_json, sign_record  # noqa: E402
from orchestrator.replication import (Peer, PeerConfig, ReplicatedLease,  # noqa: E402
                                      ReplicatedRegistry, lease_payload)

APP = FastAPI(title="CrowdGPT simulation node")
STATE: dict = {}


def _config(node_dir: Path, node_id: str | None, port: int, peers_path: str | None,
            quorum: int | None) -> PeerConfig:
    raw = json.loads(Path(peers_path).read_text()) if peers_path else []
    peers = tuple(Peer(p["node_id"], p["endpoint"], p["pubkey"]) for p in raw)
    orch = STATE["orch"]
    self_id = node_id or node_id_for(orch.identity)
    endpoint = f"http://127.0.0.1:{port}"
    return PeerConfig(node_id=self_id, endpoint=endpoint, peers=peers, quorum=quorum)


def bootstrap(node_dir: Path, port: int, node_id: str | None, peers_path: str | None,
              quorum: int | None, ttl_s: float) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator.open(node_dir / "state.sqlite3",
                             identity_path=node_dir / "node.pem")
    STATE["orch"] = orch
    cfg = _config(node_dir, node_id, port, peers_path, quorum)
    STATE["id"] = cfg.node_id
    STATE["config"] = cfg
    STATE["lease"] = ReplicatedLease(orch.store, orch.identity, cfg, ttl_s=ttl_s)
    STATE["registry"] = ReplicatedRegistry(orch.store, orch.identity, cfg)
    from orchestrator.replication import HttpTransport
    STATE["transport"] = HttpTransport()
    STATE["self_record"] = local_node_record(
        orch.identity, endpoints=[cfg.endpoint], roles=["leader", "witness"],
        software_version="sim-1", started_at="2026-11-09T00:00:00Z")


@APP.on_event("startup")
async def _startup() -> None:
    port = STATE["port"]
    bootstrap(Path(STATE["dir"]), port, STATE.get("node_id"),
              STATE.get("peers"), STATE.get("quorum"), STATE.get("ttl", 3.0))
    record = STATE["self_record"]
    sig = STATE["orch"].identity.sign(canonical_json(record).encode("utf-8"))
    STATE["registry"].accept(record, sig)


@APP.get("/sim/health")
async def health():
    cfg: PeerConfig = STATE["config"]
    return {"ok": True, "node": cfg.node_id, "endpoint": cfg.endpoint,
            "peers": [p.node_id for p in cfg.peers], "quorum": cfg.required,
            "configured": True}


@APP.get("/sim/state")
async def state(round: int = 1):
    orch = STATE["orch"]
    lease = STATE["lease"].current(round)
    return {
        "node": STATE["config"].node_id,
        "lease": None if lease is None else {"leader": lease.leader, "term": lease.term,
                                             "expiresAt": lease.expires_at},
        "submissions": len(orch.submission.load_submissions(round_no=round)),
        "registry": STATE["registry"].seen_ids(),
    }


@APP.post("/sim/lease/acquire")
async def lease_acquire(request: Request):
    body = await request.json()
    lease = STATE["lease"].acquire(int(body.get("round", 1)), term=int(body.get("term", 1)))
    if lease is None:
        raise HTTPException(status_code=409, detail={"reason": "quorum_not_acquired"})
    return {"round": lease.round, "leader": lease.leader, "term": lease.term,
            "expiresAt": lease.expires_at, "node": STATE["config"].node_id}


@APP.post("/sim/lease/ack")
async def lease_ack(request: Request):
    body = await request.json()
    lease = body.get("lease") or {}
    requester = body.get("requester") or ""
    pubkey = body.get("pubkey") or ""
    cfg: PeerConfig = STATE["config"]
    allowed = cfg.peer(requester)
    if requester != cfg.node_id and allowed is None:
        raise HTTPException(status_code=403, detail={"reason": "unknown_requester"})
    if requester == cfg.node_id:
        raise HTTPException(status_code=400, detail={"reason": "self_ack"})
    if allowed is not None and allowed.pubkey != pubkey:
        raise HTTPException(status_code=403, detail={"reason": "pubkey_mismatch"})
    peer_lease = dict(lease)
    peer_lease["signature"] = body.get("signature", "")
    if not STATE["lease"].adopt(peer_lease, requester_pubkey=pubkey):
        raise HTTPException(status_code=409, detail={"reason": "lease_conflict"})
    payload = {k: lease[k] for k in ("schema", "round", "leader", "term", "expiresAt")}
    sig = STATE["config"].node_id and STATE["orch"].identity.sign(
        canonical_json(payload).encode("utf-8"))
    return {"ack": True, "node": cfg.node_id,
            "pubkey": STATE["orch"].identity.public_key_hex, "sig": sig}


@APP.get("/sim/lease/current")
async def lease_current(round: int = 1):
    lease = STATE["lease"].current(round)
    return {"lease": None if lease is None else {"leader": lease.leader,
                                                 "term": lease.term,
                                                 "expiresAt": lease.expires_at}}


@APP.post("/sim/registry/register")
async def registry_register(request: Request):
    body = await request.json()
    node = STATE["registry"].publish(body.get("record") or {}, body.get("signature") or "")
    if node is None:
        raise HTTPException(status_code=400, detail={"reason": "bad_signature"})
    return {"ok": True, "seen": STATE["registry"].seen_ids()}


@APP.post("/sim/registry/announce")
async def registry_announce(request: Request):
    body = await request.json()
    node = STATE["registry"].accept(body.get("record") or {}, body.get("signature") or "")
    if node is None:
        raise HTTPException(status_code=400, detail={"reason": "bad_signature"})
    return {"ok": True, "node": STATE["config"].node_id}


@APP.get("/sim/registry")
async def registry():
    return {"seen": STATE["registry"].seen_ids(), "self": STATE["config"].node_id}


@APP.post("/sim/submission/finalize")
async def submission_finalize(request: Request):
    body = await request.json()
    orch = STATE["orch"]
    sub, created = orch.submission.record_submission(
        client_key=str(body["client_key"]), round_no=int(body.get("round", 1)),
        content_sha256=str(body["content_sha256"]), tokens=int(body.get("tokens", 0)),
        steps=int(body.get("steps", 0)), delta_path=body.get("delta_path"),
        status="accepted")
    if created:
        payload = dict(body); payload["submission_id"] = sub.submission_id
        for peer in STATE["config"].peers:
            STATE["transport"].post(peer.endpoint, "/sim/submission/announce", payload)
    return {"created": created, "submission_id": sub.submission_id,
            "count": len(orch.submission.load_submissions(round_no=sub.round)),
            "node": STATE["config"].node_id}


@APP.post("/sim/submission/announce")
async def submission_announce(request: Request):
    body = await request.json()
    sub, created = STATE["orch"].submission.record_submission(
        client_key=str(body["client_key"]), round_no=int(body.get("round", 1)),
        content_sha256=str(body["content_sha256"]), tokens=int(body.get("tokens", 0)),
        steps=int(body.get("steps", 0)), delta_path=body.get("delta_path"),
        status="accepted")
    return {"created": created, "submission_id": sub.submission_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--peers")
    parser.add_argument("--quorum", type=int, default=2)
    parser.add_argument("--ttl", type=float, default=3.0)
    args = parser.parse_args()
    STATE.update({"port": args.port, "dir": args.dir, "node_id": args.node_id,
                  "peers": args.peers, "quorum": args.quorum, "ttl": args.ttl})
    import uvicorn
    uvicorn.run(APP, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
