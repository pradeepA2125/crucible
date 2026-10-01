"""Chat rewind — per-turn checkpoints of the files the agent touched.

Chat controller edits are instant-promoted (TurnEditSession.accept -> promote_files)
and the turn shadow is rmtree'd at turn end, so nothing retains the pre-edit content.
This module captures it at the one moment it is still on disk: the start of
TurnEditSession.apply(), where the real workspace is the clean before-state (the
`shadow == real` invariant edit_session.py's own docstring asserts).

Capture is copy-on-first-write per checkpoint: the FIRST time a turn touches a path,
its current real content is copied aside. Later edits to the same path in the same
turn are no-ops, so the snapshot always holds the pre-turn state.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from agentd.chat.models import CapturedFile, ChatThread, Checkpoint
from agentd.chat.storage import ChatThreadStore

logger = logging.getLogger(__name__)

_RETENTION_ENV = "CRUCIBLE_REWIND_RETENTION_TURNS"
_MAX_BYTES_ENV = "CRUCIBLE_REWIND_MAX_FILE_BYTES"
_DEFAULT_RETENTION = 50
_DEFAULT_MAX_BYTES = 10_000_000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class RewindPreview(BaseModel):
    """What a rewind would cost, for the confirm dialog. Read-only."""
    messages: int
    files: int
    commands_run: int
    blocked_by_task: str | None = None


class RewindOutcome(BaseModel):
    """What a rewind actually did. `failed` and `oversize_files` are the honest half:
    a destructive operation that half-worked has to report which half."""
    restored_files: list[str] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    oversize_files: list[str] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)
    removed_messages: int = 0
    prefill_text: str = ""
    target_created_at: datetime | None = None


class RewindStore:
    def __init__(
        self,
        store: ChatThreadStore,
        workspace_path: Path,
        *,
        max_file_bytes: int | None = None,
        retention_turns: int | None = None,
    ) -> None:
        self._store = store
        self._root = Path(workspace_path)
        self._max_bytes = max_file_bytes or _env_int(_MAX_BYTES_ENV, _DEFAULT_MAX_BYTES)
        self._retention = retention_turns or _env_int(_RETENTION_ENV, _DEFAULT_RETENTION)

    def _files_dir(self, thread_id: str, seq: int) -> Path:
        return self._root / ".crucible" / "state" / "rewind" / thread_id / str(seq) / "files"

    def open_checkpoint(
        self,
        thread_id: str,
        anchor_message_id: str,
        turn_id: str,
        *,
        thread: ChatThread,
        memory_anchor_md: str | None,
    ) -> int:
        """Start a checkpoint for a turn, snapshotting pre-turn controller state.

        `thread` MUST be the object read at the top of handle_message, i.e. BEFORE
        _run_loop mutates history/todos/skill — those are the values a rewind restores.
        """
        seq = self._store.next_checkpoint_seq(thread_id)
        cp = Checkpoint(
            thread_id=thread_id, seq=seq, anchor_message_id=anchor_message_id,
            turn_id=turn_id, created_at=datetime.now(UTC), files=[],
            controller_history_json=(
                json.dumps(thread.controller_conversation_history)
                if thread.controller_conversation_history else None),
            controller_seed_json=(
                json.dumps(thread.controller_retrieval_seed)
                if thread.controller_retrieval_seed else None),
            controller_todo_json=(
                json.dumps(thread.controller_todos) if thread.controller_todos else None),
            controller_active_skill_json=(
                json.dumps(thread.controller_active_skill)
                if thread.controller_active_skill else None),
            memory_anchor_md=memory_anchor_md,
        )
        self._store.insert_checkpoint(cp)
        self._prune(thread_id)
        return seq

    def capture(self, thread_id: str, paths: list[str]) -> None:
        """Copy aside each path's current real content, first-write-wins.

        Writes into the thread's HIGHEST-seq checkpoint, so a continuation turn
        (resolve_mode -> implement, resolve_clarify) folds into the same anchor with
        no open/closed bookkeeping. No checkpoint -> silent no-op: the restart-recovery
        path (_promote_orphaned_edit) must never be broken by rewind bookkeeping.
        """
        checkpoints = self._store.list_checkpoints(thread_id)
        if not checkpoints:
            return
        cp = checkpoints[-1]
        known = {f.path for f in cp.files}
        added: list[CapturedFile] = []
        for rel in paths:
            if rel in known:
                continue
            known.add(rel)
            added.append(self._capture_one(thread_id, cp.seq, rel))
        if added:
            self._store.set_checkpoint_files(thread_id, cp.seq, cp.files + added)

    def _capture_one(self, thread_id: str, seq: int, rel: str) -> CapturedFile:
        source = self._root / rel
        if not source.exists():
            return CapturedFile(path=rel, existed=False)
        try:
            if source.stat().st_size > self._max_bytes:
                return CapturedFile(path=rel, existed=True, oversize=True)
            dest = self._files_dir(thread_id, seq) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            return CapturedFile(path=rel, existed=True)
        except OSError:
            # Marked oversize (i.e. "not restorable"), never silently "existed and
            # captured" — a rewind must not claim to restore bytes it never took.
            logger.warning("[rewind] could not capture %s", rel, exc_info=True)
            return CapturedFile(path=rel, existed=True, oversize=True)

    def _span(self, thread_id: str, anchor_message_id: str) -> list[Checkpoint]:
        """Checkpoints from the target forward, ascending. Empty when the anchor is
        unknown — which is how both preview and restore report "no rewind point"."""
        target = self._store.get_checkpoint_by_anchor(thread_id, anchor_message_id)
        if target is None:
            return []
        return [c for c in self._store.list_checkpoints(thread_id) if c.seq >= target.seq]

    @staticmethod
    def _fold(span: list[Checkpoint]) -> dict[str, tuple[int, CapturedFile]]:
        """Fold the span into one (seq, entry) per path, FIRST-SEEN WINS.

        The oldest recorded pre-state is the correct one to restore; this is what makes
        multi-turn rewind correct without storing a full workspace snapshot per turn.

        The seq travels WITH the entry deliberately. Restoring once re-derived the owning
        checkpoint separately ("the first one containing this path"), which agreed with
        the entry only because both meant "first" — two derivations of one fact, free to
        drift into restoring one checkpoint's flags with another's bytes.
        """
        folded: dict[str, tuple[int, CapturedFile]] = {}
        for cp in span:
            for f in cp.files:
                folded.setdefault(f.path, (cp.seq, f))
        return folded

    def preview(self, thread_id: str, anchor_message_id: str) -> RewindPreview | None:
        span = self._span(thread_id, anchor_message_id)
        if not span:
            return None
        thread = self._store.get_thread(thread_id)
        messages = 0
        commands = 0
        if thread is not None:
            index = next((i for i, m in enumerate(thread.messages)
                          if m.id == anchor_message_id), None)
            if index is not None:
                dropped = thread.messages[index:]
                messages = len(dropped)
                for m in dropped:
                    events = m.metadata.get("tool_events") or []
                    commands += sum(
                        1 for e in events
                        if isinstance(e, dict)
                        and str(e.get("tool", "")) in {"run_command", "session_start"})
        # Children of the rewound turns ran commands too (spec §11.6); count them so the
        # confirm dialog states everything a rewind cannot undo.
        span_turns = {cp.turn_id for cp in span}
        for record in self._store.list_agents(thread_id):
            if record.turn_id not in span_turns:
                continue
            for m in record.transcript:
                events = m.metadata.get("tool_events") or []
                commands += sum(
                    1 for e in events
                    if isinstance(e, dict)
                    and str(e.get("tool", "")) in {"run_command", "session_start"})
        return RewindPreview(
            messages=messages, files=len(self._fold(span)), commands_run=commands)

    def restore(self, thread_id: str, anchor_message_id: str) -> RewindOutcome | None:
        span = self._span(thread_id, anchor_message_id)
        if not span:
            return None
        target = span[0]
        outcome = RewindOutcome(target_created_at=target.created_at)

        # 1. Files. Per-path failures are COLLECTED, never raised: a rewind that
        #    half-worked must report it rather than stop silently partway.
        for path, (seq, entry) in self._fold(span).items():
            try:
                real = self._root / path
                if entry.oversize:
                    outcome.oversize_files.append(path)
                elif entry.existed:
                    # Same checkpoint the entry came from — flags and bytes cannot diverge.
                    stored = self._files_dir(thread_id, seq) / path
                    real.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(stored, real)
                    outcome.restored_files.append(path)
                else:
                    if real.exists():
                        real.unlink()
                    outcome.deleted_files.append(path)
            except OSError as exc:
                outcome.failed.append({"path": path, "error": str(exc)})

        # 2. Transcript + controller state.
        removed, prefill = self._store.truncate_messages_at(thread_id, anchor_message_id)
        outcome.removed_messages = removed
        outcome.prefill_text = prefill
        self._store.restore_controller_state(
            thread_id,
            history_json=target.controller_history_json,
            seed_json=target.controller_seed_json,
            todo_json=target.controller_todo_json,
            active_skill_json=target.controller_active_skill_json,
        )

        # 3. Drop the rewound checkpoints. Permanent, by design.
        for cp in span:
            shutil.rmtree(self._files_dir(thread_id, cp.seq).parent, ignore_errors=True)
        self._store.delete_checkpoints_from(thread_id, target.seq)
        return outcome

    def _prune(self, thread_id: str) -> None:
        checkpoints = self._store.list_checkpoints(thread_id)
        if len(checkpoints) <= self._retention:
            return
        cutoff = checkpoints[-self._retention].seq
        for cp in checkpoints:
            if cp.seq < cutoff:
                shutil.rmtree(self._files_dir(cp.thread_id, cp.seq).parent, ignore_errors=True)
        self._store.delete_checkpoints_before(thread_id, cutoff)
