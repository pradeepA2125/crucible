"""Startup reap after a restart (spec §11.5) and the flag-coherence warning (§12)."""
import logging
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.controller_factory import warn_if_incoherent_flags
from agentd.chat.models import AgentRecord, GateAgent, PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


def _row(agent_id: str, status: str) -> AgentRecord:
    return AgentRecord(agent_id=agent_id, thread_id="t", turn_id="u", depth=1,
                       name="general-purpose", label=agent_id, prompt="p", status=status)


def test_the_startup_reap(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    for agent_id, status in (("a", "running"), ("b", "waiting"), ("c", "queued"),
                             ("d", "completed")):
        store.insert_agent(_row(agent_id, status))
    parent_gate = store.add_controller_gate(tid, PendingGate.new("edit", {}))
    store.add_controller_gate(tid, PendingGate.new(
        "command", {"command": "ls"}, agent=GateAgent(id="a", label="a", name="g")))
    shadows = tmp_path / "shadows"
    (shadows / f"chatturn-{tid}-agent-0123456789ab").mkdir(parents=True)
    (shadows / f"chatturn-{tid}").mkdir()
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, broadcaster=EventBroadcaster(), retrieval_client=None,
        orchestrator=AgentOrchestrator(
            store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(),
            validator=_Validator(), patch_engine=PatchEngine(),
            workspace_manager=ShadowWorkspaceManager(root_path=shadows)))

    ctrl.reap_subagents()

    statuses = {r.agent_id: (r.status, r.report) for r in store.list_agents("t")}
    assert statuses["a"] == ("failed", "Status: failed — backend restarted")
    assert statuses["b"][0] == statuses["c"][0] == "failed"
    assert statuses["d"] == ("completed", "")
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [parent_gate.gate_id]
    assert thread.messages[-1].content == (
        "✗ Sub-agent approvals were cleared — the backend restarted.")
    assert not (shadows / f"chatturn-{tid}-agent-0123456789ab").exists()
    assert (shadows / f"chatturn-{tid}").exists()  # the parent's orphan recovery needs it


def test_explicit_subagents_without_the_controller_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "1")
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "0")
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("t"))
    assert any("CRUCIBLE_SUBAGENTS_ENABLED" in r.message for r in caplog.records)
    caplog.clear()
    monkeypatch.delenv("CRUCIBLE_SUBAGENTS_ENABLED")
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("t"))
    assert not any("CRUCIBLE_SUBAGENTS_ENABLED" in r.message for r in caplog.records)
