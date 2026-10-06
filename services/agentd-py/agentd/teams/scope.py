"""Which files a team member may edit (spec v2 §3.9, §8.6 step 3) — pure."""
from __future__ import annotations

from agentd.teams.models import TeamMember, TeamRecord


def team_edit_refusal(team: TeamRecord, members: list[TeamMember], label: str, path: str,
                      shared: set[str]) -> str | None:
    if team.phase != "IMPLEMENTING":
        return f"team phase {team.phase} does not allow edits"
    owner = next((m.label for m in members
                  if m.assignment and path in (m.assignment.get("files") or [])), None)
    if owner is not None and owner != label:
        return f"{path} is owned by {owner} — team_message {owner} instead"
    if owner == label or path in shared:
        return None
    if team.approval_gate:
        # The approved plan bounds what may change.
        return f"{path} is not in the approved plan — tell the main agent"
    return None
