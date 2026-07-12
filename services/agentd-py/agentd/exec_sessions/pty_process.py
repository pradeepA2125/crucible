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
- The child acquires the PTY as its controlling terminal via a TIOCSCTTY
  preexec — without it a session leader with only *inherited* slave fds has
  no ctty, the PTY's foreground pgrp is empty, and \\x03 signals nobody.
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
                    on_output: Callable[[bytes], None]) -> PtyProcess:
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
            # and \x03 (Ctrl-C) silently signals nobody. One raw syscall only —
            # preexec runs between fork and exec in a threaded parent, so it
            # must not touch allocators/locks.
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
        process-tree termination.

        UNVERIFIED: written against pywinpty's documented API without a
        Windows machine or CI — verify PTY.read/spawn/env semantics against
        the current pywinpty docs before first Windows use."""

        def __init__(self, pty_: winpty.PTY, on_output) -> None:
            self._pty = pty_
            self._on_output = on_output
            self._exit_code: int | None = None
            self.pid = pty_.pid
            self.pgid = pty_.pid
            self._loop = asyncio.get_running_loop()
            self._reader = self._loop.run_in_executor(None, self._read_loop)

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
                self._loop.call_soon_threadsafe(
                    self._on_output, data.encode("utf-8", "replace")
                    if isinstance(data, str) else data)

        def write(self, chars: str) -> None:
            self._pty.write(chars)

        def is_running(self) -> bool:
            return self._pty.isalive()

        def exit_code(self) -> int | None:
            return self._exit_code if not self._pty.isalive() else None

        def drain(self) -> None:
            pass  # the blocking reader thread owns the drain on win32

        async def wait(self, timeout_sec: float) -> bool:
            deadline = self._loop.time() + timeout_sec
            while self._pty.isalive():
                if self._loop.time() >= deadline:
                    return False
                await asyncio.sleep(0.05)
            return True

        async def kill(self, grace_sec: float = 2.0) -> None:
            self._pty.terminate(force=True)

        def close(self) -> None:
            del self._pty


def new_pty_process_class() -> type[PtyProcess]:
    """Platform dispatch — the manager calls this once. WinPtyProcess mirrors
    the PtyProcess contract structurally; the return type keeps callers typed."""
    if sys.platform == "win32":
        return WinPtyProcess  # type: ignore[return-value]
    return PtyProcess
