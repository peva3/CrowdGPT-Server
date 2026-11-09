import time

from orchestrator.outbox import Outbox
from orchestrator.state import StateStore


def test_outbox_survives_restart_and_retries_with_backoff(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(db)
    box = Outbox(store, max_backoff_s=60)
    row = box.enqueue("ticket", {"round": 1, "ticket": "t"})
    assert box.pending() == 1
    assert [r.id for r in box.due()] == [row]
    store.close()

    reopened = Outbox(StateStore(db), max_backoff_s=60)
    assert reopened.pending() == 1
    calls = []

    def fail(kind, payload):
        calls.append((kind, payload))
        return False

    now = time.time()
    out = reopened.drain(fail, now=now)
    assert out == {"delivered": 0, "retried": 1}
    assert calls == [("ticket", {"round": 1, "ticket": "t"})]
    assert reopened.due(now=now + 0.1) == []
    assert len(reopened.due(now=now + 10)) == 1


def test_outbox_delivers_once_and_purges(tmp_path):
    box = Outbox(StateStore(tmp_path / "state.sqlite3"))
    row = box.enqueue("round", {"round": 2})
    assert box.drain(lambda k, p: True)["delivered"] == 1
    assert box.pending() == 0
    assert box.due() == []
    assert box.purge_delivered(older_than_s=-1) == 1
    assert not box.store.query("SELECT * FROM peer_outbox WHERE id=?", (row,))
