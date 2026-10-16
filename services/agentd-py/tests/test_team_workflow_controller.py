"""Mode boundaries through real controller, SQLite, patch and notification paths."""

from __future__ import annotations

import asyncio
import json

import pytest

from agentd.domain.models import ShellPolicy
from agentd.teams.controller_ops import TeamWorkflowOps
from agentd.teams.validation import TeamInputError
from tests.test_team_controller import REPORT, _make, _request, _settle
from tests.test_team_implementation_controller import AGREE, DONE, PARTS, _edit


def final(text="The reviewed plan: implement A and verify it.", outcome="actionable"):
    return {
        "type": "report",
        "thought": "synthesized",
        "status": "completed",
        "summary": text,
        "final_result": {
            "summary": "Reviewed recommendation",
            "outcome": outcome,
            "basis_proposal_id": "P1",
        },
    }


def request(mode="deliberate", **over):
    return _request(
        mode=mode,
        lead="alice",
        deliverable_kind="plan",
        deliverable_description="Deliver a reviewed plan",
        **over,
    )


def quiet(ctrl, monkeypatch):
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)


@pytest.mark.asyncio
async def test_deliberate_assigned_plan_delivers_lead_result_without_edits(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    created = await ctrl._create_team(tid, "u1", request(kickoff_assignments=PARTS))
    await _settle(ctrl)
    team_id = created["team_id"]
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason) == ("DONE", "deliberation_complete")
    assert store.teams.get_post(team_id, team.result_seq).text == final()["summary"]
    assert not (tmp_path / "ws" / "a.py").exists()
    assert all(m.assignment is None for m in store.teams.members(team_id))
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["recommendation_ready"]
    assert final()["summary"] in store.unclaimed_notices(tid)[0].payload["body"]
    assert not store.get_thread(tid).pending_controller_gates
    assert store.get_agent(store.teams.member(team_id, "alice").agent_id).activation_count == 2
    assert store.get_agent(store.teams.member(team_id, "bob").agent_id).activation_count == 1
    assert all("run_command" not in names for _, _, names, _ in engine.seen)


@pytest.mark.asyncio
async def test_combined_finalizes_then_implements_and_reviews(tmp_path, monkeypatch):
    ctrl, store, tid, _ = _make(
        tmp_path,
        monkeypatch,
        {
            "alice": [AGREE, final(), _edit("a.py", "A = 1\n"), DONE],
            "bob": [AGREE, _edit("b.py", "B = 1\n"), DONE],
        },
    )
    quiet(ctrl, monkeypatch)
    created = await ctrl._create_team(
        tid, "u1", request("deliberate_and_implement", kickoff_assignments=PARTS)
    )
    await _settle(ctrl)
    team = store.teams.get_team(created["team_id"])
    assert (team.phase, team.end_reason) == ("DONE", "reviewed")
    assert (tmp_path / "ws" / "a.py").read_text() == "A = 1\n"
    assert (tmp_path / "ws" / "b.py").read_text() == "B = 1\n"
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["recommendation_ready", "done"]


@pytest.mark.asyncio
async def test_direct_implement_skips_debate_and_finalization(tmp_path, monkeypatch):
    ctrl, store, tid, _ = _make(
        tmp_path,
        monkeypatch,
        {"alice": [_edit("a.py", "A\n"), DONE], "bob": [_edit("b.py", "B\n"), DONE]},
    )
    quiet(ctrl, monkeypatch)
    created = await ctrl._create_team(tid, "u1", request("implement", kickoff_assignments=PARTS))
    await _settle(ctrl)
    team = store.teams.get_team(created["team_id"])
    assert (team.phase, team.end_reason) == ("DONE", "reviewed")
    assert not [a for a in store.teams.activity(team.team_id) if a.kind == "round_started"]
    assert not [p for p in store.teams.posts(team.team_id) if p.kind == "result"]


