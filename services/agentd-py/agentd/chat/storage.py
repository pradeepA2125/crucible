from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timezone
from pathlib import Path

from agentd.chat.models import (
    AgentRecord,
    CapturedFile,
    ChatMessage,
    ChatThread,
    Checkpoint,
    PendingGate,
)


class ChatThreadStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_threads (
                thread_id TEXT PRIMARY KEY,
                workspace_path TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at TEXT NOT NULL,
                messages_json TEXT NOT NULL DEFAULT '[]',
                touched_files_json TEXT NOT NULL DEFAULT '[]'
            );
        """)
        # active_task_id was added after the table shipped; ALTER existing DBs.
        # IF NOT EXISTS on the table create above won't add a column to pre-existing rows.
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(chat_threads)")}
        if "active_task_id" not in existing:
            self._conn.execute("ALTER TABLE chat_threads ADD COLUMN active_task_id TEXT")
        # Controller-turn gate (mode/edit), added after the table shipped.
        if "controller_gate_json" not in existing:
            self._conn.execute("ALTER TABLE chat_threads ADD COLUMN controller_gate_json TEXT")
        # Durable controller conversation history (seed_history substrate), added later.
        if "controller_history_json" not in existing:
            self._conn.execute("ALTER TABLE chat_threads ADD COLUMN controller_history_json TEXT")
        # Pinned retrieval seed (cache-prefix head), added later.
        if "controller_seed_json" not in existing:
            self._conn.execute("ALTER TABLE chat_threads ADD COLUMN controller_seed_json TEXT")
        # Request-scoped todo ledger (survives DECIDE->EDIT + clarify resume), added later.
        if "controller_todo_json" not in existing:
            self._conn.execute("ALTER TABLE chat_threads ADD COLUMN controller_todo_json TEXT")
        # Thread-scoped active skill (survives every turn boundary), added later.
        if "controller_active_skill_json" not in existing:
            self._conn.execute(
                "ALTER TABLE chat_threads ADD COLUMN controller_active_skill_json TEXT")
        # Rewind checkpoints: one row per turn, holding the thread state from just
        # before it started. File bytes live on disk under .crucible/state/rewind/.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_checkpoints (
                thread_id                     TEXT NOT NULL,
                seq                           INTEGER NOT NULL,
                anchor_message_id             TEXT NOT NULL,
                turn_id                       TEXT NOT NULL,
                created_at                    TEXT NOT NULL,
                files_json                    TEXT NOT NULL DEFAULT '[]',
                controller_history_json       TEXT,
                controller_seed_json          TEXT,
                controller_todo_json          TEXT,
                controller_active_skill_json  TEXT,
                memory_anchor_md              TEXT,
                PRIMARY KEY (thread_id, seq)
            );
        """)
        # Sub-agents (spec §11.3): one row per dispatched agent, its transcript inline.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_agents (
                agent_id            TEXT PRIMARY KEY,
                thread_id           TEXT NOT NULL,
                turn_id             TEXT NOT NULL,
                parent_agent_id     TEXT,
                depth               INTEGER NOT NULL,
                name                TEXT NOT NULL,
                label               TEXT NOT NULL,
                prompt              TEXT NOT NULL,
                status              TEXT NOT NULL,
                report              TEXT NOT NULL DEFAULT '',
                files_changed_json  TEXT NOT NULL DEFAULT '[]',
                stale_refusals      INTEGER NOT NULL DEFAULT 0,
                transcript_json     TEXT NOT NULL DEFAULT '[]',
                started_at          TEXT,
                ended_at            TEXT
            );
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS chat_agents_by_turn ON chat_agents(thread_id, turn_id)")
        self._conn.commit()

    @staticmethod
    def _history_from_row(row: sqlite3.Row) -> list[dict] | None:
        raw = row["controller_history_json"]
        return json.loads(raw) if raw else None

    @staticmethod
    def _seed_from_row(row: sqlite3.Row) -> dict | None:
        raw = row["controller_seed_json"]
        return json.loads(raw) if raw else None

    @staticmethod
    def _todos_from_row(row: sqlite3.Row) -> list[dict] | None:
        raw = row["controller_todo_json"]
        return json.loads(raw) if raw else None

    @staticmethod
    def _active_skill_from_row(row: sqlite3.Row) -> dict | None:
        raw = row["controller_active_skill_json"]
        return json.loads(raw) if raw else None

    @staticmethod
    def _gates_from_row(row: sqlite3.Row) -> list[PendingGate]:
        """The column holds a JSON list of gates. A row written before multi-gate holds a
        single gate object: read it as a one-item list with the deterministic id
        "legacy-{kind}", so repeated reads (and a decision route) agree on its id."""
        raw = row["controller_gate_json"]
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data.setdefault("gate_id", f"legacy-{data.get('kind', 'gate')}")
            return [PendingGate.model_validate(data)]
        return [PendingGate.model_validate(g) for g in data]

    def _write_gates(self, thread_id: str, gates: list[PendingGate]) -> None:
        raw = json.dumps([g.model_dump(mode="json") for g in gates]) if gates else None
        self._conn.execute(
            "UPDATE chat_threads SET controller_gate_json = ? WHERE thread_id = ?",
            (raw, thread_id),
        )
        self._conn.commit()

    def _read_gates(self, thread_id: str) -> list[PendingGate]:
        row = self._conn.execute(
            "SELECT controller_gate_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return self._gates_from_row(row) if row is not None else []

    def create_thread(self, workspace_path: str, title: str = "New Chat") -> ChatThread:
        thread_id = f"chat-{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO chat_threads (thread_id, workspace_path, title, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, workspace_path, title, created_at),
        )
        self._conn.commit()
        return ChatThread(
            thread_id=thread_id,
            workspace_path=workspace_path,
            title=title,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_threads(self, workspace_path: str) -> list[ChatThread]:
        rows = self._conn.execute(
            "SELECT * FROM chat_threads WHERE workspace_path = ? ORDER BY created_at DESC",
            (workspace_path,),
        ).fetchall()
        return [
            ChatThread(
                thread_id=row["thread_id"],
                workspace_path=row["workspace_path"],
                title=row["title"],
                created_at=datetime.fromisoformat(row["created_at"]),
                messages=[ChatMessage.model_validate(m) for m in json.loads(row["messages_json"])],
                touched_files=json.loads(row["touched_files_json"]),
                active_task_id=row["active_task_id"],
                pending_controller_gates=self._gates_from_row(row),
                controller_conversation_history=self._history_from_row(row),
                controller_retrieval_seed=self._seed_from_row(row),
                controller_todos=self._todos_from_row(row),
                controller_active_skill=self._active_skill_from_row(row),
            )
            for row in rows
        ]

    def get_thread(self, thread_id: str) -> ChatThread | None:
        row = self._conn.execute(
            "SELECT * FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        return ChatThread(
            thread_id=row["thread_id"],
            workspace_path=row["workspace_path"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            messages=[ChatMessage.model_validate(m) for m in json.loads(row["messages_json"])],
            touched_files=json.loads(row["touched_files_json"]),
            active_task_id=row["active_task_id"],
            pending_controller_gates=self._gates_from_row(row),
            controller_conversation_history=self._history_from_row(row),
            controller_retrieval_seed=self._seed_from_row(row),
            controller_todos=self._todos_from_row(row),
            controller_active_skill=self._active_skill_from_row(row),
        )

    def set_controller_seed(self, thread_id: str, seed: dict | None) -> None:
        """Pin the thread's retrieval seed (frozen cache-prefix head). Written once on
        first compute; replayed verbatim thereafter so the KV prefix survives restart."""
        raw = json.dumps(seed) if seed else None
        self._conn.execute(
            "UPDATE chat_threads SET controller_seed_json = ? WHERE thread_id = ?",
            (raw, thread_id),
        )
        self._conn.commit()

    def set_controller_history(
        self, thread_id: str, history: list[dict] | None
    ) -> None:
        """Persist the controller loop's verbatim turn history. Mirrors
        set_controller_seed: an in-place durable update the next turn rehydrates
        seed_history from (parity with TaskRecord.planning_conversation_history)."""
        raw = json.dumps(history) if history else None
        self._conn.execute(
            "UPDATE chat_threads SET controller_history_json = ? WHERE thread_id = ?",
            (raw, thread_id),
        )
        self._conn.commit()

    # Gate list mutations (spec §4.5). Each is a synchronous read-modify-write with no
    # await, so it is race-safe in single-process asyncio: two concurrent raise/resolve
    # sites can never interleave inside one of these.
    def add_controller_gate(self, thread_id: str, gate: PendingGate) -> PendingGate:
        """Append a gate; assigns a uuid when gate_id is "" (not yet stored). Returns
        the stored gate (with its id)."""
        stored = gate if gate.gate_id else gate.model_copy(update={"gate_id": uuid.uuid4().hex})
        self._write_gates(thread_id, [*self._read_gates(thread_id), stored])
        return stored

    def remove_controller_gate(self, thread_id: str, gate_id: str) -> bool:
        """Remove one gate by id. False when no such gate is pending."""
        gates = self._read_gates(thread_id)
        kept = [g for g in gates if g.gate_id != gate_id]
        if len(kept) == len(gates):
            return False
        self._write_gates(thread_id, kept)
        return True

    def clear_controller_gates(self, thread_id: str, *, agent_id: str | None = None) -> None:
        """Clear every gate, or only the gates one sub-agent raised."""
        if agent_id is None:
            self._write_gates(thread_id, [])
            return
        kept = [g for g in self._read_gates(thread_id)
                if g.agent is None or g.agent.id != agent_id]
        self._write_gates(thread_id, kept)


    def set_controller_todos(self, thread_id: str, raw: str | None) -> None:
        """Persist (raw = TodoLedger.to_json()) or clear (raw = None) the request's todo
        ledger. Mirrors set_controller_history: an in-place durable update the next loop
        run (mode-gate / clarify resume) rehydrates via TodoLedger.from_json."""
        self._conn.execute(
            "UPDATE chat_threads SET controller_todo_json = ? WHERE thread_id = ?",
            (raw, thread_id),
        )
        self._conn.commit()

    def set_controller_active_skill(self, thread_id: str, raw: str | None) -> None:
        """Persist (raw = json.dumps({"name","body"})) or clear (raw = None) the thread's
        single active skill. Deliberately survives every turn boundary including "answer"
        outcomes (unlike set_controller_todos) — a skill's mid-flow step can itself be an
        answer awaiting open-ended reply, so clearing on outcome kind would drop it right
        when the next turn needs it. Eviction happens naturally when read_skill next
        activates a DIFFERENT skill (SkillToolSource replaces, never accumulates)."""
        self._conn.execute(
            "UPDATE chat_threads SET controller_active_skill_json = ? WHERE thread_id = ?",
            (raw, thread_id),
        )
        self._conn.commit()

    def get_controller_active_skill(self, thread_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT controller_active_skill_json FROM chat_threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return row["controller_active_skill_json"] if row else None

    def get_controller_todos(self, thread_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT controller_todo_json FROM chat_threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return row["controller_todo_json"] if row else None

    def set_active_task(self, thread_id: str, task_id: str) -> None:
        """Point the thread at its current task. Resume churns the id (parent→child);
        this is the durable link the UI follows so gate/plan views survive that churn."""
        self._conn.execute(
            "UPDATE chat_threads SET active_task_id = ? WHERE thread_id = ?",
            (task_id, thread_id),
        )
        self._conn.commit()

    def append_message(self, thread_id: str, message: ChatMessage) -> str | None:
        """Append a message, stamping it with a rewind anchor id. Returns that id."""
        if message.id is None:
            message = message.model_copy(update={"id": uuid.uuid4().hex})
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        messages = json.loads(row["messages_json"])
        messages.append(message.model_dump(mode="json"))
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(messages), thread_id),
        )
        self._conn.commit()
        return message.id

    def upsert_inflight_pills(
        self,
        thread_id: str,
        turn_id: str,
        tool_events: list[dict],
        thinking_log: list[str] | None = None,
    ) -> None:
        """Append-or-update the in-flight turn's pills-only agent message (finding 5).

        Mid-turn the controller has no durable pill record — only live SSE + the bounded
        replay buffer, both lost on a thread switch / panel reopen before the turn
        completes. This writes a durable pills-only message tagged with
        ``metadata.inflight_turn_id`` and updates it in place on each tool result, so
        getChatThread reconstructs the partial pills. ``_finish`` later finalizes the
        SAME message (matched by turn id), so there is no duplicate."""
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return
        messages: list[dict] = json.loads(row["messages_json"])
        metadata: dict = {"inflight_turn_id": turn_id, "tool_events": tool_events}
        if thinking_log:
            metadata["thinking_log"] = thinking_log
        existing = next(
            (m for m in messages
             if (m.get("metadata") or {}).get("inflight_turn_id") == turn_id),
            None,
        )
        if existing is not None:
            existing["metadata"] = metadata
        else:
            messages.append(
                ChatMessage(role="agent", content="", metadata=metadata).model_dump(mode="json")
            )
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(messages), thread_id),
        )
        self._conn.commit()

    def finalize_inflight_pills(
        self,
        thread_id: str,
        turn_id: str,
        content: str,
        tool_events: list[dict],
        thinking_log: list[str] | None = None,
    ) -> bool:
        """Finalize the in-flight pills message at turn end: set the final content +
        pills and DROP the ``inflight_turn_id`` marker so it becomes a normal message.
        Returns True if an in-flight message existed (so the caller skips appending a
        duplicate). False when the turn produced no tool calls (no in-flight message)."""
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return False
        messages: list[dict] = json.loads(row["messages_json"])
        existing = next(
            (m for m in messages
             if (m.get("metadata") or {}).get("inflight_turn_id") == turn_id),
            None,
        )
        if existing is None:
            return False
        metadata: dict = {}
        if tool_events:
            metadata["tool_events"] = tool_events
        if thinking_log:
            metadata["thinking_log"] = thinking_log
        existing["content"] = content
        existing["metadata"] = metadata
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(messages), thread_id),
        )
        self._conn.commit()
        return True

    def seal_inflight_pills(self, thread_id: str, turn_id: str) -> None:
        """Freeze THIS turn's in-flight pills message in place (finding 1) — drop its
        ``inflight_turn_id`` marker WITHOUT touching content or metadata, a no-op if
        none exists yet.

        Called when a non-terminal `progress` note is persisted, BEFORE the note's own
        ``append_message``. Without this, ``finalize_inflight_pills`` at turn end updates
        the SAME message object created at the FIRST tool result (before the note was
        appended), so the final answer lands ahead of the note in transcript order
        despite happening after it live. Sealing here means the message stays exactly
        where it is (pills accumulated up to the note, in order), and the NEXT tool
        result's ``upsert_inflight_pills`` — finding no message with this turn_id any
        more — appends a FRESH in-flight message positioned after the note, mirroring
        the live webview's ``sealStreaming`` split around each note."""
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return
        messages: list[dict] = json.loads(row["messages_json"])
        existing = next(
            (m for m in messages
             if (m.get("metadata") or {}).get("inflight_turn_id") == turn_id),
            None,
        )
        if existing is None:
            return
        metadata = dict(existing.get("metadata") or {})
        metadata.pop("inflight_turn_id", None)
        existing["metadata"] = metadata
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(messages), thread_id),
        )
        self._conn.commit()

    def clear_inflight_markers(self, thread_id: str) -> None:
        """Drop the ``inflight_turn_id`` marker from any lingering in-flight pills message
        (a prior turn that was stopped/restart-orphaned before finalize). The pills STAY
        (finding 5 — partial pills survive), only the marker is removed — so the next
        turn's switch-back dedup is scoped to ITS own message and a stale message's
        per-turn pill ids can't false-match. Called at turn start."""
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return
        messages: list[dict] = json.loads(row["messages_json"])
        changed = False
        for m in messages:
            md = m.get("metadata") or {}
            if md.get("inflight_turn_id") is not None:
                md.pop("inflight_turn_id", None)
                m["metadata"] = md
                changed = True
        if changed:
            self._conn.execute(
                "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
                (json.dumps(messages), thread_id),
            )
            self._conn.commit()

    def append_plan_card(self, thread_id: str, task_id: str, plan_markdown: str) -> bool:
        """Append a plan version to a task's transcript, building a version history.

        Each feedback round produces a new plan; appending (rather than replacing)
        preserves the evolution — old plan, then ``↻ feedback`` breadcrumb, then the
        new plan. To honour "no duplicate", a write identical to the task's CURRENT
        latest plan_card is skipped (collapses the double-writer / re-presentation).
        Returns True only when a card was actually appended — lets the caller
        broadcast the live append exactly once per version.
        """
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return False
        messages: list[dict] = json.loads(row["messages_json"])
        latest = None
        for msg in messages:
            if msg.get("type") == "plan_card" and msg.get("task_id") == task_id:
                latest = msg
        if latest is not None and latest.get("content") == plan_markdown:
            return False  # identical to the current latest version — no duplicate
        messages.append(
            ChatMessage(
                role="agent", content=plan_markdown, type="plan_card", task_id=task_id,
                metadata={"taskId": task_id, "plan_markdown": plan_markdown},
            ).model_dump(mode="json")
        )
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(messages), thread_id),
        )
        self._conn.commit()
        return True

    def update_title(self, thread_id: str, title: str) -> None:
        self._conn.execute(
            "UPDATE chat_threads SET title = ? WHERE thread_id = ?", (title, thread_id)
        )
        self._conn.commit()

    def resolve_diff_card(self, inline_task_id: str, resolution: str) -> None:
        """Mark a diff_card message as resolved (applied/discarded) across all threads."""
        rows = self._conn.execute(
            "SELECT thread_id, messages_json FROM chat_threads"
        ).fetchall()
        for row in rows:
            messages: list[dict] = json.loads(row["messages_json"])
            changed = False
            for msg in messages:
                if msg.get("type") == "diff_card" and msg.get("task_id") == inline_task_id:
                    msg.setdefault("metadata", {})["resolved"] = resolution
                    changed = True
            if changed:
                self._conn.execute(
                    "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
                    (json.dumps(messages), row["thread_id"]),
                )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Rewind checkpoints
    # ------------------------------------------------------------------
    def truncate_messages_at(self, thread_id: str, anchor_message_id: str) -> tuple[int, str]:
        """Drop the anchor message and everything after it.

        Returns (removed_count, anchor_text). The anchor's own text is returned so the
        composer can prefill it — that is the persisted display content, NOT the
        @-mention-expanded turn_message (turn-scoped, deliberately never stored).
        """
        row = self._conn.execute(
            "SELECT messages_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return 0, ""
        messages: list[dict[str, object]] = json.loads(row["messages_json"])
        index = next(
            (i for i, m in enumerate(messages) if m.get("id") == anchor_message_id), None)
        if index is None:
            return 0, ""
        removed = messages[index:]
        kept = messages[:index]
        self._conn.execute(
            "UPDATE chat_threads SET messages_json = ? WHERE thread_id = ?",
            (json.dumps(kept), thread_id),
        )
        self._conn.commit()
        return len(removed), str(removed[0].get("content") or "")

    def restore_controller_state(
        self, thread_id: str, *, history_json: str | None, seed_json: str | None,
        todo_json: str | None, active_skill_json: str | None,
    ) -> None:
        """Overwrite the four controller blobs with a checkpoint's verbatim copies and
        clear any pending gate (a gate from a turn that no longer exists is
        unresolvable — its in-memory waiter is gone with the turn)."""
        self._conn.execute(
            "UPDATE chat_threads SET controller_history_json = ?, controller_seed_json = ?, "
            "controller_todo_json = ?, controller_active_skill_json = ?, "
            "controller_gate_json = NULL WHERE thread_id = ?",
            (history_json, seed_json, todo_json, active_skill_json, thread_id),
        )
        self._conn.commit()

    @staticmethod
    def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
        return Checkpoint(
            thread_id=row["thread_id"], seq=row["seq"],
            anchor_message_id=row["anchor_message_id"], turn_id=row["turn_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            files=[CapturedFile.model_validate(f) for f in json.loads(row["files_json"])],
            controller_history_json=row["controller_history_json"],
            controller_seed_json=row["controller_seed_json"],
            controller_todo_json=row["controller_todo_json"],
            controller_active_skill_json=row["controller_active_skill_json"],
            memory_anchor_md=row["memory_anchor_md"],
        )

    def next_checkpoint_seq(self, thread_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(seq) AS m FROM chat_checkpoints WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return 0 if row["m"] is None else row["m"] + 1

    def insert_checkpoint(self, cp: Checkpoint) -> None:
        self._conn.execute(
            "INSERT INTO chat_checkpoints (thread_id, seq, anchor_message_id, turn_id, "
            "created_at, files_json, controller_history_json, controller_seed_json, "
            "controller_todo_json, controller_active_skill_json, memory_anchor_md) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cp.thread_id, cp.seq, cp.anchor_message_id, cp.turn_id,
             cp.created_at.isoformat(),
             json.dumps([f.model_dump(mode="json") for f in cp.files]),
             cp.controller_history_json, cp.controller_seed_json, cp.controller_todo_json,
             cp.controller_active_skill_json, cp.memory_anchor_md),
        )
        self._conn.commit()

    def list_checkpoints(self, thread_id: str) -> list[Checkpoint]:
        rows = self._conn.execute(
            "SELECT * FROM chat_checkpoints WHERE thread_id = ? ORDER BY seq", (thread_id,)
        ).fetchall()
        return [self._checkpoint_from_row(r) for r in rows]

    def get_checkpoint_by_anchor(
        self, thread_id: str, anchor_message_id: str
    ) -> Checkpoint | None:
        row = self._conn.execute(
            "SELECT * FROM chat_checkpoints WHERE thread_id = ? AND anchor_message_id = ?",
            (thread_id, anchor_message_id),
        ).fetchone()
        return self._checkpoint_from_row(row) if row else None

    def set_checkpoint_files(
        self, thread_id: str, seq: int, files: list[CapturedFile]
    ) -> None:
        self._conn.execute(
            "UPDATE chat_checkpoints SET files_json = ? WHERE thread_id = ? AND seq = ?",
            (json.dumps([f.model_dump(mode="json") for f in files]), thread_id, seq),
        )
        self._conn.commit()

    def delete_checkpoints_from(self, thread_id: str, seq: int) -> None:
        self._conn.execute(
            "DELETE FROM chat_checkpoints WHERE thread_id = ? AND seq >= ?", (thread_id, seq)
        )
        self._conn.commit()

    def delete_checkpoints_before(self, thread_id: str, seq: int) -> None:
        self._conn.execute(
            "DELETE FROM chat_checkpoints WHERE thread_id = ? AND seq < ?", (thread_id, seq)
        )
        self._conn.commit()

    def add_touched_file(self, thread_id: str, file_path: str) -> None:
        row = self._conn.execute(
            "SELECT touched_files_json FROM chat_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        files: list[str] = json.loads(row["touched_files_json"])
        if file_path not in files:
            files.append(file_path)
        self._conn.execute(
            "UPDATE chat_threads SET touched_files_json = ? WHERE thread_id = ?",
            (json.dumps(files), thread_id),
        )
        self._conn.commit()

    # ── Sub-agents (spec §11.3) ─────────────────────────────────────────────
    _AGENT_UPDATABLE = {
        "status": "status", "report": "report", "files_changed": "files_changed_json",
        "stale_refusals": "stale_refusals", "started_at": "started_at",
        "ended_at": "ended_at",
    }

    def insert_agent(self, record: AgentRecord) -> None:
        self._conn.execute(
            "INSERT INTO chat_agents (agent_id, thread_id, turn_id, parent_agent_id, depth, "
            "name, label, prompt, status, report, files_changed_json, stale_refusals, "
            "transcript_json, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.agent_id, record.thread_id, record.turn_id, record.parent_agent_id,
             record.depth, record.name, record.label, record.prompt, record.status,
             record.report, json.dumps(record.files_changed), record.stale_refusals,
             json.dumps([m.model_dump(mode="json") for m in record.transcript]),
             record.started_at.isoformat() if record.started_at else None,
             record.ended_at.isoformat() if record.ended_at else None))
        self._conn.commit()

    def update_agent(
        self, agent_id: str, *, status: str | None = None, report: str | None = None,
        files_changed: list[str] | None = None, stale_refusals: int | None = None,
        started_at: datetime | None = None, ended_at: datetime | None = None,
    ) -> None:
        """Change only the given fields. Column names come from a fixed map, values are
        always bound parameters."""
        given: dict[str, object] = {
            "status": status, "report": report,
            "files_changed": json.dumps(files_changed) if files_changed is not None else None,
            "stale_refusals": stale_refusals,
            "started_at": started_at.isoformat() if started_at else None,
            "ended_at": ended_at.isoformat() if ended_at else None,
        }
        pairs = [(self._AGENT_UPDATABLE[k], v) for k, v in given.items() if v is not None]
        if not pairs:
            return
        assignments = ", ".join(f"{column} = ?" for column, _ in pairs)
        self._conn.execute(
            f"UPDATE chat_agents SET {assignments} WHERE agent_id = ?",  # noqa: S608 — fixed map
            (*[v for _, v in pairs], agent_id))
        self._conn.commit()

    def set_agent_transcript(self, agent_id: str, messages: list[ChatMessage]) -> None:
        self._conn.execute(
            "UPDATE chat_agents SET transcript_json = ? WHERE agent_id = ?",
            (json.dumps([m.model_dump(mode="json") for m in messages]), agent_id))
        self._conn.commit()

    @staticmethod
    def _agent_from_row(row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"], thread_id=row["thread_id"], turn_id=row["turn_id"],
            parent_agent_id=row["parent_agent_id"], depth=row["depth"], name=row["name"],
            label=row["label"], prompt=row["prompt"], status=row["status"],
            report=row["report"], files_changed=json.loads(row["files_changed_json"]),
            stale_refusals=row["stale_refusals"],
            transcript=[ChatMessage.model_validate(m)
                        for m in json.loads(row["transcript_json"])],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None)

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        row = self._conn.execute(
            "SELECT * FROM chat_agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return self._agent_from_row(row) if row is not None else None

    def list_agents(self, thread_id: str, turn_id: str | None = None) -> list[AgentRecord]:
        if turn_id is None:
            rows = self._conn.execute(
                "SELECT * FROM chat_agents WHERE thread_id = ? ORDER BY rowid",
                (thread_id,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM chat_agents WHERE thread_id = ? AND turn_id = ? ORDER BY rowid",
                (thread_id, turn_id)).fetchall()
        return [self._agent_from_row(r) for r in rows]

    def reap_agents(self, reason: str) -> int:
        """Fail every row whose process is gone (spec §11.5); returns how many."""
        cursor = self._conn.execute(
            "UPDATE chat_agents SET status = 'failed', report = ?, ended_at = ? "
            "WHERE status IN ('queued', 'running', 'waiting')",
            (f"Status: failed — {reason}", datetime.now(UTC).isoformat()))
        self._conn.commit()
        return cursor.rowcount

    def remove_child_gates(self) -> list[str]:
        """Drop every sub-agent gate from every thread; returns the affected thread ids.
        A child gate is never recoverable after a restart: the write log is gone, so
        promoting its edit would bypass the staleness guard (spec §11.5)."""
        rows = self._conn.execute(
            "SELECT thread_id, controller_gate_json FROM chat_threads "
            "WHERE controller_gate_json IS NOT NULL").fetchall()
        affected: list[str] = []
        for row in rows:
            gates = self._gates_from_row(row)
            kept = [g for g in gates if g.agent is None]
            if len(kept) != len(gates):
                self._write_gates(row["thread_id"], kept)
                affected.append(row["thread_id"])
        return affected
