#!/usr/bin/env python3
"""Back up and rehearse restoring an orchestrator state directory.

    python tools/backup_restore.py backup --source . --backup /tmp/ng-backup
    python tools/backup_restore.py restore --backup /tmp/ng-backup --target /tmp/ng-restore
    python tools/backup_restore.py verify --target /tmp/ng-restore

The SQLite copy uses the online backup API, so a WAL writer does not need to be
stopped. Keys and public metadata are copied byte-for-byte. ``verify`` reopens
the restored database and prints round, lease, submission, and node counts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import Orchestrator  # noqa: E402

FILES = ("orchestrator_node_key.pem", "orchestrator_object_secret",
         "data/public_rounds.jsonl", "data/nutrition.json",
         "data/round_metrics.jsonl")


def backup(source: Path, target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    src = source / "orchestrator_state.sqlite3"
    if not src.exists():
        raise SystemExit(f"missing {src}")
    dst = target / "orchestrator_state.sqlite3"
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dst)) as d:
        s.backup(d)
    copied = []
    for name in FILES:
        path = source / name
        if path.exists():
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)
            copied.append(name)
    (target / "backup_manifest.json").write_text(json.dumps(
        {"source": str(source), "files": copied}, indent=2), encoding="utf-8")
    return {"backup": str(target), "copied": copied}


def restore(backup_dir: Path, target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    for name in ("orchestrator_state.sqlite3",) + FILES:
        path = backup_dir / name
        if path.exists():
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)
    return {"target": str(target)}


def verify(target: Path) -> dict:
    orch = Orchestrator.open(target / "orchestrator_state.sqlite3",
                             identity_path=target / "orchestrator_node_key.pem")
    try:
        rnd = orch.scheduling.load_round()
        pools = orch.scheduling.load_pools()
        subs = orch.submission.load_submissions()
        nodes = orch.registry_active() if hasattr(orch, "registry_active") else []
        return {"round": None if rnd is None else rnd.round,
                "pools": len(pools), "submissions": len(subs),
                "nodes": len(nodes),
                "identity": orch.identity.public_key_hex[:16]}
    finally:
        orch.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup"); b.add_argument("--source", required=True); b.add_argument("--backup", required=True)
    r = sub.add_parser("restore"); r.add_argument("--backup", required=True); r.add_argument("--target", required=True)
    v = sub.add_parser("verify"); v.add_argument("--target", required=True)
    args = parser.parse_args()
    if args.command == "backup":
        out = backup(Path(args.source), Path(args.backup))
    elif args.command == "restore":
        out = restore(Path(args.backup), Path(args.target))
    else:
        out = verify(Path(args.target))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