@pytest.mark.asyncio
async def test_no_change_completes_combined_without_execution(tmp_path, monkeypatch):
    ctrl, store, tid, _ = _make(
        tmp_path,
        monkeypatch,
        {"alice": [AGREE, final("Existing design is sufficient.", "no_change")], "bob": [AGREE]},
    )
    quiet(ctrl, monkeypatch)
    created = await ctrl._create_team(tid, "u1", request("deliberate_and_implement"))
    await _settle(ctrl)
    team = store.teams.get_team(created["team_id"])
    assert (team.phase, team.end_reason) == ("DONE", "deliberation_complete")
    assert not store.get_thread(tid).pending_controller_gates


@pytest.mark.asyncio
async def test_done_lead_consultation_preserves_result_and_lifecycle_reports(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    original = ctrl._teams.report(team_id)
    original_step = engine.create_controller_step

    async def reply(*args, **kwargs):
        if kwargs.get("plan_context", args[0] if args else {}).get("reply_test_done"):
            return REPORT
        # Use a single tool call, then a terminal report, for the lead-only consultation.
        if not getattr(reply, "sent", False):
            reply.sent = True
            seq = store.teams.get_team(team_id).pending_lead_request_seq
            return {
                "type": "tool_calls",
                "thought": "answer main",
                "calls": [
                    {
                        "id": "reply1",
                        "tool": "team_reply_main",
                        "args": {"text": "The plan avoids a migration.", "request_seq": seq},
                    }
                ],
            }
        return REPORT

    monkeypatch.setattr(engine, "create_controller_step", reply)
    before = {
        m.label: store.get_agent(m.agent_id).activation_count for m in store.teams.members(team_id)
    }
    result = await TeamWorkflowOps(ctrl).message_lead(
        tid, team_id, {"expected_run_id": 1, "text": "Why this approach?"}
    )
    await _settle(ctrl)
    assert result["request_seq"]
    team = store.teams.get_team(team_id)
    assert team.phase == "DONE" and team.run_id == 1 and not team.pending_lead_request_seq
    assert ctrl._teams.report(team_id)["result"] == original["result"]
    assert ctrl._teams.report(team_id)["members"] == original["members"]
    for m in store.teams.members(team_id):
        assert store.get_agent(m.agent_id).activation_count == before[m.label] + (
            m.label == "alice"
        )
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["recommendation_ready", "lead_reply"]
    monkeypatch.setattr(engine, "create_controller_step", original_step)


@pytest.mark.asyncio
async def test_explicit_extension_keeps_roster_and_rejects_automatic_notice(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(
        tmp_path,
        monkeypatch,
        {
            "alice": [AGREE, final(), _edit("a.py", "A\n"), DONE],
            "bob": [AGREE, _edit("b.py", "B\n"), DONE],
        },
    )
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request(kickoff_assignments=PARTS)))["team_id"]
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    ids = [m.agent_id for m in store.teams.members(team_id)]
    ops = TeamWorkflowOps(ctrl)
    args = {
        "expected_run_id": 1,
        "result_seq": team.result_seq,
        "assignments": [{**part, "files": ["./" + f for f in part["files"]]} for part in PARTS],
    }
    with pytest.raises(TeamInputError, match="user-started"):
        await ops.start_implementation(tid, team_id, args)
    monkeypatch.setattr(ctrl, "turn_kind", lambda _: "user")
    out = await ops.start_implementation(tid, team_id, args)
    await _settle(ctrl)
    assert out["run_id"] == 2
    assert [m.agent_id for m in store.teams.members(team_id)] == ids
    assert store.teams.get_team(team_id).end_reason == "reviewed"
    assert (tmp_path / "ws" / "a.py").read_text() == "A\n"
    repeat = await ops.start_implementation(tid, team_id, args)
    assert repeat["already_started"]


