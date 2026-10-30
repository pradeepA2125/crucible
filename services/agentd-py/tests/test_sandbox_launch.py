"""Commands and terminal sessions run inside the sandbox (macOS: real processes).

pytest's tmp_path is under the system temp folder, which sandboxed commands may write,
so "outside" here is a probe file in the real home folder, always cleaned up.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from agentd.exec_sessions.manager import SessionManager
from agentd.sandbox.runtime import SandboxRuntime
from agentd.tools.registry import ToolRegistry
from agentd.tools.shell import run_command

pytestmark = pytest.mark.skipif(sys.platform != "darwin" and shutil.which("bwrap") is None,
                                reason="needs Seatbelt or bubblewrap")


@pytest.fixture
def probe():
    path = Path.home() / f".crucible-sandbox-probe-{uuid.uuid4().hex}"
    yield path
    path.unlink(missing_ok=True)


async def _runtime(ws: Path, *, proxy: bool = False) -> SandboxRuntime:
    runtime = SandboxRuntime(ws)
    await (runtime.start() if proxy else runtime.probe())
    assert runtime.active()
    return runtime


@pytest.mark.asyncio
async def test_run_command_write_outside_blocked(tmp_path, probe):
    runtime = await _runtime(tmp_path)
    launch = runtime.launch_for(shadow_root=None)
    out = await run_command(command="touch", args=[str(probe)], shadow_root=tmp_path,
                            real_workspace_path=tmp_path, sandbox=launch)
    runtime.release(launch)
    assert out.is_error and out.sandboxed
    assert not probe.exists()


@pytest.mark.asyncio
async def test_run_command_in_shadow_can_write_shadow(tmp_path):
    shadow = tmp_path / ".crucible" / "state" / "shadows" / "task-1"
    shadow.mkdir(parents=True)
    runtime = await _runtime(tmp_path)
    launch = runtime.launch_for(shadow_root=shadow)
    ok = await run_command(command="touch", args=["made.txt"], shadow_root=shadow,
                           real_workspace_path=tmp_path, sandbox=launch)
    blocked = await run_command(
        command="touch", args=[str(tmp_path / ".crucible" / "state" / "other.txt")],
        shadow_root=shadow, real_workspace_path=tmp_path, sandbox=launch)
    runtime.release(launch)
    assert not ok.is_error and (shadow / "made.txt").exists()
    assert blocked.is_error and not (tmp_path / ".crucible" / "state" / "other.txt").exists()


@pytest.mark.asyncio
async def test_run_command_env_has_no_token(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_not_leak")
    runtime = await _runtime(tmp_path, proxy=True)
    launch = runtime.launch_for(shadow_root=None)
    out = await run_command(command="env", args=[], shadow_root=tmp_path,
                            real_workspace_path=tmp_path, sandbox=launch)
    runtime.release(launch)
    await runtime.close()
    assert "ghp_should_not_leak" not in out.output
    assert f"HTTPS_PROXY=http://{launch.lease.token}:x@127.0.0.1:" in out.output


@pytest.mark.asyncio
async def test_lease_released_after_command(tmp_path):
    runtime = await _runtime(tmp_path, proxy=True)
    launch = runtime.launch_for(shadow_root=None)
    assert launch is not None and launch.lease is not None
    runtime.release(launch)
    assert launch.lease.token not in runtime.proxy._leases  # noqa: SLF001
    await runtime.close()


@pytest.mark.asyncio
async def test_unavailable_runs_unwrapped(tmp_path):
    runtime = SandboxRuntime(tmp_path, platform="win32")
    await runtime.probe()
    assert runtime.launch_for(shadow_root=None) is None
    out = await run_command(command="echo", args=["hi"], shadow_root=tmp_path,
                            real_workspace_path=tmp_path, sandbox=None)
    assert not out.sandboxed and "hi" in out.output


@pytest.mark.asyncio
async def test_registry_runs_commands_sandboxed(tmp_path, probe):
    runtime = await _runtime(tmp_path)
    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime)
    out = await registry.execute("run_command", {"command": "touch", "args": [str(probe)]})
    assert out.is_error and out.sandboxed and not probe.exists()
    ok = await registry.execute("run_command", {"command": "touch", "args": ["in.txt"]})
    assert not ok.is_error and (tmp_path / "in.txt").exists()


@pytest.mark.asyncio
async def test_session_keeps_wall_after_toggle_off(tmp_path, probe):
    runtime = await _runtime(tmp_path)
    manager = SessionManager(tmp_path, registry_path=tmp_path / "sessions.json")
    first = await manager.start("t1", "/bin/sh", [], None, 300,
                                sandbox=runtime.launch_for(shadow_root=None))
    runtime.enabled = False
    await manager.write_stdin("t1", first.session_id, f"touch '{probe}'; echo done\n", 1500)
    assert not probe.exists()
    second = await manager.start("t1", "/bin/sh", [], None, 300,
                                 sandbox=runtime.launch_for(shadow_root=None))
    await manager.write_stdin("t1", second.session_id, f"touch '{probe}'; echo done\n", 1500)
    await asyncio.sleep(0.2)
    assert probe.exists()
    await manager.shutdown()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.asyncio
async def test_node_set_to_use_the_system_ca_store_does_not_crash(tmp_path):
    # The Keychain is denied, and Node segfaults when it can't read it (found with npm install).
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    launch = runtime.launch_for(shadow_root=None)
    connect = "require('tls').connect({host:'127.0.0.1',port:1}).on('error',()=>{})"
    done = await asyncio.to_thread(
        subprocess.run, launch.wrap(["node", "-e", connect]), capture_output=True, text=True,
        timeout=30, env=launch.env({**os.environ, "NODE_USE_SYSTEM_CA": "1"}), cwd=tmp_path)
    assert done.returncode == 0, done.stderr


@pytest.mark.asyncio
async def test_ctrl_c_stops_a_command_in_a_sandboxed_session(tmp_path):
    runtime = await _runtime(tmp_path)
    manager = SessionManager(tmp_path, registry_path=tmp_path / "sessions.json")
    session = await manager.start("t1", "/bin/sh", [], None, 300,
                                  sandbox=runtime.launch_for(shadow_root=None))
    await manager.write_stdin("t1", session.session_id, "sleep 20\n", 800)
    await manager.write_stdin("t1", session.session_id, "\x03", 800)
    read = await manager.write_stdin("t1", session.session_id, "echo alive-$((1+1))\n", 1500)
    assert "alive-2" in read.new_output
    await manager.shutdown()
