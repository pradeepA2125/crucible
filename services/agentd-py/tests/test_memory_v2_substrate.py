import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.memory.compactor import Compactor
from agentd.memory.config import MemoryConfig
from agentd.memory.durable_store import DurableMemoryStore
from agentd.memory.harness import MemoryHarness, build_memory_harness
from agentd.memory.models import CompactionSegment, DurableMemory
from agentd.memory.store import MemoryStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def test_factory_global_store_gate_and_workspace_identity(tmp_path, monkeypatch):
    import agentd.memory.harness as module

    monkeypatch.setattr(module.threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(module, "discover_repo_identity", lambda path: "git:host/repo")
    config = MemoryConfig.from_env(
        {
            "CRUCIBLE_MEMORY_DB_PATH": str(tmp_path / "workspace.db"),
            "CRUCIBLE_MEMORY_STORE_PATH": str(tmp_path / "global.db"),
        }
    )
    legacy = build_memory_harness(config, object(), "fake-model", workspace_path=str(tmp_path))
    assert not legacy.recall_v2 and legacy.durable_store is None
    assert not (tmp_path / "global.db").exists()
    v2 = build_memory_harness(
        config.model_copy(update={"recall_v2": True}),
        object(),
        "fake-model",
        workspace_path=str(tmp_path),
    )
    assert v2.recall_v2 and v2.durable_store is not None
    assert (tmp_path / "global.db").exists()
    assert v2.scope_context("thread").repo_id == "git:host/repo"
    assert v2.durable_store.list_memories(v2.scope_context("thread")) == []
    # Phase 4 is not wired yet: enabling substrate does not silently replace legacy recall.
    assert v2._recall_engine is not None
    v2.durable_store.close()


@pytest.mark.asyncio
async def test_real_controller_batch_preserves_private_evidence_and_source_ids(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    workspace = MemoryStore(tmp_path / "workspace.db")

    async def summarize(old, text):
        return "anchor"

    harness = MemoryHarness(
        enabled=True,
        compactor=Compactor(workspace, summarize, window_tokens=100000),
        recall_v2=True,
        repo_id="git:host/repo",
    )
    engine = ScriptedReasoningEngine(
        None,
        [],
        controller_step_responses=[
            {
                "type": "tool_calls",
                "thought": "inspect a.py before answering",
                "calls": [
                    {"id": "read", "tool": "read_file", "args": {"path": "a.py"}},
                ],
            },
            {"type": "answer", "thought": "read verified the value", "answer": "value is 1",
             "recap": "Inspect a.py; the file read verified value = 1."},
        ],
    )
    registry = AggregatingToolRegistry(
        [
            BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path),
        ]
    )
    loop = ControllerLoop(
        engine,
        registry,
        EventBroadcaster(),
        channel_id="thread",
        phase_sm=ControllerPhaseSM(),
        memory_harness=harness,
    )
    outcome = await loop.run(
        {"goal": "inspect a.py", "workspace_path": str(tmp_path), "run_id": "thread"},
        max_iters=4,
        seed_history=[{"role": "user", "content": "inspect a.py"}],
    )
    assert [m["mseq"] for m in outcome.history] == [1, 2, 3, 4]
    assert outcome.history[1]["_evidence"] == {"thought": "inspect a.py before answering"}
    result = outcome.history[2]
    assert result["tool"] == "read_file" and "value = 1" in result["content"]
    assert result["is_error"] is False and result["status"] == "success"


def test_harness_rewind_retires_v2_span_and_restores_evidence_watermark(tmp_path):
    workspace = MemoryStore(tmp_path / "workspace.db")
    durable = DurableMemoryStore(tmp_path / "global.db")

    async def summarize(old, text):
        return "anchor"

    harness = MemoryHarness(
        enabled=True,
        compactor=Compactor(workspace, summarize, window_tokens=100000),
        recall_v2=True,
        durable_store=durable,
        repo_id="git:host/repo",
    )
    context = harness.scope_context("thread")
    entry = durable.put(
        DurableMemory(
            scope_kind="repo",
            scope_id=context.repo_id,
            kind="procedural",
            content="A lesson",
            topic="testing",
            applies_when="when testing",
            provenance="tool_verified",
            source_thread_id="thread",
            source_message_id=5,
        ),
        context,
        origin="agent_nomination",
    )
    after = datetime.now(UTC)
    workspace.add_segments(
        [
            CompactionSegment(
                id="new", run_id="thread", seq=0, content="evicted", created_at=after, mseq=5
            )
        ]
    )
    workspace.advance_curation("thread", 5)
    workspace.reserve_mseq("thread", 6)
    assert harness.rewind_evidence("thread", (after - timedelta(seconds=1)).isoformat(), 4) == 1
    assert durable.get(entry.id, context).valid_to is not None
    assert workspace.get_segments("thread") == []
    assert workspace.curation_watermark("thread") == 4
    assert workspace.reserve_mseq("thread", 1) == 7
    durable.close()


@pytest.mark.asyncio
async def test_cancelled_embedding_task_can_be_scheduled_again():
    calls = []

    class Writer:
        async def drain(self, context):
            calls.append(context.thread_id)

    harness = MemoryHarness(enabled=True, compactor=None, recall_v2=True, repo_id="repo")
    harness._embedding_writer = Writer()
    harness._schedule_embeddings("thread")
    task = next(iter(harness._bg_tasks))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert "thread" not in harness._embedding_runs
    harness._schedule_embeddings("thread")
    await asyncio.gather(*harness._bg_tasks)
    assert calls == ["thread"]