@pytest.mark.asyncio
async def test_deliberate_filters_shell_even_with_allow_all(tmp_path, monkeypatch):
    write = {
        "type": "tool_calls",
        "thought": "try writing",
        "calls": [{"id": "bad", "tool": "run_command", "args": {"command": "touch forbidden.txt"}}],
    }
    ctrl, store, tid, _ = _make(
        tmp_path, monkeypatch, {"alice": [write, AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    ctrl._shell_policy = ShellPolicy.ALLOW_ALL
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    assert not (tmp_path / "ws" / "forbidden.txt").exists()
    assert store.teams.get_team(team_id).phase == "DONE"


@pytest.mark.asyncio
async def test_blocked_lead_request_retries_same_request_and_can_be_cancelled(
    tmp_path, monkeypatch
):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    ops = TeamWorkflowOps(ctrl)
    saved_result = store.teams.get_team(team_id).result_seq

    async def partial(*_args, **_kwargs):
        return {**REPORT, "status": "partial"}

    monkeypatch.setattr(engine, "create_controller_step", partial)
    sent = await ops.message_lead(tid, team_id, {"expected_run_id": 1, "text": "Explain?"})
    await _settle(ctrl)
    seq = sent["request_seq"]
    assert store.teams.get_team(team_id).lead_exchange_status == "blocked"
    assert ctrl.live_team_names(tid) == ["auth"]
    with pytest.raises(TeamInputError, match="pending lead request"):
        ctrl._reopen_team(tid, store.teams.get_team(team_id), "Revise", [])
    again = await ops.message_lead(tid, team_id, {"expected_run_id": 1, "text": "New?"})
    assert again["already_pending"] and again["request_seq"] == seq
    await ops.message_lead(
        tid, team_id, {"expected_run_id": 1, "text": "Retry", "retry_request_seq": seq}
    )
    await _settle(ctrl)
    assert (
        len(
            [p for p in store.teams.posts(team_id) if p.author == "main" and p.recipient == "alice"]
        )
        == 1
    )
    await ops.cancel_lead_request(tid, team_id, {"expected_run_id": 1, "request_seq": seq})
    assert store.teams.get_team(team_id).phase == "DONE"
    assert store.teams.get_team(team_id).result_seq == saved_result
    assert not ctrl.live_team_names(tid)
    with pytest.raises(TeamInputError, match="pending request"):
        ops.reply_main(team_id, "alice", 1, "Late", seq)
    await ctrl.disband_team(tid, team_id)
    assert store.teams.get_team(team_id).phase == "DISBANDED"
    with pytest.raises(TeamInputError, match="closed"):
        await ops.message_lead(tid, team_id, {"expected_run_id": 1, "text": "Late question"})


@pytest.mark.asyncio
async def test_accept_result_recovers_once_after_notice_write_failure(tmp_path, monkeypatch):
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]})
    quiet(ctrl, monkeypatch)
    original = store.insert_notice

    def fail_notice(record):
        if record.kind == "recommendation_ready":
            raise RuntimeError("simulated crash after result commit")
        return original(record)

    monkeypatch.setattr(store, "insert_notice", fail_notice)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert team.phase == "DONE" and team.result_notice_pending
    assert not store.unclaimed_notices(tid)
    monkeypatch.setattr(store, "insert_notice", original)
    ctrl.reap_subagents()
    ctrl.reap_subagents()
    assert store.teams.get_team(team_id).phase == "DONE"
    assert not store.teams.get_team(team_id).result_notice_pending
    notices = store.unclaimed_notices(tid)
    assert len(notices) == 1 and notices[0].kind == "recommendation_ready"
    assert final()["summary"] in notices[0].payload["body"]


@pytest.mark.asyncio
async def test_stale_result_and_gate_refuse_before_authorizing_execution(tmp_path, monkeypatch):
    from agentd.teams.validation import TeamPlanConflict

    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]})
    quiet(ctrl, monkeypatch)
    team_id = (
        await ctrl._create_team(
            tid,
            "u1",
            request("deliberate_and_implement", kickoff_assignments=PARTS, approval_gate=True),
        )
    )["team_id"]
    await _settle(ctrl)
    gate = store.get_thread(tid).pending_controller_gates[0]
    assert gate.payload["run_id"] == 1
    (tmp_path / "ws" / "a.py").write_text("External change\n")
    with pytest.raises(TeamPlanConflict, match="stale_plan"):
        ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)
    assert store.get_thread(tid).pending_controller_gates[0].gate_id == gate.gate_id
    assert store.teams.get_team(team_id).phase == "AWAITING_APPROVAL"
    await ctrl.disband_team(tid, team_id)


