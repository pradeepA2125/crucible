"""macOS Seatbelt wrapper: real sandboxed processes.

Policies are built by hand: pytest's tmp_path lives under the system temp folder, which
the real policy makes writable, so "outside" here means a sibling folder of the workspace.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from agentd.sandbox.policy import SandboxPolicy
from agentd.sandbox.seatbelt import seatbelt_argv, seatbelt_profile

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS only")


def _setup(tmp_path: Path, *, extra_writable: tuple[Path, ...] = ()):
    ws = (tmp_path / "work space").resolve()
    home = (tmp_path / "home").resolve()
    (ws / ".crucible").mkdir(parents=True)
    (home / ".ssh").mkdir(parents=True)
    (ws / "AGENTS.md").write_text("rules\n")
    (home / ".ssh" / "id_rsa").write_text("secret\n")
    policy = SandboxPolicy(
        workspace=ws, writable=(ws, *extra_writable), read_only=(ws / "AGENTS.md",),
        unreadable=(home / ".ssh",), proxy_port=None, proxy_socket=None)
    return policy, ws, home


def _run(policy: SandboxPolicy, *argv: str, cwd: Path | None = None):
    return subprocess.run(seatbelt_argv(policy, list(argv)), cwd=cwd, capture_output=True,
                          text=True, timeout=30)


def test_write_inside_workspace_allowed(tmp_path):
    policy, ws, _home = _setup(tmp_path)
    assert _run(policy, "/usr/bin/touch", str(ws / "a.txt")).returncode == 0
    assert (ws / "a.txt").exists()


def test_write_outside_denied(tmp_path):
    policy, _ws, home = _setup(tmp_path)
    result = _run(policy, "/usr/bin/touch", str(home / "x"))
    assert result.returncode != 0
    assert "Operation not permitted" in result.stderr
    assert not (home / "x").exists()


def test_read_only_file_inside_workspace_denied(tmp_path):
    policy, ws, _home = _setup(tmp_path)
    result = _run(policy, "/bin/sh", "-c", f"echo hacked > '{ws / 'AGENTS.md'}'")
    assert result.returncode != 0
    assert (ws / "AGENTS.md").read_text() == "rules\n"


def test_unreadable_credentials_denied(tmp_path):
    policy, _ws, home = _setup(tmp_path)
    result = _run(policy, "/bin/cat", str(home / ".ssh" / "id_rsa"))
    assert result.returncode != 0
    assert "secret" not in result.stdout


def test_symlink_out_of_workspace_denied(tmp_path):
    policy, ws, home = _setup(tmp_path)
    (ws / "link").symlink_to(home)
    result = _run(policy, "/usr/bin/touch", str(ws / "link" / "planted"))
    assert result.returncode != 0
    assert not (home / "planted").exists()


def test_private_tmp_alias_writable(tmp_path):
    policy, _ws, _home = _setup(tmp_path, extra_writable=(Path("/private/tmp"),))
    name = f"/tmp/crucible-sandbox-{uuid.uuid4().hex}"
    try:
        assert _run(policy, "/usr/bin/touch", name).returncode == 0
    finally:
        Path(name).unlink(missing_ok=True)


def test_direct_network_denied(tmp_path):
    policy, _ws, _home = _setup(tmp_path)
    code = ("import socket\n"
            "try:\n socket.create_connection(('1.1.1.1', 80), timeout=5)\n"
            "except PermissionError: print('EPERM')\n"
            "except OSError as e: print('OTHER', e)\n")
    result = _run(policy, sys.executable, "-c", code)
    assert "EPERM" in result.stdout, result.stdout + result.stderr


def test_loopback_server_and_client_allowed(tmp_path):
    policy, _ws, _home = _setup(tmp_path)
    code = ("import socket\n"
            "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen()\n"
            "c = socket.create_connection(s.getsockname(), timeout=5)\n"
            "conn, _ = s.accept(); c.sendall(b'hi'); print(conn.recv(2).decode())\n")
    result = _run(policy, sys.executable, "-c", code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "hi"


def test_profile_escapes_quotes(tmp_path):
    odd = (tmp_path / 'we"ird\\dir').resolve()
    odd.mkdir()
    policy = SandboxPolicy(workspace=odd, writable=(odd,), read_only=(), unreadable=(),
                           proxy_port=None, proxy_socket=None)
    escaped = str(odd).replace("\\", "\\\\").replace('"', '\\"')
    assert f'"{escaped}"' in seatbelt_profile(policy)
    assert _run(policy, "/usr/bin/touch", str(odd / "ok")).returncode == 0


def test_keychain_unreachable(tmp_path):
    policy, _ws, _home = _setup(tmp_path)
    assert _run(policy, "/usr/bin/security", "list-keychains").returncode != 0


def test_python_and_git_run_inside(tmp_path):
    policy, ws, _home = _setup(tmp_path)
    assert _run(policy, sys.executable, "-c", "print(1)").returncode == 0
    assert _run(policy, "/usr/bin/git", "init", "-q", cwd=ws).returncode == 0
