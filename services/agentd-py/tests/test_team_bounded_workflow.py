"""Transport admissions and user-approved upgrades on actual controller state."""
from __future__ import annotations

import asyncio

import pytest

from agentd.providers.rate_limit import ProviderRateLimiter, RateLimitedTransport
from agentd.providers.request_budget import REQUEST_ADMISSION, RequestBudgetExhausted
from agentd.providers.usage import METER, USAGE_OWNER
from agentd.subagents.definitions import BUILTIN_AGENTS, definition_to_json
from agentd.teams.upgrades import request_upgrade
from tests.test_team_controller import _make, _settle
from tests.test_team_implementation_controller import DONE, PARTS, _edit
from tests.test_team_workflow_controller import AGREE, final, quiet, request


@pytest.mark.asyncio
async def test_followup_limit_is_persisted_before_members_run(tmp_path, monkeypatch):
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                              {"alice": [AGREE, final()], "bob": [AGREE]})
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    source = ctrl._main_team_source(tid, "u2")
    before = store.teams.get_team(team_id)
    for invalid in (True, 0, -1, "2"):
        rejected = await source.execute("post_board", {
            "team": team_id, "text": "Revise the recommendation", "request_limit": invalid})
        assert rejected.is_error
        assert store.teams.get_team(team_id).run_id == before.run_id
    accepted = await source.execute("post_board", {
        "team": team_id, "text": "Revise the recommendation", "request_limit": 2})
    assert not accepted.is_error
    team = store.teams.get_team(team_id)
    assert team.run_id == before.run_id + 1
    assert team.budget == before.requests + 2
    await ctrl.disband_team(tid, team_id)


@pytest.mark.asyncio
async def test_upgrade_cannot_override_inherited_read_only_restriction(tmp_path, monkeypatch):
    from agentd.teams.validation import TeamInputError
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                              {"alice": [AGREE, final()], "bob": [AGREE]})
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    member = store.teams.member(team_id, "alice")
    store.set_agent_inherited(member.agent_id, {"read_only": True})
    with pytest.raises(TeamInputError, match="inherited restrictions"):
        await request_upgrade(ctrl, tid, team_id, {
            "expected_run_id": team.run_id, "result_seq": team.result_seq,
            "members": ["alice"], "reason": "Implement the accepted plan"})
    assert not store.get_thread(tid).pending_controller_gates
    assert store.teams.member(team_id, "alice").definition_override is None


@pytest.mark.asyncio
async def test_concurrent_transport_calls_and_usage_rollover_cannot_overshoot():
    owners = [f"bounded-{i}" for i in range(6)]
    persisted = calls = 0

    def guard():
        if persisted + sum(METER.peek(o).requests for o in owners) >= 2:
            raise RequestBudgetExhausted("limit")

    class Inner:
        async def generate_json(self, **_):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return {}
        generate_text = generate_json

    transport = RateLimitedTransport(Inner(), ProviderRateLimiter(0))

    async def call(owner):
        token, owner_token = REQUEST_ADMISSION.set(guard), USAGE_OWNER.set(owner)
        try:
            return await transport.generate_text()
        finally:
            REQUEST_ADMISSION.reset(token)
            USAGE_OWNER.reset(owner_token)

    try:
        results = await asyncio.gather(*(call(o) for o in owners), return_exceptions=True)
        assert calls == 2
        assert sum(isinstance(r, RequestBudgetExhausted) for r in results) == 4
        persisted += METER.take(owners[0]).requests
        with pytest.raises(RequestBudgetExhausted):
            await call(owners[2])
        assert calls == 2
    finally:
        for owner in owners:
            METER.take(owner)


@pytest.mark.asyncio
async def test_rate_limiter_wait_rechecks_budget_before_provider():
    entered, release = asyncio.Event(), asyncio.Event()
    exhausted = False

    class Limiter:
        async def acquire(self, _):
            entered.set()
            await release.wait()
            return 0

    class Inner:
        async def generate_json(self, **_):
            pytest.fail("provider must not run")

    def guard():
        if exhausted:
            raise RequestBudgetExhausted("limit")

    token = REQUEST_ADMISSION.set(guard)
    try:
        task = asyncio.create_task(RateLimitedTransport(Inner(), Limiter()).generate_json())
        await entered.wait()
        exhausted = True
        release.set()
        with pytest.raises(RequestBudgetExhausted):
            await task
    finally:
        REQUEST_ADMISSION.reset(token)