@pytest.mark.asyncio
async def test_public_feedback_during_finalization_reopens_debate_without_execution(
    tmp_path, monkeypatch
):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    ready, release = asyncio.Event(), asyncio.Event()
    original_step = engine.create_controller_step

    async def held(*args, **kwargs):
        team = next(iter(store.teams.list_teams(tid)), None)
        if team and team.phase == "FINALIZING":
            ready.set()
            await release.wait()
        return await original_step(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", held)
    team_id = (
        await ctrl._create_team(
            tid, "u1", request("deliberate_and_implement", kickoff_assignments=PARTS)
        )
    )["team_id"]
    await asyncio.wait_for(ready.wait(), 2)
    ctrl._teams.post(team_id, "main", "Reconsider compatibility before execution.")
    # Record the transition at the next round before any further model work.
    phases = []
    deliberating_again = asyncio.Event()
    changed = ctrl.team_phase_changed

    def phase(team):
        phases.append(team.phase)
        if team.phase == "DELIBERATING":
            deliberating_again.set()
        changed(team)

    monkeypatch.setattr(ctrl, "team_phase_changed", phase)
    release.set()
    # End the new round immediately; the assertion concerns the finalization boundary.
    await asyncio.wait_for(deliberating_again.wait(), 2)
    assert "DELIBERATING" in phases and "IMPLEMENTING" not in phases
    assert not (tmp_path / "ws" / "a.py").exists()
    assert not [p for p in store.teams.posts(team_id) if p.kind == "result"]
    await ctrl.disband_team(tid, team_id)
    await _settle(ctrl)


@pytest.mark.asyncio
async def test_queued_command_rechecks_policy_after_mode_change(tmp_path):
    from agentd.tools.registry import ToolDefinition, ToolOutput
    from agentd.tools.sources import AggregatingToolRegistry

    class Source:
        name = "shell"

        def definitions(self):
            return [ToolDefinition(name="run_command", description="run", parameters={})]

        def owns(self, name):
            return name == "run_command"

        async def execute(self, *_args):
            (tmp_path / "unauthorized").write_text("mutation")
            return ToolOutput(output="executed")

    deliberate = False
    registry = AggregatingToolRegistry(
        [Source()], execution_guard=lambda _: "deliberation only" if deliberate else None
    )
    prepared = await registry.prepare("run_command", {})
    deliberate = True
    result = await prepared()
    assert result.is_error and "deliberation" in result.output
    assert not (tmp_path / "unauthorized").exists()


@pytest.mark.asyncio
async def test_busy_consultation_queues_finalization_and_keeps_report_purposes(
    tmp_path, monkeypatch
):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    bob_entered, bob_release = asyncio.Event(), asyncio.Event()
    consulting, reply_release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    alice_reported = asyncio.Event()
    original_step = engine.create_controller_step
    original_reported = ctrl._team_member_reported
    original_start = ctrl._start_member_now
    sent_reply = False

    def reported(record, result, *args):
        original_reported(record, result, *args)
        if record.label == "alice" and result.team_activation_kind == "lifecycle":
            asyncio.get_running_loop().call_soon(alice_reported.set)

    def start(team_id, label, extra=""):
        original_start(team_id, label, extra)
        if store.teams.member(team_id, label).pending_start_json:
            queued.set()

    async def steps(*args, **kwargs):
        nonlocal sent_reply
        label = kwargs["render_ctx"].agent_label
        team = next(iter(store.teams.list_teams(tid)), None)
        if label == "bob" and team.phase == "DELIBERATING":
            bob_entered.set()
            await bob_release.wait()
        member = store.teams.member(team.team_id, label)
        handle = ctrl._subagents.registry.get(member.agent_id)
        if handle.team_activation_kind == "lead_reply":
            consulting.set()
            await reply_release.wait()
            if not sent_reply:
                sent_reply = True
                return {
                    "type": "tool_calls",
                    "thought": "reply",
                    "calls": [
                        {
                            "id": "private",
                            "tool": "team_reply_main",
                            "args": {
                                "text": "Compatibility is covered.",
                                "request_seq": store.teams.get_team(
                                    team.team_id
                                ).pending_lead_request_seq,
                            },
                        }
                    ],
                }
            return REPORT
        return await original_step(*args, **kwargs)

    monkeypatch.setattr(ctrl, "_team_member_reported", reported)
    monkeypatch.setattr(ctrl, "_start_member_now", start)
    monkeypatch.setattr(engine, "create_controller_step", steps)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await asyncio.wait_for(bob_entered.wait(), 2)
    await asyncio.wait_for(alice_reported.wait(), 2)
    cutoff = store.teams.get_team(team_id).round_cutoff_seq
    sent = await TeamWorkflowOps(ctrl).message_lead(
        tid, team_id, {"expected_run_id": 1, "text": "Compatibility?"}
    )
    await asyncio.wait_for(consulting.wait(), 2)
    assert store.teams.get_team(team_id).round_cutoff_seq == cutoff
    assert sent["request_seq"] not in [p.seq for p in ctrl._teams.read(team_id, "bob")]
    bob_release.set()
    await asyncio.wait_for(queued.wait(), 2)
    assert store.teams.get_team(team_id).phase == "FINALIZING"
    assert store.teams.member(team_id, "alice").pending_start_json["phase"] == "FINALIZING"
    reply_release.set()
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert team.phase == "DONE" and team.result_seq and not team.pending_lead_request_seq
    assert store.teams.member(team_id, "alice").pending_start_json is None
    reports = [p for p in store.teams.posts(team_id) if p.kind == "report" and p.author == "alice"]
    assert [p.payload["activation_kind"] for p in reports] == [
        "lifecycle",
        "lead_reply",
        "finalization",
    ]
    assert store.get_agent(store.teams.member(team_id, "bob").agent_id).activation_count == 1


@pytest.mark.asyncio
async def test_rewind_later_exchange_restores_result_and_keeps_original_notice(
    tmp_path, monkeypatch
):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    saved = store.teams.get_team(team_id)
    before = store.teams.posts(team_id)
    # The later user turn has its own checkpoint; the team predates it.
    original_checkpoint = store.current_checkpoint_seq
    monkeypatch.setattr(store, "current_checkpoint_seq", lambda _: 5)

    async def partial(*_args, **_kwargs):
        return {**REPORT, "status": "partial"}

    monkeypatch.setattr(engine, "create_controller_step", partial)
    await TeamWorkflowOps(ctrl).message_lead(
        tid, team_id, {"expected_run_id": 1, "text": "A later question"}
    )
    await _settle(ctrl)
    ctrl.forget_rewound_agents(tid, [], 5)
    after = store.teams.get_team(team_id)
    assert (after.phase, after.run_id, after.result_seq) == ("DONE", 1, saved.result_seq)
    assert not after.pending_lead_request_seq
    assert store.teams.posts(team_id) == before
    assert len(store.unclaimed_notices(tid)) == 1
    assert store.unclaimed_notices(tid)[0].kind == "recommendation_ready"
    monkeypatch.setattr(store, "current_checkpoint_seq", original_checkpoint)


@pytest.mark.asyncio
async def test_tool_creation_result_extension_http_approval_and_streamed_execution(
    tmp_path, monkeypatch
):
    from tests.test_team_routes import _client

    ctrl, store, tid, _ = _make(
        tmp_path,
        monkeypatch,
        {
            "alice": [AGREE, final(), _edit("a.py", "A\n"), DONE],
            "bob": [AGREE, _edit("b.py", "B\n"), DONE],
        },
    )
    quiet(ctrl, monkeypatch)
    source = ctrl._main_team_source(tid, "u1")
    created = await source.execute(
        "create_team",
        {
            "name": "auth",
            "goal": "Prepare a reviewed plan",
            "mode": "deliberate",
            "deliverable": {"kind": "plan", "description": "A reviewed implementation plan"},
            "lead": "alice",
            "members": [
                {"label": "alice", "agent": "general-purpose"},
                {"label": "bob", "agent": "general-purpose"},
            ],
            "kickoff": {"kind": "proposal", "text": "Implement A and B", "assignments": PARTS},
        },
    )
    assert not created.is_error
    team_id = json.loads(created.output)["team_id"]
    channel = f"chat:{tid}:team:{team_id}"
    stream = ctrl._broadcaster.subscribe(channel)
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert team.phase == "DONE" and not (tmp_path / "ws" / "a.py").exists()
    monkeypatch.setattr(ctrl, "turn_kind", lambda _: "user")
    started = await source.execute(
        "start_team_implementation",
        {
            "team": team_id,
            "expected_run_id": 1,
            "result_seq": team.result_seq,
            "approval_gate": True,
        },
    )
    assert not started.is_error and json.loads(started.output)["phase"] == "AWAITING_APPROVAL"
    assert not (tmp_path / "ws" / "a.py").exists()
    async with _client(tmp_path, ctrl) as client:
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
        gate = next(g for g in live["pending_gates"] if g["kind"] == "team_plan")
        assert gate["payload"]["run_id"] == 2
        response = await client.post(
            f"/v1/chat/threads/{tid}/team-plan-decision",
            json={"gate_id": gate["gate_id"], "decision": "approve"},
        )
        assert response.status_code == 200
        await _settle(ctrl)
        detail = (await client.get(f"/v1/chat/threads/{tid}/teams/{team_id}")).json()
    assert detail["phase"] == "DONE" and detail["run_id"] == 2
    assert (tmp_path / "ws" / "a.py").read_text() == "A\n"
    assert (tmp_path / "ws" / "b.py").read_text() == "B\n"
    events = []
    while not stream.empty():
        events.append(stream.get_nowait())
    ctrl._broadcaster.unsubscribe(channel, stream)
    assert (
        len(
            [
                e
                for e in events
                if e["type"] == "team_post" and e["payload"]["post"]["kind"] == "result"
            ]
        )
        == 1
    )
    assert any(
        e["type"] == "team_phase"
        and e["payload"]["phase"] == "IMPLEMENTING"
        and e["payload"]["run_id"] == 2
        for e in events
    )


@pytest.mark.asyncio
async def test_scheduled_start_cannot_cross_run_or_paused_boundary(tmp_path, monkeypatch):
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {})
    quiet(ctrl, monkeypatch)
    # Hold dispatch so the start callback itself can be tested before any model work.
    starts = []
    monkeypatch.setattr(ctrl._subagents, "enqueue", lambda *args: starts.append(args))
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    store.teams.update_team(team_id, phase="PAUSED", run_id=2)
    await asyncio.sleep(0)
    assert starts == []
    ctrl._start_member_now(team_id, "alice")
    assert starts == []
    ctrl._coordinators.pop(team_id).close()


