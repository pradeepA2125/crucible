"""Controller debug artifacts: the controller path now writes the exact per-iteration
LLM bytes + a turn trace under chat/<thread_id>/<turn_id>/ — the controller analog of
the task path's debug-plan-turn-NN / tool-trace.json (which are keyed by task_id and so
never fired for chat turns)."""
import json

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.reasoning.engine import DefaultReasoningEngine
from agentd.runtime.artifacts import chat_turn_artifacts_root


def test_chat_turn_artifacts_root_nests_by_thread_and_turn(tmp_path, monkeypatch):
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    root = chat_turn_artifacts_root("th1", "turn1", tmp_path)
    assert root == tmp_path / ".crucible/state" / "artifacts" / "chat" / "th1" / "turn1"


class _RecordingTransport:
    supports_oneof_grammar = False

    async def generate_json(self, *, model, schema_name, schema,
                            system_instructions, user_payload, on_thinking=None, on_retry=None):
        return {"type": "answer", "thought": "t", "answer": "a"}

    async def generate_text(self, *, model, system_instructions, user_payload, on_thinking=None):
        return ""


@pytest.mark.asyncio
async def test_create_controller_step_dumps_exact_llm_bytes(tmp_path, monkeypatch):
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    engine = DefaultReasoningEngine(model="m", transport=_RecordingTransport())  # type: ignore[arg-type]
    await engine.create_controller_step(
        {"goal": "g", "workspace_path": str(tmp_path),
         "artifact_thread_id": "th1", "artifact_turn_id": "turn1"},
        history=[], tool_definitions=[], phase="DECIDE")

    dump = chat_turn_artifacts_root("th1", "turn1", tmp_path) / "controller-turn-00.json"
    assert dump.exists(), "exact-bytes dump not written"
    data = json.loads(dump.read_text())
    assert "system_instructions" in data and data["system_instructions"]
    assert "user_payload" in data and data["user_payload"]["goal"] == "g"
    assert data["phase"] == "DECIDE"
    assert data["raw_result"]["type"] == "answer"


@pytest.mark.asyncio
async def test_continuation_turn_numbers_from_zero_with_original_goal(tmp_path, monkeypatch):
    # A continuation turn replays seed_history, so `history` is non-empty on iteration 0.
    # Numbering must still start at -00 (per-turn), and original_goal comes from the first
    # user message in history — not the current `goal` (this turn's message).
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    engine = DefaultReasoningEngine(model="m", transport=_RecordingTransport())  # type: ignore[arg-type]
    seed = [
        {"role": "user", "content": "what are the issues in the pipeline?"},
        {"role": "assistant", "content": "{}"},
    ]
    await engine.create_controller_step(
        {"goal": "understood, let's do this", "workspace_path": str(tmp_path),
         "artifact_thread_id": "th1", "artifact_turn_id": "turn1",
         "artifact_seed_len": len(seed)},
        history=list(seed), tool_definitions=[], phase="DECIDE")

    root = chat_turn_artifacts_root("th1", "turn1", tmp_path)
    assert (root / "controller-turn-00.json").exists(), "continuation turn must start at -00"
    data = json.loads((root / "controller-turn-00.json").read_text())
    assert data["user_payload"]["goal"] == "understood, let's do this"
    assert data["original_goal"] == "what are the issues in the pipeline?"


@pytest.mark.asyncio
async def test_create_controller_step_no_dump_without_artifact_ids(tmp_path, monkeypatch):
    # No artifact ids in plan_context → no dump (e.g. tests / non-controller callers).
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    engine = DefaultReasoningEngine(model="m", transport=_RecordingTransport())  # type: ignore[arg-type]
    await engine.create_controller_step(
        {"goal": "g", "workspace_path": str(tmp_path)},
        history=[], tool_definitions=[], phase="DECIDE")
    assert not (tmp_path / ".crucible/state" / "artifacts" / "chat").exists()


@pytest.mark.asyncio
async def test_handle_message_writes_turn_trace(tmp_path, monkeypatch):
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=ScriptedReasoningEngine(
            None, [], controller_step_responses=[
                {"type": "answer", "thought": "t", "answer": "hello"}]),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(thread.thread_id, "hi", channel_id="c1")

    thread_dir = tmp_path / ".crucible/state" / "artifacts" / "chat" / thread.thread_id
    traces = list(thread_dir.glob("*/turn-trace.json"))
    assert len(traces) == 1, f"expected one turn-trace, found {traces}"
    data = json.loads(traces[0].read_text())
    assert data["goal"] == "hi"
    assert data["outcome_kind"] == "answer"
    assert data["phase"] == "DECIDE"
