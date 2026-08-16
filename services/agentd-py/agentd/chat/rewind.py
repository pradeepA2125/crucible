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

    def _prune(self, thread_id: str) -> None:
        checkpoints = self._store.list_checkpoints(thread_id)
        if len(checkpoints) <= self._retention:
            return
        cutoff = checkpoints[-self._retention].seq
        for cp in checkpoints:
            if cp.seq < cutoff:
                shutil.rmtree(self._files_dir(cp.thread_id, cp.seq).parent, ignore_errors=True)
        self._store.delete_checkpoints_before(thread_id, cutoff)
