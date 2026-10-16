"""Mode boundaries exercise the production SQLite store and coordinator."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_team_coordinator import _Host

from agentd.teams import state_machine as sm
from agentd.teams.coordinator import TeamCoordinator
from agentd.teams.finalization import finalization_input, validate_final_result
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.store import TeamStore
from agentd.teams.trace import CoordinatorTrace
from agentd.teams.validation import TeamInputError


def setup(tmp_path: Path, mode: str = "deliberate", *, gate: bool = False):
    conn = sqlite3.connect(tmp_path / "teams.sqlite3")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    store.create_team(
        TeamRecord(
            team_id="t",
            thread_id="thread",
            name="review",
            goal="inspect",
            mode=mode,
            deliverable_kind="diagnosis",
            deliverable_description="Give evidence",
            lead="alice",
            max_rounds=3,
            budget=100,
            approval_gate=gate,
            created_turn_id="u",
            created_at=datetime.now(UTC),
        )
    )
    for label in ("alice", "bob"):
        store.add_member(TeamMember(team_id="t", label=label, agent_id=label))
    svc = TeamService(store, tmp_path, lambda _: AgentInfo("gp", "", "idle"))
    host = _Host()
    coord = TeamCoordinator(
        "t", store, svc, host, CoordinatorTrace(tmp_path / "trace"), round_timeout_s=5
    )
    plan = store.append_post(
        "t",
        author="main",
        kind="proposal",
        text="Investigate then fix",
        payload={
            "assignments": [{"member": "bob", "part": "fix", "files": ["a.py"]}],
            "shared_files": [],
        },
    )
    return store, svc, host, coord, plan


def final_fields(outcome="actionable"):
    return {
        "final_result": {
            "summary": "Diagnosis ready",
            "outcome": outcome,
            "basis_proposal_id": "P1",
        }
    }


def adopt(store, svc, coord):
    coord.kickoff("proposal", [])
    svc.agree("t", "alice", "P1")
    svc.agree("t", "bob", "P1")
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)


@pytest.mark.asyncio
async def test_deliberation_with_assignments_finalizes_lead_and_stops(tmp_path):
    store, svc, host, coord, _ = setup(tmp_path, gate=True)
    adopt(store, svc, coord)
    assert coord.phase == "FINALIZING"
    assert host.started == ["alice", "bob", "alice"]
    assert not host.gates
    assert "Finaliz" in host.extras["alice"]
    assert store.get_team("t").round_cutoff_seq is None
    coord.on_report(
        "alice", "completed", None, report="# Evidence\nA diagnosis", team_fields=final_fields()
    )
    team = store.get_team("t")
    assert team.phase == "DONE"
    assert team.result_notice_pending
    assert store.get_post("t", team.result_seq).text == "# Evidence\nA diagnosis"
    assert host.milestones == ["recommendation_ready"]
    assert "# Evidence\nA diagnosis" in host.bodies[-1]
    assert all(m.assignment is None for m in store.members("t"))


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [False, True])
async def test_combined_final_result_starts_only_authorized_continuation(tmp_path, gate):
    store, svc, host, coord, _ = setup(tmp_path, "deliberate_and_implement", gate=gate)
    adopt(store, svc, coord)
    coord.on_report("alice", "completed", None, report="Final plan", team_fields=final_fields())
    assert coord.phase == ("AWAITING_APPROVAL" if gate else "IMPLEMENTING")
    assert (host.gates == ["P1"]) if gate else (store.member("t", "bob").assignment is not None)
    assert host.milestones == ["recommendation_ready"]
    coord.close()


@pytest.mark.asyncio
async def test_supplied_plan_enters_implementation_without_debate(tmp_path):
    store, _, host, coord, plan = setup(tmp_path, "implement")
    coord.start_execution(plan.proposal_id)
    assert coord.phase == "IMPLEMENTING"
    assert host.started == ["bob"]
    assert not [p for p in store.posts("t") if p.kind == "result"]
    coord.close()


@pytest.mark.asyncio
async def test_incomplete_finalization_pauses_and_resumes_lead_only(tmp_path):
    store, svc, host, coord, _ = setup(tmp_path)
    adopt(store, svc, coord)
    coord.on_report("alice", "partial", None, report="Unfinished")
    assert coord.phase == "PAUSED"
    assert store.get_team("t").resume_phase == "FINALIZING"
    coord.resume(10)
    assert coord.phase == "FINALIZING"
    assert host.started[-1] == "alice"
    coord.close()


@pytest.mark.asyncio
async def test_material_feedback_reopens_bounded_debate(tmp_path):
    store, svc, _, coord, _ = setup(tmp_path)
    adopt(store, svc, coord)
    coord.on_report(
        "alice",
        "completed",
        None,
        report="Need revise",
        team_fields={"finalization_feedback": {"reason": "A new dependency"}},
    )
    assert coord.phase == "DELIBERATING"
    assert store.get_team("t").adopted_proposal_id is None
    assert store.get_post("t", 1).closed == "feedback"
    coord.close()


def test_finalization_input_excludes_peer_private_messages_and_consultation(tmp_path):
    store, _, _, coord, _ = setup(tmp_path)
    store.append_post("t", author="bob", kind="post", text="SECRET", recipient="alice")
    store.append_report("t", "bob", "EVIDENCE", "completed", 1, "lifecycle")
    store.append_report("t", "bob", "CONSULTATION", "completed", 1, "lead_reply")
    text = finalization_input(store, store.get_team("t"))
    assert "EVIDENCE" in text
    assert "SECRET" not in text and "CONSULTATION" not in text
    coord.close()


def test_savepoints_rollback_nested_team_mutations(tmp_path):
    store, _, _, coord, _ = setup(tmp_path)
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.update_team("t", mode="implement")
            with store.transaction():
                store.append_post("t", author="alice", kind="result", text="No commit")
            raise RuntimeError("failure")
    assert store.get_team("t").mode == "deliberate"
    assert len(store.posts("t")) == 1
    coord.close()


def test_snapshot_restores_later_run_authority_and_post_history(tmp_path):
    store, _, _, coord, _ = setup(tmp_path)
    store.save_snapshot("t", 4)
    store.update_team("t", run_id=2, mode="implement", result_seq=2)
    store.update_team("t", requests=7, prompt_tokens=1200, completion_tokens=80, cached_tokens=600)
    store.append_post("t", author="alice", kind="result", text="Future result")
    store.update_member("t", "alice", private_delivered_seq=2, pending_start_json={"run_id": 2})
    assert store.restore_snapshots("thread", 4) == ["t"]
    assert store.get_team("t").run_id == 1
    assert store.get_team("t").mode == "deliberate"
    assert store.get_team("t").requests == 7
    assert store.get_team("t").prompt_tokens == 1200
    assert store.get_team("t").completion_tokens == 80
    assert store.get_team("t").cached_tokens == 600
    assert store.member("t", "alice").pending_start_json is None
    assert len(store.posts("t")) == 1
    coord.close()


def test_oversize_result_is_refused_without_truncation():
    with pytest.raises(TeamInputError, match="20,000"):
        validate_final_result("x" * 20001, final_fields()["final_result"], "P1")


def test_deliberate_mode_cannot_approve_stale_execution_gate():
    state = sm.TeamState(
        phase="AWAITING_APPROVAL",
        round=1,
        max_rounds=3,
        members={"alice": sm.MemberState("alice")},
        mode="deliberate",
        pending_assignees=("alice",),
    )
    after, actions = sm.apply(state, sm.Approval("approve"))
    assert after.phase == "PAUSED"
    assert not any(isinstance(a, sm.StartImplementation) for a in actions)


@pytest.mark.parametrize("event", [sm.MainResume(), sm.Revive("IMPLEMENTING")])
def test_deliberate_resume_and_revival_never_restore_old_execution(event):
    state = sm.TeamState(
        phase="PAUSED" if isinstance(event, sm.MainResume) else "FAILED",
        round=1,
        max_rounds=3,
        mode="deliberate",
        paused_from="IMPLEMENTING",
        members={"alice": sm.MemberState("alice", assigned=True)},
        lead="alice",
    )
    after, actions = sm.apply(state, event)
    assert after.phase == "DELIBERATING"
    assert not any(isinstance(a, sm.StartImplementation) for a in actions)


def test_done_pending_exchange_counts_for_admission_and_rewind(tmp_path):
    store, _, _, coord, _ = setup(tmp_path)
    store.update_team("t", phase="DONE", pending_lead_request_seq=3, lead_exchange_status="blocked")
    assert store.count_live_teams("thread") == 1
    assert store.live_team_names("thread") == ["review"]
    coord.close()
