#!/usr/bin/env python3
"""Offline self-check for a new orchestrator operator."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator import Orchestrator
from orchestrator.admission_policy import AdmissionPolicy
from orchestrator.cluster import ClusterView, LeaseManager
from orchestrator.identity import NodeIdentity
from orchestrator.nodes import NodeRegistry, local_node_record


def check(label, fn):
    try:
        detail = fn()
        print(f"OK   {label}" + (f" — {detail}" if detail else ""))
        return True
    except Exception as e:
        print(f"FAIL {label} — {type(e).__name__}: {e}")
        return False


def main() -> int:
    ok = True
    ok &= check("python", lambda: sys.version.split()[0])
    ok &= check("cryptography", lambda: __import__("cryptography").__version__)
    with tempfile.TemporaryDirectory(prefix="ng-selfcheck-") as tmp:
        tmp = Path(tmp)
        def identity():
            key = NodeIdentity.load_or_create(tmp / "node.pem")
            assert len(key.public_key_hex) == 64
            return key.public_key_hex[:16]
        ok &= check("node key", identity)
        key = NodeIdentity.load_or_create(tmp / "node.pem")
        def registry():
            orch = Orchestrator.open(tmp / "state.sqlite3", identity_path=tmp / "node.pem")
            rec = local_node_record(key, endpoints=["https://node.example"],
                                    roles=["leader"], software_version="3.2",
                                    started_at="2026-11-09T00:00:00Z")
            reg = NodeRegistry(orch.store)
            assert reg.register_signed(rec, key.sign(__import__("json").dumps(
                rec, sort_keys=True, separators=(",", ":")).encode())) is not None
            assert reg.active(signed_only=True)
            orch.close()
            return "signed registry"
        ok &= check("registry", registry)
        def policy():
            mode = AdmissionPolicy.from_env()
            assert mode.mode in ("open", "bonded", "vetted")
            return mode.mode
        ok &= check("admission policy", policy)
        def cluster():
            orch = Orchestrator.open(tmp / "state.sqlite3")
            view = ClusterView(orch.store); view.heartbeat("n0")
            assert view.healthy()
            lm = LeaseManager(orch.store); assert lm.acquire(1, "n0")
            orch.close(); return "gossip + lease"
        ok &= check("cluster", cluster)
    print("\nRESULT:", "ready" if ok else "not ready")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
