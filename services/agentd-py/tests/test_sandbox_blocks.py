"""Recognizing sandbox blocks in command output (fixtures captured from real macOS
Seatbelt runs, paths substituted) and the exact text the agent gets."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from agentd.domain.models import ApprovalOutcome
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource
from agentd.sandbox.blocks import SandboxBlock, annotate, classify_block
from agentd.sandbox.policy import SandboxPolicy
from agentd.sandbox.runtime import SandboxRuntime
from agentd.tools.registry import ToolRegistry

WS = Path("/work/space")
HOME = Path("/Users/u")
POLICY = SandboxPolicy(
    workspace=WS, writable=(WS, Path("/private/tmp")), read_only=(WS / "AGENTS.md",),
    unreadable=(HOME / ".ssh",), proxy_port=9, proxy_socket=None)


def _classify(output: str, refused: list[str] | None = None, code: int | None = 1):
    return classify_block(output, code, POLICY, refused or [])


def test_refused_host_wins():
    out = "curl: (56) CONNECT tunnel failed, response 403"
    assert _classify(out, ["db.prod.example.com"]) == SandboxBlock("network", "db.prod.example.com")


def test_write_outside_detected():
    out = "touch: /Users/u/.zshrc: Operation not permitted"
    assert _classify(out) == SandboxBlock("write", "/Users/u/.zshrc")


def test_mkdir_outside_detected():
    out = "mkdir: /Users/u/planted: Operation not permitted"
    assert _classify(out) == SandboxBlock("write", "/Users/u/planted")


def test_read_only_file_detected():
    out = "/bin/sh: /work/space/AGENTS.md: Operation not permitted"
    assert _classify(out) == SandboxBlock("write", "/work/space/AGENTS.md")


def test_credential_read_detected():
    out = "cat: /Users/u/.ssh/id_rsa: Operation not permitted"
    assert _classify(out) == SandboxBlock("read", "/Users/u/.ssh/id_rsa")


def test_python_quoted_path_detected():
    out = "PermissionError: [Errno 1] Operation not permitted: '/Users/u/notes.txt'"
    assert _classify(out) == SandboxBlock("write", "/Users/u/notes.txt")


def test_linux_read_only_file_system_detected():
    out = "touch: cannot touch '/home/u/x': Read-only file system"
    assert _classify(out) == SandboxBlock("write", "/home/u/x")


def test_proxy_ignoring_tool_reported_as_direct_network():
    for out in ("ssh: connect to host 1.1.1.1 port 22: Operation not permitted",
                "curl: (7) Failed to connect to 1.1.1.1 port 80 after 0 ms: "
                "Couldn't connect to server",
                "curl: (6) Could not resolve host: example.com",
                "    sock.connect(sa)\nPermissionError: [Errno 1] Operation not permitted"):
        assert _classify(out) == SandboxBlock("direct_network", ""), out


def test_ordinary_permission_error_inside_workspace_not_relabelled():
    assert _classify("cat: /work/space/locked.txt: Permission denied") is None
    assert _classify("touch: /work/space/a.txt: Operation not permitted") is None


def test_local_connection_refused_not_relabelled():
    out = ("curl: (7) Failed to connect to localhost port 9999 after 0 ms: "
           "Couldn't connect to server")
    assert _classify(out) is None


def test_success_never_relabelled():
    assert _classify("touch: /Users/u/.zshrc: Operation not permitted", code=0) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="spawns Seatbelt processes")
@pytest.mark.asyncio
async def test_registry_annotates_blocked_command(tmp_path, probe):
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime)
    out = await registry.execute("run_command", {"command": "touch", "args": [str(probe)]})
    assert out.status == "sandbox_blocked"
    assert out.output.startswith(f"Blocked by the sandbox: write to {probe} is outside")
    ok = await registry.execute("run_command", {"command": "false", "args": []})
    assert ok.status != "sandbox_blocked" and "Blocked by the sandbox" not in ok.output


@pytest.mark.skipif(sys.platform != "darwin", reason="spawns Seatbelt processes")
@pytest.mark.asyncio
async def test_session_output_annotated(tmp_path, probe):
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    manager = SessionManager(tmp_path, registry_path=tmp_path / "sessions.json")
    source = ExecSessionToolSource(manager, "t1", _allow, sandbox_runtime=runtime)
    started = await source.execute("start_session", {"command": "/bin/sh", "args": [],
                                                     "yield_time_ms": 300})
    sid = started.output.split("session ")[1].split(" ")[0]
    out = await source.execute("write_stdin", {"session_id": sid,
                                               "chars": f"touch '{probe}'\n",
                                               "yield_time_ms": 1500})
    assert out.status == "sandbox_blocked"
    assert "Blocked by the sandbox: write to" in out.output
    assert "call start_session again with outside_sandbox" in out.output
    await manager.shutdown()


async def _allow(*_args, **_kwargs):
    return ApprovalOutcome.allow()


@pytest.fixture
def probe():
    path = Path.home() / f".crucible-sandbox-probe-{uuid.uuid4().hex}"
    yield path
    path.unlink(missing_ok=True)


def test_annotation_text_exact():
    assert annotate(SandboxBlock("write", "/Users/u/.zshrc"), "run_command") == (
        "Blocked by the sandbox: write to /Users/u/.zshrc is outside the allowed folders. "
        "To run this command outside the sandbox, call run_command again with "
        "outside_sandbox: true and a short reason.")
    assert annotate(SandboxBlock("read", "/Users/u/.ssh/id_rsa"), "run_command") == (
        "Blocked by the sandbox: reading /Users/u/.ssh/id_rsa is not allowed. To run this "
        "command outside the sandbox, call run_command again with outside_sandbox: true "
        "and a short reason.")
    assert annotate(SandboxBlock("network", "db.example.com"), "start_session") == (
        "Blocked by the sandbox: network access to db.example.com is not allowed. To allow "
        'this host, call start_session again with allow_hosts: ["db.example.com"] and a '
        "short reason.")
    assert annotate(SandboxBlock("direct_network", ""), "run_command") == (
        "Blocked by the sandbox: this command connected to the network directly instead of "
        "through the sandbox's proxy. To run it outside the sandbox, call run_command again "
        "with outside_sandbox: true and a short reason.")
