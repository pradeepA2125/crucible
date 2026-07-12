# Exec Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PTY-backed background process sessions for the chat controller — start servers, poll output, send stdin, kill — surviving across turns, with a `/live` strip + expandable transcript in the UI.

**Architecture:** New self-contained `agentd/exec_sessions/` module (config → pty_process → manager → registry_file → tool_source), registered in `ChatController._build_registry` behind `CRUCIBLE_EXEC_SESSIONS_ENABLED` (the write_doc/MCP pattern). `start_session` reuses the existing command-approval gate (`kind="command"`); sessions are thread-scoped, group-killed, reaped on startup, killed at shutdown. Frontend mirrors the todos live-card flow (`/live` → signature → `renderLiveSessions` → LiveSlot strip) plus a host round-trip for the expand transcript.

**Tech Stack:** Python 3.13 asyncio + stdlib `pty` (Unix) / `pywinpty` (Windows), FastAPI, Zod/TypeScript (editor-client), React webview.

**Spec:** `docs/superpowers/specs/2026-07-11-exec-sessions-design.md` — read it first.

## Global Constraints

- Flag: `CRUCIBLE_EXEC_SESSIONS_ENABLED` — engine default **OFF** (truthy = `1/true/yes/on`); `start-backend.sh` and repo `.env` opt in with `${VAR:-1}`.
- Controller-only: `tools/shell.py`, the task ToolLoop, and `verify_phase_sm` are **untouched**.
- Env knobs (exact names/defaults): `CRUCIBLE_EXEC_SESSION_MAX_COUNT=16`, `CRUCIBLE_EXEC_SESSION_BUFFER_BYTES=1048576`, `CRUCIBLE_EXEC_SESSION_DEFAULT_YIELD_MS=2000`, `CRUCIBLE_EXEC_SESSION_RESULT_MAX_CHARS=4000`. Yield clamp: 250–30000 ms.
- Registry file: `<workspace>/.crucible/state/exec-sessions.json`.
- Dependency: `pywinpty>=2; sys_platform == 'win32'` (conditional). No new Unix deps.
- Tool names (exact): `start_session`, `write_stdin`, `kill_session`, `list_sessions`.
- Tool results ride `ToolOutput`; failures are `is_error=True`, never raised (loop adapts).
- Auxiliary IO (registry file, reap) is best-effort: `logger.warning` + degrade, never breaks a turn or startup.
- Tests: real processes on `tmp_path`, no mocks of fs/subprocess (house style). `pytest-asyncio` for async. Never `pytest -q` (addopts already `-q`); never pipe pytest.
- Commit after every task: `feat(exec-sessions): …`.
- TS build order: after editor-client changes run `npm run -w @crucible/editor-client build` before extension typecheck.

## File Structure

```
services/agentd-py/agentd/exec_sessions/
  __init__.py        # empty
  config.py          # env resolution + clamp_yield_ms
  pty_process.py     # PtyProcess (unix pty) / WinPtyProcess (pywinpty) — one child per session
  manager.py         # RingBuffer, Session, SessionRead, SessionManager (single owner)
  registry_file.py   # crash-reap registry (.crucible/state/exec-sessions.json)
  tool_source.py     # ExecSessionToolSource — the 4 tools
services/agentd-py/tests/
  test_exec_sessions_config.py
  test_exec_sessions_pty.py
  test_exec_sessions_manager.py
  test_exec_sessions_registry.py
  test_exec_sessions_tool_source.py
  test_exec_sessions_wiring.py
  test_exec_sessions_routes.py
Modified:
  agentd/chat/controller.py           # ctor param + _run_loop source construction
  agentd/chat/controller_factory.py   # manager build when enabled
  agentd/chat/controller_prompts.py   # _SESSIONS_BLOCK auto-append
  agentd/chat/models.py               # ThreadLiveState.sessions
  agentd/api/routes.py                # /live fill, transcript route, /v1/config
  agentd/main.py                      # startup reap + shutdown kill handlers
  services/agentd-py/pyproject.toml   # pywinpty conditional dep
  scripts/stress/start-backend.sh + .env  # flag opt-in
  apps/editor-client/src/contracts/task-contracts.ts   # Zod + client interface
  apps/editor-client/src/client/http-backend-client.ts # getSessionTranscript + mapping
  apps/vscode-extension/src/controller.ts              # signature + renderLiveSessions + transcript fetch
  apps/vscode-extension/src/chat-panel.ts              # postMessage plumbing
  apps/vscode-extension/webview-ui/src/types.ts        # mirror types
  apps/vscode-extension/webview-ui/src/hooks/useAppState.ts  # reducer cases
  apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx + components/SessionStrip.tsx
```

---

### Task 1: Config module

**Files:**
- Create: `services/agentd-py/agentd/exec_sessions/__init__.py` (empty), `services/agentd-py/agentd/exec_sessions/config.py`
- Test: `services/agentd-py/tests/test_exec_sessions_config.py`

**Interfaces:**
- Produces: `is_exec_sessions_enabled() -> bool`, `max_session_count() -> int`, `buffer_bytes() -> int`, `default_yield_ms() -> int`, `result_max_chars() -> int`, `clamp_yield_ms(raw: object) -> int` (None/garbage → default; clamped 250–30000).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exec_sessions_config.py
from agentd.exec_sessions.config import (
    clamp_yield_ms, is_exec_sessions_enabled, max_session_count,
)


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", raising=False)
    assert is_exec_sessions_enabled() is False


