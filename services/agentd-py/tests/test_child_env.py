"""Every backend subprocess gets child_env() (spec §3.5): a nested app started by an
agent or validator must refuse to start, not inherit the parent's port."""
from __future__ import annotations

import ast
from pathlib import Path

from agentd.child_env import child_env

ROOT = Path(__file__).resolve().parents[1] / "agentd"
_SUBPROCESS_ATTRS = {"run", "Popen", "call", "check_call", "check_output"}
_ASYNC_ATTRS = {"create_subprocess_exec", "create_subprocess_shell"}
# The MCP SDK builds stdio env from its own allowlist; pty_process receives the env its
# only caller (exec_sessions/manager.py) built with child_env().
_EXEMPT = {"mcp/client.py", "exec_sessions/pty_process.py"}


def test_exec_sessions_build_their_env_with_child_env() -> None:
    # The PTY spawn takes its env from manager.py, which never names subprocess itself,
    # so the enumeration below cannot see it.
    assert "env = child_env()" in (ROOT / "exec_sessions/manager.py").read_text()


def test_child_env_strips_listen_port_and_pid() -> None:
    env = child_env({"CRUCIBLE_LISTEN_PORT": "1", "CRUCIBLE_SERVE_PID": "2", "PATH": "/b"})
    assert env == {"PATH": "/b"}


def _references(tree: ast.AST) -> list[ast.Attribute]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "subprocess" and node.attr in _SUBPROCESS_ATTRS:
                out.append(node)
            if node.value.id == "asyncio" and node.attr in _ASYNC_ATTRS:
                out.append(node)
    return out


def test_every_subprocess_reference_uses_child_env() -> None:
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _EXEMPT:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)  # one tree: the identity check below needs it
        refs = _references(tree)
        if not refs:
            continue
        if "child_env(" not in source:
            offenders.append(f"{rel}: references subprocess but never calls child_env()")
        direct_calls = [n for n in ast.walk(tree)
                        if isinstance(n, ast.Call) and any(n.func is r for r in refs)]
        for call in direct_calls:
            if not any(kw.arg == "env" for kw in call.keywords):
                offenders.append(f"{rel}:{call.lineno}: subprocess call without env=")
    assert offenders == []
