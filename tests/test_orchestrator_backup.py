import json

from orchestrator import Orchestrator
from tools import backup_restore


def test_backup_restore_rehearsal(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    orch = Orchestrator.open(live / "orchestrator_state.sqlite3",
                             identity_path=live / "orchestrator_node_key.pem")
    orch.scheduling.save_round(5, state="open", started_at=100.0, global_step=4)
    orch.scheduling.create_pool("p1", 5, "ip:a")
    orch.admission.issue("ip:a", "round_5_p1", 5, pool_id="p1")
    orch.submission.record_submission(client_key="ip:a", round_no=5,
                                      content_sha256="a" * 64, tokens=42)
    key = orch.identity.public_key_hex
    orch.close()

    report = backup_restore.backup(live, tmp_path / "backup")
    assert (tmp_path / "backup" / "orchestrator_state.sqlite3").exists()
    backup_restore.restore(tmp_path / "backup", tmp_path / "restored")
    verified = backup_restore.verify(tmp_path / "restored")
    assert verified["round"] == 5
    assert verified["pools"] == 1 and verified["submissions"] == 1
    assert verified["identity"] == key[:16]
