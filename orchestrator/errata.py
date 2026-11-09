"""Signed errata for a published round record.

A publication is first-write-wins, so a wrong record is never deleted. An
``ng-errata-1`` object points at the original record hash, states the reason,
and optionally names a replacement. The original and the errata both remain
auditable.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

from .identity import NodeIdentity
from .records import canonical_json, verify_certificate
from .state import StateStore

SCHEMA = "ng-errata-1"


def build_errata(*, round_no: int, original_record_hash: str, reason: str,
                 replacement_record_hash: Optional[str], issuer: str,
                 issued_at: Optional[str] = None) -> dict:
    if len(original_record_hash) != 64:
        raise ValueError("original_record_hash must be 64 hex characters")
    if replacement_record_hash is not None and len(replacement_record_hash) != 64:
        raise ValueError("replacement_record_hash must be 64 hex characters")
    if not reason.strip():
        raise ValueError("reason is required")
    return {
        "schema": SCHEMA,
        "round": int(round_no),
        "original_record_hash": original_record_hash.lower(),
        "replacement_record_hash": (replacement_record_hash.lower()
                                    if replacement_record_hash else None),
        "reason": reason.strip()[:2000],
        "issuer": issuer,
        "issued_at": issued_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "certificate": [],
    }


def errata_id(errata: dict) -> str:
    body = dict(errata)
    body.pop("certificate", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def sign_errata(errata: dict, identity: NodeIdentity, node_id: str) -> dict:
    body = dict(errata)
    body.pop("certificate", None)
    sig = identity.sign(canonical_json(body).encode("utf-8"))
    body["certificate"] = [{"node": node_id, "pubkey": identity.public_key_hex, "sig": sig}]
    return body


def verify_errata(errata: dict, *, expected_pubkey: Optional[str] = None) -> bool:
    certs = errata.get("certificate") or []
    if not certs:
        return False
    return all(verify_certificate(errata, c, expected_pubkey=expected_pubkey)
               for c in certs)


class ErrataService:
    def __init__(self, store: StateStore):
        self.store = store

    def record(self, errata: dict) -> dict:
        if errata.get("schema") != SCHEMA or not verify_errata(errata):
            raise ValueError("invalid errata")
        eid = errata_id(errata)
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO errata(errata_id,round,payload,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(errata_id) DO NOTHING",
                (eid, int(errata["round"]), canonical_json(errata), time.time()),
            )
            conn.execute(
                "INSERT INTO events(ts,kind,payload) VALUES(?,?,?)",
                (time.time(), "round_errata",
                 json.dumps({"round": int(errata["round"]), "errata": eid,
                             "replacement": errata.get("replacement_record_hash")},
                            sort_keys=True, separators=(",", ":"))),
            )
        return self.get(eid) or errata

    def get(self, eid: str) -> Optional[dict]:
        row = self.store.query_one("SELECT * FROM errata WHERE errata_id=?", (eid,))
        if not row:
            return None
        data = json.loads(row["payload"])
        data["errata_id"] = row["errata_id"]
        return data

    def for_round(self, round_no: int) -> list[dict]:
        rows = self.store.query("SELECT * FROM errata WHERE round=? ORDER BY created_at",
                                (int(round_no),))
        out = []
        for row in rows:
            data = json.loads(row["payload"])
            data["errata_id"] = row["errata_id"]
            out.append(data)
        return out

    def all(self) -> list[dict]:
        rows = self.store.query("SELECT * FROM errata ORDER BY created_at")
        out = []
        for row in rows:
            data = json.loads(row["payload"])
            data["errata_id"] = row["errata_id"]
            out.append(data)
        return out


__all__ = ["SCHEMA", "ErrataService", "build_errata", "errata_id", "sign_errata",
           "verify_errata"]
