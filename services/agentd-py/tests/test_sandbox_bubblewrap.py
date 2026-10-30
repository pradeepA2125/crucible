"""Linux bubblewrap wrapper and the network bridge.

Argument building and the bridge run everywhere; real sandboxed processes run only where
`bwrap` works (Linux with unprivileged user namespaces — CI installs it)."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from agentd.sandbox import netbridge
from agentd.sandbox.bubblewrap import bwrap_argv
from agentd.sandbox.policy import SandboxPolicy

BWRAP = "/usr/bin/bwrap"


def _policy(tmp_path: Path, *, proxy: bool = False) -> tuple[SandboxPolicy, Path, Path]:
    ws = (tmp_path / "work space").resolve()
    home = (tmp_path / "home").resolve()
    (ws / ".crucible" / "state" / "shadows" / "t1").mkdir(parents=True)
    (home / ".ssh").mkdir(parents=True)
    (home / ".netrc").write_text("machine x\n")
    (ws / "AGENTS.md").write_text("rules\n")
    shadow = ws / ".crucible" / "state" / "shadows" / "t1"
    policy = SandboxPolicy(
        workspace=ws, writable=(ws, shadow),
        read_only=(ws / "AGENTS.md", ws / ".crucible" / "state"),
        unreadable=(home / ".ssh", home / ".netrc", home / "absent"),
        proxy_port=8877 if proxy else None, proxy_socket=tmp_path / "proxy.sock" if proxy else None)
    return policy, ws, home


def _pairs(argv: list[str], flag: str) -> list[tuple[str, str]]:
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag]


def test_argv_binds_workspace_rw(tmp_path):
    policy, ws, _home = _policy(tmp_path)
    argv = bwrap_argv(policy, ["true"], bwrap=BWRAP, python=sys.executable)
    assert argv[0] == BWRAP
    assert ("/", "/") in _pairs(argv, "--ro-bind")
    assert (str(ws), str(ws)) in _pairs(argv, "--bind")
    assert {"--unshare-net", "--unshare-pid", "--die-with-parent"} <= set(argv)
    assert argv[-2:] == ["--", "true"] or argv[-1] == "true"


def test_argv_masks_unreadable(tmp_path):
    policy, _ws, home = _policy(tmp_path)
    argv = bwrap_argv(policy, ["true"], bwrap=BWRAP, python=sys.executable)
    tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    assert str(home / ".ssh") in tmpfs
    assert ("/dev/null", str(home / ".netrc")) in _pairs(argv, "--ro-bind")
    assert str(home / "absent") not in argv  # bwrap cannot mount over a missing path


def test_argv_quotes_space_path(tmp_path):
    policy, ws, _home = _policy(tmp_path)
    argv = bwrap_argv(policy, ["true"], bwrap=BWRAP, python=sys.executable)
    assert str(ws) in argv  # one element, never shell-split
    assert all(" " not in a or a == str(ws) or a.startswith(str(ws)) for a in argv[:-1])


def test_argv_reopens_shadow_after_read_only(tmp_path):
    policy, ws, _home = _policy(tmp_path)
    argv = bwrap_argv(policy, ["true"], bwrap=BWRAP, python=sys.executable)
    state = str(ws / ".crucible" / "state")
    shadow = str(ws / ".crucible" / "state" / "shadows" / "t1")
    ro_index = argv.index(state)
    rebinds = [i for i, a in enumerate(argv) if a == "--bind" and argv[i + 1] == shadow]
    assert rebinds and rebinds[-1] > ro_index


def test_argv_runs_bridge_when_proxy(tmp_path):
    policy, _ws, _home = _policy(tmp_path, proxy=True)
    argv = bwrap_argv(policy, ["curl", "x"], bwrap=BWRAP, python=sys.executable)
    sock = str(tmp_path / "proxy.sock")
    assert (sock, sock) in _pairs(argv, "--bind")
    tail = argv[argv.index(netbridge.__file__):]
    assert tail == [netbridge.__file__, sock, "8877", "--", "curl", "x"]


def test_netbridge_forwards_to_unix_socket():
    # Unix socket paths are limited to ~104 characters; pytest's tmp_path is longer.
    sock_dir = Path(tempfile.mkdtemp(prefix="nb", dir="/tmp"))
    sock_path = sock_dir / "s.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(sock_path))
    server.listen()

    def echo() -> None:
        conn, _ = server.accept()
        conn.sendall(conn.recv(5).upper())
        conn.close()

    threading.Thread(target=echo, daemon=True).start()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    client = (f"import socket; c = socket.create_connection(('127.0.0.1', {port}), timeout=5); "
              "c.sendall(b'hello'); print(c.recv(5).decode())")
    result = subprocess.run(
        [sys.executable, netbridge.__file__, str(sock_path), str(port), "--",
         sys.executable, "-c", client], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HELLO"


def test_netbridge_exits_with_command_status(tmp_path):
    result = subprocess.run(
        [sys.executable, netbridge.__file__, str(tmp_path / "none.sock"), "0", "--",
         sys.executable, "-c", "raise SystemExit(3)"], timeout=30)
    assert result.returncode == 3


def _bwrap_works() -> bool:
    path = shutil.which("bwrap")
    if not path or not sys.platform.startswith("linux"):
        return False
    return subprocess.run([path, "--ro-bind", "/", "/", "--unshare-net", "true"],
                          capture_output=True).returncode == 0


real = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap unavailable")


def _run(policy: SandboxPolicy, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(bwrap_argv(policy, list(argv), bwrap=shutil.which("bwrap") or BWRAP,
                                     python=sys.executable),
                          capture_output=True, text=True, timeout=60)


@real
def test_real_write_inside_and_outside(tmp_path):
    policy, ws, home = _policy(tmp_path)
    assert _run(policy, "touch", str(ws / "a.txt")).returncode == 0
    assert _run(policy, "touch", str(home / "x")).returncode != 0
    assert not (home / "x").exists()


@real
def test_real_read_only_and_credentials(tmp_path):
    policy, ws, home = _policy(tmp_path)
    assert _run(policy, "sh", "-c", f"echo x > '{ws / 'AGENTS.md'}'").returncode != 0
    assert (ws / "AGENTS.md").read_text() == "rules\n"
    assert "machine" not in _run(policy, "cat", str(home / ".netrc")).stdout
    shadow = ws / ".crucible" / "state" / "shadows" / "t1"
    assert _run(policy, "touch", str(shadow / "ok")).returncode == 0


@real
def test_real_network_denied_loopback_allowed(tmp_path):
    policy, _ws, _home = _policy(tmp_path)
    out = _run(policy, sys.executable, "-c",
               "import socket\ntry:\n socket.create_connection(('1.1.1.1', 80), timeout=5)\n"
               "except OSError as e: print('blocked', e.errno)\n").stdout
    assert "blocked" in out
    loop = _run(policy, sys.executable, "-c",
                "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); "
                "c=socket.create_connection(s.getsockname()); print('ok')")
    assert loop.stdout.strip() == "ok", loop.stderr


def test_argv_hides_host_sockets_and_shares_nothing_in_tmp(tmp_path):
    policy, _ws, _home = _policy(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    argv = bwrap_argv(replace(policy, private_tmp=private), ["true"], bwrap=BWRAP, python=sys.executable)
    assert (str(private), "/tmp") in _pairs(argv, "--bind")
    tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    assert "/run" in tmpfs


def test_argv_makes_masked_folders_read_only(tmp_path):
    policy, _ws, home = _policy(tmp_path)
    argv = bwrap_argv(policy, ["true"], bwrap=BWRAP, python=sys.executable)
    remounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--remount-ro"]
    assert str(home / ".ssh") in remounts


def test_argv_keeps_the_terminal_for_interactive_sessions(tmp_path):
    policy, _ws, _home = _policy(tmp_path)
    assert "--new-session" in bwrap_argv(policy, ["true"], bwrap=BWRAP, python=sys.executable)
    # setsid would drop the controlling terminal PtyProcess acquires: Ctrl-C would reach nobody.
    assert "--new-session" not in bwrap_argv(policy, ["true"], bwrap=BWRAP, python=sys.executable,
                                             interactive=True)


def test_argv_creates_a_missing_cache_folder_so_it_can_be_bound(tmp_path):
    # Home is read-only; on a fresh machine ~/.npm doesn't exist and npm would hit EROFS.
    policy, _ws, home = _policy(tmp_path)
    cache = home / ".npm"
    argv = bwrap_argv(replace(policy, writable=(*policy.writable, cache)), ["true"],
                      bwrap=BWRAP, python=sys.executable)
    assert cache.is_dir() and (str(cache), str(cache)) in _pairs(argv, "--bind")