def test_flag_truthy(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", "1")
    assert is_exec_sessions_enabled() is True


def test_clamp_yield_defaults_and_bounds(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_EXEC_SESSION_DEFAULT_YIELD_MS", raising=False)
    assert clamp_yield_ms(None) == 2000
    assert clamp_yield_ms(50) == 250
    assert clamp_yield_ms(99_999) == 30_000
    assert clamp_yield_ms("not a number") == 2000
    assert clamp_yield_ms(5000) == 5000


def test_max_count_env(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSION_MAX_COUNT", "3")
    assert max_session_count() == 3
```

- [ ] **Step 2: Run to verify it fails** — `cd services/agentd-py && pytest tests/test_exec_sessions_config.py` → `ModuleNotFoundError: agentd.exec_sessions`

- [ ] **Step 3: Implement**

```python
# agentd/exec_sessions/config.py
"""Env resolution for exec sessions (mirrors controller_factory flag style)."""
from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}
YIELD_MIN_MS = 250
YIELD_MAX_MS = 30_000


def is_exec_sessions_enabled() -> bool:
    """Default OFF (ship dark); start-backend.sh opts in."""
    return os.getenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", "0").strip().lower() in _TRUTHY


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


def max_session_count() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_MAX_COUNT", 16)


def buffer_bytes() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_BUFFER_BYTES", 1_048_576)


def default_yield_ms() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_DEFAULT_YIELD_MS", 2000)


def result_max_chars() -> int:
    return _int_env("CRUCIBLE_EXEC_SESSION_RESULT_MAX_CHARS", 4000)


def clamp_yield_ms(raw: object) -> int:
    """Model-supplied yield → int clamped to [250, 30000]; garbage → default."""
    try:
        val = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        val = default_yield_ms()
    return max(YIELD_MIN_MS, min(YIELD_MAX_MS, val))
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_exec_sessions_config.py` → all pass
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(exec-sessions): config module (flags + yield clamp)"`

---

### Task 2: PtyProcess (Unix) + pywinpty dependency

**Files:**
- Create: `services/agentd-py/agentd/exec_sessions/pty_process.py`
- Modify: `services/agentd-py/pyproject.toml` (dependencies list: add `"pywinpty>=2; sys_platform == 'win32'"`)
- Test: `services/agentd-py/tests/test_exec_sessions_pty.py`

**Interfaces:**
- Produces: `class PtyProcess` with `@classmethod async spawn(command: str, args: list[str], cwd: Path, env: dict[str, str], on_output: Callable[[bytes], None]) -> PtyProcess`; attributes `pid: int`, `pgid: int`; methods `write(chars: str) -> None` (capped ~4 KB, non-blocking, best-effort), `is_running() -> bool`, `exit_code() -> int | None`, `async wait(timeout_sec: float) -> bool` (True if exited within timeout; **drains remaining PTY output before returning True**), `drain() -> None` (deterministic post-exit flush), `async kill(grace_sec: float = 2.0) -> None` (group SIGTERM→SIGKILL, idempotent), `close() -> None` (release fds/readers/waiter task).
- `on_output` is called on the event loop thread with raw bytes as they arrive (PTY master reads).
- `new_pty_process_class() -> type` — platform dispatch (PtyProcess on Unix, WinPtyProcess on win32).
- **Review-fix constraints (dry-run 2026-07-12):** (a) `pty`/`fcntl`/`termios` imports MUST sit under `if sys.platform != "win32":` — they don't exist on win32 and an unguarded import crashes the module before the winpty branch is reachable; (b) ONE persistent `proc.wait()` task per process, created at spawn — `wait()` races it with `asyncio.wait({task}, timeout=…)`; never `wait_for(shield(proc.wait()), t)` per poll (leaks one pending task per timed-out poll); (c) master fd is `O_NONBLOCK`; (d) `wait()`→True implies `drain()` already ran — callers never sleep to "let output arrive"; (e) `get_running_loop()`, never `get_event_loop()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_exec_sessions_pty.py
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
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_exec_sessions_pty.py` → `ImportError`

- [ ] **Step 3: Implement**

```python
# agentd/exec_sessions/pty_process.py
"""One PTY-backed child per session.

Unix: stdlib pty + loop.add_reader. The child is its own session/process-group
leader (start_new_session=True) so kill() can killpg the whole tree — the fix
for the Codex-class orphaned-grandchild leak. Windows: pywinpty (WinPtyProcess),
same contract; group-kill maps to winpty's process-tree kill.

Hardening (dry-run review 2026-07-12):
- Unix-only imports live under a platform guard — pty/fcntl/termios don't
  exist on win32 and would crash the import before WinPtyProcess is reachable.
- The master fd is O_NONBLOCK: a stuffed PTY buffer must never block the loop.
- One persistent proc.wait() task per process; wait(timeout) races it. A fresh
  wait_for(shield(...)) per poll leaks one pending task per timed-out poll.
- wait()==True implies drain() ran: the final output chunk is deterministically
  flushed before "exited" is ever reported. Callers never sleep-and-hope.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import struct
import sys
from collections.abc import Callable
from pathlib import Path

if sys.platform != "win32":
    import fcntl
    import pty
    import termios

logger = logging.getLogger(__name__)

_WINSIZE_ROWS, _WINSIZE_COLS = 50, 200
_READ_CHUNK = 65536
WRITE_MAX_BYTES = 4096


class PtyProcess:
    def __init__(self, proc: asyncio.subprocess.Process, master_fd: int,
                 on_output: Callable[[bytes], None]) -> None:
        self._proc = proc
        self._master_fd = master_fd
        self._on_output = on_output
        self._closed = False
        self._drained = False
        self.pid = proc.pid
        self.pgid = proc.pid  # start_new_session=True ⇒ child is its own pgid
        loop = asyncio.get_running_loop()
        # ONE waiter for the process's whole life — see module docstring.
        self._wait_task = loop.create_task(proc.wait())
        loop.add_reader(master_fd, self._readable)

    @classmethod
    async def spawn(cls, command: str, args: list[str], cwd: Path,
                    env: dict[str, str],
                    on_output: Callable[[bytes], None]) -> "PtyProcess":
        master, slave = pty.openpty()
        # Sane window so TUIs/spinners render instead of degrading to 80x24.
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", _WINSIZE_ROWS, _WINSIZE_COLS, 0, 0))
        os.set_blocking(master, False)  # reads AND writes must never block the loop
        env = {**env, "TERM": env.get("TERM", "xterm-256color")}

        def _acquire_ctty() -> None:
            # Runs in the forked child (dup2s and setsid already done — CPython
            # child_exec order). start_new_session gives a new session but
            # inheriting the slave fd is NOT an open(): without TIOCSCTTY the
            # PTY has no controlling terminal, its foreground pgrp is empty,
            # and \x03 (Ctrl-C) silently signals nobody (review fix #8).
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            proc = await asyncio.create_subprocess_exec(
                command, *args, cwd=str(cwd), env=env,
                stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, preexec_fn=_acquire_ctty)
        finally:
            os.close(slave)  # parent's copy; the child holds its own
        return cls(proc, master, on_output)

    def _readable(self) -> None:
        try:
            data = os.read(self._master_fd, _READ_CHUNK)
        except BlockingIOError:
            return  # spurious wakeup on a nonblocking fd
        except OSError:
            # EIO on Linux / closed on macOS when the child side is gone.
            self._remove_reader()
            return
        if data:
            self._on_output(data)
        else:
            self._remove_reader()

    def _remove_reader(self) -> None:
        if not self._closed:
            try:
                asyncio.get_running_loop().remove_reader(self._master_fd)
            except (ValueError, OSError, RuntimeError):
                pass

    def drain(self) -> None:
        """Deterministically flush output still in the kernel PTY buffer after
        exit. proc.wait() returning does NOT imply the reader callback already
        delivered the final chunk — without this, fast commands return
        truncated output and a drained-session drop can race the last bytes."""
        if self._drained or self._closed:
            return
        self._drained = True
        self._remove_reader()
        while True:
            try:
                data = os.read(self._master_fd, _READ_CHUNK)
            except (BlockingIOError, OSError):
                return  # empty (nonblocking) or EIO — nothing more can arrive
            if not data:
                return
            self._on_output(data)

    def write(self, chars: str) -> None:
        """Best-effort, capped, non-blocking. A full PTY buffer (child not
        reading stdin, ~64 KB kernel side) must drop the write with a warning
        rather than freeze the event loop; partial writes are tolerated."""
        data = chars.encode("utf-8")[:WRITE_MAX_BYTES]
        try:
            os.write(self._master_fd, data)
        except BlockingIOError:
            logger.warning("[exec-sessions] stdin write dropped (PTY buffer full)")
        except OSError:
            logger.warning("[exec-sessions] stdin write failed", exc_info=True)

    def is_running(self) -> bool:
        return self._proc.returncode is None

    def exit_code(self) -> int | None:
        return self._proc.returncode

    async def wait(self, timeout_sec: float) -> bool:
        """True if the process exited within timeout_sec. Races the single
        persistent waiter task — never creates a task per call. On True, the
        remaining output has already been drained into on_output."""
        if not self._wait_task.done():
            await asyncio.wait({self._wait_task}, timeout=timeout_sec)
        if self._wait_task.done():
            self.drain()
            return True
        return False

    async def kill(self, grace_sec: float = 2.0) -> None:
        """Group SIGTERM → grace → group SIGKILL. Idempotent; never raises."""
        if not self.is_running():
            self.drain()
            return
        for sig_ in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.pgid, sig_)
            except (ProcessLookupError, PermissionError):
                self.drain()
                return
            if await self.wait(grace_sec):
                return

    def close(self) -> None:
        """Release the reader + master fd + waiter task. Call after death."""
        self._remove_reader()
        self._closed = True
        if not self._wait_task.done():
            self._wait_task.cancel()
        try:
            os.close(self._master_fd)
        except OSError:
            pass


if sys.platform == "win32":  # pragma: no cover — no Windows CI; mirrors PtyProcess
    import winpty  # type: ignore[import-not-found]  # pywinpty

    class WinPtyProcess:
        """pywinpty adapter with the PtyProcess contract. Reader runs in a
        thread (winpty has no fd to add_reader); kill() uses winpty's
        process-tree termination. Untested on CI — exercised only on a real
        Windows machine.

        SPECULATIVE (dry-run review 2026-07-12): the sketch below was written
        blind and does NOT match pywinpty's real API in places — verify
        `PTY.read`'s signature (the `blocking=` kwarg may not exist), the
        `spawn` env format (pywinpty may expect an env *string block*, not a
        dict), and the exit-status API against the current pywinpty docs at
        implementation time. Treat this class as a contract description, not
        working code."""

        def __init__(self, pty_: "winpty.PTY", on_output) -> None:
            self._pty = pty_
            self._on_output = on_output
            self._exit_code: int | None = None
            self.pid = pty_.pid
            self.pgid = pty_.pid
            self._reader = asyncio.get_event_loop().run_in_executor(
                None, self._read_loop)

        @classmethod
        async def spawn(cls, command, args, cwd, env, on_output):
            pty_ = winpty.PTY(_WINSIZE_COLS, _WINSIZE_ROWS)
            argv = " ".join([command, *args])
            pty_.spawn(argv, cwd=str(cwd), env=env)
            return cls(pty_, on_output)

        def _read_loop(self):
            while True:
                data = self._pty.read(_READ_CHUNK, blocking=True)
                if not data:
                    self._exit_code = self._pty.get_exitstatus() or 0
                    return
                asyncio.get_event_loop().call_soon_threadsafe(
                    self._on_output, data.encode("utf-8", "replace")
                    if isinstance(data, str) else data)

        def write(self, chars: str) -> None:
            self._pty.write(chars)

        def is_running(self) -> bool:
            return self._pty.isalive()

        def exit_code(self) -> int | None:
            return self._exit_code if not self._pty.isalive() else None

        async def wait(self, timeout_sec: float) -> bool:
            deadline = asyncio.get_event_loop().time() + timeout_sec
            while self._pty.isalive():
                if asyncio.get_event_loop().time() >= deadline:
                    return False
                await asyncio.sleep(0.05)
            return True

        async def kill(self, grace_sec: float = 2.0) -> None:
            self._pty.terminate(force=True)

        def close(self) -> None:
            del self._pty


def new_pty_process_class() -> type:
    """Platform dispatch — the manager calls this once."""
    return WinPtyProcess if sys.platform == "win32" else PtyProcess  # type: ignore[name-defined]
```

- [ ] **Step 4: pyproject** — in `services/agentd-py/pyproject.toml`, append to the `dependencies = [...]` array: `"pywinpty>=2; sys_platform == 'win32'",`
- [ ] **Step 5: Run** — `pytest tests/test_exec_sessions_pty.py` → 5 pass (on macOS/Linux)
- [ ] **Step 6: Commit** — `git commit -am "feat(exec-sessions): PtyProcess (unix pty + pywinpty adapter)"`

---

### Task 3: RingBuffer + SessionManager core

**Files:**
- Create: `services/agentd-py/agentd/exec_sessions/manager.py`, plus a **stub** `services/agentd-py/agentd/exec_sessions/registry_file.py` (`record()`/`clear()` no-ops, `reap_orphans()` returns 0) so imports resolve — Task 4 replaces it.
- Test: `services/agentd-py/tests/test_exec_sessions_manager.py`

**Interfaces:**
- Consumes: `new_pty_process_class()` (Task 2), config fns (Task 1).
- Produces:
  - `@dataclass SessionRead: session_id: str; status: str  # "running"|"exited"; exit_code: int | None; new_output: str; still_running: bool`
  - Exceptions: `SessionCapError`, `SessionNotFoundError`, `SessionSpawnError`.
  - `class SessionManager(workspace_path: Path, registry_path: Path | None = None)`:
    - `async start(thread_id, command: str, args: list[str], cwd: str | None, yield_time_ms: object) -> SessionRead` — cap-hit raises `SessionCapError`; spawn failure raises `SessionSpawnError(msg)`. **Reuses `run_command`'s spawn hardening** (review fix #1): `_split_command` from `agentd.tools.shell` (models pack whole lines into `command`; exec does no word-splitting), `resolve_workspace_bin` from `agentd.tools._paths` (naked names find the workspace `.venv/bin/…`), and the venv env hygiene (`UV_PROJECT_ENVIRONMENT`/`VIRTUAL_ENV` → workspace venv, never the backend's own leaked one).
    - `async write_stdin(thread_id, session_id, chars: str, yield_time_ms: object) -> SessionRead` — unknown/foreign-thread id raises `SessionNotFoundError` (message lists known ids). **Decodes literal escape sequences** (`\n`, `\r`, `\t`, `\xNN`, `\uNNNN`, `\\`) before writing — JSON can't carry raw control bytes, so models emit `\x03` as four literal characters; without decoding Ctrl-C never fires (review fix #5).
    - `async kill(thread_id, session_id) -> SessionRead` — final unread output; drops the session.
    - `list_sessions(thread_id) -> list[dict]` — `{id, command, status, exit_code, age_sec, unread_bytes}` (model-facing rows); drops exited sessions whose cursor is at end (spec retention rule).
    - `live_summaries(thread_id) -> list[dict]` — **`{id, command, status, exit_code, started_at}` — deliberately DIFFERENT rows from `list_sessions`**: no drop side-effect AND no `age_sec`/`unread_bytes`, because `/live` rows must serialize identically while nothing real changes or the webview's `lastLiveSignature` dedup churns at 1 Hz (review fix #2). Age is computed client-side from `started_at`.
    - `transcript(thread_id, session_id) -> dict | None` — `{output_tail, stdin_history, status, exit_code}`; **never advances the model cursor** (spec invariant); `output_tail` capped 16000 chars.
    - `async shutdown() -> None` — kill-all **concurrently** (`asyncio.gather`; serial kills cost up to ~4 s × N) + clear registry.
    - `reap_orphans() -> int` — delegates to the registry file.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_exec_sessions_manager.py
import asyncio
import sys

import pytest

from agentd.exec_sessions.manager import (
    SessionCapError, SessionManager, SessionNotFoundError,
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
    code = "import time;print('first',flush=True);time.sleep(0.8);print('second',flush=True);time.sleep(60)"
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
    code = ("import time\nfor i in range(999):\n print(i,flush=True)\n time.sleep(0.05)")
    r = await m.start("t1", PY, ["-u", "-c", code], None, 300)
    a = m.live_summaries("t1")
    await asyncio.sleep(1.1)  # more output emitted, more age elapsed
    b = m.live_summaries("t1")
    assert a == b
    assert set(a[0]) == {"id", "command", "status", "exit_code", "started_at"}
    await m.shutdown()
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_exec_sessions_manager.py` → ImportError

- [ ] **Step 3: Implement**

```python
# agentd/exec_sessions/manager.py
"""SessionManager — single owner of all live exec sessions.

Thread-scoped (chat thread id), Codex yield semantics, ring-buffered output
with an independent model read-cursor vs. UI inspect view. See the spec:
docs/superpowers/specs/2026-07-11-exec-sessions-design.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from agentd.exec_sessions.config import (
    buffer_bytes, clamp_yield_ms, max_session_count,
)
from agentd.exec_sessions.pty_process import new_pty_process_class
from agentd.exec_sessions.registry_file import SessionRegistryFile
from agentd.tools._paths import resolve_workspace_bin
from agentd.tools.shell import _split_command

logger = logging.getLogger(__name__)

_TRANSCRIPT_TAIL_CHARS = 16_000
_STDIN_HISTORY_MAX = 200
_DROP_MARKER = b"\n[... output dropped]\n"
STDIN_MAX_CHARS = 4096  # stdin is interactive input, not bulk data

_ESCAPE_RE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[nrt\\])")


def _decode_stdin_escapes(chars: str) -> str:
    """JSON can't carry raw control bytes except as \\uNNNN, so a model
    following the '\\x03 is Ctrl-C' teaching emits backslash-x-0-3 as four
    literal characters. Decode the standard escapes so control chars actually
    fire; already-raw control chars pass through untouched."""
    def _sub(m: re.Match[str]) -> str:
        tok = m.group(1)
        if tok == "n":
            return "\n"
        if tok == "r":
            return "\r"
        if tok == "t":
            return "\t"
        if tok == "\\":
            return "\\"
        return chr(int(tok[1:], 16))
    return _ESCAPE_RE.sub(_sub, chars)


class SessionCapError(Exception):
    pass


class SessionNotFoundError(Exception):
    pass


class SessionSpawnError(Exception):
    pass


class RingBuffer:
    """Byte ring with absolute offsets so multiple cursors read independently."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._buf = bytearray()
        self._start = 0  # absolute offset of _buf[0]
        self._marker_pending = False

    @property
    def end(self) -> int:
        return self._start + len(self._buf)

    def append(self, data: bytes) -> None:
        if self._marker_pending:
            data = _DROP_MARKER + data
            self._marker_pending = False
        self._buf.extend(data)
        overflow = len(self._buf) - self._cap
        if overflow > 0:
            del self._buf[:overflow]
            self._start += overflow
            self._marker_pending = True  # one marker per overflow episode

    def read_from(self, cursor: int) -> tuple[str, int]:
        """(text past cursor, new cursor). Dropped bytes are skipped silently —
        the drop marker was already injected inline at overflow time."""
        lo = max(cursor, self._start)
        chunk = bytes(self._buf[lo - self._start:])
        return chunk.decode("utf-8", errors="replace"), self.end

    def tail(self, max_chars: int) -> str:
        return bytes(self._buf).decode("utf-8", errors="replace")[-max_chars:]


@dataclass
class SessionRead:
    session_id: str
    status: str  # "running" | "exited"
    exit_code: int | None
    new_output: str
    still_running: bool


@dataclass
class _Session:
    session_id: str
    thread_id: str
    command_line: str
    proc: object  # PtyProcess contract
    buffer: RingBuffer
    started_at: float
    model_cursor: int = 0
    stdin_history: list[dict] = field(default_factory=list)


class SessionManager:
    def __init__(self, workspace_path: Path, registry_path: Path | None = None) -> None:
        self._workspace = Path(workspace_path)
        self._sessions: dict[str, _Session] = {}
        self._registry = SessionRegistryFile(
            registry_path
            or self._workspace / ".crucible" / "state" / "exec-sessions.json")
        self._pty_cls = new_pty_process_class()

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def start(self, thread_id: str, command: str, args: list[str],
                    cwd: str | None, yield_time_ms: object) -> SessionRead:
        if len(self._sessions) >= max_session_count():
            raise SessionCapError(
                f"Session cap ({max_session_count()}) reached backend-wide — "
                "kill_session one of yours (see list_sessions). If yours are "
                "all needed, sessions from other conversations may be holding "
                "slots; tell the user.")
        # Spawn hardening — the same three fixes shell.run_command carries
        # (dropping any of them reintroduces a known failure class):
        # 1. whole-line `command` → split back into executable + args;
        command, args = _split_command(command, args, self._workspace)
        # 2. naked names probe the workspace venv/bin dirs;
        if "/" not in command and "\\" not in command and not Path(command).is_absolute():
            local = resolve_workspace_bin(self._workspace, command)
            if local is not None:
                command = str(local)
        # 3. never leak the backend's own venv into the child.
        env = os.environ.copy()
        workspace_venv = self._workspace / (cwd or "") / ".venv"
        env["UV_PROJECT_ENVIRONMENT"] = str(workspace_venv)
        env["VIRTUAL_ENV"] = str(workspace_venv)
        target_cwd = self._resolve_cwd(cwd)
        buf = RingBuffer(buffer_bytes())
        try:
            proc = await self._pty_cls.spawn(
                command, args, cwd=target_cwd, env=env, on_output=buf.append)
        except FileNotFoundError as exc:
            raise SessionSpawnError(f"'{command}' not found on PATH") from exc
        except Exception as exc:
            raise SessionSpawnError(f"spawn failed: {exc}") from exc
        sess = _Session(
            session_id=f"sess-{uuid4().hex[:8]}", thread_id=thread_id,
            command_line=" ".join([command, *args]), proc=proc,
            buffer=buf, started_at=time.time())
        self._sessions[sess.session_id] = sess
        self._registry.record(self._sessions.values())
        # wait()==True implies the final output was already drained (PtyProcess
        # drain-on-exit) — no sleep heuristics here.
        exited = await proc.wait(clamp_yield_ms(yield_time_ms) / 1000)
        return self._read(sess, exited_hint=exited)

    async def write_stdin(self, thread_id: str, session_id: str, chars: str,
                          yield_time_ms: object) -> SessionRead:
        sess = self._get(thread_id, session_id)
        chars = _decode_stdin_escapes(chars)
        if chars and sess.proc.is_running():
            sess.proc.write(chars)
            sess.stdin_history.append({"ts": time.time(), "chars": chars})
            del sess.stdin_history[:-_STDIN_HISTORY_MAX]
        exited = await sess.proc.wait(clamp_yield_ms(yield_time_ms) / 1000)
        read = self._read(sess, exited_hint=exited)
        self._drop_if_drained(sess)
        return read

    async def kill(self, thread_id: str, session_id: str) -> SessionRead:
        sess = self._get(thread_id, session_id)
        await sess.proc.kill()  # kill() drains before returning
        read = self._read(sess, exited_hint=True)
        self._remove(sess)
        return read

    async def shutdown(self) -> None:
        async def _kill_one(sess: _Session) -> None:
            try:
                await sess.proc.kill()
                sess.proc.close()
            except Exception:
                logger.warning("[exec-sessions] shutdown kill failed for %s",
                               sess.session_id, exc_info=True)

        # Concurrent: a serial sweep would stall shutdown ~4s per stubborn session.
        await asyncio.gather(*(_kill_one(s) for s in list(self._sessions.values())))
        self._sessions.clear()
        self._registry.clear()

    def reap_orphans(self) -> int:
        return self._registry.reap_orphans()

    # ── views ────────────────────────────────────────────────────────────────
    def list_sessions(self, thread_id: str) -> list[dict]:
        """Model-facing rows (rich: age + unread size); applies the
        drained-exited retention drop."""
        for sess in [s for s in self._sessions.values()
                     if s.thread_id == thread_id]:
            self._drop_if_drained(sess)
        return [{
            "id": s.session_id,
            "command": s.command_line,
            "status": "running" if s.proc.is_running() else "exited",
            "exit_code": s.proc.exit_code(),
            "age_sec": int(time.time() - s.started_at),
            "unread_bytes": max(0, s.buffer.end - s.model_cursor),
        } for s in self._sessions.values() if s.thread_id == thread_id]

    def live_summaries(self, thread_id: str) -> list[dict]:
        """/live rows. INVARIANT: must serialize identically while nothing real
        changes — the webview dedups /live on a JSON signature, and a ticking
        age_sec or per-log-line unread_bytes would re-render the world at 1 Hz.
        So: started_at (stable), no age, no unread. No drop side-effect either
        (/live is read-only)."""
        return [{
            "id": s.session_id,
            "command": s.command_line,
            "status": "running" if s.proc.is_running() else "exited",
            "exit_code": s.proc.exit_code(),
            "started_at": s.started_at,
        } for s in self._sessions.values() if s.thread_id == thread_id]

    def transcript(self, thread_id: str, session_id: str) -> dict | None:
        sess = self._sessions.get(session_id)
        if sess is None or sess.thread_id != thread_id:
            return None
        # INVARIANT: independent view — never touches model_cursor (spec).
        return {
            "output_tail": sess.buffer.tail(_TRANSCRIPT_TAIL_CHARS),
            "stdin_history": list(sess.stdin_history),
            "status": "running" if sess.proc.is_running() else "exited",
            "exit_code": sess.proc.exit_code(),
        }

    # ── internals ────────────────────────────────────────────────────────────
    def _get(self, thread_id: str, session_id: str) -> _Session:
        sess = self._sessions.get(session_id)
        if sess is None or sess.thread_id != thread_id:
            known = [s["id"] for s in self.live_summaries(thread_id)]
            raise SessionNotFoundError(
                f"No session '{session_id}' in this thread. Known: {known or 'none'}.")
        return sess

    def _read(self, sess: _Session, exited_hint: bool) -> SessionRead:
        text, sess.model_cursor = sess.buffer.read_from(sess.model_cursor)
        running = sess.proc.is_running() and not exited_hint
        return SessionRead(
            session_id=sess.session_id,
            status="running" if running else "exited",
            exit_code=sess.proc.exit_code(),
            new_output=text, still_running=running)

    def _drop_if_drained(self, sess: _Session) -> None:
        """Spec retention: an exited session drops once its output is fully read."""
        if not sess.proc.is_running() and sess.model_cursor >= sess.buffer.end:
            self._remove(sess)

    def _remove(self, sess: _Session) -> None:
        sess.proc.close()
        self._sessions.pop(sess.session_id, None)
        self._registry.record(self._sessions.values())

    def _resolve_cwd(self, cwd: str | None) -> Path:
        """Real-workspace cwd, clamped inside it (mirrors shell._resolve_workspace_cwd)."""
        root = self._workspace.resolve()
        if not cwd:
            return root
        target = (root / cwd).resolve() if not Path(cwd).is_absolute() else Path(cwd).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return root
        return target if target.is_dir() else root
```

Stub for this task (replaced in Task 4):

```python
# agentd/exec_sessions/registry_file.py — Task-3 stub; Task 4 implements for real.
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class SessionRegistryFile:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def record(self, sessions: Iterable[object]) -> None:
        pass

    def clear(self) -> None:
        pass

    def reap_orphans(self) -> int:
        return 0
```

- [ ] **Step 4: Run** — `pytest tests/test_exec_sessions_manager.py` → 14 pass
- [ ] **Step 5: Commit** — `git commit -am "feat(exec-sessions): SessionManager + ring buffer with independent cursors"`

---

### Task 4: Crash-reap registry file

**Files:**
- Modify: `services/agentd-py/agentd/exec_sessions/registry_file.py` (replace Task-3 stub)
- Test: `services/agentd-py/tests/test_exec_sessions_registry.py`

**Interfaces:**
- Produces: `class SessionRegistryFile(path: Path)` — `record(sessions: Iterable) -> None` (rewrites the JSON list `[{session_id, pid, pgid, thread_id, command, started_at}]` from objects with `.session_id/.thread_id/.command_line/.started_at/.proc.pid/.proc.pgid`), `clear() -> None`, `reap_orphans() -> int` (kill recorded pgids still alive whose `ps -o command=` output contains the recorded command's first token; Unix-only — on win32 just clears the file; always ends by clearing; returns count killed). All methods best-effort: `OSError`/parse errors → `logger.warning`, never raise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_exec_sessions_registry.py
import json
import subprocess
import sys
import time

import pytest

from agentd.exec_sessions.registry_file import SessionRegistryFile

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix reap")


class _FakeSession:
    def __init__(self, sid, pid, pgid, thread_id, cmd):
        self.session_id, self.thread_id = sid, thread_id
        self.command_line, self.started_at = cmd, time.time()
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
                             f"{sys.executable} -c ...")])
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
```

- [ ] **Step 2: Run to verify fail** — stub writes nothing / returns 0 → roundtrip + reap tests FAIL

- [ ] **Step 3: Implement**

```python
# agentd/exec_sessions/registry_file.py
"""Crash-reap registry (the agentd.lock pattern): pids on disk so a restarted
backend can kill sessions a crashed predecessor leaked. Best-effort throughout —
registry IO must never break a turn or startup."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


class SessionRegistryFile:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def record(self, sessions: Iterable[object]) -> None:
        entries = [{
            "session_id": s.session_id,
            "pid": s.proc.pid,
            "pgid": s.proc.pgid,
            "thread_id": s.thread_id,
            "command": s.command_line,
            "started_at": s.started_at,
        } for s in sessions]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("[exec-sessions] registry write failed", exc_info=True)

    def clear(self) -> None:
        self.record([])

    def reap_orphans(self) -> int:
        """Kill recorded pgids still alive from a crashed prior run. Guarded
        against pid reuse: the live process's `ps` command line must contain the
        recorded command's first token. Always ends by clearing the file."""
        try:
            entries = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        killed = 0
        if sys.platform != "win32":
            for e in entries if isinstance(entries, list) else []:
                killed += self._reap_one(e)
        self.clear()
        return killed

    @staticmethod
    def _reap_one(entry: dict) -> int:
        try:
            pid, pgid = int(entry["pid"]), int(entry["pgid"])
            recorded = str(entry.get("command", ""))
        except (KeyError, TypeError, ValueError):
            return 0
        try:
            live_cmd = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 0
        first_token = recorded.split(" ", 1)[0]
        if not live_cmd or not first_token or first_token not in live_cmd:
            return 0  # gone, or pid reused by something else — leave it alone
        try:
            os.killpg(pgid, signal.SIGTERM)
            logger.warning("[exec-sessions] reaped orphan pgid=%s (%s)", pgid, recorded)
            return 1
        except (ProcessLookupError, PermissionError):
            return 0
```

- [ ] **Step 4: Run** — `pytest tests/test_exec_sessions_registry.py tests/test_exec_sessions_manager.py` → all pass (manager tests confirm the real registry doesn't break them)
- [ ] **Step 5: Commit** — `git commit -am "feat(exec-sessions): crash-reap registry file"`

---

### Task 5: ExecSessionToolSource (the 4 tools + gate)

**Files:**
- Create: `services/agentd-py/agentd/exec_sessions/tool_source.py`
- Test: `services/agentd-py/tests/test_exec_sessions_tool_source.py`

**Interfaces:**
- Consumes: `SessionManager` (Task 3); the controller's command-approval callback shape `async (command: str, args: list[str], cwd: str) -> CommandDecision` (see `controller._command_approval_cb`; `CommandDecision.approve: bool` from `agentd.domain.models`).
- Produces: `class ExecSessionToolSource(manager, thread_id: str, command_approval_callback)` with the tool-source protocol: `name = "exec_sessions"`, `definitions() -> list[ToolDefinition]`, `owns(tool) -> bool`, `async execute(tool, args) -> ToolOutput`. A still-running result ends with the literal guidance `Session <id> is still running. Poll with write_stdin(session_id, chars="") or stop it with kill_session.`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_exec_sessions_tool_source.py
import sys

import pytest

from agentd.domain.models import CommandDecision
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")
PY = sys.executable


def _source(tmp_path, approve=True, calls=None):
    async def cb(command, args, cwd):
        if calls is not None:
            calls.append((command, args, cwd))
        return CommandDecision(approve=approve)
    return ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)


def test_definitions_names(tmp_path):
    names = [d.name for d in _source(tmp_path).definitions()]
    assert names == ["start_session", "write_stdin", "kill_session", "list_sessions"]


@pytest.mark.asyncio
async def test_start_gated_and_fast_command_runs(tmp_path):
    calls = []
    src = _source(tmp_path, approve=True, calls=calls)
    out = await src.execute("start_session", {
        "command": PY, "args": ["-c", "print('ok')"], "yield_time_ms": 5000})
    assert not out.is_error and "ok" in out.output and "exit code: 0" in out.output
    assert calls and calls[0][0] == PY  # gate consulted


@pytest.mark.asyncio
async def test_start_rejected_is_error_no_spawn(tmp_path):
    src = _source(tmp_path, approve=False)
    out = await src.execute("start_session", {"command": PY, "args": ["-c", "print(1)"]})
    assert out.is_error and "rejected" in out.output.lower()
    assert "(none)" in (await src.execute("list_sessions", {})).output


@pytest.mark.asyncio
async def test_long_runner_roundtrip_poll_kill(tmp_path):
    src = _source(tmp_path)
    out = await src.execute("start_session", {
        "command": PY,
        "args": ["-u", "-c", "import time;print('up',flush=True);time.sleep(60)"],
        "yield_time_ms": 600})
    assert "still running" in out.output.lower() and "sess-" in out.output
    sid = "sess-" + out.output.split("sess-", 1)[1].split()[0].strip(".,:]")
    listed = await src.execute("list_sessions", {})
    assert sid in listed.output and "running" in listed.output
    poll = await src.execute("write_stdin", {"session_id": sid, "chars": ""})
    assert not poll.is_error
    killed = await src.execute("kill_session", {"session_id": sid})
    assert not killed.is_error
    assert "exited" in killed.output or "killed" in killed.output


@pytest.mark.asyncio
async def test_write_stdin_ungated(tmp_path):
    calls = []
    src = _source(tmp_path, approve=True, calls=calls)
    out = await src.execute("start_session", {
        "command": PY, "args": ["-c", "import time;time.sleep(60)"],
        "yield_time_ms": 300})
    sid = "sess-" + out.output.split("sess-", 1)[1].split()[0].strip(".,:]")
    await src.execute("write_stdin", {"session_id": sid, "chars": "x\n"})
    assert len(calls) == 1  # only the start was gated


@pytest.mark.asyncio
async def test_unknown_session_is_error_not_raise(tmp_path):
    out = await _source(tmp_path).execute("write_stdin", {"session_id": "sess-zzz"})
    assert out.is_error and "No session" in out.output


@pytest.mark.asyncio
async def test_oversized_stdin_rejected(tmp_path):
    """A blocked event loop is the failure mode: a huge write into a full PTY
    buffer would freeze the backend — reject before it reaches the fd."""
    src = _source(tmp_path)
    out = await src.execute("start_session", {
        "command": PY, "args": ["-c", "import time;time.sleep(60)"],
        "yield_time_ms": 300})
    sid = "sess-" + out.output.split("sess-", 1)[1].split()[0].strip(".,:]")
    res = await src.execute("write_stdin", {"session_id": sid, "chars": "x" * 5000})
    assert res.is_error and "too large" in res.output.lower()
```

- [ ] **Step 2: Run to verify fail** — ImportError

- [ ] **Step 3: Implement**

```python
# agentd/exec_sessions/tool_source.py
"""ExecSessionToolSource — PTY session tools for the controller.

start_session is gated through the SAME command-approval callback as
run_command (shell policy + remember-rules apply unchanged); write_stdin /
kill_session / list_sessions are ungated (they operate on an already-approved
process). Everything returns ToolOutput; is_error for unknown ids / cap /
spawn failures — the loop adapts, never crashes."""
from __future__ import annotations

from agentd.exec_sessions.config import result_max_chars
from agentd.exec_sessions.manager import (
    STDIN_MAX_CHARS, SessionCapError, SessionManager, SessionNotFoundError,
    SessionRead, SessionSpawnError,
)
from agentd.tools.registry import ToolDefinition, ToolOutput

_TOOLS = ("start_session", "write_stdin", "kill_session", "list_sessions")

_STILL_RUNNING_GUIDE = (
    'Session {sid} is still running. Poll with write_stdin(session_id, '
    'chars="") or stop it with kill_session.')


class ExecSessionToolSource:
    name = "exec_sessions"

    def __init__(self, manager: SessionManager, thread_id: str,
                 command_approval_callback) -> None:
        self._manager = manager
        self._thread_id = thread_id
        self._approve = command_approval_callback

    def definitions(self) -> list[ToolDefinition]:
        yield_prop = {"type": "integer", "description":
                      "How long to wait for output before returning, ms "
                      "(default 2000, clamped 250-30000)"}
        return [
            ToolDefinition(
                name="start_session",
                description=(
                    "Start a command in a PTY session (its own process group). "
                    "Use for anything long-running or interactive: dev servers, "
                    "watchers, REPLs, prompt-driven CLIs. If it exits within the "
                    "yield window you get the final output + exit code (like a "
                    "normal command); otherwise you get a session_id to poll via "
                    "write_stdin. Each start pauses for user approval — that "
                    "pause is expected. For quick one-shot commands prefer "
                    "run_command."),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string",
                                    "description": "Executable to run"},
                        "args": {"type": "array", "items": {"type": "string"},
                                 "description": "Arguments"},
                        "cwd": {"type": "string", "description":
                                "Workspace-relative working dir (default root)"},
                        "yield_time_ms": yield_prop,
                    },
                    "required": ["command"],
                }),
            ToolDefinition(
                name="write_stdin",
                description=(
                    "Send input to a running session AND/OR poll it: writes "
                    "`chars` to the PTY (empty string = pure poll, no write; "
                    "\\n submits a line; \\x03 is Ctrl-C — escape sequences "
                    "like \\x03 are decoded server-side, so sending the "
                    "literal characters works), waits the yield window, "
                    "returns only NEW output since your last read."),
                parameters={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "chars": {"type": "string", "description":
                                  "Raw chars to write; empty = just poll"},
                        "yield_time_ms": yield_prop,
                    },
                    "required": ["session_id"],
                }),
            ToolDefinition(
                name="kill_session",
                description=("Stop a session (SIGTERM then SIGKILL to its whole "
                             "process group). Returns any final unread output. "
                             "Always kill sessions you started unless the user "
                             "asked to keep them running."),
                parameters={
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"],
                }),
            ToolDefinition(
                name="list_sessions",
                description=("List this conversation's sessions: id, command, "
                             "running/exited, age, unread output size."),
                parameters={"type": "object", "properties": {}}),
        ]

    def owns(self, tool: str) -> bool:
        return tool in _TOOLS

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        try:
            if tool == "start_session":
                return await self._start(args)
            if tool == "write_stdin":
                chars = str(args.get("chars", ""))
                if len(chars) > STDIN_MAX_CHARS:
                    return ToolOutput(
                        output=(f"Error: chars too large ({len(chars)} > "
                                f"{STDIN_MAX_CHARS}). stdin is for interactive "
                                "input, not bulk data — write a file instead."),
                        is_error=True)
                return self._render(await self._manager.write_stdin(
                    self._thread_id, str(args.get("session_id", "")),
                    chars, args.get("yield_time_ms")))
            if tool == "kill_session":
                return self._render(await self._manager.kill(
                    self._thread_id, str(args.get("session_id", ""))),
                    killed=True)
            if tool == "list_sessions":
                return self._render_list()
        except SessionNotFoundError as exc:
            return ToolOutput(output=str(exc), is_error=True)
        except (SessionCapError, SessionSpawnError) as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)

    async def _start(self, args: dict[str, object]) -> ToolOutput:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolOutput(output="Error: start_session requires a command",
                              is_error=True)
        cmd_args = [str(a) for a in args.get("args") or []]
        cwd = str(args.get("cwd") or "")
        decision = await self._approve(command, cmd_args, cwd)
        if not getattr(decision, "approve", False):
            return ToolOutput(
                output=(f"Command rejected by user: {command}. Do not retry the "
                        "same command — adapt or ask."), is_error=True)
        read = await self._manager.start(
            self._thread_id, command, cmd_args, cwd or None,
            args.get("yield_time_ms"))
        return self._render(read)

    def _render(self, read: SessionRead, killed: bool = False) -> ToolOutput:
        out = read.new_output[-result_max_chars():]
        if read.still_running:
            head = f"[session {read.session_id} — running]\n"
            tail = "\n" + _STILL_RUNNING_GUIDE.format(sid=read.session_id)
        else:
            verb = "killed" if killed else "exited"
            head = (f"[session {read.session_id} — {verb} "
                    f"(exit code: {read.exit_code})]\n")
            tail = ""
        return ToolOutput(output=head + out + tail)

    def _render_list(self) -> ToolOutput:
        rows = self._manager.list_sessions(self._thread_id)
        if not rows:
            return ToolOutput(output="Sessions: (none)")
        lines = [
            f"- {r['id']}: `{r['command']}` [{r['status']}"
            + (f", exit {r['exit_code']}" if r["exit_code"] is not None else "")
            + f"] age {r['age_sec']}s, {r['unread_bytes']}B unread"
            for r in rows]
        return ToolOutput(output="Sessions:\n" + "\n".join(lines))
```

- [ ] **Step 4: Run** — `pytest tests/test_exec_sessions_tool_source.py` → 7 pass
- [ ] **Step 5: Commit** — `git commit -am "feat(exec-sessions): tool source (4 tools, gated start)"`

---

### Task 6: Controller / factory / main wiring + flag opt-ins

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`, `services/agentd-py/agentd/chat/controller_factory.py`, `services/agentd-py/agentd/main.py`, `scripts/stress/start-backend.sh`, repo-root `.env`
- Test: `services/agentd-py/tests/test_exec_sessions_wiring.py`

**Interfaces:**
- Consumes: `SessionManager`, `ExecSessionToolSource`, `is_exec_sessions_enabled` (from `agentd.exec_sessions.config`).
- Produces: `ChatController.__init__(..., exec_session_manager: object | None = None)` storing `self._exec_sessions`; `_build_registry(..., exec_session_source: object | None = None)` appends it; `_run_loop` constructs `ExecSessionToolSource(self._exec_sessions, thread_id, command_cb)` when the manager is present. `select_chat_handler` builds the manager from the frozen `workspace_path` when enabled. `main.py` registers startup reap + shutdown kill.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_exec_sessions_wiring.py
"""Registry + phase-availability wiring."""
import sys

import pytest

from agentd.domain.models import CommandDecision
from agentd.exec_sessions.manager import SessionManager
from agentd.exec_sessions.tool_source import ExecSessionToolSource
from agentd.chat.controller_loop import _decide_state_change_correction

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")


def test_registry_aggregation_dispatches_session_tools(tmp_path):
    # NOTE: verify the real import first:
    #   grep -rn "class AggregatingToolRegistry" services/agentd-py/agentd/
    from agentd.tools.aggregate import AggregatingToolRegistry  # ← adjust to actual module

    async def cb(command, args, cwd):
        return CommandDecision(approve=True)

    src = ExecSessionToolSource(SessionManager(tmp_path), "t1", cb)
    reg = AggregatingToolRegistry([src])
    names = [d.name for d in reg.definitions()]
    assert "start_session" in names and "list_sessions" in names


def test_session_tools_allowed_in_decide_phase():
    """Sessions are deliberately available in DECIDE (spec: live smokes are
    conversational). Only run_command is in _STATE_CHANGING_TOOLS — this is
    the regression guard against someone adding session tools to it."""
    for tool in ("start_session", "write_stdin", "kill_session", "list_sessions"):
        resp = {"type": "tool_call", "tool": tool, "args": {}}
        assert _decide_state_change_correction(resp, "DECIDE") is None
    # sanity: run_command IS still barred in DECIDE
    resp = {"type": "tool_call", "tool": "run_command", "args": {}}
    assert _decide_state_change_correction(resp, "DECIDE") is not None
```

- [ ] **Step 2: Run** — fix the `AggregatingToolRegistry` import to the real module until the aggregation test passes; the DECIDE test documents current behavior (should pass immediately — it is the regression guard).

- [ ] **Step 3: Wire the controller.** In `agentd/chat/controller.py`:
  - Ctor (after `mcp_manager: object | None = None`): add `exec_session_manager: object | None = None`; store `self._exec_sessions = exec_session_manager` next to `self._mcp_manager`.
  - `_build_registry(...)`: add parameter `exec_session_source: object | None = None`; after the doc_write append:
    ```python
    if exec_session_source is not None:
        sources.append(exec_session_source)
    ```
  - `_run_loop(...)`: after `command_cb = partial(self._command_approval_cb, thread_id, channel_id)` (controller.py:364):
    ```python
    # PTY exec sessions (thread-scoped; start gated through the SAME command
    # approval gate as run_command). Available in DECIDE and EDIT by design.
    exec_source = None
    if self._exec_sessions is not None:
        from agentd.exec_sessions.tool_source import ExecSessionToolSource
        exec_source = ExecSessionToolSource(
            self._exec_sessions, thread_id, command_cb)
    ```
    and pass `exec_session_source=exec_source` in the `self._build_registry(...)` call.

- [ ] **Step 4: Wire the factory.** In `controller_factory.select_chat_handler` (controller path only, where the MCP manager is built):
    ```python
    from agentd.exec_sessions.config import is_exec_sessions_enabled

    exec_manager = None
    if is_exec_sessions_enabled():
        from agentd.exec_sessions.manager import SessionManager
        exec_manager = SessionManager(Path(workspace_path))
    ```
    and pass `exec_session_manager=exec_manager` into the `ChatController(...)` construction.

- [ ] **Step 5: Wire main.py.** Next to the `_mcp_manager` block (`main.py:267`):
    ```python
    _exec_manager = getattr(_chat_agent, "_exec_sessions", None)
    if _exec_manager is not None:
        async def _reap_exec_orphans() -> None:
            _exec_manager.reap_orphans()
        app.router.add_event_handler("startup", _reap_exec_orphans)
        app.router.add_event_handler("shutdown", _exec_manager.shutdown)
    ```

- [ ] **Step 6: Flag opt-ins.** In `scripts/stress/start-backend.sh`, next to `CRUCIBLE_DOC_WRITE_ENABLED`: `export CRUCIBLE_EXEC_SESSIONS_ENABLED="${CRUCIBLE_EXEC_SESSIONS_ENABLED:-1}"`. Add `CRUCIBLE_EXEC_SESSIONS_ENABLED=1` to the repo-root `.env` beside the other chat flags.

- [ ] **Step 7: Run** — `pytest tests/test_exec_sessions_wiring.py`, then the full backend suite `pytest` (no regressions; the flag defaults OFF so existing tests are untouched)
- [ ] **Step 8: Commit** — `git commit -am "feat(exec-sessions): controller/factory/main wiring + flag opt-ins"`

---

### Task 7: Prompt teaching block

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py`
- Test: extend the existing controller-prompts test file (find it: `grep -rln "_DOC_WRITE_BLOCK\|format_controller_system_prompt" services/agentd-py/tests/` and mirror the `_DOC_WRITE_BLOCK` presence/absence test shape, including the real call signature of `format_controller_system_prompt`)

**Interfaces:**
- Consumes: the `_MCP_BLOCK`/`_DOC_WRITE_BLOCK` auto-append seam in `format_controller_system_prompt` (detection from `tool_definitions`, ~line 516 — no new parameter).
- Produces: `_SESSIONS_BLOCK` appended when any tool def is named `start_session`.

- [ ] **Step 1: Write the failing tests** (adapt the call shape to the existing tests):

```python
def test_sessions_block_appended_when_tools_present():
    defs = [{"name": "start_session"}]
    prompt = format_controller_system_prompt(tool_definitions=defs)
    assert "PTY session" in prompt and "kill_session" in prompt


def test_sessions_block_absent_without_tools():
    prompt = format_controller_system_prompt(tool_definitions=[])
    assert "PTY session" not in prompt
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement.** Next to `_DOC_WRITE_BLOCK` (~line 400):

```python
_SESSIONS_BLOCK = """
## BACKGROUND PROCESS SESSIONS

start_session runs a command in a PTY session that SURVIVES across turns —
use it for dev servers, watchers, REPLs, and anything interactive; use
run_command only for quick one-shot commands.
- Yield semantics: start_session waits ~2s (yield_time_ms). If the command
  finishes in time you get the final output; otherwise a session_id.
- Poll with write_stdin(session_id, chars="") — returns only NEW output since
  your last read. Send input with chars ("y\\n" answers a prompt; "\\x03" is
  Ctrl-C). Give slow processes a longer yield_time_ms instead of hammering
  short polls.
- Each start_session pauses for a user approval card — that pause is expected,
  not an error.
- ALWAYS kill_session what you started once you're done with it, unless the
  user asked to keep it running (say so explicitly if you leave one running).
- Sessions belong to this conversation. When resuming work, check
  list_sessions — a server you started earlier may still be up.
- Typical live smoke: start_session the server -> poll until the ready line
  appears -> run_command curl against it -> kill_session.
"""
```

and next to the `mcp__` detection in `format_controller_system_prompt` (match the exact local it appends to — read the function body first):

```python
    # exec-session teaching block: keyed off the merged tool definitions (same
    # pattern as the MCP/write_doc blocks) so no separate flag parameter is needed.
    if any(str((d or {}).get("name", "")) == "start_session"
           for d in tool_definitions if isinstance(d, dict)):
        base += _SESSIONS_BLOCK
```

(Insert directly after the `_DOC_WRITE_BLOCK` append, before `return base` — the function accumulates into a local string `base`.)

- [ ] **Step 4: Run** — targeted tests + full `pytest` green
- [ ] **Step 5: Commit** — `git commit -am "feat(exec-sessions): controller prompt teaching block"`

---

### Task 8: Backend routes + /live exposure

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py`, `services/agentd-py/agentd/api/routes.py`
- Test: `services/agentd-py/tests/test_exec_sessions_routes.py`

**Interfaces:**
- Consumes: `SessionManager.live_summaries(thread_id)` / `.transcript(thread_id, session_id)`; the `/live` route's `getattr(_chat_agent, "_active_turns", {})` pattern (routes.py:1346).
- Produces:
  - `ThreadLiveState.sessions: list[dict[str, Any]] | None = None` (models.py, after `todos`).
  - `/live` fills it: `mgr = getattr(_chat_agent, "_exec_sessions", None)` → `live.sessions = (mgr.live_summaries(thread_id) or None) if mgr is not None else None`.
  - `GET /v1/chat/threads/{thread_id}/sessions/{session_id}/transcript` → 200 `{output_tail, stdin_history, status, exit_code}` | 404 unknown session | 404 feature off (no manager).
  - `/v1/config` gains `"exec_sessions_enabled": is_exec_sessions_enabled()` (find the config route via `grep -n "skills_enabled" agentd/api/routes.py`, add beside it).

- [ ] **Step 1: Write the failing tests.** Read `agentd/chat/app_factory.py` first and mirror how existing chat-route tests build the app (`grep -rln "app_factory\|build_app" services/agentd-py/tests/ | head -3`). If `build_app` doesn't expose the chat handler, add `app.state.chat_agent = <handler>` inside it (a one-line, test-friendly improvement).

  **These route tests use a stub manager, deliberately** (the stub-`ControllerUI` pattern): they test the *view seam* (route → manager → JSON), while real process behavior is fully covered by Task 3. Two hazards the stub avoids: (a) driving a **sync `TestClient` from inside an async test can deadlock** the anyio portal — keep these tests sync; (b) a real `PtyProcess` needs a running event loop for `add_reader`, which a sync test doesn't have.

```python
# tests/test_exec_sessions_routes.py
import sys

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix tests")


class _StubExecManager:
    """View-seam stub; real process behavior is Task 3's coverage."""

    def live_summaries(self, thread_id):
        return [{"id": "sess-1", "command": "python -m http.server",
                 "status": "running", "exit_code": None,
                 "started_at": 1_720_000_000.0}]

    def transcript(self, thread_id, session_id):
        if session_id != "sess-1":
            return None
        return {"output_tail": "serving", "stdin_history": [],
                "status": "running", "exit_code": None}


@pytest.fixture()
def app_with_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "1")
    monkeypatch.setenv("CRUCIBLE_EXEC_SESSIONS_ENABLED", "1")
    from agentd.chat.app_factory import build_app
    return build_app(workspace_path=str(tmp_path))  # match build_app's real signature


