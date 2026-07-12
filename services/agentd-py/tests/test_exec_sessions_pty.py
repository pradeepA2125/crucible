import asyncio
import os
import sys

import pytest

from agentd.exec_sessions.pty_process import PtyProcess

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix pty tests")


async def _spawn(code: str, tmp_path, chunks: list[bytes]) -> PtyProcess:
    return await PtyProcess.spawn(
        sys.executable, ["-u", "-c", code], cwd=tmp_path,
        env=dict(os.environ), on_output=chunks.append)


@pytest.mark.asyncio
async def test_fast_command_exits_and_captures_output(tmp_path):
    chunks: list[bytes] = []
    proc = await _spawn("print('hello pty')", tmp_path, chunks)
    assert await proc.wait(timeout_sec=10) is True
    assert proc.exit_code() == 0
    # NO sleep here — wait()==True guarantees drain-on-exit already flushed
    # the final chunk. A sleep would mask the drain race this test guards.
    assert b"hello pty" in b"".join(chunks)
    proc.close()


@pytest.mark.asyncio
async def test_repeated_waits_are_cheap_and_idempotent(tmp_path):
    # Waiter-task hygiene: many timed-out polls must not error or leak;
    # wait() after exit keeps returning True.
    chunks: list[bytes] = []
    proc = await _spawn("import time; time.sleep(1.0)", tmp_path, chunks)
    for _ in range(5):
        assert await proc.wait(timeout_sec=0.05) is False
    assert await proc.wait(timeout_sec=10) is True
    assert await proc.wait(timeout_sec=0.05) is True
    proc.close()


@pytest.mark.asyncio
async def test_long_runner_still_running_then_group_kill_reaps_grandchild(tmp_path):
    chunks: list[bytes] = []
    # Parent spawns a child sleeper (a grandchild of us) then sleeps itself.
    code = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "print('CHILD',p.pid,flush=True);time.sleep(60)"
    )
    proc = await _spawn(code, tmp_path, chunks)
    assert await proc.wait(timeout_sec=1.0) is False
    assert proc.is_running() is True
    await asyncio.sleep(0.3)
    line = b"".join(chunks).decode()
    grandchild = int(line.split("CHILD", 1)[1].split()[0])
    await proc.kill(grace_sec=0.5)
    assert proc.is_running() is False
    await asyncio.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)  # group kill must have reaped it
    proc.close()


@pytest.mark.asyncio
async def test_write_reaches_stdin(tmp_path):
    chunks: list[bytes] = []
    proc = await _spawn("print(input(), 'echoed', flush=True)", tmp_path, chunks)
    await asyncio.sleep(0.3)
    proc.write("ping\n")
    assert await proc.wait(timeout_sec=10) is True
    assert b"echoed" in b"".join(chunks)  # drain-on-exit: no sleep needed
    proc.close()


@pytest.mark.asyncio
async def test_ctrl_c_interrupts(tmp_path):
    chunks: list[bytes] = []
    proc = await _spawn("import time; time.sleep(60)", tmp_path, chunks)
    await asyncio.sleep(0.3)
    proc.write("\x03")  # Ctrl-C via the PTY line discipline
    assert await proc.wait(timeout_sec=10) is True
    assert proc.exit_code() != 0
    proc.close()
