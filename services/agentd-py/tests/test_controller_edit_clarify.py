"""ACTIVE-phase clarify: the controller may ask a clarifying question while acting,
and the user's reply (via the clarify gate → resolve_clarify) RESUMES the loop in
the SAME phase it was raised in (not a PLAN restart that would force re-picking the
mode). The phase is preserved via `resume_phase` carried in the clarify gate
payload — both PLAN and ACTIVE clarifies carry their own phase through."""
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
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


class _PhaseRecordingEngine(ScriptedReasoningEngine):
    """Records the `phase` each controller step ran in, in call order."""

    def __init__(self, responses):
        super().__init__(None, [], controller_step_responses=responses)
        self.phases: list[str] = []

    async def create_controller_step(
        self, plan_context, history, tool_definitions, *, phase, on_thinking=None, on_retry=None, on_progress=None, on_salvage=None, on_usage=None, unconstrained=False):
        self.phases.append(phase)
        return await super().create_controller_step(
            plan_context, history, tool_definitions, phase=phase, on_thinking=on_thinking)


def _orchestrator(tmp_path: Path) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_NoopReasoning(),
        validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )


@pytest.mark.asyncio
async def test_clarify_in_active_phase_resumes_in_active(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(ws), title="t")
    chan = f"chat:{th.thread_id}"

    eng = _PhaseRecordingEngine([
        # turn 1 (PLAN): propose implement
        {"type": "propose_mode", "thought": "t", "plan_sketch": "add clamp() to util.py",
         "reason": "r", "recommended": "implement", "options": [
             {"mode": "implement", "label": "Edit inline now", "description": "d"}]},
        # mode pick → ACTIVE: agent is blocked, asks a question
        {"type": "clarify", "thought": "t", "question": "clamp to what range?"},
        # user replies → MUST resume in ACTIVE: emit the edit, then submit
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "create_file", "file": "util.py",
             "content": "def clamp(x):\n    return max(0, min(1, x))\n", "reason": "add"}]},
        {"type": "submit_changes", "thought": "done", "summary": "added clamp"},
    ])
    ctrl = ChatController(
        workspace_path=str(ws), reasoning_engine=eng, thread_store=store,
        orchestrator=_orchestrator(tmp_path), broadcaster=EventBroadcaster(),
        retrieval_client=None)

    # turn 1 → propose_mode gate (Plan Mode toggle on, so the turn starts in PLAN)
    await ctrl.handle_message(
        th.thread_id, "add a clamp helper", channel_id=chan, plan_mode=True)
    # pick implement → ACTIVE loop emits clarify (question to the user)
    await ctrl.resolve_mode(th.thread_id, "implement", channel_id=chan, goal="add a clamp helper")
    # The ACTIVE clarify sets a durable clarify gate carrying resume_phase=ACTIVE, so the
    # answer (via resolve_clarify) resumes ACTIVE rather than restarting PLAN.
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "clarify"
    assert gate.payload["resume_phase"] == "ACTIVE"
    # user answers via the card → the resumed turn runs in ACTIVE, emits the edit + submit
    await ctrl.resolve_clarify(th.thread_id, "[0, 1]", channel_id=chan, goal="add a clamp helper")

    # Phases: turn1=PLAN, mode-pick=ACTIVE(clarify), resumed turn=ACTIVE,ACTIVE (edit+submit).
    assert eng.phases == ["PLAN", "ACTIVE", "ACTIVE", "ACTIVE"]
    # The edit was actually applied to the real workspace (instant-promote).
    assert (ws / "util.py").read_text().startswith("def clamp(")
    # Gate cleared once the ACTIVE turn terminated cleanly.
    assert store.get_thread(th.thread_id).pending_controller_gate is None


@pytest.mark.asyncio
async def test_plan_clarify_sets_plan_resume(tmp_path: Path):
    """A PLAN-phase clarify (Plan Mode toggle on) sets a clarify gate with
    resume_phase=PLAN — it carries through its own originating phase, same as an
    ACTIVE-phase clarify carries resume_phase=ACTIVE (see the other test above)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(ws), title="t")
    eng = _PhaseRecordingEngine([
        {"type": "clarify", "thought": "t", "question": "which thing?"},
    ])
    ctrl = ChatController(
        workspace_path=str(ws), reasoning_engine=eng, thread_store=store,
        orchestrator=_orchestrator(tmp_path), broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(
        th.thread_id, "fix it", channel_id=f"chat:{th.thread_id}", plan_mode=True)
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "clarify"
    assert gate.payload["resume_phase"] == "PLAN"
