"""A team implements its adopted plan inside the controller (spec v2 §8.5, §8.6, §8.2)."""
from __future__ import annotations

import asyncio

import pytest

from agentd.chat.models import GateNotFoundError
from agentd.providers.usage import METER, Usage
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.tools import TeamMemberSpec
from agentd.teams.validation import TeamInputError, TeamPlanConflict, TeamPlanInvalid
from tests.test_team_controller import REPORT, _make, _request, _settle

AGREE = {"type": "report", "thought": "ok", "summary": "P1 holds", "status": "completed",
         "stances": [{"proposal_id": "P1", "stance": "agree", "note": "checked"}]}
DONE = {"type": "report", "thought": "done", "summary": "part done", "status": "completed"}
WAITING = {"type": "report", "thought": "w", "summary": "waiting", "status": "awaiting_peer"}
PARTIAL = {"type": "report", "thought": "f", "summary": "out of budget", "status": "partial"}
LOOK = {"type": "tool_call", "thought": "look", "tool": "list_directory", "args": {}}
PARTS = [{"member": "alice", "part": "api", "files": ["a.py"]},
         {"member": "bob", "part": "tests", "files": ["b.py"]}]


def _edit(path: str, content: str) -> dict[str, object]:
    return {"type": "edit", "thought": f"write {path}", "patch_ops": [
        {"op": "create_file", "file": path, "content": content, "reason": "part"}]}


def _member(label: str) -> TeamMemberSpec:
    return TeamMemberSpec(label, BUILTIN_AGENTS["general-purpose"])


def _quiet(ctrl, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)


@pytest.mark.asyncio
async def test_adoption_runs_implementation_and_ends_implemented(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("a.py", "A = 1\n"), DONE],
        "bob": [AGREE, _edit("b.py", "B = 1\n"), DONE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(
        tid, "turn1", _request(kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason) == ("DONE", "implemented")
    ws = tmp_path / "ws"
    assert (ws / "a.py").read_text() == "A = 1\n" and (ws / "b.py").read_text() == "B = 1\n"
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["adopted", "done"]
    alice_inputs = [h[-1]["content"] for label, h, _, _ in engine.seen
                    if label == "alice" and h and h[-1].get("role") == "user"]
    assert any("Your assignment: api. Files you own: a.py." in str(c) for c in alice_inputs)


@pytest.mark.asyncio
async def test_member_cannot_edit_anothers_file(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("b.py", "x\n"), _edit("b.py", "y\n"), _edit("a.py", "A\n"), DONE],
        "bob": [AGREE, _edit("b.py", "B\n"), DONE]})
    _quiet(ctrl, monkeypatch)
    await ctrl._create_team(tid, "turn1", _request(kickoff_assignments=PARTS))
    await _settle(ctrl)
    assert (tmp_path / "ws" / "b.py").read_text() == "B\n"
    alice_history = [str(m.get("content")) for label, h, _, _ in engine.seen
                     if label == "alice" for m in h]
    assert any("b.py is owned by bob — team_message bob instead" in c for c in alice_history)
    assert any("You were already told this file is owned by bob" in c for c in alice_history)


@pytest.mark.asyncio
async def test_unassigned_files_are_refused_with_an_approval_gate(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("other.py", "x\n"), _edit("a.py", "A\n"), DONE],
        "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS[:1])))["team_id"])
    await _settle(ctrl)
    gate = store.get_thread(tid).pending_controller_gates[0]
    ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)
    await _settle(ctrl)
    assert not (tmp_path / "ws" / "other.py").exists()
    history = [str(m.get("content")) for label, h, _, _ in engine.seen
               if label == "alice" for m in h]
    assert any("other.py is not in the approved plan — tell the main agent" in c
               for c in history)
    assert store.teams.get_team(team_id).phase == "DONE"


