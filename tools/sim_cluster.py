#!/usr/bin/env python3
"""Run the on-machine orchestrator cluster drills.

    python tools/sim_cluster.py --out /tmp/ng-sim/report.json

This launches real processes: each node has its own SQLite store, Ed25519 key,
and HTTP port. The drills cover signed registry convergence, quorum leases,
leader death and takeover (x3), a 3-of-4 quorum under a 2-2 partition, duplicate
finalize to two nodes, and restart under write load.

Everything here is a **simulation on one machine**. It is not a production
multi-node RTO and must not be cited as the P3/P4 gate.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.identity import NodeIdentity  # noqa: E402
from orchestrator.cluster import certify
from orchestrator.nodes import local_node_record, node_id_for  # noqa: E402
from orchestrator.records import ZERO_ROOT, build_round_record, canonical_json  # noqa: E402


@dataclass
class Spec:
    node_id: str
    port: int
    directory: Path
    endpoint: str
    proc: subprocess.Popen | None = None

    @property
    def pem(self) -> Path:
        return self.directory / "node.pem"


def _post(endpoint: str, path: str, body: dict, timeout: float = 3.0):
    data = json.dumps(body, sort_keys=True).encode()
    req = urllib.request.Request(endpoint + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def _get(endpoint: str, path: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(endpoint + path, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def make_specs(root: Path, count: int, base_port: int, prefix: str = "node") -> list[Spec]:
    specs = []
    for i in range(count):
        directory = root / f"{prefix}-{i}"
        directory.mkdir(parents=True, exist_ok=True)
        identity = NodeIdentity.load_or_create(directory / "node.pem")
        node_id = node_id_for(identity)
        port = base_port + i
        specs.append(Spec(node_id, port, directory, f"http://127.0.0.1:{port}"))
    return specs


def write_peers(specs: list[Spec]) -> None:
    for spec in specs:
        peers = []
        for other in specs:
            if other.node_id == spec.node_id:
                continue
            identity = NodeIdentity.load_or_create(other.pem)
            peers.append({"node_id": other.node_id, "endpoint": other.endpoint,
                          "pubkey": identity.public_key_hex})
        (spec.directory / "peers.json").write_text(json.dumps(peers, indent=2))


def start(spec: Spec, quorum: int, ttl: float) -> subprocess.Popen:
    cmd = [sys.executable, str(ROOT / "tools" / "sim_node.py"),
           "--node-id", spec.node_id, "--port", str(spec.port),
           "--dir", str(spec.directory), "--peers", str(spec.directory / "peers.json"),
           "--quorum", str(quorum), "--ttl", str(ttl)]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    spec.proc = proc
    return proc


def wait_ready(specs: list[Spec], timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    for spec in specs:
        while time.time() < deadline:
            status, body = _get(spec.endpoint, "/sim/health", timeout=2.0)
            if status == 200 and body and body.get("ok"):
                break
            time.sleep(0.15)
        else:
            raise RuntimeError(f"node {spec.node_id} did not become ready")


def kill(spec: Spec) -> None:
    if spec.proc and spec.proc.poll() is None:
        spec.proc.kill()
        try:
            spec.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    spec.proc = None


def restart(spec: Spec, quorum: int, ttl: float) -> None:
    start(spec, quorum, ttl)
    wait_ready([spec])


def register_all(specs: list[Spec]) -> None:
    for spec in specs:
        identity = NodeIdentity.load_or_create(spec.pem)
        record = local_node_record(identity, endpoints=[spec.endpoint],
                                   roles=["leader", "witness"], software_version="sim-1",
                                   started_at="2026-11-09T00:00:00Z")
        signature = identity.sign(canonical_json(record).encode())
        for target in specs:
            status, _ = _post(target.endpoint, "/sim/registry/register",
                              {"record": record, "signature": signature})
            if status != 200:
                raise RuntimeError(f"register {spec.node_id} on {target.node_id} failed: {status}")


def acquire_until(spec: Spec, round_no: int, deadline_s: float) -> tuple[float, dict] | None:
    start_t = time.time()
    while time.time() - start_t <= deadline_s:
        status, body = _post(spec.endpoint, "/sim/lease/acquire",
                             {"round": round_no, "term": round_no})
        if status == 200 and body:
            return time.time() - start_t, body
        time.sleep(0.2)
    return None


def finalize(spec: Spec, content: str, client: str = "sim-client") -> dict:
    status, body = _post(spec.endpoint, "/sim/submission/finalize", {
        "client_key": client, "round": 1, "content_sha256": content,
        "tokens": 1000, "steps": 10, "delta_path": f"/tmp/{content[:8]}.bin"})
    return {"status": status, **(body or {})}


def submissions(spec: Spec) -> int:
    status, body = _get(spec.endpoint, "/sim/state?round=1")
    return int(body.get("submissions", 0)) if status == 200 and body else -1


def lease_state(spec: Spec, round_no: int) -> dict | None:
    status, body = _get(spec.endpoint, f"/sim/lease/current?round={round_no}")
    return body.get("lease") if status == 200 and body else None


def run_cluster(root: Path, base_port: int, quorum: int = 2, ttl: float = 3.0) -> dict:
    specs = make_specs(root, 3, base_port, "quorum")
    write_peers(specs)
    for spec in specs:
        start(spec, quorum, ttl)
    wait_ready(specs)
    results: dict = {"kind": "simulation", "nodes": [s.node_id for s in specs],
                     "quorum": quorum, "ttl_s": ttl, "drills": {}}
    try:
        register_all(specs)
        time.sleep(0.3)
        seen = []
        for spec in specs:
            status, body = _get(spec.endpoint, "/sim/registry")
            seen.append(sorted(body.get("seen", [])) if status == 200 else [])
        results["drills"]["registry"] = {
            "expected": sorted(s.node_id for s in specs),
            "views": seen,
            "converged": all(view == sorted(s.node_id for s in specs) for view in seen),
        }

        takeovers = []
        leader_order = [0, 1, 2]
        for i, idx in enumerate(leader_order):
            round_no = i + 1
            leader = specs[idx]
            restart(leader, quorum, ttl) if leader.proc is None else None
            got = acquire_until(leader, round_no, 10)
            if got is None:
                raise RuntimeError(f"initial lease round {round_no} failed")
            _, lease = got
            followers = [s for s in specs if s is not leader and s.proc is not None]
            observed = [lease_state(s, round_no) for s in followers]
            kill(leader)
            survivor = specs[(idx + 1) % 3]
            if survivor.proc is None:
                restart(survivor, quorum, ttl)
            takeover = acquire_until(survivor, round_no, ttl + 8)
            if takeover is None:
                raise RuntimeError(f"takeover round {round_no} failed")
            rto, new_lease = takeover
            takeovers.append({"round": round_no, "old_leader": leader.node_id,
                              "new_leader": new_lease["leader"], "rto_s": round(rto, 3),
                              "followers_observed_lease": all(o and o.get("leader") == leader.node_id
                                                              for o in observed)})
            restart(leader, quorum, ttl)
        results["drills"]["leader_kill_x3"] = {
            "takeovers": takeovers,
            "all_takeovers_succeeded": len(takeovers) == 3,
            "max_rto_s": max(t["rto_s"] for t in takeovers),
        }

        # Duplicate finalize to two nodes: announce makes the second node idempotent.
        content = "a" * 64
        first = finalize(specs[0], content)
        second = finalize(specs[1], content)
        third = finalize(specs[0], content)
        counts = [submissions(s) for s in specs]
        results["drills"]["duplicate_finalize"] = {
            "first_created": first.get("created"), "second_created": second.get("created"),
            "third_created": third.get("created"), "counts": counts,
            "exactly_once": first.get("created") is True and second.get("created") is False
                            and third.get("created") is False and counts == [1, 1, 1],
        }

        # 2-of-3 witness certificates across real processes.
        record = build_round_record(
            round_no=99, opened_at="2026-11-09T00:00:00Z",
            closed_at="2026-11-09T00:10:00Z", base_weights="a" * 64,
            aggregate_weights="b" * 64, leader=specs[0].node_id, witnesses=(),
            tickets_root="1" * 64, submissions_root="2" * 64, deltas_root="3" * 64,
            prev_record_hash=ZERO_ROOT, eval=None, bytes_down=1, bytes_up=2,
            code_versions=("sim-1",))
        certs = []
        for spec in specs[:2]:
            status, body = _post(spec.endpoint, "/sim/witness/sign", {"record": record})
            if status == 200 and body:
                certs.append(body["certificate"])
        allowed = {s.node_id: NodeIdentity.load_or_create(s.pem).public_key_hex for s in specs}
        certified = certify(record, certs, required=2, allowed=allowed)
        short = None
        try:
            certify(record, certs[:1], required=2, allowed=allowed)
        except ValueError as exc:
            short = str(exc)
        results["drills"]["witness_quorum"] = {
            "certificates": len(certified["certificate"]),
            "two_of_three_ok": len(certified["certificate"]) >= 2,
            "one_of_three_rejected": short is not None,
            "reason": short,
        }

        # Restart under write load.
        initial = submissions(specs[0])
        load = [f"{i:064x}" for i in range(40)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda c: finalize(specs[0], c), load))
        before = submissions(specs[0])
        kill(specs[0])
        restart(specs[0], quorum, ttl)
        after = submissions(specs[0])
        for c in load[:10]:
            finalize(specs[0], c)
        replayed = submissions(specs[0])
        results["drills"]["restart_under_load"] = {
            "written": len(load), "initial": initial,
            "before_restart": before, "after_restart": after,
            "after_replay": replayed,
            "no_loss": before == initial + len(load) and after == before,
            "no_duplicates": replayed == before,
        }
    finally:
        for spec in specs:
            kill(spec)
    return results


def run_partition(root: Path, base_port: int, ttl: float = 2.0) -> dict:
    """Two groups of two, each unable to see the other side, global quorum 3-of-4."""
    specs = make_specs(root, 4, base_port, "partition")
    left, right = specs[:2], specs[2:]
    for group in (left, right):
        for spec in group:
            peers = []
            for other in group:
                if other.node_id == spec.node_id:
                    continue
                identity = NodeIdentity.load_or_create(other.pem)
                peers.append({"node_id": other.node_id, "endpoint": other.endpoint,
                              "pubkey": identity.public_key_hex})
            (spec.directory / "peers.json").write_text(json.dumps(peers, indent=2))
    for spec in specs:
        start(spec, quorum=3, ttl=ttl)
    wait_ready(specs)
    try:
        left_result = acquire_until(left[0], 1, 2.0)
        right_result = acquire_until(right[0], 1, 2.0)
        return {
            "kind": "simulation", "partition": "2-2", "global_quorum": 3,
            "left_lease": left_result is not None,
            "right_lease": right_result is not None,
            "neither_certified": left_result is None and right_result is None,
        }
    finally:
        for spec in specs:
            kill(spec)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/tmp/ng-sim")
    parser.add_argument("--port", type=int, default=7300)
    parser.add_argument("--out", default="/tmp/ng-sim/report.json")
    parser.add_argument("--fresh", action="store_true", default=True)
    args = parser.parse_args()
    root = Path(args.root)
    if args.fresh and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "scope": "single-machine simulation; not a production RTO"}
    report["cluster"] = run_cluster(root / "cluster", args.port)
    report["partition"] = run_partition(root / "partition", args.port + 20)
    report["elapsed_s"] = round(time.time() - started, 1)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    ok = (report["cluster"]["drills"]["registry"]["converged"]
          and report["cluster"]["drills"]["leader_kill_x3"]["all_takeovers_succeeded"]
          and report["cluster"]["drills"]["duplicate_finalize"]["exactly_once"]
          and report["cluster"]["drills"]["witness_quorum"]["two_of_three_ok"]
          and report["cluster"]["drills"]["witness_quorum"]["one_of_three_rejected"]
          and report["cluster"]["drills"]["restart_under_load"]["no_loss"]
          and report["cluster"]["drills"]["restart_under_load"]["no_duplicates"]
          and report["partition"]["neither_certified"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
