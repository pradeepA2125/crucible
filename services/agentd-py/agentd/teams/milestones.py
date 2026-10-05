"""Milestone text (spec v2 §8.8): a headline plus ids, counts and pointers — never full
proposal texts, evidence or reports, which stay on the board."""
from __future__ import annotations

from agentd.teams.models import TeamRecord


def milestone_text(team: TeamRecord, kind: str, data: dict[str, object]) -> tuple[str, str]:
    name = repr(team.name)
    if kind == "adopted":
        headline = f"Team {name} adopted {data['proposal_id']}"
        by = " by you (the main agent)" if data.get("by") == "main" else " by the team"
        raw_parts = data.get("assignments")
        parts = raw_parts if isinstance(raw_parts, list) else []
        assigned = [f"{a.get('member')} → {a.get('part')} ({len(a.get('files') or [])} files)"
                    for a in parts if isinstance(a, dict)]
        details = [f"{data['proposal_id']} was adopted{by}.",
                   "Assignments: " + ("; ".join(assigned) if assigned else "none"),
                   "The team has ended with this plan adopted (implementation by the team "
                   "is not available yet): carry it out or tell the user."]
    elif kind == "deadlock":
        headline = f"Team {name} deadlocked after {data['round']} rounds"
        details = []
        raw_proposals = data.get("proposals")
        for p in raw_proposals if isinstance(raw_proposals, list) else []:
            if not isinstance(p, dict):
                continue
            stances = list((p.get("stances") or {}).values())
            details.append(f"{p['id']}: {stances.count('agree')} agree, "
                           f"{stances.count('object')} object, {stances.count('none')} no stance")
        details = details or ["No open proposals."]
        details.append("Next: post_board to run one more round, adopt_proposal to adopt one "
                       "of its open proposals, or disband_team.")
    elif kind == "member_lost":
        headline = f"Team {name} lost {data['label']} ({data['status']})"
        details = [f"{data['label']} left the quorum; the team continues without it."]
    else:
        headline = f"Team {name} ended — {data.get('reason', team.end_reason or '')}"
        details = []
    lines = [headline, f"phase {team.phase} · round {team.round} · "
             f"{team.requests} / {team.budget} requests", *details,
             "Read more with team_status, or the board in the team window."]
    return headline, "\n".join(lines)
