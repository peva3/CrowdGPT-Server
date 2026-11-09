import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "orchestrator_cli.py"


def run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd,
                          capture_output=True, text=True)


def test_init_doctor_and_invite_join(tmp_path):
    issuer = tmp_path / "issuer"
    joining = tmp_path / "joining"
    init = run("init", "--dir", str(issuer), "--public-url", "https://issuer.example")
    assert init.returncode == 0, init.stderr
    payload = json.loads(init.stdout)
    assert payload["nodeId"].startswith("node-")
    assert (issuer / "orchestrator_node_key.pem").stat().st_mode & 0o077 == 0

    doctor = run("doctor", "--dir", str(issuer))
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert json.loads(doctor.stdout)["ok"] is True

    invite_path = tmp_path / "invite.json"
    invite = run("invite", "--dir", str(issuer), "--endpoint", "https://issuer.example",
                 "--out", str(invite_path))
    assert invite.returncode == 0, invite.stderr
    token = json.loads(invite_path.read_text())
    assert token["schema"] == "ng-join-1"

    join = run("init", "--dir", str(joining), "--public-url", "https://joining.example")
    assert join.returncode == 0, join.stderr
    joined = run("join", "--dir", str(joining), "--token", str(invite_path),
                 "--trusted-pubkey", payload["pubkey"], "--offline")
    assert joined.returncode == 0, joined.stdout + joined.stderr
    peers = json.loads((joining / "peers.json").read_text())
    assert peers[0]["endpoint"] == "https://issuer.example"

    bad = run("join", "--dir", str(joining), "--token", str(invite_path),
              "--trusted-pubkey", "0" * 64, "--offline")
    assert bad.returncode != 0
    assert "not trusted" in bad.stderr or "not trusted" in bad.stdout


def test_cli_backup_restore_round_trip(tmp_path):
    from orchestrator import Orchestrator
    node = tmp_path / "node"
    assert run("init", "--dir", str(node)).returncode == 0
    orch = Orchestrator.open(node / "orchestrator_state.sqlite3",
                             identity_path=node / "orchestrator_node_key.pem")
    orch.scheduling.save_round(3, state="open", started_at=1.0, global_step=2)
    orch.close()
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    b = run("backup", "--dir", str(node), "--backup", str(backup))
    assert b.returncode == 0, b.stderr
    r = run("restore", "--dir", str(restored), "--backup", str(backup))
    assert r.returncode == 0, r.stderr
    assert (restored / "orchestrator_state.sqlite3").exists()