def test_live_carries_sessions_and_transcript_roundtrip(app_with_sessions, tmp_path):
    client = TestClient(app_with_sessions)
    thread = client.post("/v1/chat/threads",
                         json={"workspace": str(tmp_path), "title": "t"}).json()
    tid = thread["thread_id"]
    app_with_sessions.state.chat_agent._exec_sessions = _StubExecManager()

    live = client.get(f"/v1/chat/threads/{tid}/live").json()
    assert live["sessions"] and live["sessions"][0]["status"] == "running"
    row = live["sessions"][0]
    # /live rows are the STABLE shape — a ticking age_sec/unread_bytes here
    # would churn the webview's lastLiveSignature at 1 Hz (review fix #2).
    assert set(row) == {"id", "command", "status", "exit_code", "started_at"}
    sid = row["id"]

    t = client.get(f"/v1/chat/threads/{tid}/sessions/{sid}/transcript").json()
    assert "serving" in t["output_tail"] and t["status"] == "running"

    missing = client.get(f"/v1/chat/threads/{tid}/sessions/sess-nope/transcript")
    assert missing.status_code == 404


def test_config_reports_flag(app_with_sessions):
    client = TestClient(app_with_sessions)
    assert client.get("/v1/config").json()["exec_sessions_enabled"] is True
