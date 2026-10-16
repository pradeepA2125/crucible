"""Regressions discovered during independent execution-mode review."""

from __future__ import annotations

import asyncio

import pytest

from agentd.teams.controller_ops import TeamWorkflowOps
from tests.test_team_controller import REPORT, _make, _settle
from tests.test_team_implementation_controller import DONE, PARTS, _edit
from tests.test_team_workflow_controller import AGREE, final, quiet, request


@pytest.mark.asyncio
async def test_rewind_cancellation_restores_exchange_as_retryable(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    monkeypatch.setattr(store, "current_checkpoint_seq", lambda _: 5)
    entered = asyncio.Event()
    hold = asyncio.Event()

    async def consulting(*_args, **_kwargs):
        entered.set()
        await hold.wait()
        return {**REPORT, "status": "partial"}

    monkeypatch.setattr(engine, "create_controller_step", consulting)
    ops = TeamWorkflowOps(ctrl)
    sent = await ops.message_lead(
        tid, team_id, {"expected_run_id": 1, "text": "Explain this recommendation"}
    )
    await asyncio.wait_for(entered.wait(), 2)
    seq = sent["request_seq"]
    assert store.teams.get_team(team_id).lead_exchange_status == "running"
    monkeypatch.setattr(store, "current_checkpoint_seq", lambda _: 6)
    await ops.cancel_lead_request(tid, team_id, {"expected_run_id": 1, "request_seq": seq})
    assert not ctrl.live_team_names(tid)
    ctrl.forget_rewound_agents(tid, [], 6)
    restored = store.teams.get_team(team_id)
    assert restored.pending_lead_request_seq == seq
    assert restored.lead_exchange_status == "blocked"
    assert not any(p.payload.get("cancelled") == seq for p in store.teams.posts(team_id))

    async def blocked(*_args, **_kwargs):
        return {**REPORT, "status": "partial"}

    monkeypatch.setattr(engine, "create_controller_step", blocked)
    count = store.get_agent(store.teams.member(team_id, "alice").agent_id).activation_count
    await ops.message_lead(tid, team_id, {"expected_run_id": 1, "retry_request_seq": seq})
    await _settle(ctrl)
    assert store.get_agent(store.teams.member(team_id, "alice").agent_id).activation_count > count


@pytest.mark.asyncio
async def test_rewind_later_reply_restores_unanswered_request(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    monkeypatch.setattr(store, "current_checkpoint_seq", lambda _: 5)

    async def blocked(*_args, **_kwargs):
        return {**REPORT, "status": "partial"}

    monkeypatch.setattr(engine, "create_controller_step", blocked)
    ops = TeamWorkflowOps(ctrl)
    sent = await ops.message_lead(
        tid, team_id, {"expected_run_id": 1, "text": "Why this recommendation?"}
    )
    await _settle(ctrl)
    seq = sent["request_seq"]
    assert store.teams.get_team(team_id).lead_exchange_status == "blocked"
    monkeypatch.setattr(store, "current_checkpoint_seq", lambda _: 6)
    reply = ops.reply_main(team_id, "alice", 1, "Because it preserves compatibility.", seq)
    assert store.teams.get_team(team_id).pending_lead_request_seq is None
    ctrl.forget_rewound_agents(tid, [], 6)
    restored = store.teams.get_team(team_id)
    assert restored.pending_lead_request_seq == seq
    assert restored.lead_exchange_status == "blocked"
    assert store.teams.get_post(team_id, reply["seq"]) is None
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["recommendation_ready"]
    channel = f"chat:{tid}:team:{team_id}"
    replay = ctrl._broadcaster.subscribe(channel)
    while not replay.empty():
        event = replay.get_nowait()
        assert not (
            event["type"] == "team_post" and event["payload"]["post"]["seq"] == reply["seq"]
        )
    ctrl._broadcaster.unsubscribe(channel, replay)


@pytest.mark.asyncio
async def test_rewind_approval_restores_recoverable_gate_debt(tmp_path, monkeypatch):
    ctrl, store, tid, _engine = _make(
        tmp_path,
        monkeypatch,
        {
            "alice": [AGREE, final(), _edit("a.py", "A\n"), DONE],
            "bob": [AGREE, _edit("b.py", "B\n"), DONE],
        },
    )
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
    monkeypatch.setattr(store, "current_checkpoint_seq", lambda _: 6)
    ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)
    await _settle(ctrl)
    assert store.teams.get_team(team_id).phase == "DONE"
    # Real rewind restores these files too. Keep the selected plan's fingerprints valid.
    (tmp_path / "ws" / "a.py").unlink()
    (tmp_path / "ws" / "b.py").unlink()
    ctrl.forget_rewound_agents(tid, [], 6)
    restored = store.teams.get_team(team_id)
    assert restored.phase == "FAILED"
    assert restored.resume_phase == "AWAITING_APPROVAL"
    assert not store.get_thread(tid).pending_controller_gates
    assert team_id not in ctrl._coordinators
    before = {
        m.label: store.get_agent(m.agent_id).activation_count for m in store.teams.members(team_id)
    }
    ctrl._revive_team(tid, restored, "Recover the pending approval", [])
    await _settle(ctrl)
    recovered = store.teams.get_team(team_id)
    assert recovered.phase == "AWAITING_APPROVAL"
    assert len(store.get_thread(tid).pending_controller_gates) == 1
    assert all(
        store.get_agent(m.agent_id).activation_count == before[m.label]
        for m in store.teams.members(team_id)
    )
    await ctrl.disband_team(tid, team_id)


@pytest.mark.asyncio
async def test_failed_team_with_pending_exchange_reuses_its_admission_slot(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(
        tmp_path, monkeypatch, {"alice": [AGREE, final()], "bob": [AGREE]}
    )
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)

    async def blocked(*_args, **_kwargs):
        return {**REPORT, "status": "partial"}

    monkeypatch.setattr(engine, "create_controller_step", blocked)
    await TeamWorkflowOps(ctrl).message_lead(
        tid, team_id, {"expected_run_id": 1, "text": "Explain?"}
    )
    await _settle(ctrl)
    store.teams.update_team(team_id, phase="FAILED", resume_phase="DELIBERATING")
    await ctrl._create_team(tid, "u2", request(name="other"))
    await _settle(ctrl)
    assert store.teams.count_live_teams(tid) == 2
    revived = ctrl._revive_team(tid, store.teams.get_team(team_id), "Recover this run", [])
    assert revived["revived"] and revived["phase"] == "DELIBERATING"
    await _settle(ctrl)
    assert store.teams.count_live_teams(tid) == 2
    await ctrl.stop_all_agents(tid)
