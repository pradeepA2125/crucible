"""Thread-wide write log + attributed staleness guard for the shared workspace (spec §7).

Every promote — the main agent's and every sub-agent's — bumps one monotonic sequence and
records who wrote each file; every successful read_file records what the reader last saw.
An agent editing a file that ANOTHER agent promoted after the editor last saw it (or after
the editor spawned) is refused with that agent named, so it re-reads instead of silently
overwriting a sibling's work.

In memory only, one log per thread: after a backend restart the log is empty, which can
only ever miss a refusal, never produce a false one (spec §7.1).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAIN_AGENT_ID = "main"


def canonical_path(workspace_root: Path, raw: str) -> str | None:
    """The workspace-relative POSIX key for `raw` (relative, `./`-prefixed, or absolute
    inside the workspace), or None when it is empty or resolves outside the workspace.

    Reads and patch-op `file` values arrive in every one of those spellings; keying the
    log on one canonical form is what makes "read ./a.py, then edit a.py" count."""
    text = raw.strip()
    if not text:
        return None
    root = workspace_root.resolve()
    candidate = Path(text)
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        relative = absolute.resolve().relative_to(root)
    except ValueError:
        return None
    key = PurePosixPath(*relative.parts).as_posix()
    return None if key in ("", ".") else key


@dataclass(frozen=True)
class WriteRecord:
    agent_id: str
    label: str
    name: str
    seq: int


@dataclass
class _AgentView:
    spawn_seq: int
    last_seen: dict[str, int] = field(default_factory=dict)
    stale_refusals: int = 0


class WorkspaceWriteLog:
    """Sequence of promotes + per-agent read watermarks for one thread.

    Every method is synchronous and await-free, so a check (`stale_writer`) and the
    promote that follows it can never interleave with another agent's promote in
    single-process asyncio."""

    def __init__(self) -> None:
        self._seq = 0
        self._last_write: dict[str, WriteRecord] = {}
        # main's spawn_seq is 0 and its watermarks persist across turns: a parent editing
        # a file a child changed in an EARLIER turn, without re-reading, is refused too.
        self._agents: dict[str, _AgentView] = {MAIN_AGENT_ID: _AgentView(spawn_seq=0)}

    @property
    def seq(self) -> int:
        return self._seq

    def register_agent(self, agent_id: str) -> None:
        """A sub-agent sees the workspace as of its spawn: writes before it are not stale."""
        self._agents[agent_id] = _AgentView(spawn_seq=self._seq)

    def _view(self, agent_id: str) -> _AgentView:
        view = self._agents.get(agent_id)
        if view is None:
            raise KeyError(f"agent {agent_id!r} is not registered with this write log")
        return view

    def note_read(self, agent_id: str, path: str) -> None:
        self._view(agent_id).last_seen[path] = self._seq

    def note_promote(self, agent_id: str, label: str, name: str, paths: list[str]) -> int:
        """Record one promote of `paths` by `agent_id`; returns the new sequence number."""
        view = self._view(agent_id)
        self._seq += 1
        for path in paths:
            self._last_write[path] = WriteRecord(agent_id, label, name, self._seq)
            view.last_seen[path] = self._seq
        return self._seq

    def stale_writer(self, agent_id: str, path: str) -> WriteRecord | None:
        """The other agent whose promote `agent_id` has not seen, or None when it is safe."""
        record = self._last_write.get(path)
        if record is None or record.agent_id == agent_id:
            return None
        view = self._view(agent_id)
        if record.seq > max(view.spawn_seq, view.last_seen.get(path, -1)):
            return record
        return None

    def note_stale_refusal(self, agent_id: str) -> int:
        view = self._view(agent_id)
        view.stale_refusals += 1
        return view.stale_refusals

    def stale_refusals(self, agent_id: str) -> int:
        return self._view(agent_id).stale_refusals

    def read_observer(self, workspace_root: Path, agent_id: str) -> Callable[[str], None]:
        """A callback for BuiltinToolSource: records a successful read_file of `raw`.
        Paths outside the workspace are not tracked (they can never be promoted)."""
        def observe(raw: str) -> None:
            key = canonical_path(workspace_root, raw)
            if key is not None:
                self.note_read(agent_id, key)
        return observe


@dataclass(frozen=True)
class WriteGuard:
    """What a TurnEditSession needs to check and record writes for one agent."""
    log: WorkspaceWriteLog
    agent_id: str
    label: str
    name: str