```

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement.**
  - `models.py` — after `todos` on `ThreadLiveState`:
    ```python
    # Live exec sessions for this thread ({id, command, status, exit_code,
    # started_at}); None when the feature is off or no sessions. STABLE rows
    # only — no age_sec/unread_bytes, they'd churn the /live dedup signature
    # every tick (the webview computes age locally from started_at).
    sessions: list[dict[str, Any]] | None = None
    ```
  - `routes.py` `/live` handler — after the `live.turn_active` line (routes.py:1346):
    ```python
    _exec_mgr = getattr(_chat_agent, "_exec_sessions", None)
    if _exec_mgr is not None:
        rows = _exec_mgr.live_summaries(thread_id)
        live.sessions = rows or None
    ```
  - `routes.py` — new route beside `/doc-decision` (same chat-gated block):
    ```python
    @router.get("/chat/threads/{thread_id}/sessions/{session_id}/transcript")
    async def get_session_transcript(thread_id: str, session_id: str) -> dict:
        _exec_mgr = getattr(_chat_agent, "_exec_sessions", None)
        if _exec_mgr is None:
            raise HTTPException(status_code=404, detail="exec sessions disabled")
        transcript = _exec_mgr.transcript(thread_id, session_id)
        if transcript is None:
            raise HTTPException(status_code=404, detail="unknown session")
        return transcript
    ```
  - `/v1/config`: add `"exec_sessions_enabled": is_exec_sessions_enabled(),` (import from `agentd.exec_sessions.config`).

- [ ] **Step 4: Run** — `pytest tests/test_exec_sessions_routes.py` then full `pytest`
- [ ] **Step 5: Commit** — `git commit -am "feat(exec-sessions): /live sessions + transcript route + config flag"`

---

### Task 9: editor-client contracts

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`
- Test: extend the existing contracts test covering `ThreadLiveStateSchema` (`grep -rln "ThreadLiveStateSchema" apps/editor-client/src/**/*.test.ts`)

