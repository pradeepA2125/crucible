"""The parent is guarded by its thread's write log when sub-agents are enabled."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.subagents.write_log import MAIN_AGENT_ID
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _controller(ws: Path, tmp_path: Path, store: ChatThreadStore,
                steps: list[dict[str, object]]) -> ChatController:
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    return ChatController(
        workspace_path=str(ws),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        thread_store=store, orchestrator=orchestrator, broadcaster=EventBroadcaster(),
        retrieval_client=None)


def _edit() -> dict[str, object]:
    return {"type": "edit", "thought": "t", "patch_ops": [
        {"op": "search_replace", "file": "f.py", "search": "x = 1", "replace": "x = 2",
         "reason": "r"}]}


def test_no_log_when_the_flag_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "0")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    ctrl = _controller(tmp_path, tmp_path, store, [])
    assert ctrl._write_log_for("t1") is None


def test_one_log_per_thread_when_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    ctrl = _controller(tmp_path, tmp_path, store, [])
    first = ctrl._write_log_for("t1")
    assert first is not None and ctrl._write_log_for("t1") is first
    assert ctrl._write_log_for("t2") is not first


@pytest.mark.asyncio
async def test_the_parent_is_refused_until_it_rereads(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    steps = [
        _edit(),
        {"type": "tool_call", "thought": "reread", "tool": "read_file",
         "args": {"path": "f.py"}},
        _edit(),
        {"type": "submit_changes", "thought": "d", "summary": "ok"},
    ]
    ctrl = _controller(ws, tmp_path, store, steps)
    log = ctrl._write_log_for(tid)
    assert log is not None
    log.register_agent("a1")
    log.note_promote("a1", "impl", "general-purpose", ["f.py"])  # a sibling's earlier write

    await ctrl.handle_message(tid, "bump x", channel_id=f"chat:{tid}")

    assert (ws / "f.py").read_text() == "x = 2\n"
    assert log.stale_refusals(MAIN_AGENT_ID) == 1
    history = store.get_thread(tid).controller_conversation_history or []
    assert any(str(m.get("content", "")).startswith("PATCH FAILED: `f.py` was modified")
               for m in history)


@pytest.mark.asyncio
async def test_flag_off_the_same_turn_is_never_refused(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "0")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("x = 1\n")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    ctrl = _controller(ws, tmp_path, store,
                       [_edit(), {"type": "submit_changes", "thought": "d", "summary": "ok"}])
    await ctrl.handle_message(tid, "bump x", channel_id=f"chat:{tid}")
    assert (ws / "f.py").read_text() == "x = 2\n"
