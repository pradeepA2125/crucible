"""After the main agent hands work to a team or to agents, the per-turn hint says so.

Live 2026-10-07 (gpt-5.6-luna, "design a snake game, use a team"): after create_team,
every call still read "This is your FIRST action and nothing is started yet" (active_entry
held: no todo list, no edit). The model polled team_status/wait_agents, tried
adopt_proposal, then dispatched two fresh agents to rebuild the game the team was building.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.controller_prompts import build_controller_step_payload
from agentd.chat.models import AgentRecord
from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.teams.models import TeamMember, TeamRecord
from agentd.tools.registry import ToolDefinition, ToolOutput
from agentd.tools.sources import AggregatingToolRegistry
from tests.test_background_dispatch import _setup
from tests.test_controller_todo_gate import _RecordingPlanCtx
from tests.test_notice_turns import ANSWER, _settle, _wake

HISTORY = [{"role": "assistant", "content": "{}"},
           {"role": "tool_result", "tool": "", "content": "x"}]


class _Delegation:
    """create_team / dispatch_agents / wait_agents answering like the real tools."""
    name = "delegation"

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(name=n, description=n, parameters={"type": "object"})
                for n in ("create_team", "dispatch_agents", "wait_agents", "team_status")]

    def owns(self, tool: str) -> bool:
        return tool in {"create_team", "dispatch_agents", "wait_agents", "team_status"}

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool == "create_team" and args.get("fail"):
            return ToolOutput(output="Error: bad team", is_error=True)
        return ToolOutput(output=f"{tool} ok")


def _call(tool: str, **args: object) -> dict[str, object]:
    return {"type": "tool_call", "thought": "t", "tool": tool, "args": args}


async def _contexts(*steps: dict[str, object]) -> list[dict]:
    rec = _RecordingPlanCtx([*steps, {"type": "answer", "thought": "t", "answer": "done"}])
    ledger = TodoLedger()
    loop = ControllerLoop(
        rec, AggregatingToolRegistry([TodoToolSource(ledger), _Delegation()]),
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(), todo_ledger=ledger)
    await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    return rec.plan_contexts


@pytest.mark.asyncio
async def test_create_team_ends_the_entry_hint_and_names_the_team() -> None:
    ctx = await _contexts(_call("create_team", name="snake-game", members=[]))
    assert ctx[0].get("active_entry") is True
    assert ctx[1].get("active_entry") is False
    assert ctx[1]["delegated"] == {"teams": ["snake-game"], "agents": [], "agents_collected": False}


@pytest.mark.asyncio
async def test_a_failed_create_team_delegates_nothing() -> None:
    ctx = await _contexts(_call("create_team", name="x", fail=True))
    assert ctx[1].get("active_entry") is True and not ctx[1].get("delegated")


@pytest.mark.asyncio
async def test_dispatched_agents_are_running_until_wait_agents_collects_them() -> None:
    ctx = await _contexts(
        _call("dispatch_agents", agents=[{"label": "builder", "agent": "general-purpose"},
                                         {"label": "reviewer", "agent": "explore"}]),
        _call("wait_agents"))
    assert ctx[1]["delegated"] == {"teams": [], "agents": ["builder", "reviewer"],
                                   "agents_collected": False}
    assert ctx[2]["delegated"]["agents_collected"] is True


def _instruction(delegated: dict[str, object]) -> str:
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "active_entry": False, "delegated": delegated},
        history=HISTORY, tool_definitions=[], phase="ACTIVE")
    return str(payload["instruction"])


def test_after_a_team_the_hint_says_answer_and_do_not_poll() -> None:
    text = _instruction({"teams": ["snake-game"], "agents": [], "agents_collected": False})
    assert "FIRST action" not in text and "nothing is started" not in text
    assert "snake-game" in text and "Answer the user now" in text
    assert "team_status" in text and "wait_agents" in text   # named as what not to call


def test_after_dispatch_the_hint_offers_wait_or_answer() -> None:
    text = _instruction({"teams": [], "agents": ["builder", "reviewer"], "agents_collected": False})
    assert "builder, reviewer" in text
    assert "wait_agents" in text and "answer" in text.lower()
    assert "Do not redo their work" in text


def test_after_reports_are_collected_the_hint_says_check_them() -> None:
    text = _instruction({"teams": [], "agents": ["builder"], "agents_collected": True})
    assert "reports of builder are in" in text


def test_without_delegation_the_entry_hint_is_unchanged() -> None:
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "active_entry": True},
        history=HISTORY, tool_definitions=[], phase="ACTIVE")
    assert "FIRST action" in str(payload["instruction"])


def test_a_notice_turn_gets_the_notice_hint_not_nothing_is_started() -> None:
    # A team milestone wakes the main agent in a fresh turn (no list, no edit yet). The
    # entry hint told it to decide an approach and start work; it re-checked a team's
    # already-reviewed files instead of reporting (live 2026-10-07, gpt-5.6-terra).
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "active_entry": True, "notice_turn": True},
        history=HISTORY, tool_definitions=[], phase="ACTIVE")
    text = str(payload["instruction"])
    assert "FIRST action" not in text and "nothing is started" not in text
    assert "woke you" in text and "tell the user" in text.lower()
    assert "not yours to redo" in text


@pytest.mark.asyncio
async def test_a_notice_turn_tells_the_loop_it_is_one(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("agentd.chat.controller.NOTICE_BATCH_SEC", 0.01)
    ctrl, store, tid = _setup(Path(tmp_path), monkeypatch, [ANSWER, ANSWER], {})
    seen: list[dict] = []
    original = ctrl._reasoning.create_controller_step

    async def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(dict(kwargs.get("plan_context") or {}))
        return await original(*args, **kwargs)

    monkeypatch.setattr(ctrl._reasoning, "create_controller_step", spy)
    await ctrl.handle_message(tid, "hello", channel_id=f"chat:{tid}")
    store.insert_notice(_wake(tid))
    ctrl._rearm_notices(tid)
    await _settle(ctrl, tid)
    assert [c.get("notice_turn") for c in seen] == [False, True]


# ---------------------------------------------------------------- live work in the thread
# Live 2026-10-07 (gpt-5.6-luna): woken by "team adopted P1", the main agent wrote its own
# todo list and edited files to build the game the team had just planned. Nothing in its
# per-turn hint said the team was still at work — the delegated hint only knows THIS turn.

LIVE = {"teams": [{"name": "snake-game", "phase": "IMPLEMENTING", "goal": "build snake"}],
        "agents": ["scout"]}


@pytest.mark.asyncio
async def test_the_loop_reads_live_work_every_iteration() -> None:
    calls = iter([LIVE, None])
    rec = _RecordingPlanCtx([_call("team_status"), {"type": "answer", "thought": "t",
                                                    "answer": "done"}])
    ledger = TodoLedger()
    loop = ControllerLoop(
        rec, AggregatingToolRegistry([TodoToolSource(ledger), _Delegation()]),
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(), todo_ledger=ledger)
    await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10,
                   live_work=lambda: next(calls))
    assert rec.plan_contexts[0]["live_work"] == LIVE
    assert "live_work" not in rec.plan_contexts[1]


def test_live_work_leads_every_main_hint() -> None:
    for extra in ({"active_entry": True}, {"active_entry": True, "notice_turn": True},
                  {"active_entry": False}):
        payload = build_controller_step_payload(
            {"goal": "g", "workspace_path": "/w", "live_work": LIVE, **extra},
            history=HISTORY, tool_definitions=[], phase="ACTIVE")
        text = str(payload["instruction"])
        assert "Team snake-game (implementing) is working on: build snake" in text
        assert "Agents scout are running" in text
        assert "not yours" in text and "post_board" in text
        assert "only if the user's latest message asks" in text
        assert "wait_agents is for agents you dispatched" in text
        situation = [i for i in (text.find(k) for k in (
            "FIRST action", "Notices woke", "reflect on your last edit")) if i >= 0]
        assert situation and text.index("snake-game") < min(situation)   # live work leads


def test_a_team_started_this_turn_is_not_repeated_as_live_work() -> None:
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "active_entry": False, "live_work": LIVE,
         "delegated": {"teams": ["snake-game"], "agents": [], "agents_collected": False}},
        history=HISTORY, tool_definitions=[], phase="ACTIVE")
    assert str(payload["instruction"]).count("snake-game") == 1


@pytest.mark.asyncio
async def test_a_later_main_turn_is_told_about_the_live_team(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")
    ctrl, store, tid = _setup(Path(tmp_path), monkeypatch, [ANSWER], {})
    now = datetime.now(UTC)
    store.teams.create_team(TeamRecord(
        team_id="team-1", thread_id=tid, name="snake-game", goal="build snake", max_rounds=3,
        round_started_at=now, approval_gate=False, budget=60, created_turn_id="t0",
        checkpoint_seq=0, created_at=now))
    store.insert_agent(AgentRecord(agent_id="agent-b", thread_id=tid, turn_id="t0", depth=1,
                                   name="general-purpose", label="builder", prompt="p",
                                   status="running", team_id="team-1"))
    store.teams.add_member(TeamMember(team_id="team-1", agent_id="agent-b", label="builder"))
    seen: list[dict] = []
    original = ctrl._reasoning.create_controller_step

    async def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(dict(kwargs.get("plan_context") or {}))
        return await original(*args, **kwargs)

    monkeypatch.setattr(ctrl._reasoning, "create_controller_step", spy)
    await ctrl.handle_message(tid, "how is it going?", channel_id=f"chat:{tid}")
    assert seen[0]["live_work"]["teams"] == [
        {"name": "snake-game", "phase": "DELIBERATING", "goal": "build snake"}]


# ---------------------------------------------------------------- notice turns end with answer
# Live 2026-10-07 (gpt-5.6-luna): woken by "team adopted P1", the main agent told the user the
# status with `progress` (which does not end a turn), was redirected with "a progress note
# does NOT count as doing the work — take the actual next action", and then polled
# team_status and nudged the busy team with post_board: 9 calls instead of one answer.


def _notice_instruction() -> str:
    payload = build_controller_step_payload(
        {"goal": "g", "workspace_path": "/w", "active_entry": True, "notice_turn": True},
        history=HISTORY, tool_definitions=[], phase="ACTIVE")
    return str(payload["instruction"])


def test_the_notice_hint_names_the_action_that_ends_the_turn() -> None:
    text = _notice_instruction()
    assert "type='answer'" in text and "ends this turn" in text
    assert "progress does not end" in text


def test_the_notice_hint_says_the_notice_already_has_the_team_state() -> None:
    text = _notice_instruction()
    assert "do not call team_status" in text
    assert "only when the notice asks" in text and "post_board" in text


def test_a_repeated_progress_in_a_notice_turn_says_end_the_turn() -> None:
    from agentd.chat import controller_loop as cl
    repeat = cl._progress_repeat_correction({"note": "x"}, "progress", True, notice_turn=True)
    dedup = cl._progress_dedup_correction({"note": "x"}, "progress", {"x"}, notice_turn=True)
    for text in (repeat or "", dedup or ""):
        assert "type='answer'" in text and "ends this turn" in text
        assert "next action" not in text and "next real action" not in text


def test_outside_notice_turns_the_progress_redirects_are_unchanged() -> None:
    from agentd.chat import controller_loop as cl
    repeat = cl._progress_repeat_correction({"note": "x"}, "progress", True)
    assert repeat is not None and "does NOT count as doing the work" in repeat


# ---------------------------------------------------------------- a notice turn that acted
# Live 2026-10-07 (gpt-5.6-luna, run 3): woken by "team is stuck", the main agent restarted
# the builder with post_board — right — then did it five more times: the notice still said
# "stuck", the result showed no change and the hint still said "act when the notice asks".


class _Board:
    name = "board"

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="post_board", description="p", parameters={"type": "object"})]

    def owns(self, tool: str) -> bool:
        return tool == "post_board"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        self.posts = getattr(self, "posts", 0) + 1
        return ToolOutput(output='{"seq": 10}')


async def _notice_run(*steps: dict[str, object]) -> tuple[list[dict], _Board, list]:
    rec = _RecordingPlanCtx([*steps, {"type": "answer", "thought": "t", "answer": "done"}])
    board = _Board()
    ledger = TodoLedger()
    loop = ControllerLoop(
        rec, AggregatingToolRegistry([TodoToolSource(ledger), board]),
        EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(), todo_ledger=ledger)
    out = await loop.run({"goal": "g", "workspace_path": "/tmp", "notice_turn": True},
                         max_iters=10)
    return rec.plan_contexts, board, out.history or []


@pytest.mark.asyncio
async def test_after_acting_on_a_notice_the_hint_says_answer() -> None:
    ctx, _board, _hist = await _notice_run(
        _call("post_board", team="snake-game", mentions=["builder"], text="@builder go on"))
    assert not ctx[0].get("notice_acted")
    assert ctx[1]["notice_acted"] == ["post_board to @builder (snake-game)"]
    payload = build_controller_step_payload(
        {**ctx[1], "active_entry": True}, history=HISTORY, tool_definitions=[], phase="ACTIVE")
    text = str(payload["instruction"])
    assert "You acted on the notice" in text and "@builder" in text
    assert "type='answer'" in text and "Do not post again" in text


@pytest.mark.asyncio
async def test_a_repeat_post_to_the_same_members_in_a_notice_turn_is_refused() -> None:
    _ctx, board, hist = await _notice_run(
        _call("post_board", team="snake-game", mentions=["builder"], text="@builder go on"),
        _call("post_board", team="snake-game", mentions=["builder"], text="@builder close out"))
    assert board.posts == 1
    assert any("already posted to @builder" in str(m.get("content")) for m in hist)


@pytest.mark.asyncio
async def test_outside_notice_turns_posts_are_not_limited() -> None:
    rec = _RecordingPlanCtx([
        _call("post_board", team="t", mentions=["builder"], text="a"),
        _call("post_board", team="t", mentions=["builder"], text="b"),
        {"type": "answer", "thought": "t", "answer": "done"}])
    board = _Board()
    ledger = TodoLedger()
    loop = ControllerLoop(rec, AggregatingToolRegistry([TodoToolSource(ledger), board]),
                          EventBroadcaster(), channel_id="c", phase_sm=ControllerPhaseSM(),
                          todo_ledger=ledger)
    await loop.run({"goal": "g", "workspace_path": "/tmp"}, max_iters=10)
    assert board.posts == 2 and not rec.plan_contexts[1].get("notice_acted")