**Interfaces:**
- Produces (exact exported names later tasks use):
  ```typescript
  // STABLE /live rows (review fix #2): started_at instead of a ticking
  // age_sec/unread_bytes — the webview computes displayed age locally.
  export const SessionSummarySchema = z.object({
    id: z.string(),
    command: z.string(),
    status: z.enum(["running", "exited"]),
    exit_code: z.number().nullable(),
    started_at: z.number(),
  });
  export type SessionSummary = z.infer<typeof SessionSummarySchema>;

  export const SessionTranscriptSchema = z.object({
    output_tail: z.string(),
    stdin_history: z.array(z.object({ ts: z.number(), chars: z.string() })),
    status: z.enum(["running", "exited"]),
    exit_code: z.number().nullable(),
  });
  export type SessionTranscript = z.infer<typeof SessionTranscriptSchema>;
  ```
  `ThreadLiveStateSchema` (~line 279) gains `sessions: z.array(SessionSummarySchema).nullable().optional(),` (snake keys inside rows pass through unmapped, like the memory-inspect signals). `BackendTaskClient` (~line 410) gains `getSessionTranscript(threadId: string, sessionId: string): Promise<SessionTranscript>;`; `HttpBackendClient` implements it (`GET /v1/chat/threads/${threadId}/sessions/${sessionId}/transcript`, parsed with `SessionTranscriptSchema`).

