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
from typing import Protocol
from uuid import uuid4

from agentd.exec_sessions.config import (
    buffer_bytes,
    clamp_yield_ms,
    max_session_count,
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


class PtyProcessLike(Protocol):
    """The PtyProcess/WinPtyProcess contract the manager relies on."""

    pid: int
    pgid: int

    def write(self, chars: str) -> None: ...
    def is_running(self) -> bool: ...
    def exit_code(self) -> int | None: ...
    async def wait(self, timeout_sec: float) -> bool: ...
    async def kill(self, grace_sec: float = 2.0) -> None: ...
    def close(self) -> None: ...


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
    proc: PtyProcessLike
    buffer: RingBuffer
    started_at: float
    model_cursor: int = 0
    stdin_history: list[dict[str, object]] = field(default_factory=list)


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
    def list_sessions(self, thread_id: str) -> list[dict[str, object]]:
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

    def live_summaries(self, thread_id: str) -> list[dict[str, object]]:
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

    def transcript(self, thread_id: str, session_id: str) -> dict[str, object] | None:
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
