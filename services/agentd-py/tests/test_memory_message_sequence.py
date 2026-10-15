import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest

from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.history_metadata import project_history
from agentd.memory.compactor import Compactor
from agentd.memory.history import EvidenceHistory
from agentd.memory.models import CompactionSegment
from agentd.memory.store import MemoryStore
from agentd.reasoning.react_common import assistant_turn


def test_pending_slots_restart_and_compaction_do_not_renumber(tmp_path):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    history = EvidenceHistory(
        [{"role": "user", "content": "original request"}], partial(store.reserve_mseq, "thread")
    )
    history.append(
        assistant_turn({"type": "tool_calls", "thought": "private intent"}, capture_evidence=True)
    )
    slot = {"role": "tool_result", "tool": "run_command", "content": "", "status": "pending"}
    history.append(slot)
    seq = slot["mseq"]
    slot.update(content="exit 0", status="completed", is_error=False)
    assert history[-1] is slot and history[-1]["mseq"] == seq
    assert [m["mseq"] for m in history] == [1, 2, 3]
    projected = copy.deepcopy(history)
    assert type(projected) is list and projected == history
    assert projected[0] is not history[0]
    assert store.reserve_mseq("thread", 0) == 4
    history[:] = [
        {
            "role": "user",
            "content": "synthetic summary",
            "_evidence": {"kind": "compaction_summary"},
        },
        history[-1],
    ]
    assert "mseq" not in history[0]
    assert history[-1]["mseq"] == 3
    resumed = EvidenceHistory(
        json.loads(json.dumps(history)), partial(store.reserve_mseq, "thread")
    )
    resumed.append({"role": "user", "content": "next turn"})
    assert resumed[-1]["mseq"] == 4
    # Replay an earlier checkpoint while retaining the allocator's high-water mark.
    rewound = EvidenceHistory(
        [{"role": "user", "content": "old", "mseq": 1}], partial(store.reserve_mseq, "thread")
    )
    rewound.append({"role": "user", "content": "replacement turn"})
    assert rewound[-1]["mseq"] == 5
    second = MemoryStore(path)
    restarted = EvidenceHistory([], partial(second.reserve_mseq, "thread"))
    restarted.append({"role": "assistant", "content": "after restart"})
    assert restarted[-1]["mseq"] == 6
    restarted.extend(restarted)
    assert [m["mseq"] for m in restarted] == [6, 7]


def test_identity_failure_does_not_fail_chat_or_invent_ids():
    def broken(count, floor):
        raise sqlite3.OperationalError("disk full")

    history = EvidenceHistory([], broken)
    entry = assistant_turn(
        {"type": "answer", "answer": "still useful", "thought": "intent"}, capture_evidence=True
    )
    history.append(entry)
    assert "mseq" not in entry
    assert entry["_evidence"] == {"thought": "intent", "source_identity_unavailable": True}
    assert project_history(history) == [
        assistant_turn({"type": "answer", "answer": "still useful", "thought": "intent"})
    ]


