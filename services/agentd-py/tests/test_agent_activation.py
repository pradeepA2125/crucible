"""Activations: saved history, resume, continuation, cascade (spec §3.2, §3.4, §4.3)."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.subagents.runtime import AgentBusyError, AgentNotFoundError, AgentNotYoursError
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


class _Recording(ScriptedReasoningEngine):
    """Records (agent label, history) for every model call."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.seen: list[tuple[str, list[dict[str, object]]]] = []

    async def create_controller_step(self, plan_context, history, tool_definitions, **kwargs):  # type: ignore[no-untyped-def]
        label = getattr(kwargs.get("render_ctx"), "agent_label", "")
        self.seen.append((label, [dict(m) for m in history]))
        return await super().create_controller_step(
            plan_context, history, tool_definitions, **kwargs)


def _controller(ws: Path, tmp_path: Path, store: ChatThreadStore, engine) -> ChatController:  # type: ignore[no-untyped-def]
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    return ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=orchestrator, broadcaster=EventBroadcaster(), retrieval_client=None)


def _dispatch(label: str, prompt: str) -> dict[str, object]:
    return {"type": "tool_call", "thought": "go", "tool": "dispatch_agents",
            "args": {"agents": [{"agent": "general-purpose", "label": label, "prompt": prompt}]}}


DONE = {"type": "submit_changes", "thought": "d", "summary": "done"}


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kid_script):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = _Recording(None, [], controller_step_responses=[
        _dispatch("kid", "Investigate the parser"), DONE], agent_scripts={"kid": kid_script})
    return ws, store, tid, engine


@pytest.mark.asyncio
async def test_resume_continues_with_the_original_task(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    ws, store, tid, engine = _setup(tmp_path, monkeypatch, [
        {"type": "report", "thought": "t", "summary": "first pass", "status": "partial"},
        {"type": "report", "thought": "t", "summary": "second pass"}])
    ctrl = _controller(ws, tmp_path, store, engine)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    assert (row.status, row.activation_count, row.dispatcher_id) == ("partial", 1, "main")
    assert row.history and row.history[0] == {"role": "user", "content": "Investigate the parser"}

    handle = ctrl.resume_agent(tid, row.agent_id, "Now finish it")
    [result] = await ctrl._subagents.wait([handle])  # type: ignore[union-attr]

    assert result.status == "completed"
    second = [h for label, h in engine.seen if label == "kid"][-1]
    contents = [str(m.get("content")) for m in second]
    assert "Investigate the parser" in contents             # the original task survived
    assert "Message from main:\nNow finish it" in contents  # the resume input
    row = store.get_agent(row.agent_id)
    assert row is not None and (row.activation_count, row.report) == (2, "second pass")
    dividers = [m for m in row.transcript if m.metadata.get("divider")]
    assert len(dividers) == 1 and "Now finish it" in dividers[0].content
    seqs = [int(m.metadata["seq"]) for m in row.transcript]
    assert seqs == sorted(seqs) and row.last_seq >= seqs[-1]


@pytest.mark.asyncio
async def test_resume_after_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws, store, tid, engine = _setup(tmp_path, monkeypatch, [
        {"type": "report", "thought": "t", "summary": "one"},
        {"type": "report", "thought": "t", "summary": "two"}])
    await _controller(ws, tmp_path, store, engine).handle_message(
        tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)

    # A restarted backend: no write log, no in-memory handles.
    fresh = _controller(ws, tmp_path, store, engine)
    [result] = await fresh._subagents.wait(  # type: ignore[union-attr]
        [fresh.resume_agent(tid, row.agent_id, "again")])
    assert result.status == "completed"
    assert store.get_agent(row.agent_id).report == "two"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_resume_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws, store, tid, engine = _setup(tmp_path, monkeypatch, [
        {"type": "report", "thought": "t", "summary": "one"}])
    ctrl = _controller(ws, tmp_path, store, engine)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    with pytest.raises(AgentNotFoundError):
        ctrl.resume_agent(tid, "agent-nope", "x")
    with pytest.raises(AgentNotYoursError):
        ctrl.resume_agent(tid, row.agent_id, "x", caller_id="agent-other")
    handle = ctrl.resume_agent(tid, row.agent_id, "x")
    with pytest.raises(AgentBusyError):
        ctrl.resume_agent(tid, row.agent_id, "y")
    await ctrl._subagents.wait([handle])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_files_changed_accumulate_across_activations(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def create(file: str) -> dict[str, object]:
        return {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "create_file", "file": file, "content": "x = 1\n", "reason": "r"}]}

    ws, store, tid, engine = _setup(tmp_path, monkeypatch, [
        create("a.py"), {"type": "report", "thought": "t", "summary": "a"},
        create("b.py"), {"type": "report", "thought": "t", "summary": "b"}])
    ctrl = _controller(ws, tmp_path, store, engine)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    [result] = await ctrl._subagents.wait(  # type: ignore[union-attr]
        [ctrl.resume_agent(tid, row.agent_id, "also b")])
    assert result.files_changed == ["a.py", "b.py"]
    assert store.get_agent(row.agent_id).files_changed == ["a.py", "b.py"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_dispatch_stamps_checkpoint_and_inherits(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    ws, store, tid, engine = _setup(tmp_path, monkeypatch, [
        {"type": "report", "thought": "t", "summary": "one"}])
    ctrl = _controller(ws, tmp_path, store, engine)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    assert row.checkpoint_seq == store.current_checkpoint_seq(tid)
    assert row.definition["name"] == "general-purpose"
    assert row.inherited == {}  # the main agent passes down no restrictions (§3.12)


def test_divider_text() -> None:
    from agentd.chat.controller import _divider_text

    assert _divider_text("Message from main:\nNow finish it\nmore") == (
        '↩ Message from main: "Now finish it"')
    assert _divider_text("Your previous run ended failed: Status: failed\n\nUnfinished:\n- x\n\n"
                         "Message from main:\nretry") == '↩ Message from main: "retry"'
    assert _divider_text("New message:\nchild done") == '↩ New message: "child done"'


@pytest.mark.asyncio
async def test_a_resumed_helper_keeps_inherited_restrictions(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws, store, tid, engine = _setup(tmp_path, monkeypatch, [
        {"type": "report", "thought": "t", "summary": "one"},
        {"type": "report", "thought": "t", "summary": "two"}])
    ctrl = _controller(ws, tmp_path, store, engine)
    await ctrl.handle_message(tid, "go", channel_id=f"chat:{tid}")
    [row] = store.list_agents(tid)
    # Simulate a capped, read-only dispatcher's helper: the row carries what it inherited.
    store._conn.execute("UPDATE chat_agents SET inherited_json = ? WHERE agent_id = ?",
                        ('{"read_only": true, "no_ask": true, "capped": true}', row.agent_id))
    store._conn.commit()
    handle = ctrl._handle_from_record(tid, row.agent_id)
    assert (handle.context.permission, handle.context.no_ask, handle.context.edit_review) == (
        "plan", True, "required")