- [ ] **Step 1: Write the failing test**

```typescript
it("parses live state with sessions and a transcript payload", () => {
  const live = ThreadLiveStateSchema.parse({
    active_task_id: null, turn_active: false, status: null,
    pending_gate: null, plan: null,
    sessions: [{ id: "sess-1", command: "python -m http.server", status: "running",
                 exit_code: null, started_at: 1720000000 }],
  });
  expect(live.sessions?.[0]?.id).toBe("sess-1");
  const t = SessionTranscriptSchema.parse({
    output_tail: "Serving HTTP", stdin_history: [{ ts: 1, chars: "" }],
    status: "exited", exit_code: 0,
  });
  expect(t.stdin_history[0]?.chars).toBe("");
});
```

- [ ] **Step 2: Run to verify fail** — `npm run -w @crucible/editor-client test`
- [ ] **Step 3: Implement** per the Produces block, following the neighbouring client-method conventions.
- [ ] **Step 4: Run** — `npm run -w @crucible/editor-client test && npm run -w @crucible/editor-client build` (build REQUIRED before Task 10 typechecks)
- [ ] **Step 5: Commit** — `git commit -am "feat(exec-sessions): editor-client session schemas + transcript client"`

---

### Task 10: Extension host (controller.ts + chat-panel.ts)

