"""End to end: the parent dispatches children that edit the shared workspace (spec §5, §6)."""
import json
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.subagents.framing import frame
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _controller(ws: Path, tmp_path: Path, store: ChatThreadStore,
                engine: ScriptedReasoningEngine) -> ChatController:
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    return ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=orchestrator, broadcaster=EventBroadcaster(), retrieval_client=None)


def _creates(file: str, text: str) -> list[dict[str, object]]:
    return [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "create_file", "file": file, "content": text, "reason": "r"}]},
        {"type": "report", "thought": "t", "summary": f"Created {file}."},
    ]


def _dispatch(*agents: tuple[str, str, str]) -> dict[str, object]:
    return {"type": "tool_call", "thought": "fan out", "tool": "dispatch_agents",
            "args": {"agents": [{"agent": a, "label": label, "prompt": prompt}
                                for a, label, prompt in agents]}}


DONE = {"type": "submit_changes", "thought": "d", "summary": "All parts done."}
# Dispatch returns at once (spec §4.1); reports are collected with wait_agents.
WAIT = {"type": "tool_call", "thought": "collect", "tool": "wait_agents", "args": {}}


@pytest.mark.asyncio
async def test_two_children_edit_disjoint_files(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(
        None, [], controller_step_responses=[
            _dispatch(("general-purpose", "impl-a", "Create a.py"),
                      ("general-purpose", "impl-b", "Create b.py")), WAIT, DONE],
        agent_scripts={"impl-a": _creates("a.py", "A = 1\n"),
                       "impl-b": _creates("b.py", "B = 2\n")})
    ctrl = _controller(ws, tmp_path, store, engine)
    thread_events = ctrl._broadcaster.subscribe(f"chat:{tid}")

    await ctrl.handle_message(tid, "build both parts", channel_id=f"chat:{tid}")

    assert (ws / "a.py").read_text() == "A = 1\n"
    assert (ws / "b.py").read_text() == "B = 2\n"
    rows = {r.label: r for r in store.list_agents(tid)}
    assert {k: (r.status, r.files_changed, r.depth) for k, r in rows.items()} == {
        "impl-a": ("completed", ["a.py"], 1), "impl-b": ("completed", ["b.py"], 1)}
    for label, file in (("impl-a", "a.py"), ("impl-b", "b.py")):
        transcript = rows[label].transcript
        assert transcript[-1].content == f"Created {file}."
        assert transcript[-1].metadata["report"] is True
        assert any(m.type == "diff_card" for m in transcript)
        assert all("seq" in m.metadata for m in transcript)
    thread = store.get_thread(tid)
    assert thread is not None
    [roster] = [m for m in thread.messages if m.type == "agent_dispatch"]
    assert sorted(roster.metadata["agent_ids"]) == sorted(r.agent_id for r in rows.values())
    assert not any(m.type == "diff_card" for m in thread.messages)  # children's stay theirs
    history = thread.controller_conversation_history or []
    result = next(m for m in history if m.get("tool") == "wait_agents")
    entries = json.loads(str(result["content"]))
    assert [(e["label"], e["status"], e["report"]) for e in entries] == [
        ("impl-a", "completed", frame("impl-a (general-purpose)", "report", "Created a.py.")),
        ("impl-b", "completed", frame("impl-b (general-purpose)", "report", "Created b.py."))]
    types = [thread_events.get_nowait()["type"] for _ in range(thread_events.qsize())]
    assert types.count("agent_started") == 2 and types.count("agent_finished") == 2


@pytest.mark.asyncio
async def test_nested_dispatch_stops_at_the_depth_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_SUBAGENT_MAX_CONCURRENT", "1")  # the lead must lend its slot
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = ScriptedReasoningEngine(
        None, [], controller_step_responses=[
            _dispatch(("general-purpose", "lead", "Split the work")), WAIT, DONE],
        agent_scripts={
            "lead": [_dispatch(("general-purpose", "leaf", "Create leaf.py")), WAIT,
                     {"type": "report", "thought": "t", "summary": "Lead done."}],
            "leaf": _creates("leaf.py", "LEAF = 1\n")})
    ctrl = _controller(ws, tmp_path, store, engine)

    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")

    rows = {r.label: r for r in store.list_agents(tid)}
    assert rows["leaf"].depth == 2 and rows["leaf"].parent_agent_id == rows["lead"].agent_id
    assert rows["lead"].files_changed == ["leaf.py"]  # the subtree's files
    assert any(m.type == "agent_dispatch" for m in rows["lead"].transcript)
    assert ctrl._subagents is not None
    lead = ctrl._subagents.registry.get(rows["lead"].agent_id)
    leaf = ctrl._subagents.registry.get(rows["leaf"].agent_id)
    assert lead is not None and leaf is not None
    assert "dispatch_agents" in [d.name for d in lead.loop._registry.definitions()]
    assert "dispatch_agents" not in [d.name for d in leaf.loop._registry.definitions()]


@pytest.mark.asyncio
async def test_flag_off_the_parent_has_no_dispatch_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "0")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    ctrl = _controller(tmp_path, tmp_path, store, ScriptedReasoningEngine(None, []))
    assert ctrl._subagents is None
    names = [d.name for d in ctrl._build_registry().definitions()]
    assert "dispatch_agents" not in names
