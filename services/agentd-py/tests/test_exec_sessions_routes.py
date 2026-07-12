"""Route-level tests of the /live sessions fill + transcript route.

These use a stub manager, deliberately (the stub-ControllerUI pattern): they
test the view seam (route → manager → JSON) while real process behavior is
covered by test_exec_sessions_manager.py. Two hazards the stub avoids:
(a) driving a sync TestClient from inside an async test can deadlock the anyio
portal — these tests stay sync; (b) a real PtyProcess needs a running event
loop for add_reader, which a sync test doesn't have.
"""
import sys

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")


class _StubExecManager:
    """View-seam stub; real process behavior is Task 3's coverage."""

    def live_summaries(self, thread_id):
        return [{"id": "sess-1", "command": "python -m http.server",
                 "status": "running", "exit_code": None,
                 "started_at": 1_720_000_000.0}]

    def transcript(self, thread_id, session_id):
        if session_id != "sess-1":
            return None
        return {"output_tail": "serving", "stdin_history": [],
                "status": "running", "exit_code": None}


@pytest.fixture()
def app_with_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", "1")
    from agentd.chat.app_factory import build_app
    return build_app(workspace_path=str(tmp_path))


def test_live_carries_sessions_and_transcript_roundtrip(app_with_sessions, tmp_path):
    client = TestClient(app_with_sessions)
    thread = client.post("/v1/chat/threads",
                         json={"workspace": str(tmp_path), "title": "t"}).json()
    tid = thread["thread_id"]
    app_with_sessions.state.chat_agent._exec_sessions = _StubExecManager()

    live = client.get(f"/v1/chat/threads/{tid}/live").json()
    assert live["sessions"] and live["sessions"][0]["status"] == "running"
    row = live["sessions"][0]
    # /live rows are the STABLE shape — a ticking age_sec/unread_bytes here
    # would churn the webview's lastLiveSignature at 1 Hz (review fix #2).
    assert set(row) == {"id", "command", "status", "exit_code", "started_at"}
    sid = row["id"]

    t = client.get(f"/v1/chat/threads/{tid}/sessions/{sid}/transcript").json()
    assert "serving" in t["output_tail"] and t["status"] == "running"

    missing = client.get(f"/v1/chat/threads/{tid}/sessions/sess-nope/transcript")
    assert missing.status_code == 404


def test_transcript_404_when_feature_off(app_with_sessions, tmp_path):
    client = TestClient(app_with_sessions)
    thread = client.post("/v1/chat/threads",
                         json={"workspace": str(tmp_path), "title": "t"}).json()
    tid = thread["thread_id"]
    # No _exec_sessions on the handler (feature off) → 404, and /live omits rows.
    resp = client.get(f"/v1/chat/threads/{tid}/sessions/sess-1/transcript")
    assert resp.status_code == 404
    live = client.get(f"/v1/chat/threads/{tid}/live").json()
    assert live["sessions"] is None


def test_config_reports_flag(app_with_sessions):
    client = TestClient(app_with_sessions)
    assert client.get("/v1/config").json()["exec_sessions_enabled"] is True
