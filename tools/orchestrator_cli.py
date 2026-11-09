#!/usr/bin/env python3
"""Operator CLI for a CrowdGPT orchestrator node.

    python tools/orchestrator_cli.py init --dir /var/lib/crowdgpt
    python tools/orchestrator_cli.py doctor --dir /var/lib/crowdgpt
    python tools/orchestrator_cli.py invite --dir /var/lib/crowdgpt \
        --endpoint https://node.example --out invite.json
    python tools/orchestrator_cli.py join --dir /var/lib/crowdgpt \
        --token invite.json --trusted-pubkey <hex> --public-url https://me.example
    python tools/orchestrator_cli.py run --dir /var/lib/crowdgpt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.identity import NodeIdentity  # noqa: E402
from orchestrator.join import create_invite, invite_peer, verify_invite  # noqa: E402
from orchestrator.nodes import local_node_record, node_id_for  # noqa: E402
from orchestrator.records import canonical_json  # noqa: E402
from orchestrator.replication import HttpTransport  # noqa: E402

CONFIG = "config.json"
PEERS = "peers.json"
KEY = "orchestrator_node_key.pem"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _http_json(url: str, *, method: str = "GET", body: dict | None = None,
               admin_secret: str | None = None, timeout: float = 10.0) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if admin_secret:
        req.add_header("X-Admin-Secret", admin_secret)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def cmd_init(args) -> int:
    directory = Path(args.dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    identity = NodeIdentity.load_or_create(directory / KEY)
    public_url = args.public_url.rstrip("/")
    cfg = {
        "schema": "ng-node-config-1",
        "publicUrl": public_url,
        "host": args.host,
        "port": int(args.port),
        "orchestratorDb": str(directory / "orchestrator_state.sqlite3"),
        "objectDir": str(directory / "objects"),
        "peersFile": str(directory / PEERS),
        "roles": ["leader", "witness", "validator"],
    }
    _write_json(directory / CONFIG, cfg)
    peers_path = directory / PEERS
    if not peers_path.exists():
        _write_json(peers_path, [])
    print(json.dumps({"dir": str(directory), "nodeId": node_id_for(identity),
                      "pubkey": identity.public_key_hex,
                      "config": cfg}, indent=2))
    return 0


def cmd_invite(args) -> int:
    directory = Path(args.dir).expanduser().resolve()
    identity = NodeIdentity.load_or_create(directory / KEY)
    token = create_invite(identity, node_id=node_id_for(identity),
                          endpoint=args.endpoint,
                          roles=args.role or ("leader", "witness", "validator"),
                          ttl_s=args.ttl)
    text = json.dumps(token, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(json.dumps({"out": args.out, "expiresAt": token["expiresAt"]}))
    else:
        print(text)
    return 0


def cmd_join(args) -> int:
    token = json.loads(Path(args.token).read_text(encoding="utf-8"))
    trusted = args.trusted_pubkey or []
    body = verify_invite(token, trusted_pubkeys=trusted)
    directory = Path(args.dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    NodeIdentity.load_or_create(directory / KEY)
    peer = invite_peer(body)
    peers_path = directory / PEERS
    peers = _read_json(peers_path, [])
    peers = [p for p in peers if p.get("node_id") != peer.node_id]
    peers.append({"node_id": peer.node_id, "endpoint": peer.endpoint,
                  "pubkey": peer.pubkey})
    _write_json(peers_path, peers)
    cfg = _read_json(directory / CONFIG, {})
    cfg.setdefault("schema", "ng-node-config-1")
    cfg.setdefault("host", "0.0.0.0")
    cfg.setdefault("port", 5006)
    cfg.setdefault("orchestratorDb", str(directory / "orchestrator_state.sqlite3"))
    cfg.setdefault("objectDir", str(directory / "objects"))
    cfg["peersFile"] = str(peers_path)
    if args.public_url:
        cfg["publicUrl"] = args.public_url.rstrip("/")
    _write_json(directory / CONFIG, cfg)
    probe = None
    if not args.offline:
        try:
            probe = _http_json(body["endpoint"].rstrip("/") + "/cluster/health",
                               timeout=args.timeout)
        except Exception as exc:
            probe = {"error": type(exc).__name__, "detail": str(exc)}
    print(json.dumps({"joined": peer.node_id, "endpoint": peer.endpoint,
                      "peersFile": str(peers_path), "probe": probe}, indent=2))
    return 0 if probe is None or "error" not in probe else 1


def cmd_doctor(args) -> int:
    directory = Path(args.dir).expanduser().resolve()
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("python", sys.version_info >= (3, 12), sys.version.split()[0])
    try:
        import cryptography
        add("cryptography", True, cryptography.__version__)
    except Exception as exc:
        add("cryptography", False, str(exc))
    add("directory", directory.is_dir() and os.access(directory, os.W_OK),
        str(directory))
    key = directory / KEY
    add("node key", key.exists() and (key.stat().st_mode & 0o077) == 0,
        str(key))
    peers = _read_json(directory / PEERS, [])
    add("peers file", isinstance(peers, list), f"{len(peers)} peer(s)")
    cfg = _read_json(directory / CONFIG, {})
    add("config", bool(cfg.get("publicUrl")), cfg.get("publicUrl", ""))
    db_parent = Path(cfg.get("orchestratorDb", directory / "state.sqlite3")).parent
    add("database parent", db_parent.exists() or os.access(db_parent.parent, os.W_OK),
        str(db_parent))
    if args.probe:
        transport = HttpTransport(timeout_s=args.timeout)
        for peer in peers:
            reply = transport.get(peer.get("endpoint", ""), "/cluster/health")
            add(f"peer {peer.get('node_id', '?')}", bool(reply and reply.get("ok")),
                json.dumps(reply or {}, sort_keys=True))
    ok = all(item["ok"] for item in checks)
    print(json.dumps({"ok": ok, "checks": checks}, indent=2))
    return 0 if ok else 1


def _base(args) -> str:
    return (args.url or _read_json(Path(args.dir).expanduser() / CONFIG, {}).get(
        "publicUrl") or "http://127.0.0.1:5006").rstrip("/")


def cmd_status(args) -> int:
    base = _base(args)
    out = {}
    for name, path in (("cluster", "/api/cluster"),
                       ("round", "/fl/round_status"),
                       ("audit", "/api/audit/chain")):
        try:
            out[name] = _http_json(base + path, admin_secret=args.admin_secret)
        except Exception as exc:
            out[name] = {"error": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if not any("error" in v for v in out.values()) else 1


def cmd_tui(args) -> int:
    base = _base(args)
    while True:
        try:
            cluster = _http_json(base + "/api/cluster", admin_secret=args.admin_secret)
            round_status = _http_json(base + "/fl/round_status", admin_secret=args.admin_secret)
            audit = _http_json(base + "/api/audit/chain", admin_secret=args.admin_secret)
            error = None
        except Exception as exc:
            cluster, round_status, audit = {}, {}, {}
            error = f"{type(exc).__name__}: {exc}"
        if not args.once:
            print("\033[2J\033[H", end="")
        print(f"CrowdGPT orchestrator  {base}")
        if error:
            print(f"ERROR {error}")
        else:
            lease = cluster.get("lease") or {}
            peer = cluster.get("peer") or {}
            print(f"round={cluster.get('round')} step={round_status.get('global_step')} "
                  f"lease={lease.get('leader', 'none')} term={lease.get('term', '-')}")
            print(f"peers={peer.get('peers', [])} quorum={peer.get('quorum', '-')} "
                  f"configured={peer.get('configured', False)}")
            print(f"nodes={len(cluster.get('nodes', []))} "
                  f"audit_ok={audit.get('ok')} broken={audit.get('broken', [])}")
            print(f"replicated={cluster.get('replicated', {})}")
        if args.once:
            return 0 if not error else 1
        time.sleep(max(1.0, float(args.interval)))

def cmd_peers(args) -> int:
    print(json.dumps(_read_json(Path(args.dir).expanduser() / PEERS, []), indent=2))
    return 0


def cmd_rotate(args) -> int:
    directory = Path(args.dir).expanduser().resolve()
    old = NodeIdentity.load_or_create(directory / KEY)
    new = NodeIdentity.load_or_create(directory / "orchestrator_node_key.next.pem")
    record = local_node_record(new, endpoints=[args.public_url],
                               roles=["leader", "witness", "validator"],
                               software_version="3.2",
                               started_at=args.started_at)
    signature = new.sign(canonical_json(record).encode("utf-8"))
    body = {"node_id": node_id_for(old), "pubkey": old.public_key_hex,
            "record": record, "signature": signature,
            "overlap_s": args.overlap}
    try:
        out = _http_json(_base(args) + "/api/admin/node/rotate", method="POST",
                         body=body, admin_secret=args.admin_secret)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc),
                          "nextKey": str(directory / "orchestrator_node_key.next.pem")},
                         indent=2))
        return 1
    print(json.dumps({"rotated": out, "nextKey": str(directory / "orchestrator_node_key.next.pem"),
                      "restart": "move the next key into place and restart the node"},
                     indent=2))
    return 0


def cmd_backup(args) -> int:
    from tools.backup_restore import backup
    print(json.dumps(backup(Path(args.dir).expanduser(), Path(args.backup).expanduser()),
                     indent=2))
    return 0


def cmd_restore(args) -> int:
    from tools.backup_restore import restore
    print(json.dumps(restore(Path(args.backup).expanduser(), Path(args.dir).expanduser()),
                     indent=2))
    return 0


def cmd_verify(args) -> int:
    base = _base(args)
    out = {}
    for name, path in (("chain", "/api/audit/chain"),
                       ("checkpoint", "/api/audit/checkpoint")):
        try:
            out[name] = _http_json(base + path, admin_secret=args.admin_secret)
        except Exception as exc:
            out[name] = {"error": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out.get("chain", {}).get("ok") else 1


def cmd_run(args) -> int:
    directory = Path(args.dir).expanduser().resolve()
    cfg = _read_json(directory / CONFIG, {})
    env = os.environ.copy()
    env.setdefault("CROWDGPT_ORCHESTRATOR_DB", cfg.get("orchestratorDb",
                    str(directory / "orchestrator_state.sqlite3")))
    env.setdefault("CROWDGPT_OBJECT_DIR", cfg.get("objectDir", str(directory / "objects")))
    env.setdefault("CROWDGPT_PEERS_FILE", cfg.get("peersFile", str(directory / PEERS)))
    env.setdefault("CROWDGPT_PUBLIC_URL", cfg.get("publicUrl", "http://localhost:5006"))
    env.setdefault("CROWDGPT_HOST", str(cfg.get("host", "0.0.0.0")))
    env.setdefault("CROWDGPT_PORT", str(cfg.get("port", 5006)))
    os.chdir(directory)
    os.execve(sys.executable, [sys.executable, str(ROOT / "server.py")], env)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CrowdGPT orchestrator operator CLI")
    sub = p.add_subparsers(dest="command", required=True)

    def add_dir(sp):
        sp.add_argument("--dir", required=True, help="node state directory")

    i = sub.add_parser("init"); add_dir(i)
    i.add_argument("--public-url", default="http://localhost:5006")
    i.add_argument("--host", default="0.0.0.0")
    i.add_argument("--port", type=int, default=5006)
    i.set_defaults(func=cmd_init)

    inv = sub.add_parser("invite"); add_dir(inv)
    inv.add_argument("--endpoint", required=True)
    inv.add_argument("--role", action="append")
    inv.add_argument("--ttl", type=float, default=3600)
    inv.add_argument("--out")
    inv.set_defaults(func=cmd_invite)

    j = sub.add_parser("join"); add_dir(j)
    j.add_argument("--token", required=True)
    j.add_argument("--trusted-pubkey", action="append", required=True)
    j.add_argument("--public-url")
    j.add_argument("--offline", action="store_true")
    j.add_argument("--timeout", type=float, default=10)
    j.set_defaults(func=cmd_join)

    d = sub.add_parser("doctor"); add_dir(d)
    d.add_argument("--probe", action="store_true")
    d.add_argument("--timeout", type=float, default=5)
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("status"); add_dir(s)
    s.add_argument("--url")
    s.add_argument("--admin-secret")
    s.set_defaults(func=cmd_status)

    t = sub.add_parser("tui"); add_dir(t)
    t.add_argument("--url")
    t.add_argument("--admin-secret")
    t.add_argument("--interval", type=float, default=5.0)
    t.add_argument("--once", action="store_true")
    t.set_defaults(func=cmd_tui)

    pe = sub.add_parser("peers"); add_dir(pe)
    pe.set_defaults(func=cmd_peers)

    r = sub.add_parser("rotate-key"); add_dir(r)
    r.add_argument("--url")
    r.add_argument("--admin-secret", required=True)
    r.add_argument("--public-url", required=True)
    r.add_argument("--overlap", type=float, default=3600)
    r.add_argument("--started-at", default="2026-11-09T00:00:00Z")
    r.set_defaults(func=cmd_rotate)

    b = sub.add_parser("backup"); add_dir(b)
    b.add_argument("--backup", required=True)
    b.set_defaults(func=cmd_backup)

    rs = sub.add_parser("restore"); add_dir(rs)
    rs.add_argument("--backup", required=True)
    rs.set_defaults(func=cmd_restore)

    v = sub.add_parser("verify"); add_dir(v)
    v.add_argument("--url")
    v.add_argument("--admin-secret")
    v.set_defaults(func=cmd_verify)

    run = sub.add_parser("run"); add_dir(run)
    run.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
