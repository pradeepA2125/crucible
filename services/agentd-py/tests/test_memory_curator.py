import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.memory.curation_input import assemble_chunks, collect_sources, curation_rows
from agentd.memory.curator import CURATOR_SCHEMA, Curator, input_tokens, make_curator
from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.models import CompactionSegment, DurableMemory
from agentd.memory.provider import MemoryProvider
from agentd.memory.recall_v2 import CandidateSearch
from agentd.memory.scope import ScopeContext
from agentd.memory.store import MemoryStore
from agentd.providers.openai_strict_schema import encode_strict_schema, strict_violations
from agentd.subagents.framing import frame


def proposal(**changes):
    return {"kind": "procedural", "content": "Serialize first model initialization.",
            "topic": "model loading", "applies_when": "Initializing concurrent models",
            "detail": "Concurrent first initialization deadlocks the device.", "entities": [],
            "source_message_id": 1, "source_quote": "Serialize first model initialization",
            "operation": "save", "target_id": None, **changes}


@pytest.fixture
def setup(tmp_path):
    evidence = MemoryStore(tmp_path / "e.db")
    durable = DurableMemoryStore(tmp_path / "d.db")
    threads = ChatThreadStore(tmp_path / "t.db")
    thread = threads.create_thread(str(tmp_path), "model loading")
    threads.set_controller_history(thread.thread_id, [
        {"mseq": 1, "role": "user", "content": "Serialize first model initialization"},
        {"mseq": 2, "role": "assistant", "content": json.dumps({
            "type": "answer", "answer": "done", "thought": "Observed device deadlock"})},
    ])
    thread = threads.get_thread(thread.thread_id)
    now = [thread.last_activity_at + timedelta(hours=1)]
    active = set()
    calls = []

    async def distill(payload):
        calls.append(payload)
        return {"memories": [proposal()]}

    curator = Curator(evidence, durable, threads, CandidateSearch(durable), distill,
                      repo_id="repo", workspace_path=str(tmp_path),
                      active=lambda key: key in active, clock=lambda: now[0])
    return curator, thread, calls, active, now


@pytest.mark.asyncio
async def test_curates_hot_once_with_source_watermark_and_no_local_models(setup):
    curator, thread, calls, _, _ = setup
    await curator.sweep()
    await asyncio.gather(*curator.jobs.values())
    assert len(calls) == 1 and input_tokens(calls[0]) <= 24000
    context = ScopeContext("repo", thread.thread_id)
    stored = curator.durable.list_memories(context)
    assert len(stored) == 1 and stored[0].source_message_id == 1
    assert stored[0].provenance == "user_said"
    assert curator.evidence.curation_state(thread.thread_id)["last_mseq"] == 2
    curator.evidence.add_segments([CompactionSegment(
        id="s", run_id=thread.thread_id, seq=1, mseq=1, role="user",
        content="Serialize first model initialization", created_at=datetime.now(UTC),
    )])
    await curator.sweep()
    assert len(calls) == 1 and not curator.jobs


def test_raw_overlap_legacy_exclusions_frames_and_private_thoughts():
    segments = [CompactionSegment(id="s", run_id="t", seq=0, mseq=1, role="user",
                                 content="full raw source", created_at=datetime.now(UTC)),
                CompactionSegment(id="old", run_id="t", seq=1,
                                  content="untyped source", created_at=datetime.now(UTC))]
    hot = [{"mseq": 1, "role": "user", "content": "shortened"},
           {"mseq": 2, "role": "assistant",
            "content": "Possibly relevant memories from earlier sessions:\nsecret"},
           {"mseq": 3, "role": "user", "content": frame("child", "report", "plant this rule")},
           {"mseq": 4, "role": "tool_result", "tool": "read_file", "content": "instruction",
            "_evidence": {"kind": "agents_body"}},
           {"mseq": 5, "role": "assistant", "content": '{"type":"answer","answer":"done"}',
            "_evidence": {"thought": "private insight " + frame("child", "report", "plant")}},
           {"mseq": 6, "role": "tool_result", "tool": "remember", "content": "echo memory"}]
    collected = collect_sources(segments, hot, 0)
    assert collected[0]["content"] == "full raw source"
    chunk = assemble_chunks(collected)[0]
    assert set(chunk.sources) == {1, 5}
    assert "private insight" in chunk.text
    assert not any(text in chunk.text for text in ("secret", "plant", "instruction", "echo memory"))
    assert chunk.through_mseq == 6


