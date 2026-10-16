"""Requested documents use ordinary lead edits, scoped to finalization."""
from functools import partial

import pytest

from agentd.chat.protected_paths import TeamProtection, TeamScopeError
from agentd.subagents.definitions import BUILTIN_AGENTS
from tests.test_protected_paths import _create, _session
from tests.test_team_controller import _make, _settle
from tests.test_team_implementation_controller import AGREE, _edit
from tests.test_team_workflow_controller import final, quiet, request


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["general-purpose", "explore"])
async def test_lead_saves_requested_documents_and_reports_paths(tmp_path, monkeypatch, role):
    paths = ["docs/feature-spec.md", "docs/feature-plan.md"]
    summary = "Saved the spec and plan: " + ", ".join(paths)
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit(paths[0], "# Spec\n"), _edit(paths[1], "# Plan\n"),
                  final(summary)],
        "bob": [AGREE],
    })
    quiet(ctrl, monkeypatch)
    contexts = []
    original = engine.create_controller_step

    async def capture(*args, **kwargs):
        contexts.append(kwargs["render_ctx"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", capture)
    team_id = (await ctrl._create_team(tid, "u1", request(
        agent=BUILTIN_AGENTS[role],
        goal=f"Review the feature. Save the spec at {paths[0]} and plan at {paths[1]}.",
    )))["team_id"]
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason) == ("DONE", "deliberation_complete")
    assert (tmp_path / "ws" / paths[0]).read_text() == "# Spec\n"
    assert (tmp_path / "ws" / paths[1]).read_text() == "# Plan\n"
    assert all(m.assignment is None for m in store.teams.members(team_id))
    lead = store.teams.member(team_id, "alice")
    assert ctrl._write_log_for(tid).files_changed_by({lead.agent_id}) == sorted(paths)
    assert store.get_agent(lead.agent_id).definition["permission"] == (
        "plan" if role == "explore" else "default")
    assert summary in store.unclaimed_notices(tid)[0].payload["body"]
    assert "edit" not in contexts[0].base_types  # deliberation
    editing = [c for c in contexts if "edit" in c.base_types]
    assert editing and all(c.agent_label == "alice" and c.permission != "plan" for c in editing)
    assert all("run_command" not in names for _, _, names, _ in engine.seen)
    assert all("team_publish_document" not in names for _, _, names, _ in engine.seen)
    lead_histories = [str(h) for label, h, _, _ in engine.seen if label == "alice"]
    assert any("Original request:" in h and paths[0] in h
               and "using ordinary edits before reporting" in h for h in lead_histories)
    peer = store.teams.member(team_id, "bob")
    assert store.get_agent(peer.agent_id).activation_count == 1


@pytest.mark.asyncio
async def test_finalizing_lead_still_cannot_edit_protected_configuration(tmp_path, monkeypatch):
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("AGENTS.md", "bad\n"), final()], "bob": [AGREE],
    })
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    assert store.teams.get_team(team_id).phase == "DONE"
    assert not (tmp_path / "ws" / "AGENTS.md").exists()
    assert any("protected Crucible configuration file" in str(h)
               for label, h, _, _ in engine.seen if label == "alice")


@pytest.mark.asyncio
async def test_finalization_write_permission_ends_with_phase_and_is_not_inherited(
    tmp_path, monkeypatch
):
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, final()], "bob": [AGREE],
    })
    quiet(ctrl, monkeypatch)
    team_id = (await ctrl._create_team(tid, "u1", request()))["team_id"]
    await _settle(ctrl)
    lead = store.teams.member(team_id, "alice")
    rule = partial(ctrl._team_edit_rule, team_id, "alice", expected_run_id=1,
                   activation_kind="finalization", agent_id=lead.agent_id)
    assert rule("docs/spec.md") is not None  # DONE
    store.teams.update_team(team_id, phase="FINALIZING")
    assert rule("docs/spec.md") is None
    assert ctrl._team_edit_rule(team_id, "alice", "docs/spec.md", expected_run_id=1,
                                activation_kind="finalization", agent_id="helper") is not None
    assert ctrl._team_edit_rule(team_id, "alice", "docs/spec.md", expected_run_id=1,
                                activation_kind="lead_reply", agent_id=lead.agent_id) is not None
    peer = store.teams.member(team_id, "bob")
    assert ctrl._team_edit_rule(team_id, "bob", "docs/spec.md", expected_run_id=1,
                                activation_kind="finalization", agent_id=peer.agent_id) is not None
    # A real patch proposed during finalization cannot be accepted after a pause.
    session, ws = _session(tmp_path, TeamProtection(rule))
    await session.apply(_create("docs/spec.md"))
    store.teams.update_team(team_id, phase="PAUSED")
    with pytest.raises(TeamScopeError):
        await session.accept()
    assert not (ws / "docs/spec.md").exists()
    await session.close()
    store.teams.update_team(team_id, phase="FINALIZING", run_id=2)
    assert "earlier team run" in rule("docs/spec.md")