@pytest.mark.asyncio
async def test_narrowing_stops_executable_activations_before_new_deliberate_run(
    tmp_path, monkeypatch
):
    ctrl, store, tid, engine = _make(
        tmp_path,
        monkeypatch,
        {
            "alice": [AGREE, final(), _edit("a.py", "A\n"), DONE],
            "bob": [AGREE, _edit("b.py", "B\n"), DONE],
        },
    )
    quiet(ctrl, monkeypatch)
    implementing = asyncio.Event()
    hold = asyncio.Event()
    original = engine.create_controller_step

    async def steps(*args, **kwargs):
        team = next(iter(store.teams.list_teams(tid)), None)
        if team and team.phase == "IMPLEMENTING":
            implementing.set()
            await hold.wait()
        if team and team.run_id > 1:
            return {**REPORT, "status": "partial"}
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", steps)
    team_id = (
        await ctrl._create_team(
            tid, "u1", request("deliberate_and_implement", kickoff_assignments=PARTS)
        )
    )["team_id"]
    await asyncio.wait_for(implementing.wait(), 2)
    out = await TeamWorkflowOps(ctrl).set_mode(
        tid,
        team_id,
        {"expected_run_id": 1, "mode": "deliberate", "reason": "Only produce a diagnosis."},
    )
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert out["reopened"] and team.run_id == 2 and team.mode == "deliberate"
    assert not (tmp_path / "ws" / "a.py").exists()
    assert not (tmp_path / "ws" / "b.py").exists()
    assert all(m.assignment is None for m in store.teams.members(team_id))
    await ctrl.disband_team(tid, team_id)
