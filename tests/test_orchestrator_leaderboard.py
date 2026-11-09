from orchestrator.leaderboard import LeaderboardService
from orchestrator.state import StateStore


def test_verified_leaderboard_counts_stored_submissions_once(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    sub = __import__("orchestrator.submission", fromlist=["SubmissionService"]).SubmissionService(store)
    sub.record_submission(client_key="ip:a", round_no=1, content_sha256="a" * 64,
                          tokens=1000, user_uuid="user-a")
    sub.record_submission(client_key="ip:a", round_no=1, content_sha256="a" * 64,
                          tokens=1000, user_uuid="user-a")  # duplicate content
    sub.record_submission(client_key="ip:a", round_no=2, content_sha256="b" * 64,
                          tokens=500, user_uuid="user-a")
    sub.record_submission(client_key="ip:b", round_no=1, content_sha256="c" * 64,
                          tokens=999999, status="rejected")
    sub.record_submission(client_key="ip:c", round_no=1, content_sha256="d" * 64,
                          tokens=250)  # anonymous
    board = LeaderboardService(store).verified()
    assert board["source"] == "verified"
    by_actor = {e["actor"]: e for e in board["entries"]}
    assert by_actor["user-a"]["tokens"] == 1500
    assert by_actor["user-a"]["submissions"] == 2
    assert by_actor["user-a"]["rounds"] == 2
    assert "ip:b" not in " ".join(by_actor)  # rejected work is excluded
    assert by_actor["client:ip:c"]["identity"] == "anonymous"
    assert board["totals"]["tokens"] == 1750
    store.close()
