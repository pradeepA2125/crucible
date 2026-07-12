import json
import subprocess
import sys
import time

import pytest

from agentd.exec_sessions.registry_file import SessionRegistryFile

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix reap")


class _FakeSession:
    def __init__(self, sid, pid, pgid, thread_id, cmd, executable=None):
        self.session_id, self.thread_id = sid, thread_id
        self.command_line, self.started_at = cmd, time.time()
        self.executable = executable if executable is not None else cmd.split(" ", 1)[0]
        self.proc = type("P", (), {"pid": pid, "pgid": pgid})()


def test_record_and_clear_roundtrip(tmp_path):
    reg = SessionRegistryFile(tmp_path / "exec-sessions.json")
    reg.record([_FakeSession("s1", 123, 123, "t1", "sleep 60")])
    data = json.loads((tmp_path / "exec-sessions.json").read_text())
    assert data[0]["session_id"] == "s1" and data[0]["pgid"] == 123
    reg.clear()
    assert json.loads((tmp_path / "exec-sessions.json").read_text()) == []


def test_reap_kills_live_recorded_process(tmp_path):
    # A real detached sleeper standing in for a crashed backend's orphan.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(120)"],
        start_new_session=True)
    reg = SessionRegistryFile(tmp_path / "exec-sessions.json")
    reg.record([_FakeSession("s1", proc.pid, proc.pid, "t1",
                             f"{sys.executable} -c ...",
                             executable=sys.executable)])
    killed = SessionRegistryFile(tmp_path / "exec-sessions.json").reap_orphans()
    assert killed == 1
    time.sleep(0.3)
    assert proc.poll() is not None  # actually dead
    assert json.loads((tmp_path / "exec-sessions.json").read_text()) == []


def test_reap_skips_pid_reuse_mismatch(tmp_path):
    # Recorded command doesn't match what the pid runs now ⇒ must NOT kill.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(120)"],
        start_new_session=True)
    reg = SessionRegistryFile(tmp_path / "exec-sessions.json")
    reg.record([_FakeSession("s1", proc.pid, proc.pid, "t1",
                             "totally-different-binary --flag")])
    killed = reg.reap_orphans()
    assert killed == 0
    assert proc.poll() is None  # still alive
    proc.kill()


def test_reap_tolerates_missing_or_garbage_file(tmp_path):
    reg = SessionRegistryFile(tmp_path / "missing.json")
    assert reg.reap_orphans() == 0
    (tmp_path / "bad.json").write_text("{not json")
    assert SessionRegistryFile(tmp_path / "bad.json").reap_orphans() == 0
