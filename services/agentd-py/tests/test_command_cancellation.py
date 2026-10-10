import asyncio
import os
import sys

import pytest

from agentd.tools.shell import run_command


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group shell contract")
async def test_cancel_kills_command_and_pipeline_children(tmp_path):
    pidfile = tmp_path / "child.pid"
    child = (
        "import os,time;from pathlib import Path;"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(60)"
    )
    task = asyncio.create_task(
        run_command(
            command=sys.executable,
            args=["-c", child, "|", "cat"],
            shadow_root=tmp_path,
            real_workspace_path=tmp_path,
        )
    )
    try:
        for _ in range(200):
            if pidfile.exists():
                break
            await asyncio.sleep(0.01)
        assert pidfile.exists()
        pid = int(pidfile.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("pipeline child survived cancellation")
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