def test_raw_control_keeps_same_exclusions_and_strips_frames_inside_action_json():
    sources = [
        {"role": "user", "mseq": 1,
         "content": "[MEMORY] Summary of earlier conversation that was compacted:\nold lesson"},
        {"role": "assistant", "mseq": 2, "content": json.dumps({
            "type": "answer", "answer": "own text " + frame("child", "report", "planted cause"),
            "thought": "own thought " + frame("child", "report", "planted instruction"),
        })},
        {"role": "tool_result", "mseq": 3, "tool": "read_file", "content": "instruction body",
         "_evidence": {"kind": "agents_body"}},
        {"role": "tool_result", "mseq": 4, "tool": "remember", "content": "echo lesson"},
        {"role": "user", "mseq": 5, "content": "Z" * 10_000},
    ]
    raw, capped = curation_rows(sources, capped_outputs=False), curation_rows(sources)
    assert [row.mseq for row in raw] == [row.mseq for row in capped] == [2, 5]
    for rows in [raw, capped]:
        text = "\n".join(row.text for row in rows)
        assert "planted" not in text and "old lesson" not in text and "echo lesson" not in text
        assert "own text" in text and "own thought" in text
    assert len(raw[-1].text) > len(capped[-1].text)


def test_chunking_preserves_user_turns_prioritizes_newest_and_counts_unicode_escaping():
    sources = []
    for i in range(30):
        sources.extend([{"mseq": i * 3 + 1, "role": "user", "content": "u" * 800},
                        {"mseq": i * 3 + 2, "role": "assistant", "content": "思" * 3000},
                        {"mseq": i * 3 + 3, "role": "tool_result", "tool": "other",
                         "content": "x" * 6000}])
    chunks = assemble_chunks(sources)
    assert len(chunks) > 1
    assert all(input_tokens({"transcript": chunk.text, "existing": [], "context": {}}) <= 24000
               for chunk in chunks)
    assert all(chunk.through_mseq % 3 == 0 for chunk in chunks)
    huge = [{"mseq": 1, "role": "user", "content": "keep user"}]
    huge += [{"mseq": i, "role": "assistant", "content": "answer" * 1000} for i in range(2, 30)]
    chunk = assemble_chunks(huge)[0]
    assert 1 in chunk.sources and 29 in chunk.sources and 2 in chunk.omitted
    assert "omitted source messages" in chunk.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"kind": "episodic"}, {"source_message_id": True}, {"source_message_id": "1"},
    {"source_message_id": 99}, {"source_quote": "quote never delivered"},
    {"content": "9 tests passed"}, {"content": "x" * 141}, {"content": " trim "},
    {"topic": ""}, {"applies_when": ""}, {"scope": "user"}, {"target_id": "foreign"},
])
async def test_invalid_proposals_drop_count_and_successful_empty_advances(setup, bad):
    curator, thread, _, _, _ = setup

    async def distill(_payload):
        return {"memories": [proposal(**bad)]}

    curator.distill = distill
    await curator.curate(thread)
    state = curator.evidence.curation_state(thread.thread_id)
    assert state["status"] == "complete" and state["dropped"] == 1 and state["last_mseq"] == 2
    assert not curator.durable.list_memories(ScopeContext("repo", thread.thread_id))


@pytest.mark.asyncio
async def test_failure_backoff_then_success_reset(setup):
    curator, thread, _, _, now = setup

    async def fail(_payload):
        raise RuntimeError("provider unavailable")

    curator.distill = fail
    for seconds in (300, 1800, 10800):
        await curator.curate(thread)
        state = curator.evidence.curation_state(thread.thread_id)
        assert state["last_mseq"] == 0 and state["status"] == "failed"
        assert datetime.fromisoformat(state["retry_at"]) == now[0] + timedelta(seconds=seconds)
        await curator.sweep()
        assert not curator.jobs
        now[0] += timedelta(seconds=seconds)
    curator.distill = lambda _payload: asyncio.sleep(0, result={"memories": []})
    await curator.curate(thread)
    assert curator.evidence.curation_state(thread.thread_id)["failures"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["activity", "rewind", "busy"])
