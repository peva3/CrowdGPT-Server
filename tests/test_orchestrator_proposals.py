import pytest

from orchestrator.proposals import ProposalService
from orchestrator.state import StateStore
from orchestrator.submission import SubmissionService


def _store(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    return store, ProposalService(store), SubmissionService(store)


def test_verified_weight_controls_voting(tmp_path):
    store, props, subs = _store(tmp_path)
    p = props.create("Next improvement", "more eval", "user-a")
    with pytest.raises(PermissionError):
        props.vote_with_ledger(p["proposalId"], "user-b")
    subs.record_submission(client_key="ip:b", round_no=1, content_sha256="b" * 64,
                           tokens=700, user_uuid="user-b")
    tally = props.vote_with_ledger(p["proposalId"], "user-b")
    assert tally == {"count": 1, "weight": 700, "voters": ["user-b"]}
    # Voting again replaces the ballot instead of adding one.
    subs.record_submission(client_key="ip:b", round_no=2, content_sha256="c" * 64,
                           tokens=300, user_uuid="user-b")
    tally = props.vote_with_ledger(p["proposalId"], "user-b")
    assert tally == {"count": 1, "weight": 1000, "voters": ["user-b"]}
    store.close()


def test_create_validates_and_lists_newest_first(tmp_path):
    store, props, _ = _store(tmp_path)
    with pytest.raises(ValueError):
        props.create("", "x", "a")
    with pytest.raises(ValueError):
        props.create("t", "x" * 4001, "a")
    first = props.create("first", "", "a")
    second = props.create("second", "", "b")
    assert [p["proposalId"] for p in props.list()] == [second["proposalId"],
                                                       first["proposalId"]]
    store.close()