@pytest.mark.asyncio
async def test_controller_cap_preserves_actual_patch_evidence(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [_edit("a.py", "A\n"), DONE], "bob": [_edit("b.py", "B\n"), DONE],
    })
    quiet(ctrl, monkeypatch)
    original = engine.create_controller_step

    class Inner:
        async def generate_json(self, **kwargs):
            return await original(**kwargs)

    monkeypatch.setattr(engine, "create_controller_step",
                        RateLimitedTransport(Inner(), ProviderRateLimiter(0)).generate_json)
    team_id = (await ctrl._create_team(tid, "u1", request("implement", budget=2,
               kickoff_assignments=PARTS)))["team_id"]
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.resume_phase, team.requests) == ("PAUSED", "IMPLEMENTING", 2)
    assert (tmp_path / "ws" / "a.py").read_text() == "A\n"
    actual_files = sorted(p.name for p in (tmp_path / "ws").glob("*.py"))
    evidence = ctrl._teams.report(team_id)["partial_progress"]
    assert evidence["files_changed"] == actual_files
    assert any(m["status"] == "partial" for m in evidence["members"])
    await ctrl.disband_team(tid, team_id)
    notice = next(n for n in store.unclaimed_notices(tid) if n.kind == "ended")
    assert "a.py" in notice.payload["body"]


@pytest.mark.asyncio
async def test_upgrade_requires_card_preserves_identity_and_rewinds(tmp_path, monkeypatch):
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                              {"alice": [AGREE, final()], "bob": [AGREE]})
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    member = store.teams.member(team_id, "alice")
    store.teams.update_member(team_id, "alice",
        definition_override=definition_to_json(BUILTIN_AGENTS["explore"]))
    team, before = store.teams.get_team(team_id), store.get_agent(member.agent_id)
    args = {"expected_run_id": team.run_id, "result_seq": team.result_seq,
            "members": ["alice"], "reason": "Implement the accepted plan"}
    assert not ctrl._member_can_edit(team_id, "alice")
    await request_upgrade(ctrl, tid, team_id, args)
    assert not ctrl._member_can_edit(team_id, "alice")
    gate = store.get_thread(tid).pending_controller_gates[0]
    monkeypatch.setattr(store, "current_checkpoint_seq", lambda _: 7)
    ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)
    assert ctrl._member_can_edit(team_id, "alice")
    after = store.get_agent(member.agent_id)
    assert (after.history, after.activation_count) == (before.history, before.activation_count)
    assert store.teams.get_team(team_id).phase == "DONE"
    assert store.teams.get_team(team_id).result_seq == team.result_seq
    store.teams.restore_snapshots(tid, 7)
    assert not ctrl._member_can_edit(team_id, "alice")


@pytest.mark.asyncio
async def test_rejected_or_stale_upgrade_does_not_expand_permissions(tmp_path, monkeypatch):
    from agentd.chat.controller import TeamPlanConflict
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                              {"alice": [AGREE, final()], "bob": [AGREE]})
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    args = {"expected_run_id": team.run_id, "result_seq": team.result_seq,
            "members": ["alice"], "reason": "Prepare plan execution"}
    await request_upgrade(ctrl, tid, team_id, args)
    gate = store.get_thread(tid).pending_controller_gates[0]
    ctrl.decide_team_plan(tid, gate.gate_id, "reject", None)
    assert store.teams.member(team_id, "alice").definition_override is None
    await request_upgrade(ctrl, tid, team_id, args)
    gate = store.get_thread(tid).pending_controller_gates[0]
    store.teams.update_team(team_id, run_id=team.run_id + 1)
    with pytest.raises(TeamPlanConflict):
        ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)
    assert store.teams.member(team_id, "alice").definition_override is None


@pytest.mark.asyncio
@pytest.mark.parametrize("report_run,newer_request,should_wake", [(1, False, True),
                                                                 (0, False, False),
                                                                 (1, True, False)])
async def test_wait_registration_reconciles_only_new_current_run_completion(
    tmp_path, report_run, newer_request, should_wake
):
    from test_team_execution_modes_foundation import setup
    store, _, host, coord, _ = setup(tmp_path, "implement")
    plan = store.append_post("t", author="main", kind="proposal", text="two parts",
        payload={"assignments": PARTS, "shared_files": []})
    coord.start_execution(plan.proposal_id)
    store.append_post("t", author="alice", kind="post", text="Review please", recipient="bob")
    store.append_report("t", "bob", "Review completed", "completed", report_run, "lifecycle")
    coord.on_report("bob", "completed", None)
    if newer_request:
        store.append_post("t", author="alice", kind="post", text="New question", recipient="bob")
    coord.on_report("alice", "awaiting_peer", None, waiting_on=["bob"])
    assert bool(host.woken) is should_wake
    coord.close()