@pytest.mark.asyncio
async def test_raw_segments_preserve_source_and_oversized_backstop(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    history = EvidenceHistory([], partial(store.reserve_mseq, "thread"))
    history.append(
        assistant_turn(
            {
                "type": "tool_call",
                "thought": "read the contract",
                "tool": "read_file",
                "args": {"path": "module.py"},
            },
            capture_evidence=True,
        )
    )
    history.append(
        {
            "role": "tool_result",
            "tool": "read_file",
            "content": "raw" * 80,
            "delivered_read": {"start_line": 1, "end_line": 80},
            "is_error": False,
        }
    )
    original = [dict(m) for m in history]
    history.append({"role": "user", "content": "huge" * 200})

    async def summarize(old, text):
        assert "read the contract" not in text  # private thought stays out of v1 rendering
        return "anchor"

    compactor = Compactor(store, summarize, window_tokens=100, hot_turns=1)
    result = await compactor.maybe_compact(history, "thread", force=True)
    history[:] = result.history
    segments = store.get_segments("thread")
    assert [s.content for s in segments[:2]] == [m["content"] for m in original]
    assert segments[0].mseq == 1 and segments[0].role == "assistant"
    assert segments[0].metadata["_evidence"] == {"thought": "read the contract"}
    assert segments[1].mseq == 2 and segments[1].tool == "read_file"
    assert segments[1].metadata["delivered_read"] == {"start_line": 1, "end_line": 80}
    assert segments[2].content == "huge" * 200 and segments[2].mseq == 3
    assert "mseq" not in history[0]  # summary is not source evidence
    assert history[-1]["mseq"] == 3
    history.append({"role": "assistant", "content": "after compact"})
    assert history[-1]["mseq"] == 4


def test_legacy_segment_migration_has_no_invented_source_metadata(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE compaction_segments(id TEXT PRIMARY KEY,run_id TEXT,"
            "seq INTEGER,content TEXT,created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO compaction_segments VALUES(?,?,?,?,?)",
            ("old", "thread", 0, "old raw evidence", datetime.now(UTC).isoformat()),
        )
    store = MemoryStore(path)
    old = store.get_segments("thread")[0]
    assert old.content == "old raw evidence"
    assert old.mseq is None and old.role is None and old.tool is None and old.metadata == {}


@pytest.mark.asyncio
async def test_two_compactions_keep_original_source_ids_and_mark_summaries(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    history = EvidenceHistory(
        [
            {"role": "user", "content": "first request"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second request"},
        ],
        partial(store.reserve_mseq, "thread"),
    )

    async def summarize(old, text):
        return "anchor"

    compactor = Compactor(store, summarize, window_tokens=1000, hot_turns=1)
    history[:] = (await compactor.maybe_compact(history, "thread", force=True)).history
    history.append({"role": "assistant", "content": "second answer"})
    history.append({"role": "user", "content": "third request"})
    history[:] = (await compactor.maybe_compact(history, "thread", force=True)).history
    segments = store.get_segments("thread")
    assert [segment.mseq for segment in segments if segment.mseq is not None] == [1, 2, 3, 4]
    synthetic = [segment for segment in segments if segment.mseq is None]
    assert len(synthetic) == 1
    assert synthetic[0].metadata["_evidence"]["kind"] == "compaction_summary"
    assert "mseq" not in history[0] and history[-1]["mseq"] == 5
    history.append({"role": "assistant", "content": "third answer"})
    assert history[-1]["mseq"] == 6


def test_watermark_and_rewind_keep_reservations_and_remove_new_evictions(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    before, after = datetime.now(UTC) - timedelta(hours=1), datetime.now(UTC)
    # An old hot message curated earlier is evicted later; its original mseq remains stable.
    store.advance_curation("thread", 2)
    store.add_segments(
        [
            CompactionSegment(
                id="old",
                run_id="thread",
                seq=0,
                content="prior",
                created_at=before,
                mseq=1,
                role="user",
            ),
            CompactionSegment(
                id="evicted",
                run_id="thread",
                seq=1,
                content="old hot",
                created_at=after,
                mseq=2,
                role="assistant",
            ),
        ]
    )
    assert not [
        s
        for s in store.get_segments("thread")
        if s.mseq is not None and s.mseq > store.curation_watermark("thread")
    ]
    store.reserve_mseq("thread", 6)
    store.advance_curation("thread", 5)
    store.rewind_evidence("thread", after.isoformat(), 2)
    assert [s.id for s in store.get_segments("thread")] == ["old"]
    assert store.curation_watermark("thread") == 2
    assert store.reserve_mseq("thread", 1) == 7


def test_chat_checkpoint_preserves_metadata_without_exposing_counter(tmp_path):
    store = ChatThreadStore(tmp_path / "chat.db")
    thread = store.create_thread(str(tmp_path))
    original = [
        {"role": "assistant", "content": "prior", "mseq": 4, "_evidence": {"thought": "private"}}
    ]
    store.set_controller_history(thread.thread_id, original)
    anchor = store.append_message(thread.thread_id, ChatMessage(role="user", content="new"))
    rewind = RewindStore(store, tmp_path)
    rewind.open_checkpoint(
        thread.thread_id,
        anchor,
        "turn",
        thread=store.get_thread(thread.thread_id),
        memory_anchor_md=None,
    )
    store.set_controller_history(
        thread.thread_id, [{"role": "assistant", "content": "later", "mseq": 8}]
    )
    outcome = rewind.restore(thread.thread_id, anchor)
    assert outcome.target_memory_mseq == 4
    assert "target_memory_mseq" not in outcome.model_dump()
    assert store.get_thread(thread.thread_id).controller_conversation_history == original
