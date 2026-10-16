"""What a team produced reaches the main agent (live 2026-10-11: a planning team ended DONE
with "Files changed: none", the main agent had nothing to tell the user, and it reopened the
team asking members to "deliver the report … so it reaches the main conversation")."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.models import NoticeRecord
from agentd.chat.storage import ChatThreadStore
from agentd.subagents.notices import notice_turn_input
from agentd.teams.milestones import milestone_text
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.tools import MAIN_TOOL_NAMES, MainTeamOps, MainTeamToolSource

LONG = "x" * 9000


def _setup(tmp_path: Path):
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="plan", goal="Plan the meter",
                      phase="REVIEWING", max_rounds=3, budget=160, created_turn_id="turn1",
                      created_at=datetime.now(UTC), lead="design")
    teams.create_team(team)
    for label in ("design", "ui"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    info = lambda agent_id: AgentInfo("general-purpose", "does things", "completed")  # noqa: E731
    service = TeamService(teams, tmp_path, info, on_post=lambda t, p: None)
    plan = teams.append_post(team.team_id, author="design", kind="proposal",
                             text="Meter design: show a 0-100% ring fed by /live.")
    closing = service.closing_proposal(team.team_id, "Closing: plan delivered.", {"closing": True})
    teams.update_team(team.team_id, adopted_proposal_id=plan.proposal_id,
                      closing_proposal_id=closing.proposal_id)
    teams.append_post(team.team_id, author="ui", kind="object", ref_id=closing.proposal_id,
                      text="The closing wording claims implementation; this was planning only.")
    service.record(team.team_id, "design", "wrapped_up",
                   payload={"status": "completed", "report": "Earlier draft."})
    service.record(team.team_id, "design", "wrapped_up", payload={
        "status": "completed",
        "report": "\nDesign: a ring meter with three states.\nDetails follow on the board."})
    service.record(team.team_id, "ui", "wrapped_up", payload={"status": "objected", "report": LONG})
    return service, team.team_id, plan, closing


def test_report_holds_proposals_final_reports_and_objections(tmp_path: Path) -> None:
    service, tid, plan, closing = _setup(tmp_path)
    report = service.report(tid)
    assert report["adopted"] == {"id": plan.proposal_id, "author": "design", "text": plan.text}
    assert report["closing"]["text"] == "Closing: plan delivered."
    members = {m["label"]: m for m in report["members"]}
    assert members["design"]["report"].strip().startswith("Design: a ring meter")  # latest only
    assert members["ui"]["status"] == "objected"
    assert len(members["ui"]["report"]) < 8200 and "team window" in members["ui"]["report"]
    assert report["objections"] == [{"seq": 3, "author": "ui", "on": closing.proposal_id,
                                     "text": "The closing wording claims implementation; "
                                             "this was planning only."}]


def test_member_summaries_are_one_capped_line(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    summaries = {s["label"]: s for s in service.member_summaries(tid)}
    assert summaries["design"]["summary"] == "Design: a ring meter with three states."
    assert len(summaries["ui"]["summary"]) <= 241


def test_done_body_carries_each_members_summary() -> None:
    team = TeamRecord(team_id="team-1", thread_id="t", name="plan", goal="g", max_rounds=3,
                      budget=160, created_turn_id="u", created_at=datetime.now(UTC), phase="DONE")
    _, body = milestone_text(team, "done", {
        "reason": "reviewed", "adopted": "P1", "closing": "P2", "cycle": 1, "files": [],
        "members": [{"label": "design", "status": "completed", "summary": "A ring meter."},
                    {"label": "ui", "status": "completed", "summary": ""}]})
    assert "Member reports:" in body
    assert "- design (completed): A ring meter." in body
    assert "- ui (completed): (no report)" in body


def test_deadlock_body_previews_each_open_proposal() -> None:
    team = TeamRecord(team_id="team-1", thread_id="t", name="plan", goal="g", max_rounds=3,
                      budget=160, created_turn_id="u", created_at=datetime.now(UTC),
                      phase="DEADLOCKED", round=3)
    _, body = milestone_text(team, "deadlock", {"round": 3, "proposals": [
        {"id": "P1", "stances": {"a": "agree"}, "preview": "Use a ring meter fed by /live."}]})
    assert 'P1: "Use a ring meter fed by /live."' in body


@pytest.mark.asyncio
async def test_team_report_tool_returns_the_report(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)

    async def unused(*_: object) -> dict[str, object]:
        raise AssertionError("not called")

    ops = MainTeamOps(create=unused, resolve=lambda name: tid, post=lambda *a: {},
                      status=service.summary, disband=unused, adopt=lambda *a: {},
                      report=service.report)
    source = MainTeamToolSource({}, ops, first_turn_team_ids=set())
    assert "team_report" in MAIN_TOOL_NAMES
    assert any(d.name == "team_report" for d in source.definitions())
    out = await source.execute("team_report", {"team": "plan"})
    assert not out.is_error
    assert json.loads(out.output)["adopted"]["text"].startswith("Meter design")


def _notice(kind: str) -> NoticeRecord:
    return NoticeRecord(notice_id=f"n-{kind}", thread_id="t", source_kind=kind, source_id="s",
                        kind="agent_finished" if kind == "agent" else "done", delivery="wake",
                        payload={}, created_at=datetime.now(UTC))


def test_notice_turn_input_names_what_arrived() -> None:
    agents = notice_turn_input([_notice("agent")])
    teams = notice_turn_input([_notice("team")])
    both = notice_turn_input([_notice("agent"), _notice("team")])
    assert "agents finished" in agents and "milestone" not in agents
    assert "finished" not in teams and "milestone" in teams
    assert "agents finished" in both and "milestone" in both


def test_modern_report_evidence_filters_run_and_activation_purpose(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    teams = service._store
    teams.update_team(tid, mode="deliberate", run_id=2,
                      deliverable_kind="diagnosis", deliverable_description="Find the cause")
    teams.append_report(tid, "design", "Old run", "completed", 1, "lifecycle")
    teams.append_report(tid, "design", "Current evidence", "completed", 2, "lifecycle")
    teams.append_report(tid, "design", "Private consultation", "completed", 2, "lead_reply")
    teams.append_report(tid, "ui", "Final synthesis", "completed", 2, "finalization")
    result = teams.append_post(tid, author="design", kind="result", text="Full diagnosis", payload={
        "run_id": 2, "summary": "Root cause found", "outcome": "no_change", "kind": "diagnosis"})
    teams.update_team(tid, result_seq=result.seq)
    report = service.report(tid)
    members = {m["label"]: m for m in report["members"]}
    assert members["design"]["report"] == "Current evidence"
    assert members["ui"]["report"] == "Final synthesis"
    assert report["deliverable"] == {"kind": "diagnosis", "description": "Find the cause"}
    assert report["result_summary"] == {"seq": result.seq, "run_id": 2, "summary": "Root cause found",
                                        "outcome": "no_change", "kind": "diagnosis"}
    assert service.summary(tid)["result_summary"] == report["result_summary"]
