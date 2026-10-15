import json

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.models import ChatMessage
from agentd.chat.rewind import RewindStore
from agentd.chat.storage import ChatThreadStore
from agentd.memory.config import MemoryConfig
from agentd.memory.harness import build_memory_harness
from agentd.memory.models import DurableMemory
from agentd.memory.recap import validate_recap
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


@pytest.mark.asyncio
async def test_controller_recall_skips_notice_and_uses_prior_completed_answer(tmp_path):
    chat = ChatThreadStore(tmp_path / "chat.db")
    thread = chat.create_thread(str(tmp_path))
    chat.append_message(thread.thread_id, ChatMessage(
        role="agent", content="Use transaction writes",
    ))
    chat.append_message(thread.thread_id, ChatMessage(
        role="agent", content="⚠️ Usage limit reached.", metadata={"system_notice": True},
    ))
    chat.append_message(thread.thread_id, ChatMessage(
        role="agent", content="Checking files", metadata={"progress": True},
    ))
    calls = []

    class Transport:
        async def generate_json(self, **kwargs):
            calls.append(kwargs["user_payload"])
            return {"memory_ids": [], "reasons": []}

    cfg = MemoryConfig.from_env({
        "CRUCIBLE_MEMORY_RECALL_V2": "1", "CRUCIBLE_LOCAL_MODELS": "0",
        "CRUCIBLE_MEMORY_DB_PATH": str(tmp_path / "workspace.db"),
        "CRUCIBLE_MEMORY_STORE_PATH": str(tmp_path / "durable.db"),
    })
    harness = build_memory_harness(cfg, Transport(), "fake", workspace_path=str(tmp_path))
    context = harness.scope_context(thread.thread_id)
    harness.durable_store.put(DurableMemory(
        scope_kind="repo", scope_id=context.repo_id, kind="procedural",
        content="Use BEGIN IMMEDIATE for transaction writes", topic="transaction writes",
        applies_when="when SQLite writers race", provenance="tool_verified",
        source_thread_id=thread.thread_id, source_message_id=1,
    ), context, origin="agent_nomination")
    engine = ScriptedReasoningEngine(None, [], controller_step_responses=[{
        "type": "answer", "answer": "Done", "recap": "Transaction writes reviewed.",
    }])
    controller = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=engine, thread_store=chat,
        orchestrator=None, broadcaster=EventBroadcaster(), retrieval_client=None,
        memory_harness=harness,
    )
    await controller.handle_message(thread.thread_id, "transaction", channel_id="c1")
    assert calls == []
    assert harness._v2_recall.turns[thread.thread_id].signals.c == "Use transaction writes"
    harness.durable_store.close()


