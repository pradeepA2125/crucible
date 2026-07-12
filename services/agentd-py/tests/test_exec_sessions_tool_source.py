import sys

import pytest

from agentd.domain.models import CommandDecision
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")
PY = sys.executable


def _source(tmp_path, approve=True, calls=None):
    async def cb(command, args, cwd):
        if calls is not None:
            calls.append((command, args, cwd))
        return CommandDecision(approve=approve)
    return ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)


def test_definitions_names(tmp_path):
    names = [d.name for d in _source(tmp_path).definitions()]
    assert names == ["start_session", "write_stdin", "kill_session", "list_sessions"]


@pytest.mark.asyncio
async def test_start_gated_and_fast_command_runs(tmp_path):
    calls = []
    src = _source(tmp_path, approve=True, calls=calls)
    out = await src.execute("start_session", {
        "command": PY, "args": ["-c", "print('ok')"], "yield_time_ms": 5000})
    assert not out.is_error and "ok" in out.output and "exit code: 0" in out.output
    assert calls and calls[0][0] == PY  # gate consulted


@pytest.mark.asyncio
async def test_start_rejected_is_error_no_spawn(tmp_path):
    src = _source(tmp_path, approve=False)
    out = await src.execute("start_session", {"command": PY, "args": ["-c", "print(1)"]})
    assert out.is_error and "rejected" in out.output.lower()
    assert "(none)" in (await src.execute("list_sessions", {})).output


@pytest.mark.asyncio
async def test_long_runner_roundtrip_poll_kill(tmp_path):
    src = _source(tmp_path)
    out = await src.execute("start_session", {
        "command": PY,
        "args": ["-u", "-c", "import time;print('up',flush=True);time.sleep(60)"],
        "yield_time_ms": 600})
    assert "still running" in out.output.lower() and "sess-" in out.output
    sid = "sess-" + out.output.split("sess-", 1)[1].split()[0].strip(".,:]")
    listed = await src.execute("list_sessions", {})
    assert sid in listed.output and "running" in listed.output
    poll = await src.execute("write_stdin", {"session_id": sid, "chars": ""})
    assert not poll.is_error
    killed = await src.execute("kill_session", {"session_id": sid})
    assert not killed.is_error
    assert "exited" in killed.output or "killed" in killed.output


@pytest.mark.asyncio
async def test_write_stdin_ungated(tmp_path):
    calls = []
    src = _source(tmp_path, approve=True, calls=calls)
    out = await src.execute("start_session", {
        "command": PY, "args": ["-c", "import time;time.sleep(60)"],
        "yield_time_ms": 300})
    sid = "sess-" + out.output.split("sess-", 1)[1].split()[0].strip(".,:]")
    await src.execute("write_stdin", {"session_id": sid, "chars": "x\n"})
    assert len(calls) == 1  # only the start was gated


@pytest.mark.asyncio
async def test_unknown_session_is_error_not_raise(tmp_path):
    out = await _source(tmp_path).execute("write_stdin", {"session_id": "sess-zzz"})
    assert out.is_error and "No session" in out.output


@pytest.mark.asyncio
async def test_oversized_stdin_rejected(tmp_path):
    """A blocked event loop is the failure mode: a huge write into a full PTY
    buffer would freeze the backend — reject before it reaches the fd."""
    src = _source(tmp_path)
    out = await src.execute("start_session", {
        "command": PY, "args": ["-c", "import time;time.sleep(60)"],
        "yield_time_ms": 300})
    sid = "sess-" + out.output.split("sess-", 1)[1].split()[0].strip(".,:]")
    res = await src.execute("write_stdin", {"session_id": sid, "chars": "x" * 5000})
    assert res.is_error and "too large" in res.output.lower()
