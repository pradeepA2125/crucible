"""The active skill must survive a clarify round-trip (a multi-round flow like
brainstorming's "ask one question at a time" is one logical flow spanning many
_run_loop calls), so a resumed turn never needs to call read_skill again for a
skill it already activated earlier in the SAME thread."""
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


class _SkillsPayloadRecordingEngine(ScriptedReasoningEngine):
    """Records plan_context["active_skills"] as seen on EVERY controller step call,
    in order — lets a test assert a resumed turn's FIRST call already had the skill,
    without needing a fresh read_skill tool_call to populate it."""

    def __init__(self, responses):
        super().__init__(None, [], controller_step_responses=responses)
        self.active_skills_per_call: list[object] = []

    async def create_controller_step(
        self, plan_context, history, tool_definitions, *, phase, on_thinking=None, on_retry=None):
        self.active_skills_per_call.append(plan_context.get("active_skills"))
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


def _write_skill(ws: Path, name: str, body: str) -> None:
    d = ws / ".crucible" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_active_skill_survives_clarify_resume_without_reread(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_SKILLS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_skill(ws, "brainstorming", "STEP 1: explore. STEP 2: ask one question at a time.")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(ws), title="t")
    chan = f"chat:{th.thread_id}"

    eng = _SkillsPayloadRecordingEngine([
        # turn 1 iter 0: activate the skill
        {"type": "tool_call", "thought": "t", "tool": "read_skill",
         "args": {"name": "brainstorming"}},
        # turn 1 iter 1: ask a clarifying question (ends the turn on a clarify gate)
        {"type": "clarify", "thought": "t", "question": "what scope?"},
        # turn 2 (resolve_clarify resume): answers directly — no read_skill needed
        {"type": "answer", "thought": "t", "answer": "done"},
    ])
    ctrl = ChatController(
        workspace_path=str(ws), reasoning_engine=eng, thread_store=store,
        orchestrator=_orchestrator(tmp_path), broadcaster=EventBroadcaster(),
        retrieval_client=None)

    # turn 1: activates the skill, then raises the clarify gate
    await ctrl.handle_message(th.thread_id, "build something", channel_id=chan)
    assert store.get_controller_active_skill(th.thread_id) is not None
    gate = store.get_thread(th.thread_id).pending_controller_gate
    assert gate is not None and gate.kind == "clarify"

    skill_body = (ws / ".crucible" / "skills" / "brainstorming" / "SKILL.md").read_text()

    # turn 1's two calls: iter 0 has no skill yet (not read), iter 1 already has it
    # (activated mid-turn, shared in-memory dict — this part already worked before the fix).
    assert eng.active_skills_per_call[0] in (None, [])
    assert eng.active_skills_per_call[1] == [{"name": "brainstorming", "body": skill_body}]

    # turn 2 (a FRESH _run_loop call via resolve_clarify): its first call must ALREADY
    # have the skill rehydrated from the thread — the bug this fix closes.
    await ctrl.resolve_clarify(th.thread_id, "core", channel_id=chan, goal="build something")
    assert eng.active_skills_per_call[2] == [{"name": "brainstorming", "body": skill_body}]


@pytest.mark.asyncio
async def test_active_skill_replaced_not_accumulated_across_turns(tmp_path: Path, monkeypatch):
    """A second, different skill activated in a later turn REPLACES the first
    (only one active skill at a time) rather than growing the persisted set."""
    monkeypatch.setenv("CRUCIBLE_SKILLS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_skill(ws, "brainstorming", "brainstorm body")
    _write_skill(ws, "writing-plans", "writing-plans body")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    th = store.create_thread(str(ws), title="t")
    chan = f"chat:{th.thread_id}"

    eng = _SkillsPayloadRecordingEngine([
        {"type": "tool_call", "thought": "t", "tool": "read_skill",
         "args": {"name": "brainstorming"}},
        {"type": "answer", "thought": "t", "answer": "spec written"},
    ])
    ctrl = ChatController(
        workspace_path=str(ws), reasoning_engine=eng, thread_store=store,
        orchestrator=_orchestrator(tmp_path), broadcaster=EventBroadcaster(),
        retrieval_client=None)
    await ctrl.handle_message(th.thread_id, "brainstorm this", channel_id=chan)

    import json
    stored = json.loads(store.get_controller_active_skill(th.thread_id))
    assert stored["name"] == "brainstorming"

    # A second turn reads a DIFFERENT skill — it must replace, not accumulate.
    eng2 = _SkillsPayloadRecordingEngine([
        {"type": "tool_call", "thought": "t", "tool": "read_skill",
         "args": {"name": "writing-plans"}},
        {"type": "answer", "thought": "t", "answer": "plan written"},
    ])
    ctrl._reasoning = eng2  # swap the scripted engine for the next turn
    await ctrl.handle_message(th.thread_id, "now write the plan", channel_id=chan)
    stored2 = json.loads(store.get_controller_active_skill(th.thread_id))
    assert stored2["name"] == "writing-plans"
