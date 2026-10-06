"""Who may edit what while a team implements (spec v2 §3.9, §8.6 step 3)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentd.chat.protected_paths import ProtectedPathError, TeamProtection, TeamScopeError
from agentd.domain.models import PatchFailureCode
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.scope import team_edit_refusal


def _team(**over) -> TeamRecord:  # type: ignore[no-untyped-def]
    base = dict(team_id="team-1", thread_id="t", name="auth", goal="g", max_rounds=3,
                budget=100, created_turn_id="u", created_at=datetime.now(UTC),
                phase="IMPLEMENTING", approval_gate=False)
    base.update(over)
    return TeamRecord(**base)


MEMBERS = [
    TeamMember(team_id="team-1", agent_id="a", label="alice",
               assignment={"member": "alice", "part": "api", "files": ["a.py"]}),
    TeamMember(team_id="team-1", agent_id="b", label="bob",
               assignment={"member": "bob", "part": "tests", "files": ["t.py"]}),
    TeamMember(team_id="team-1", agent_id="c", label="carol"),
]


@pytest.mark.parametrize("team,label,path,expected", [
    (_team(phase="DELIBERATING"), "alice", "a.py", "team phase DELIBERATING does not allow edits"),
    (_team(), "alice", "a.py", None),
    (_team(), "alice", "shared.py", None),
    (_team(), "alice", "t.py", "t.py is owned by bob — team_message bob instead"),
    (_team(), "carol", "other.py", None),
    (_team(approval_gate=True), "alice", "other.py",
     "other.py is not in the approved plan — tell the main agent"),
    (_team(approval_gate=True), "alice", "shared.py", None),
])
def test_refusals(team, label, path, expected) -> None:
    assert team_edit_refusal(team, MEMBERS, label, path, {"shared.py"}) == expected


def test_team_protection_ownership_and_repeat() -> None:
    def rule(key: str) -> str | None:
        return f"{key} is owned by bob — team_message bob instead" if key == "t.py" else None

    protection = TeamProtection(rule)
    with pytest.raises(TeamScopeError) as first:
        protection.check_apply(["t.py"])
    assert first.value.issues[0].code == PatchFailureCode.TEAM_SCOPE
    assert "already told" not in str(first.value)
    with pytest.raises(TeamScopeError) as second:
        protection.check_apply(["t.py"])
    assert "You were already told this file is owned by bob. Do not retry the edit." in str(
        second.value)
    protection.check_apply(["a.py"])                         # allowed


def test_protected_paths_still_come_first() -> None:
    with pytest.raises(ProtectedPathError):
        TeamProtection(lambda _k: None).check_apply([".crucible/mcp.json"])
