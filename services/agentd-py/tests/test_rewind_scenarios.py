"""Chat rewind — cross-cutting scenarios: memory, compaction, cache prefix, edges.

The unit suites cover each piece in isolation. These drive the pieces TOGETHER, mostly
through the real routes with a real MemoryStore wired, because that is where the
interactions actually live (retire cutoffs, anchor round-trips, prefix stability).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import ChatMessage, PendingGate
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import ValidationResult
from agentd.memory.compactor import Compactor
from agentd.memory.harness import NO_OP_HARNESS, MemoryHarness
from agentd.memory.models import Memory
from agentd.memory.store import MemoryStore
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager
from tests.gate_helpers import first_gate


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path) -> ValidationResult:
        return ValidationResult(success=True, diagnostics=[], duration_ms=1)


async def _never_summarize(_prior, _new):  # pragma: no cover - never invoked here
    raise AssertionError("compaction summarizer must not run in these tests")


def _memory_harness(tmp_path: Path) -> tuple[MemoryHarness, MemoryStore]:
    """A real harness over a real store — `_store` is derived from the compactor, so
    nothing private is poked. No embedder/LLM: these tests never compact for real."""
    store = MemoryStore(tmp_path / "memory.sqlite3")
    compactor = Compactor(store, _never_summarize, window_tokens=128000)
    return MemoryHarness(enabled=True, compactor=compactor), store


def _memory_row(mid: str, source_ref: str, created: datetime) -> Memory:
    return Memory(
        id=mid, scope_kind="workspace", scope_id="/ws", kind="semantic",
        content=f"fact {mid}", entities=[], importance=5,
        valid_from=created, valid_to=None, superseded_by=None,
        source_kind="consolidation", source_ref=source_ref,
        source_seq_lo=None, source_seq_hi=None, created_at=created,
    )


def _build(tmp_path: Path, *, harness=NO_OP_HARNESS, retention: int | None = None):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    task_store = InMemoryTaskStore()
    ws_manager = ShadowWorkspaceManager(tmp_path / "shadows")
    chat_store = ChatThreadStore(tmp_path / "chat.db")
    orch = AgentOrchestrator(
        store=task_store, reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(), workspace_manager=ws_manager, chat_store=chat_store,
    )
    controller = ChatController(
        workspace_path=str(ws), reasoning_engine=_NoopReasoning(), thread_store=chat_store,
        orchestrator=orch, broadcaster=orch.broadcaster, memory_harness=harness,
        rewind_store=RewindStore(chat_store, ws, retention_turns=retention),
    )
    app = FastAPI()
    app.include_router(build_router(task_store, orch, ws_manager, None, controller))
    return app, controller, chat_store, ws


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _turn(chat_store, controller, tid, text, turn_id, *, anchor_md=None):
    msg_id = chat_store.append_message(tid, ChatMessage(role="user", content=text))
    controller._rewind.open_checkpoint(
        tid, msg_id, turn_id, thread=chat_store.get_thread(tid),
        memory_anchor_md=(anchor_md if anchor_md is not None
                          else controller._memory_harness.anchor_markdown(tid)))
    return msg_id


# ── Memory ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rewind_retires_memories_written_during_the_span(tmp_path: Path):
    harness, mem = _memory_harness(tmp_path)
    app, controller, chat_store, _ws = _build(tmp_path, harness=harness)
    thread = chat_store.create_thread(str(tmp_path / "ws"), "t")
    tid = thread.thread_id

    before = datetime.now(UTC) - timedelta(hours=1)
    mem.insert_memory(_memory_row("older", tid, before), [])
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    # Consolidation during the turn writes with the thread as its run_id.
    mem.insert_memory(_memory_row("during", tid, datetime.now(UTC)), [])

    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert r.status_code == 200
    assert r.json()["retired_memories"] == 1
    assert mem.get_memory("during").valid_to is not None
    assert mem.get_memory("older").valid_to is None, "pre-span memory must survive"


@pytest.mark.asyncio
async def test_rewind_leaves_another_threads_memories_alone(tmp_path: Path):
    harness, mem = _memory_harness(tmp_path)
    app, controller, chat_store, _ws = _build(tmp_path, harness=harness)
    mine = chat_store.create_thread(str(tmp_path / "ws"), "mine")
    other = chat_store.create_thread(str(tmp_path / "ws"), "other")
    anchor = _turn(chat_store, controller, mine.thread_id, "go", "t1")
    now = datetime.now(UTC)
    mem.insert_memory(_memory_row("mine", mine.thread_id, now), [])
    mem.insert_memory(_memory_row("theirs", other.thread_id, now), [])

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{mine.thread_id}/rewind",
                          json={"message_id": anchor})

    assert mem.get_memory("mine").valid_to is not None
    assert mem.get_memory("theirs").valid_to is None


@pytest.mark.asyncio
async def test_explicit_remember_from_the_span_is_retired(tmp_path: Path):
    """The remember() tool writes with source_ref = run_id; the controller passes the
    thread id, which is what makes an explicit memory rewindable at all."""
    from agentd.memory.tool_source import MemoryToolSource

    harness, mem = _memory_harness(tmp_path)
    app, controller, chat_store, _ws = _build(tmp_path, harness=harness)
    thread = chat_store.create_thread(str(tmp_path / "ws"), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "go", "t1")

    class _Consolidator:
        def __init__(self, store): self._store = store

        async def write_explicit(self, content, kind, entities, scope_kind, scope_id,
                                 run_id=""):
            m = _memory_row("explicit", run_id, datetime.now(UTC))
            self._store.insert_memory(m, [])
            return m.id

    source = MemoryToolSource(_Consolidator(mem), "workspace", "/ws", run_id=tid)
    await source.execute("remember", {"content": "a fact", "kind": "semantic"})
    assert mem.get_memory("explicit").source_ref == tid, "run_id must reach the memory"

    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert r.json()["retired_memories"] == 1
    assert mem.get_memory("explicit").valid_to is not None


@pytest.mark.asyncio
async def test_rewind_works_with_memory_disabled(tmp_path: Path):
    app, controller, chat_store, ws = _build(tmp_path, harness=NO_OP_HARNESS)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    (ws / "a.py").write_text("v0\n")
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    controller._rewind.capture(tid, ["a.py"])
    (ws / "a.py").write_text("v1\n")

    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert r.status_code == 200
    assert r.json()["retired_memories"] == 0
    assert (ws / "a.py").read_text() == "v0\n"


@pytest.mark.asyncio
async def test_memory_written_after_the_rewind_is_not_retired(tmp_path: Path):
    """Consolidation is fire-and-forget background work. One that lands AFTER the
    rewind cannot be retired by it — a real limitation, pinned here on purpose."""
    harness, mem = _memory_harness(tmp_path)
    app, controller, chat_store, _ws = _build(tmp_path, harness=harness)
    thread = chat_store.create_thread(str(tmp_path / "ws"), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "go", "t1")

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    mem.insert_memory(_memory_row("late", tid, datetime.now(UTC)), [])
    assert mem.get_memory("late").valid_to is None


# ── Compaction ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rewind_restores_the_anchor_the_checkpoint_captured(tmp_path: Path):
    harness, mem = _memory_harness(tmp_path)
    app, controller, chat_store, _ws = _build(tmp_path, harness=harness)
    thread = chat_store.create_thread(str(tmp_path / "ws"), "t")
    tid = thread.thread_id
    mem.upsert_anchor(tid, "summary as of turn 1")
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    mem.upsert_anchor(tid, "summary that includes the turn being rewound")

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert mem.get_anchor(tid).summary_md == "summary as of turn 1"


@pytest.mark.asyncio
async def test_rewind_deletes_an_anchor_the_checkpoint_predates(tmp_path: Path):
    """No anchor at checkpoint time means compaction had not happened yet. Leaving the
    newer anchor would keep summarizing turns the rewind just deleted."""
    harness, mem = _memory_harness(tmp_path)
    app, controller, chat_store, _ws = _build(tmp_path, harness=harness)
    thread = chat_store.create_thread(str(tmp_path / "ws"), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    assert mem.get_anchor(tid) is None
    mem.upsert_anchor(tid, "summary of the rewound turn")

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert mem.get_anchor(tid) is None


@pytest.mark.asyncio
async def test_rewind_leaves_compaction_segments_in_place(tmp_path: Path):
    """Documented, accepted tradeoff: segments feed only the summarizer and the
    consolidator's A+link render, both downstream of the anchor that IS restored."""
    from agentd.memory.models import CompactionSegment

    harness, mem = _memory_harness(tmp_path)
    app, controller, chat_store, _ws = _build(tmp_path, harness=harness)
    thread = chat_store.create_thread(str(tmp_path / "ws"), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    mem.add_segments([CompactionSegment(
        id="s1", run_id=tid, seq=0, content="evicted text",
        created_at=datetime.now(UTC))])

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert [s.id for s in mem.get_segments(tid)] == ["s1"]


# ── Cache prefix stability ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rewind_restores_the_retrieval_seed_byte_identically(tmp_path: Path):
    """The seed is the pinned cache-prefix head. A rewind must hand back exactly the
    bytes the earlier turn replayed, or the KV prefix breaks on the next turn."""
    app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    original = {"files": ["a.py", "b.py"], "symbols": {"A": 1}}
    chat_store.set_controller_seed(tid, original)
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    chat_store.set_controller_seed(tid, {"files": ["totally", "different"]})

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert chat_store.get_thread(tid).controller_retrieval_seed == original


@pytest.mark.asyncio
async def test_rewind_restores_history_verbatim_for_prefix_replay(tmp_path: Path):
    app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    history = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}]
    chat_store.set_controller_history(tid, history)
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    chat_store.set_controller_history(tid, history + [{"role": "user", "content": "three"}])

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert chat_store.get_thread(tid).controller_conversation_history == history