**Files:**
- Modify: `apps/vscode-extension/src/controller.ts`, `apps/vscode-extension/src/chat-panel.ts`
- Test: extend the existing controller vitest that stubs `ControllerUI` (find it: `grep -rln "renderLiveTodos" apps/vscode-extension --include="*.test.ts"`)

**Interfaces:**
- Consumes: `SessionSummary`, `SessionTranscript`, `getSessionTranscript` (Task 9).
- Produces:
  - `ControllerUI` gains `renderLiveSessions(view: LiveSessionsView): void;` and `clearLiveSessions(): void;` (next to `renderLiveTodos`, controller.ts:90). `export interface LiveSessionsView { items: SessionSummary[]; }`
  - **Dedup signature (controller.ts:1759): add `sessions: live.sessions,` to the `JSON.stringify({...})`** with an invariant comment in the style of the `todos:` line — this is the documented `/live` footgun; omit it and the strip never updates. The inverse footgun (review fix #2): this only works because `/live` session rows are STABLE (`started_at`, never a ticking `age_sec`/`unread_bytes`) — a mutating field in the rows would flip the signature on every 1 s poll and re-fire the whole render block at 1 Hz. The comment should state both directions.
  - After the todos render branch (controller.ts:~1876):
    ```typescript
    if (live.sessions?.length) {
      this.ui.renderLiveSessions({ items: live.sessions });
    } else {
      this.ui.clearLiveSessions();
    }
    ```
  - New method on `CrucibleController`: `async fetchSessionTranscript(sessionId: string): Promise<SessionTranscript | null>` — uses the current chat thread id + `this.clientForChat().getSessionTranscript(...)`; returns null on any error (webview shows "unavailable").
  - `chat-panel.ts`: implement the two `ControllerUI` methods as postMessages (`{type:"renderLiveSessions", sessions}` / `{type:"clearLiveSessions"}`, mirroring chat-panel.ts:416); handle webview message `{type:"fetchSessionTranscript", sessionId}` → call the controller method → post `{type:"sessionTranscript", sessionId, transcript}`.

- [ ] **Step 1: Write the failing test.** Every existing `ControllerUI` stub gains the two new no-op methods (the compile error is the failing state). Add tests: (a) a `/live` payload with sessions calls `renderLiveSessions` with the items; (b) an identical follow-up payload does NOT re-render (dedup); (c) a payload where one session's `status` flipped from running→exited DOES re-render (proves sessions is in the signature).
- [ ] **Step 2: Run to verify fail** — `npm run -w crucible-vscode-extension test`
- [ ] **Step 3: Implement** per the Produces block.
- [ ] **Step 4: Run** — `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
- [ ] **Step 5: Commit** — `git commit -am "feat(exec-sessions): host live-sessions render + transcript round-trip"`

---

### Task 11: Webview UI (SessionStrip + expand transcript)

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/SessionStrip.tsx`, `apps/vscode-extension/webview-ui/src/components/SessionStrip.test.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`, `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts`, `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx`, plus the component that passes `liveTodos=` into LiveSlot (find it: `grep -rn "liveTodos=" apps/vscode-extension/webview-ui/src`), plus the shared stylesheet next to the TodoCard styles

**Interfaces:**
- Consumes: host messages `renderLiveSessions` / `clearLiveSessions` / `sessionTranscript`; posts `fetchSessionTranscript`.
- Produces (webview mirror types in `types.ts` — this bundle does NOT import editor-client):
  ```typescript
  export interface LiveSessionItem {
    id: string; command: string; status: "running" | "exited";
    exit_code: number | null; started_at: number; // epoch sec; age computed locally
  }
  export interface LiveSessionsView { items: LiveSessionItem[]; }
  export interface SessionTranscriptView {
    output_tail: string;
    stdin_history: { ts: number; chars: string }[];
    status: "running" | "exited"; exit_code: number | null;
  }
  ```
  Host→webview message union (types.ts:~133, beside `renderLiveTodos`) gains `| { type: "renderLiveSessions"; sessions: LiveSessionsView } | { type: "clearLiveSessions" } | { type: "sessionTranscript"; sessionId: string; transcript: SessionTranscriptView | null }`.

- [ ] **Step 1: Write the failing component test**

```tsx
// SessionStrip.test.tsx — mirror the render patterns of TriggerDropdown.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { SessionStrip } from "./SessionStrip";

const items = [{ id: "sess-1", command: "python -m http.server 8765",
  status: "running" as const, exit_code: null,
  started_at: Date.now() / 1000 - 42 }];

it("renders a running session row", () => {
  render(<SessionStrip items={items} transcripts={{}} onExpand={() => {}} />);
  expect(screen.getByText(/http\.server 8765/)).toBeTruthy();
  expect(screen.getByText(/running/)).toBeTruthy();
});

it("expand requests the transcript and renders it when supplied", () => {
  const onExpand = vi.fn();
  const { rerender } = render(
    <SessionStrip items={items} transcripts={{}} onExpand={onExpand} />);
  fireEvent.click(screen.getByText(/http\.server 8765/));
  expect(onExpand).toHaveBeenCalledWith("sess-1");
  rerender(<SessionStrip items={items} onExpand={onExpand} transcripts={{
    "sess-1": { output_tail: "Serving HTTP on :: port 8765",
      stdin_history: [{ ts: 1, chars: "y\n" }], status: "running", exit_code: null },
  }} />);
  expect(screen.getByText(/Serving HTTP/)).toBeTruthy();
  expect(screen.getByText(/y\\n/)).toBeTruthy(); // control chars rendered escaped
});
```

- [ ] **Step 2: Run to verify fail** — the webview vitest command (check `apps/vscode-extension/webview-ui/package.json` scripts; existing component tests show the runner)

- [ ] **Step 3: Implement `SessionStrip.tsx`** (presentational; design tokens like TodoCard — no hardcoded hex):

```tsx
import { useState } from "react";
import type { LiveSessionItem, SessionTranscriptView } from "../types";

interface Props {
  items: LiveSessionItem[];
  transcripts: Record<string, SessionTranscriptView | null>;
  onExpand: (sessionId: string) => void; // posts fetchSessionTranscript; host re-poll while expanded
}

const esc = (chars: string) =>
  chars.replace(/[\x00-\x1f]/g, (c) =>
    c === "\n" ? "\\n" : `\\x${c.charCodeAt(0).toString(16).padStart(2, "0")}`);

// Age is computed HERE, not shipped in /live rows — a ticking age_sec in the
// payload would churn the host's lastLiveSignature every second (review fix #2).
// Display-only: computed at render time; never stored in app state.
const ageSec = (startedAt: number) =>
  Math.max(0, Math.floor(Date.now() / 1000 - startedAt));

export function SessionStrip({ items, transcripts, onExpand }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  if (items.length === 0) return null;
  return (
    <div className="session-strip">
      {items.map((s) => (
        <div key={s.id}>
          <button
            className="session-row"
            onClick={() => {
              const next = open === s.id ? null : s.id;
              setOpen(next);
              if (next) onExpand(s.id);
            }}
          >
            <span className={`session-dot ${s.status}`}>●</span>
            <code>{s.command}</code>
            <span className="session-meta">
              {s.status}
              {s.exit_code !== null ? ` (exit ${s.exit_code})` : ""} · {ageSec(s.started_at)}s
            </span>
          </button>
          {open === s.id && (
            <div className="session-transcript">
              {transcripts[s.id] === undefined && <div>Loading…</div>}
              {transcripts[s.id] === null && <div>Transcript unavailable.</div>}
              {transcripts[s.id] && (
                <>
                  <pre className="session-output">{transcripts[s.id]!.output_tail}</pre>
                  {transcripts[s.id]!.stdin_history.length > 0 && (
                    <div className="session-stdin">
                      <div>stdin sent:</div>
                      {transcripts[s.id]!.stdin_history.map((e, i) => (
                        <code key={i}>{esc(e.chars)}</code>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Wire state.** In `useAppState.ts`, mirror the `renderLiveTodos` reducer cases: `renderLiveSessions` stores `liveSessions`, `clearLiveSessions` nulls it AND clears the transcript map, `sessionTranscript` merges into `sessionTranscripts: Record<string, SessionTranscriptView | null>`. Expose `expandSession(sessionId)` that posts `{type: "fetchSessionTranscript", sessionId}`; while a session stays expanded, re-post every 2s (a `useEffect` interval inside `SessionStrip`'s parent or the strip itself — follow whichever matches existing overlay patterns; clear on collapse/unmount). Render `<SessionStrip …/>` from `LiveSlot` (new optional prop `liveSessions`, passed exactly like `liveTodos`), above the gate card. Styles beside the TodoCard styles: monospace `pre`, `overflow-x: auto`, `max-height: 240px; overflow-y: auto` on `.session-transcript`.
- [ ] **Step 5: Run** — webview tests, then root `npm run build && npm run test && npm run typecheck`
- [ ] **Step 6: Commit** — `git commit -am "feat(exec-sessions): webview session strip + expandable PTY transcript"`

---

### Task 12: Full-suite verification + live smoke

**Files:** none (verification only; fixes go in the task that owns the code)

- [ ] **Step 1: Full backend suite** — `cd services/agentd-py && pytest --color=no > /tmp/exec-sessions-pytest.txt 2>&1; echo exit=$?; tail -5 /tmp/exec-sessions-pytest.txt` → exit=0
- [ ] **Step 2: Full TS suite** — `npm run build && npm run test && npm run typecheck` at repo root → all green
- [ ] **Step 3: Live smoke (spec exit criteria).** Start the backend against a scratch workspace:
  ```bash
  export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
  bash scripts/stress/start-backend.sh --backend <provider> --workspace "$PWD/workspaces/crucible-stress" --validation-profile none
  code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/crucible-stress"
  ```
  Then, in the chat panel:
  1. Ask: *"start `python -m http.server 8765` in the background, confirm it serves, then leave it running"* → command gate → approve → session strip shows `● running`; the agent's poll output includes the "Serving HTTP" line.
  2. Click the strip row → transcript expands, shows the server log tail; refreshes while open.
  3. Send a SECOND message: *"is the server still up? curl it then stop it"* → `list_sessions` finds it (cross-turn survival), curl 200, `kill_session`, strip clears.
  4. `ps aux | grep http.server` → no orphan.
  5. Restart the backend mid-session with a server running (`kill -9` the uvicorn pid, restart) → startup log shows the reap; `ps` confirms the orphan was killed.
- [ ] **Step 4: Record results** — append a "Live smoke 2026-MM-DD" note to the spec with what was verified (house convention).
- [ ] **Step 5: Commit** — `git commit -am "docs(exec-sessions): live smoke results"`

---

## Dry-Run Review Fixes (2026-07-12, already folded into the tasks above)

A pre-implementation dry-run traced every runtime path (spawn → yield → poll →
kill → reload → restart) against the real seams. Seven findings, all fixed
inline; the fix sites are marked "review fix #N" in the tasks:

1. **Spawn hardening reuse (T3):** `start()` reuses `_split_command` +
   `resolve_workspace_bin` + the venv env hygiene from `tools/shell.py` /
   `tools/_paths.py`. Without them: whole-line commands (`"python -m
   http.server 8765"`) `FileNotFoundError` (exec does no word-splitting),
   naked names miss the workspace `.venv/bin`, and the backend's own
   `VIRTUAL_ENV` leaks into the child.
2. **Stable `/live` rows (T3/T8/T9/T10/T11):** `live_summaries` returns
   `{id, command, status, exit_code, started_at}` — never a ticking `age_sec`
   or per-log-line `unread_bytes`, which would flip `lastLiveSignature` every
   1 s poll and re-fire the whole render block at 1 Hz. Age is computed in the
   webview, display-only. `age_sec`/`unread_bytes` stay model-facing in
   `list_sessions`.
3. **Deterministic drain-on-exit (T2/T3):** `proc.wait()` returning does not
   mean the reader callback delivered the final chunk. `wait()==True` now
   implies a non-blocking `drain()` read-until-empty/EIO already ran — the
   0.05 s "drain sleep" heuristic is gone (it truncated fast-command output
   and let `_drop_if_drained` race the last bytes; also the #1 future
   test-flake source).
4. **win32 import guard (T2):** `pty`/`fcntl`/`termios` imports sit under
   `if sys.platform != "win32"` — unguarded, the module can't even import on
   Windows, making the winpty branch unreachable. `WinPtyProcess` is
   explicitly marked speculative (verify pywinpty's real API at impl time).
5. **stdin escape decoding (T3/T5):** JSON can't carry raw control bytes, so
   models emit `\x03` as four literal characters; `write_stdin` decodes
   `\n \r \t \xNN \uNNNN \\` server-side, and the tool description says so.
6. **Waiter-task hygiene (T2):** one persistent `proc.wait()` task per
   process, raced via `asyncio.wait({task}, timeout)` — the old
   `wait_for(shield(proc.wait()), t)` per poll leaked one pending task per
   timed-out poll. `get_running_loop()` throughout.
7. **Non-blocking PTY writes (T2/T5):** master fd is `O_NONBLOCK`; writes
   capped (4 KB at the fd, `STDIN_MAX_CHARS` at the tool with an `is_error`
   reject) — a large write into a full PTY buffer would otherwise block the
   entire event loop.
8. **Controlling-TTY acquisition (T2, round-2 dry run):**
   `start_new_session=True` + inherited slave fds gives the child a session
   but NO controlling terminal (dup2 is not an `open()`), so the PTY's
   foreground pgrp is empty and `\x03` Ctrl-C signals nobody — `write_stdin`
   interrupt semantics silently dead. Fix: `preexec_fn` doing
   `fcntl.ioctl(0, TIOCSCTTY, 0)` in the child (the `pty.fork()`/pexpect
   approach; dup2s + setsid precede preexec in CPython's child_exec, so fd 0
   is the slave and the child is a session leader). Kept to one raw syscall —
   preexec_fn runs between fork and exec in a threaded parent, so it must not
   touch allocators/locks.

Accepted-risk warts (global cap vs thread-local visibility, exited-unread
strip lingering, first-token pid-reuse guard, full-yield polls, UTF-8 cursor
splits) are documented in the spec's Deferred section — noted, not fixed in v1.
Shutdown kills were also made concurrent (`asyncio.gather`) and the T8 route
tests now use a stub manager + sync `TestClient` (async-test-drives-sync-client
deadlock hazard; real process behavior is T3's coverage).

## Self-Review Notes (already applied)

- **Spec coverage:** config/env table → T1+T6; PTY + group-kill + winsize + pywinpty → T2; yield/cursor/ring/retention/thread-scope/cap → T3; crash reap + pid-reuse guard → T4; 4 tools + gate + is_error discipline → T5; controller/factory/main/start-backend wiring + DECIDE availability → T6; teaching block → T7; /live + transcript route + /v1/config → T8; Zod/client → T9; signature invariant + host render + transcript round-trip → T10; strip + expand UI (inspect-never-advances-cursor is enforced server-side, tested in T3) → T11; live smoke incl. orphan + reap checks → T12.
- **Known judgment calls for implementers:** (a) the exact import of `AggregatingToolRegistry` and the real `build_app` / `format_controller_system_prompt` call shapes must be read from source at T6/T7/T8 — the greps to run are inlined at each site; (b) the webview 2s transcript re-poll may live in `SessionStrip` via `useEffect` or in the parent — follow the existing overlay patterns; (c) a shifting full-suite failure = order/state pollution — reproduce in isolation before attributing (house rule).