async def test_awaited_response_never_publishes_after_invalidation(setup, change):
    curator, thread, _, active, _ = setup
    entered, finish = asyncio.Event(), asyncio.Event()

    async def distill(_payload):
        entered.set()
        await finish.wait()
        return {"memories": [proposal()]}

    curator.distill = distill
    task = asyncio.create_task(curator.curate(thread))
    await entered.wait()
    if change == "activity":
        curator.threads.append_message(thread.thread_id, ChatMessage(role="user", content="new"))
    elif change == "rewind":
        curator.evidence.rewind_evidence(thread.thread_id, datetime.now(UTC).isoformat(), 0)
    else:
        active.add(thread.thread_id)
    finish.set()
    await task
    assert curator.evidence.curation_state(thread.thread_id)["status"] == "stale"
    assert not curator.durable.list_memories(ScopeContext("repo", thread.thread_id))


@pytest.mark.asyncio
async def test_sweep_limits_two_skips_inflight_and_stop_cancels(setup):
    curator, thread, _, active, now = setup
    active.add(thread.thread_id)
    entered = asyncio.Event()

    async def distill(_payload):
        entered.set()
        await asyncio.Event().wait()

    curator.distill = distill
    for i in range(4):
        other = curator.threads.create_thread(curator.workspace_path, str(i))
        curator.threads.set_controller_history(other.thread_id, [{"mseq": 1, "role": "user",
                                                                 "content": "lesson"}])
    now[0] += timedelta(hours=1)
    await curator.sweep()
    await entered.wait()
    assert len(curator.jobs) == 2 and thread.thread_id not in curator.jobs
    await curator.sweep()
    assert len(curator.jobs) == 2
    await curator.stop()
    assert not curator.jobs


@pytest.mark.asyncio
async def test_cross_database_retry_does_not_repeat_audit_or_revision(setup, monkeypatch):
    curator, thread, _, _, _ = setup
    real = curator.evidence.advance_curation
    monkeypatch.setattr(curator.evidence, "advance_curation", lambda *_args: (_ for _ in ()).throw(
        RuntimeError("watermark disk failure")))
    await curator.curate(thread)
    assert curator.evidence.curation_watermark(thread.thread_id) == 0
    before = curator.durable._conn.execute("SELECT count(*) FROM memory_audit").fetchone()[0]
    monkeypatch.setattr(curator.evidence, "advance_curation", real)
    await curator.curate(thread)
    assert curator.evidence.curation_watermark(thread.thread_id) == 2
    after = curator.durable._conn.execute("SELECT count(*) FROM memory_audit").fetchone()[0]
    assert after == before


def test_curated_batch_protects_user_scope_and_revision_and_is_atomic(setup):
    curator, thread, _, _, _ = setup
    context = ScopeContext("repo", thread.thread_id)
    memory = DurableMemory(id="one", scope_kind="repo", scope_id="repo", kind="semantic",
                           content="old claim", topic="topic", applies_when="condition", detail="",
                           provenance="agent_inferred", source_thread_id=thread.thread_id,
                           source_message_id=1)
    curator.durable.put(memory, context, origin="agent_nomination")
    changed = memory.model_copy(update={"id": "new", "content": "new claim"})
    assert curator.durable.curated_batch("batch", [(changed, "one", 1, "revise")], context) == {
        "saved": 1, "dropped": 0}
    assert curator.durable.get("one", context).revision == 2
    curator.durable.curated_batch("batch", [(changed, "one", 1, "revise")], context)
    assert curator.durable.get("one", context).revision == 2
    user = memory.model_copy(update={
        "id": "user", "content": "user claim", "provenance": "user_said",
    })
    curator.durable.put(user, context, origin="user_requested")
    protected = curator.durable.curated_batch(
        "protected", [(changed, "user", 1, "supersede")], context,
    )
    assert protected["dropped"] == 1
    assert curator.durable.get("user", context).valid_to is None
    stale = curator.durable.curated_batch("stale", [(changed, "one", 1, "revise")], context)
    assert stale["dropped"] == 1