@pytest.mark.asyncio
async def test_rewind_does_not_touch_another_thread(tmp_path: Path):
    app, controller, chat_store, ws = _build(tmp_path)
    mine = chat_store.create_thread(str(ws), "mine")
    other = chat_store.create_thread(str(ws), "other")
    chat_store.set_controller_history(other.thread_id, [{"role": "user", "content": "keep"}])
    chat_store.append_message(other.thread_id, ChatMessage(role="user", content="theirs"))
    anchor = _turn(chat_store, controller, mine.thread_id, "go", "t1")

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{mine.thread_id}/rewind",
                          json={"message_id": anchor})

    survivor = chat_store.get_thread(other.thread_id)
    assert [m.content for m in survivor.messages] == ["theirs"]
    assert survivor.controller_conversation_history == [{"role": "user", "content": "keep"}]


# ── Thread state ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rewind_clears_a_pending_gate(tmp_path: Path):
    """A gate raised by a turn that no longer exists can never be resolved — its
    in-memory waiter died with the turn, so it would wedge the composer forever."""
    app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    chat_store.add_controller_gate(tid, PendingGate(kind="edit", payload={"diff_entries": []}))

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert first_gate(chat_store.get_thread(tid)) is None


@pytest.mark.asyncio
async def test_rewind_restores_active_skill_and_clears_later_todos(tmp_path: Path):
    app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    chat_store.set_controller_active_skill(tid, '{"name": "brainstorming", "body": "b"}')
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    chat_store.set_controller_active_skill(tid, '{"name": "writing-plans", "body": "w"}')
    chat_store.set_controller_todos(tid, '[{"title": "from the rewound turn"}]')

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    reloaded = chat_store.get_thread(tid)
    assert reloaded.controller_active_skill == {"name": "brainstorming", "body": "b"}
    assert reloaded.controller_todos is None


