"""agentd.serve (spec §3.1): bind first, then token, lock, handshake, serve."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process semantics")

NONCE = "00112233445566778899aabbccddeeff"


def _env(home: Path, ws: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CRUCIBLE_") and k != "HOME"}
    env.update({
        "HOME": str(home),
        "CRUCIBLE_REASONING_BACKEND": "scripted",
        "CRUCIBLE_WORKSPACE_PATH": str(ws),
        "CRUCIBLE_MEMORY_ENABLED": "0",
    })
    return env


def _start(tmp_path: Path, *args: str, env_extra: dict[str, str] | None = None):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    cwd = tmp_path / "cwd"
    for p in (home, ws, cwd):
        p.mkdir(exist_ok=True)
    env = {**_env(home, ws), **(env_extra or {})}
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentd.serve", *args], cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, home, ws


def _handshake(proc: subprocess.Popen[str], timeout: float = 30) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("CRUCIBLE_SERVE "):
            return json.loads(line[len("CRUCIBLE_SERVE "):])
        if not line and proc.poll() is not None:
            raise AssertionError(f"exited {proc.returncode}: {proc.stderr.read()}")
    raise AssertionError("no handshake")


def _get(port: int, path: str, token: str | None = None, timeout: float = 30):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 503 and time.monotonic() < deadline:
                time.sleep(0.2)
                continue
            return exc.code, None
        except urllib.error.URLError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _stop(proc: subprocess.Popen[str]) -> int:
    """SIGTERM and wait. A clean stop reports 0, or -SIGTERM: after a graceful shutdown
    uvicorn re-raises the signal it caught, so the process dies "of" SIGTERM."""
    proc.send_signal(signal.SIGTERM)
    code = proc.wait(15)
    return 0 if code == -signal.SIGTERM else code


def test_port_zero_handshake_token_lock_and_proof(tmp_path: Path) -> None:
    proc, home, ws = _start(tmp_path, "--port", "0", "--workspace-lock", str(tmp_path / "ws"))
    try:
        hs = _handshake(proc)
        port = hs["port"]
        assert hs["pid"] == proc.pid
        token = (home / ".crucible/run" / f"agentd-{port}.token").read_text()
        lock = json.loads((ws / ".crucible/state/agentd.lock").read_text())
        assert lock["pid"] == proc.pid and lock["port"] == port
        status, body = _get(port, f"/health?nonce={NONCE}")
        assert status == 200 and body["pid"] == proc.pid
        expected = hmac.new(token.encode(), b"crucible-health-v1\0" + NONCE.encode(),
                            hashlib.sha256).hexdigest()
        assert body["proof"] == expected
        assert _get(port, "/v1/config")[0] == 401
        assert _get(port, "/v1/config", token)[0] == 200
    finally:
        assert _stop(proc) == 0
    # Never deleted at shutdown (spec §3.1).
    assert (home / ".crucible/run" / f"agentd-{port}.token").exists()


def test_busy_port_exits_without_touching_files(tmp_path: Path) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        proc, home, ws = _start(tmp_path, "--port", str(port),
                                "--workspace-lock", str(tmp_path / "ws"))
        assert proc.wait(30) == 2
    assert not (home / ".crucible/run" / f"agentd-{port}.token").exists()
    assert not (ws / ".crucible/state/agentd.lock").exists()


def test_startup_failure_exits_3(tmp_path: Path) -> None:
    proc, _home, _ws = _start(tmp_path, "--port", "0",
                              env_extra={"CRUCIBLE_AST_CUTOVER_MODE": "soft"})
    _handshake(proc)
    assert proc.wait(60) == 3


def test_old_token_on_the_port_is_overwritten(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run = tmp_path / "home/.crucible/run"
    run.mkdir(parents=True, mode=0o700)
    os.chmod(tmp_path / "home/.crucible", 0o700)
    stale = run / f"agentd-{port}.token"
    stale.write_text("s" * 43)
    os.chmod(stale, 0o600)
    proc, _home, _ws = _start(tmp_path, "--port", str(port))
    try:
        _handshake(proc)
        assert stale.read_text() != "s" * 43
    finally:
        _stop(proc)


def test_lock_write_does_not_follow_a_symlink(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    state = ws / ".crucible/state"
    state.mkdir(parents=True)
    target = tmp_path / "target.txt"
    target.write_text("untouched")
    os.symlink(target, state / "agentd.lock")
    proc, _home, _ws = _start(tmp_path, "--port", "0", "--workspace-lock", str(ws))
    try:
        _handshake(proc)
        assert target.read_text() == "untouched"
        assert not (state / "agentd.lock").is_symlink()
    finally:
        _stop(proc)


def test_legacy_dirs_migrated_before_lock(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".ai-editor").mkdir(parents=True)
    (ws / ".ai-editor/marker").write_text("m")
    proc, _home, _ws = _start(tmp_path, "--port", "0", "--workspace-lock", str(ws))
    try:
        _handshake(proc)
        assert (ws / ".crucible/marker").read_text() == "m"
        assert (ws / ".crucible/state/agentd.lock").exists()
    finally:
        _stop(proc)


def test_reload_keeps_token_and_pid(tmp_path: Path) -> None:
    proc, home, ws = _start(tmp_path, "--port", "0", "--reload",
                            "--workspace-lock", str(tmp_path / "ws"))
    try:
        port = _handshake(proc)["port"]
        token_file = home / ".crucible/run" / f"agentd-{port}.token"
        token = token_file.read_text()
        assert _get(port, f"/health?nonce={NONCE}")[1]["pid"] == proc.pid
        (tmp_path / "cwd" / "touch_me.py").write_text("x = 1\n")  # trigger a reload
        time.sleep(4)
        status, body = _get(port, f"/health?nonce={NONCE}")
        assert status == 200 and body["pid"] == proc.pid
        assert token_file.read_text() == token
        assert _get(port, "/v1/config", token)[0] == 200
    finally:
        _stop(proc)


def test_stop_with_open_sse_stream_finishes_quickly(tmp_path: Path) -> None:
    proc, home, _ws = _start(tmp_path, "--port", "0")
    port = _handshake(proc)["port"]
    token = (home / ".crucible/run" / f"agentd-{port}.token").read_text()
    _get(port, "/v1/config", token)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/channels/c1/stream",
                                 headers={"Authorization": f"Bearer {token}"})
    stream = urllib.request.urlopen(req, timeout=30)
    try:
        started = time.monotonic()
        assert _stop(proc) == 0
        assert time.monotonic() - started < 9
    finally:
        stream.close()


def test_import_creates_no_files_and_stays_light(tmp_path: Path) -> None:
    code = ("import sys, agentd.serve; "
            "assert 'agentd.main' not in sys.modules; "
            "assert 'fastapi' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True,
                   env=_env(tmp_path, tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows socket option")
def test_windows_second_bind_fails() -> None:
    from agentd.serve import _bind

    first = _bind(0)
    try:
        with pytest.raises(OSError):
            _bind(first.getsockname()[1])
    finally:
        first.close()


def test_bare_uvicorn_refuses_to_start(tmp_path: Path) -> None:
    home, ws, cwd = tmp_path / "h", tmp_path / "w", tmp_path / "c"
    for p in (home, ws, cwd):
        p.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "agentd.main:app", "--port", str(port)],
        cwd=cwd, env=_env(home, ws), capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "agentd.serve" in proc.stderr