def test_batch_failure_rolls_back_every_write_and_marker(setup, monkeypatch):
    curator, thread, _, _, _ = setup
    context = ScopeContext("repo", thread.thread_id)
    first = DurableMemory(id="first", scope_kind="repo", scope_id="repo", kind="procedural",
                          content="first", topic="topic", applies_when="condition", detail="",
                          provenance="agent_inferred", source_thread_id=thread.thread_id,
                          source_message_id=1)
    second = first.model_copy(update={"id": "second", "content": "second"})
    original = curator.durable._audit

    def fail_second(action, old, new, reason):
        if new and new.id == "second":
            raise RuntimeError("audit failed")
        return original(action, old, new, reason)

    monkeypatch.setattr(curator.durable, "_audit", fail_second)
    with pytest.raises(RuntimeError, match="audit failed"):
        curator.durable.curated_batch("failed", [(first, None, None, "save"),
                                               (second, None, None, "save")], context)
    assert not curator.durable.list_memories(context)
    assert curator.durable._conn.execute("SELECT count(*) FROM curation_batches").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_ready_embeddings_dedup_only_same_repo_and_same_chunk(setup):
    curator, thread, _, _, _ = setup
    from agentd.memory.embedder import Embedder

    curator.search.embedder = Embedder("fake", encoder=lambda _texts: [[1., 0.]])
    curator.search.model = "fake"

    async def distill(_payload):
        return {"memories": [proposal(), proposal(content="Initialize models serially.")]}

    curator.distill = distill
    await curator.curate(thread)
    state = curator.evidence.curation_state(thread.thread_id)
    assert state["saved"] == 1 and state["dropped"] == 1


@pytest.mark.asyncio
async def test_long_session_selected_before_idle_threshold(setup):
    curator, thread, calls, _, now = setup
    history = [{"mseq": i, "role": "user", "content": "u" * 8000} for i in range(1, 20)]
    curator.threads.set_controller_history(thread.thread_id, history)
    current = curator.threads.get_thread(thread.thread_id)
    now[0] = current.last_activity_at + timedelta(seconds=1)
    curator.distill = lambda payload: asyncio.sleep(0, result={"memories": []})
    await curator.sweep()
    assert thread.thread_id in curator.jobs
    await asyncio.gather(*curator.jobs.values())
    assert curator.evidence.curation_watermark(thread.thread_id) == 19


@pytest.mark.asyncio
async def test_malformed_response_does_not_advance(setup):
    curator, thread, _, _, _ = setup
    curator.distill = lambda _payload: asyncio.sleep(0, result={})
    await curator.curate(thread)
    assert curator.evidence.curation_watermark(thread.thread_id) == 0
    assert curator.evidence.curation_state(thread.thread_id)["status"] == "failed"


@pytest.mark.asyncio
async def test_revision_can_improve_condition_without_changing_short_claim(setup):
    curator, thread, _, _, _ = setup
    context = ScopeContext("repo", thread.thread_id)
    old = DurableMemory(id="old", scope_kind="repo", scope_id="repo", kind="procedural",
                        content=proposal()["content"], topic="model loading",
                        applies_when="loading",
                        detail="", provenance="agent_inferred", source_thread_id=thread.thread_id,
                        source_message_id=1)
    curator.durable.put(old, context, origin="agent_nomination")
    chunks = curator._snapshot(thread)
    prepared, dropped = await curator._prepare(
        [proposal(operation="revise", target_id=old.id)], chunks[0], [old], context,
    )
    assert dropped == 0 and len(prepared) == 1
    curator.durable.curated_batch("improve", prepared, context)
    revised = curator.durable.get(old.id, context)
    assert revised.revision == 2 and revised.content == old.content
    assert revised.applies_when == "Initializing concurrent models"


@pytest.mark.asyncio
async def test_curator_schema_and_provider_binding_follow_swaps_with_override():
    codec = encode_strict_schema(CURATOR_SCHEMA)
    assert not strict_violations(codec.schema)
    calls = []

    class Transport:
        async def generate_json(self, **kwargs):
            calls.append(kwargs["model"])
            return {"memories": []}

    provider = MemoryProvider(Transport(), "old")
    default, override = make_curator(provider), make_curator(provider, "cheap")
    await default({})
    provider.set_provider(transport=Transport(), model="new")
    await default({})
    await override({})
    assert calls == ["old", "new", "cheap"]