# ── Files and checkpoints ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_created_then_deleted_in_span_stays_gone(tmp_path: Path):
    app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    controller._rewind.capture(tid, ["scratch.py"])   # absent -> existed=False
    (ws / "scratch.py").write_text("made\n")
    _turn(chat_store, controller, tid, "again", "t2")
    controller._rewind.capture(tid, ["scratch.py"])   # present now, but t1 saw it absent
    (ws / "scratch.py").write_text("edited\n")

    async with _client(app) as client:
        await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert not (ws / "scratch.py").exists(), "first-seen-wins must use the absent record"


@pytest.mark.asyncio
async def test_two_sequential_rewinds(tmp_path: Path):
    app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    (ws / "a.py").write_text("v0\n")

    first = _turn(chat_store, controller, tid, "one", "t1")
    controller._rewind.capture(tid, ["a.py"])
    (ws / "a.py").write_text("v1\n")
    second = _turn(chat_store, controller, tid, "two", "t2")
    controller._rewind.capture(tid, ["a.py"])
    (ws / "a.py").write_text("v2\n")

    async with _client(app) as client:
        r2 = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": second})
        assert r2.status_code == 200
        assert (ws / "a.py").read_text() == "v1\n"
        r1 = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": first})
        assert r1.status_code == 200

    assert (ws / "a.py").read_text() == "v0\n"
    assert chat_store.list_checkpoints(tid) == []
    assert chat_store.get_thread(tid).messages == []