@pytest.mark.asyncio
async def test_v2_controller_delivers_once_with_keyword_only_models_and_real_tool_slots(
    tmp_path, monkeypatch
):
    chat = ChatThreadStore(tmp_path / "chat.db")
    thread = chat.create_thread(str(tmp_path))
    judge_calls = []

    class Transport:
        async def generate_json(self, **kwargs):
            judge_calls.append(kwargs)
            chosen = kwargs["user_payload"]["candidates"][0].split("@")[0][3:]
            return {"memory_ids": [chosen], "reasons": ["needed for transaction writes"]}

        async def generate_text(self, **kwargs):
            return "<summary>anchor</summary>"

    cfg = MemoryConfig.from_env(
        {
            "CRUCIBLE_MEMORY_RECALL_V2": "1",
            "CRUCIBLE_LOCAL_MODELS": "0",
            "CRUCIBLE_MEMORY_DB_PATH": str(tmp_path / "workspace.db"),
            "CRUCIBLE_MEMORY_STORE_PATH": str(tmp_path / "durable.db"),
        }
    )
    harness = build_memory_harness(cfg, Transport(), "fake", workspace_path=str(tmp_path))
    harness.bind_memory_state(chat)
    searches = []
    original_search = harness._v2_recall.search.search

    async def record_search(*args, **kwargs):
        searches.append(args[0].payload())
        return await original_search(*args, **kwargs)

    monkeypatch.setattr(harness._v2_recall.search, "search", record_search)
    context = harness.scope_context(thread.thread_id)
    entry = harness.durable_store.put(
        DurableMemory(
            scope_kind="repo",
            scope_id=context.repo_id,
            kind="procedural",
            content="Use BEGIN IMMEDIATE for transaction writes",
            topic="transaction writes",
            applies_when="when SQLite writers race",
            detail="Acquire the SQLite write reservation before modifying durable entries.",
            provenance="tool_verified",
            source_thread_id=thread.thread_id,
            source_message_id=1,
        ),
        context,
        origin="agent_nomination",
    )
    (tmp_path / "a.py").write_text("value = 1\n")
    captured = []

    class Engine(ScriptedReasoningEngine):
        async def create_controller_step(self, *, history, **kwargs):
            captured.append(json.loads(json.dumps(history)))
            return await super().create_controller_step(history=history, **kwargs)

    engine = Engine(
        None,
        [],
        controller_step_responses=[
            {
                "type": "tool_calls",
                "thought": "read a.py",
                "calls": [
                    {"id": "read", "tool": "read_file", "args": {"path": "a.py"}},
                ],
            },
            {
                "type": "tool_calls",
                "thought": "Read the applicable memory before using its procedure",
                "calls": [{"id": "memory", "tool": "read_memory", "args": {"id": entry.id}}],
            },
            {
                "type": "answer",
                "answer": "value is 1",
                "recap": "Inspecting transaction writes in a.py.",
            },
        ],
    )
    registry = AggregatingToolRegistry(
        [
            BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path),
            harness.memory_tool_source(thread.thread_id),
        ]
    )
    loop = ControllerLoop(
        engine,
        registry,
        EventBroadcaster(),
        channel_id=thread.thread_id,
        phase_sm=ControllerPhaseSM(),
        memory_harness=harness,
    )
    result = await loop.run(
        {
            "goal": "Fix transaction writes",
            "workspace_path": str(tmp_path),
            "run_id": thread.thread_id,
            "memory_turn_key": "turn-1",
            "memory_budget_key": "user-message-1",
            "artifact_thread_id": thread.thread_id,
            "artifact_turn_id": "turn-1",
        },
        max_iters=4,
        seed_history=[{"role": "user", "content": "Fix transaction writes"}],
    )
    assert result.kind == "answer" and judge_calls == []
    assert len(searches) == 1  # The read_file result must not cause another lookup.
    assert all(
        sum("[m:" in str(message.get("content")) for message in history) == 1
        for history in captured
    )
    assert entry.id in str(captured[0])
    assert chat.get_memory_state(thread.thread_id).get("judge_claims") is None
    source = harness.memory_tool_source(thread.thread_id).source_mseq()
    assert source == result.history[-1]["mseq"]
    assert any(
        message.get("tool") == "read_file" and message["status"] == "success"
        for message in result.history
    )

    assert any(message.get("tool") == "read_memory" and "Acquire the SQLite" in message["content"]
               for message in result.history)
    assert "Acquire the SQLite" not in str(captured[0])  # Only a catalog was initially offered.


def test_recap_and_private_shown_state_restore_with_checkpoint(tmp_path):
    chat = ChatThreadStore(tmp_path / "chat.db")
    thread = chat.create_thread(str(tmp_path))
    chat.set_recap(thread.thread_id, "Original thread purpose")
    chat.set_memory_state(thread.thread_id, {"judge_claims": ["old"]})
    original = [
        {
            "role": "user",
            "content": "background",
            "mseq": 1,
            "_evidence": {
                "kind": "injected_memory",
                "memory_ids": ["old-id"],
            },
        }
    ]
    chat.set_controller_history(thread.thread_id, original)
    anchor = chat.append_message(thread.thread_id, ChatMessage(role="user", content="new request"))
    rewind = RewindStore(chat, tmp_path)
    rewind.open_checkpoint(
        thread.thread_id,
        anchor,
        "turn",
        thread=chat.get_thread(thread.thread_id),
        memory_anchor_md=None,
    )
    assert chat.turn_message_id(thread.thread_id, "turn") == anchor
    chat.set_recap(thread.thread_id, "Later purpose")
    chat.set_memory_state(thread.thread_id, {"judge_claims": ["old", "new"]})
    chat.set_controller_history(thread.thread_id, [])
    rewind.restore(thread.thread_id, anchor)
    restored = chat.get_thread(thread.thread_id)
    assert restored.recap == "Original thread purpose"
    assert restored.memory_state == {"judge_claims": ["old"]}
    assert restored.controller_conversation_history == original
    for invalid in ("", "x" * 401, "\n".join("line" for _ in range(6))):
        with pytest.raises(ValueError):
            validate_recap(invalid)


def test_source_allocation_failure_cannot_borrow_an_older_action_id(tmp_path):
    cfg = MemoryConfig.from_env({
        "CRUCIBLE_MEMORY_RECALL_V2": "1", "CRUCIBLE_LOCAL_MODELS": "0",
        "CRUCIBLE_MEMORY_DB_PATH": str(tmp_path / "workspace.db"),
        "CRUCIBLE_MEMORY_STORE_PATH": str(tmp_path / "durable.db"),
    })
    harness = build_memory_harness(cfg, object(), "fake", workspace_path=str(tmp_path))
    harness._histories["thread"] = [
        {"role": "assistant", "content": "old action", "mseq": 1},
        {"role": "assistant", "content": "latest action", "_evidence": {
            "source_identity_unavailable": True,
        }},
    ]
    assert harness.memory_tool_source("thread").source_mseq() is None