@pytest.mark.asyncio
async def test_team_gate_does_not_block_notice_turns(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    gates = store.get_thread(tid).pending_controller_gates
    assert [g.kind for g in gates] == ["team_plan"]
    assert gates[0].team.id == team_id and gates[0].agent is None
    assert gates[0].payload["proposal_id"] == "P1"
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["approval_needed"]
    assert ctrl._notice_blocked(tid) is False


@pytest.mark.asyncio
async def test_plan_decisions(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    gate = store.get_thread(tid).pending_controller_gates[0]
    with pytest.raises(GateNotFoundError):
        ctrl.decide_team_plan(tid, "nope", "approve", None)
    with pytest.raises(TeamPlanInvalid):
        ctrl.decide_team_plan(tid, gate.gate_id, "feedback", "x" * 8001)
    out = ctrl.decide_team_plan(tid, gate.gate_id, "reject", None)
    assert out == {"team_id": team_id, "phase": "DISBANDED"}
    assert store.get_thread(tid).pending_controller_gates == []
    with pytest.raises(GateNotFoundError):
        ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)


@pytest.mark.asyncio
async def test_a_stale_card_conflicts(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    gate = store.get_thread(tid).pending_controller_gates[0]
    store.teams.update_team(team_id, adopted_proposal_id="P9")
    with pytest.raises(TeamPlanConflict):
        ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)


@pytest.mark.asyncio
async def test_disband_removes_the_plan_card(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    await ctrl.disband_team(tid, team_id)
    assert store.get_thread(tid).pending_controller_gates == []


@pytest.mark.asyncio
async def test_stuck_milestone_after_mutual_wait(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE, WAITING],
                                                         "bob": [AGREE, WAITING]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(
        tid, "turn1", _request(kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    assert "stuck" in [n.kind for n in store.unclaimed_notices(tid)]
    assert store.teams.get_team(team_id).stuck_count == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_pauses_and_resume_continues(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, LOOK, _edit("a.py", "A\n"), DONE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def forced_reports_partial(*args, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("allowed_types") == ["report"]:
            return PARTIAL
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", forced_reports_partial)
    # Over budget only once implementing: every activation's end also checks the budget,
    # so a constant would pause the team in round 1 already.
    monkeypatch.setattr(ctrl, "team_requests", lambda team_id: (
        60 if store.teams.get_team(team_id).phase == "IMPLEMENTING" else 0))
    team_id = str((await ctrl._create_team(
        tid, "turn1", _request(budget=50, kickoff_assignments=PARTS[:1])))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.paused_reason) == ("PAUSED", "budget")
    assert not store.teams.member(team_id, "alice").assignment_done
    assert "member_blocked" not in [n.kind for n in store.unclaimed_notices(tid)]
    with pytest.raises(TeamInputError, match="runs only in a turn the user started"):
        ctrl._resume_team(tid, team_id, None)
    monkeypatch.setattr(ctrl, "turn_kind", lambda _thread_id: "user")
    out = ctrl._resume_team(tid, team_id, None)
    assert out["budget"] == 50 + 160
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason) == ("DONE", "implemented")
    assert (tmp_path / "ws" / "a.py").read_text() == "A\n"


@pytest.mark.asyncio
async def test_team_usage_is_the_sum_of_its_activations(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    monkeypatch.setattr(METER, "take", lambda _owner: Usage(requests=1, prompt_tokens=5))
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.requests, team.prompt_tokens) == (2, 10)


@pytest.mark.asyncio
async def test_kickoff_assignments_are_validated(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    with pytest.raises(TeamInputError, match="protected"):
        await ctrl._create_team(tid, "turn1", _request(kickoff_assignments=[
            {"member": "alice", "part": "cfg", "files": [".crucible/mcp.json"]}]))
    assert store.teams.list_teams(tid) == []


@pytest.mark.asyncio
async def test_a_named_wait_is_answered_when_the_peer_finishes(tmp_path, monkeypatch) -> None:
    wait_api = {**WAITING, "waiting_on": ["api"]}
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "api": [AGREE, _edit("a.py", "A\n"), DONE],
        "tests": [AGREE, wait_api, _edit("b.py", "B\n"), DONE]})
    _quiet(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def api_is_slow(*args, **kwargs):  # type: ignore[no-untyped-def]
        if getattr(kwargs.get("render_ctx"), "agent_label", "") == "api" and \
                "edit" in (kwargs.get("allowed_types") or []):
            await asyncio.sleep(0.2)          # tests reaches its wait while api still works
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", api_is_slow)
    parts = [{"member": "api", "part": "api", "files": ["a.py"]},
             {"member": "tests", "part": "tests", "files": ["b.py"]}]
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        members=[_member("api"), _member("tests")], kickoff_assignments=parts)))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason, team.stuck_count) == ("DONE", "implemented", 0)
    assert "stuck" not in [n.kind for n in store.unclaimed_notices(tid)]
    tests_inputs = [h[-1]["content"] for label, h, _, _ in engine.seen
                    if label == "tests" and h and h[-1].get("role") == "user"]
    assert any("api reported completed — you were waiting on it" in str(c)
               for c in tests_inputs)