@pytest.mark.asyncio
async def test_rewind_to_the_first_message_empties_the_thread(tmp_path: Path):
    app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "the only turn", "t1")
    chat_store.append_message(tid, ChatMessage(role="agent", content="reply"))

    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    assert r.json()["prefill_text"] == "the only turn"
    assert chat_store.get_thread(tid).messages == []


def test_retention_prunes_oldest_checkpoints_and_their_snapshots(tmp_path: Path):
    _app, controller, chat_store, ws = _build(tmp_path, retention=2)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    (ws / "a.py").write_text("v0\n")

    for n in range(4):
        _turn(chat_store, controller, tid, f"turn {n}", f"t{n}")
        controller._rewind.capture(tid, ["a.py"])

    seqs = [c.seq for c in chat_store.list_checkpoints(tid)]
    assert seqs == [2, 3], "retention=2 keeps only the newest two"
    root = ws / ".crucible" / "state" / "rewind" / tid
    assert not (root / "0").exists() and not (root / "1").exists()
    assert (root / "2").exists()


@pytest.mark.asyncio
async def test_oversize_file_is_reported_not_restored(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "big.bin").write_text("x" * 200)
    app, controller, chat_store, _ws = _build(tmp_path)
    controller._rewind = RewindStore(chat_store, ws, max_file_bytes=10)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    anchor = _turn(chat_store, controller, tid, "go", "t1")
    controller._rewind.capture(tid, ["big.bin"])
    (ws / "big.bin").write_text("changed by the agent")

    async with _client(app) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": anchor})

    body = r.json()
    assert body["oversize_files"] == ["big.bin"]
    assert body["restored_files"] == []
    # Never silently claimed as restored, and never truncated to nothing.
    assert (ws / "big.bin").read_text() == "changed by the agent"


def test_continuation_turn_folds_into_the_same_anchor(tmp_path: Path):
    """resolve_mode -> implement and resolve_clarify re-enter the loop WITHOUT a new
    user message, so their edits must land in the checkpoint already open."""
    _app, controller, chat_store, ws = _build(tmp_path)
    thread = chat_store.create_thread(str(ws), "t")
    tid = thread.thread_id
    (ws / "a.py").write_text("v0\n")
    (ws / "b.py").write_text("w0\n")

    anchor = _turn(chat_store, controller, tid, "go", "t1")
    controller._rewind.capture(tid, ["a.py"])          # first dispatch
    controller._rewind.capture(tid, ["b.py"])          # continuation, same turn

    cp = chat_store.get_checkpoint_by_anchor(tid, anchor)
    assert sorted(f.path for f in cp.files) == ["a.py", "b.py"]
    assert len(chat_store.list_checkpoints(tid)) == 1
