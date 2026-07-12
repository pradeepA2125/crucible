import asyncio
import sys

import pytest

from agentd.exec_sessions.manager import (
    SessionCapError,
    SessionManager,
    SessionNotFoundError,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")
PY = sys.executable


@pytest.mark.asyncio
async def test_fast_command_completes_within_yield(tmp_path):
    m = SessionManager(tmp_path)
    r = await m.start("t1", PY, ["-c", "print('done fast')"], None, 5000)
    assert r.still_running is False
    assert r.status == "exited"
    assert r.exit_code == 0
    assert "done fast" in r.new_output
    await m.shutdown()


@pytest.mark.asyncio
async def test_long_runner_returns_session_id_and_cursor_poll(tmp_path):
    m = SessionManager(tmp_path)
    code = ("import time;print('first',flush=True);time.sleep(0.8);"
            "print('second',flush=True);time.sleep(60)")
    r = await m.start("t1", PY, ["-u", "-c", code], None, 400)
    assert r.still_running is True and r.status == "running"
    assert "first" in r.new_output
    sid = r.session_id
    r2 = await m.write_stdin("t1", sid, "", 900)  # pure poll
    assert "second" in r2.new_output
    assert "first" not in r2.new_output  # cursor: only NEW output
    k = await m.kill("t1", sid)
    assert k.status == "exited"
    assert m.list_sessions("t1") == []  # killed sessions drop
    await m.shutdown()


@pytest.mark.asyncio
async def test_write_stdin_sends_input(tmp_path):
    m = SessionManager(tmp_path)
    r = await m.start("t1", PY, ["-u", "-c", "print(input(),'!',flush=True)"], None, 300)
    assert r.still_running is True
    r2 = await m.write_stdin("t1", r.session_id, "hey\n", 3000)
    assert "hey" in r2.new_output and "!" in r2.new_output
    await m.shutdown()


@pytest.mark.asyncio
async def test_unknown_session_raises(tmp_path):
    m = SessionManager(tmp_path)
    with pytest.raises(SessionNotFoundError):
        await m.write_stdin("t1", "nope", "", 300)


@pytest.mark.asyncio
async def test_cap_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSION_MAX_COUNT", "1")
    m = SessionManager(tmp_path)
    await m.start("t1", PY, ["-c", "import time;time.sleep(60)"], None, 300)
    with pytest.raises(SessionCapError):
        await m.start("t1", PY, ["-c", "import time;time.sleep(60)"], None, 300)
    await m.shutdown()


@pytest.mark.asyncio
async def test_exited_session_readable_then_dropped_after_read(tmp_path):
    m = SessionManager(tmp_path)
    code = "import time;time.sleep(0.6);print('parting words',flush=True)"
    r = await m.start("t1", PY, ["-u", "-c", code], None, 300)
    sid = r.session_id
    await asyncio.sleep(1.0)  # process exits after start returned
    listed = m.list_sessions("t1")
    assert listed and listed[0]["status"] == "exited"
    r2 = await m.write_stdin("t1", sid, "", 300)  # read final output
    assert "parting words" in r2.new_output
    assert m.list_sessions("t1") == []  # cursor at end ⇒ dropped
    await m.shutdown()


@pytest.mark.asyncio
async def test_transcript_does_not_advance_model_cursor(tmp_path):
    m = SessionManager(tmp_path)
    code = "import time;time.sleep(0.4);print('for the model',flush=True);time.sleep(60)"
    r = await m.start("t1", PY, ["-u", "-c", code], None, 300)
    await asyncio.sleep(0.8)
    t = m.transcript("t1", r.session_id)
    assert t is not None and "for the model" in t["output_tail"]
    r2 = await m.write_stdin("t1", r.session_id, "", 300)
    assert "for the model" in r2.new_output  # inspect did NOT consume it
    await m.shutdown()


@pytest.mark.asyncio
async def test_stdin_history_recorded(tmp_path):
    m = SessionManager(tmp_path)
    r = await m.start("t1", PY, ["-c", "import time;time.sleep(60)"], None, 300)
    await m.write_stdin("t1", r.session_id, "abc\n", 300)
    t = m.transcript("t1", r.session_id)
    assert [e["chars"] for e in t["stdin_history"]] == ["abc\n"]
    await m.shutdown()


@pytest.mark.asyncio
async def test_thread_scoping(tmp_path):
    m = SessionManager(tmp_path)
    r = await m.start("t1", PY, ["-c", "import time;time.sleep(60)"], None, 300)
    assert m.list_sessions("t2") == []
    with pytest.raises(SessionNotFoundError):
        await m.write_stdin("t2", r.session_id, "", 300)  # other thread can't touch it
    await m.shutdown()


@pytest.mark.asyncio
async def test_ring_buffer_overflow_drops_oldest_with_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSION_BUFFER_BYTES", "2048")
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSION_RESULT_MAX_CHARS", "1000000")
    m = SessionManager(tmp_path)
    code = "print('x'*8000,flush=True);print('TAIL_SENTINEL',flush=True);import time;time.sleep(60)"
    r = await m.start("t1", PY, ["-u", "-c", code], None, 1200)
    out = r.new_output
    assert "TAIL_SENTINEL" in out
    assert "[... output dropped]" in out
    await m.shutdown()


def test_ring_buffer_marker_when_overflow_is_the_last_append():
    """Regression (v0.5.0 CI): Linux PTYs deliver a whole burst in ONE read, so
    the overflow happens on the LAST append and an in-buffer marker written on
    the next append never materializes. The marker must be injected at serving
    time for any cursor below the evicted boundary."""
    from agentd.exec_sessions.manager import RingBuffer

    buf = RingBuffer(cap=64)
    buf.append(b"x" * 200 + b"TAIL")  # single oversized chunk, nothing after
    text, cursor = buf.read_from(0)
    assert "[... output dropped]" in text
    assert "TAIL" in text
    assert cursor == buf.end
    # A reader that missed nothing sees no marker.
    again, _ = buf.read_from(cursor)
    assert again == ""
    # The transcript tail also flags the drop.
    assert "[... output dropped]" in buf.tail(1000)


# ── review-fix regression guards (dry-run 2026-07-12) ──────────────────────

@pytest.mark.asyncio
async def test_whole_line_command_is_split(tmp_path):
    """Models pack the whole line into `command` — _split_command must recover
    (exec does no word-splitting; without this every such spawn FileNotFoundErrors)."""
    m = SessionManager(tmp_path)
    r = await m.start("t1", f"{PY} -c print('split_ok')", [], None, 5000)
    assert r.exit_code == 0 and "split_ok" in r.new_output
    await m.shutdown()


@pytest.mark.asyncio
async def test_child_env_points_at_workspace_venv(tmp_path):
    """os.environ.copy() must not leak the backend's own VIRTUAL_ENV."""
    m = SessionManager(tmp_path)
    r = await m.start(
        "t1", PY, ["-c", "import os;print('VENV='+os.environ.get('VIRTUAL_ENV',''))"],
        None, 5000)
    assert f"VENV={tmp_path}" in r.new_output.replace("\r", "")
    await m.shutdown()


@pytest.mark.asyncio
async def test_literal_escape_sequences_decoded(tmp_path):
    """A model following the \\x03 teaching sends backslash-x-0-3 as four
    literal characters (JSON can't carry raw control bytes) — write_stdin must
    decode it to a real Ctrl-C."""
    m = SessionManager(tmp_path)
    r = await m.start("t1", PY, ["-c", "import time;time.sleep(60)"], None, 300)
    r2 = await m.write_stdin("t1", r.session_id, "\\x03", 5000)
    assert r2.status == "exited" and r2.exit_code != 0
    await m.shutdown()


@pytest.mark.asyncio
async def test_live_summaries_rows_are_stable(tmp_path):
    """/live rows must serialize identically while nothing real changes —
    age_sec/unread_bytes churn would defeat lastLiveSignature at 1 Hz."""
    m = SessionManager(tmp_path)
    code = "import time\nfor i in range(999):\n print(i,flush=True)\n time.sleep(0.05)"
    await m.start("t1", PY, ["-u", "-c", code], None, 300)
    a = m.live_summaries("t1")
    await asyncio.sleep(1.1)  # more output emitted, more age elapsed
    b = m.live_summaries("t1")
    assert a == b
    assert set(a[0]) == {"id", "command", "status", "exit_code", "started_at"}
    await m.shutdown()
