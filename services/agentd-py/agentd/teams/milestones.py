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
        following = {
            "implementing": "The members are implementing it; milestones will report progress.",
            "ended": "It has no assignments, so the team has ended with the plan adopted: "
                     "carry it out or tell the user.",
        }.get(str(data.get("next", "")), "")
        details = [f"{data['proposal_id']} was adopted{by}.",
                   "Assignments: " + ("; ".join(assigned) if assigned else "none"),
                   *([following] if following else [])]
    elif kind == "approval_needed":
        headline = f"Team {name} needs your approval for {data['proposal_id']}"
        details = [f"{data['proposal_id']} was adopted; a card is waiting for the user to "
                   "approve it, send feedback, or reject it. Tell the user."]
    elif kind == "member_blocked":
        headline = f"Team {name}: {data['label']} is blocked"
        details = [f"{data['label']} reported {data['status']}: {data.get('report', '')}",
                   f"Its assignment stays open. Read its full report in the team window; "
                   f"post_board mentioning @{data['label']} restarts it."]
    elif kind == "stuck":
        headline = f"Team {name} is stuck"
        raw_idle = data.get("idle")
        idle = raw_idle if isinstance(raw_idle, list) else []
        details = ["Nobody is working and assignments are open:",
                   *[f"- {pair[0]}: {pair[1]}" for pair in idle
                     if isinstance(pair, list) and len(pair) == 2],
                   f"Stuck {data.get('count')} of 3 times in a row (the third pauses the "
                   "team). post_board to unblock them, or disband_team."]
    elif kind == "paused":
        reason = str(data.get("reason", ""))
        why = {"budget": "its request budget ran out",
               "transient_burst": "the provider kept failing",
               "stuck": "it was stuck three times in a row"}.get(reason, reason)
        headline = f"Team {name} paused — {why}"
        details = [f"Paused from {data.get('paused_from')}; "
                   f"{data.get('done', 0)} of {data.get('total', 0)} assignments done.",
                   "resume_team continues it (only after the user agrees — it spends more "
                   "requests); disband_team ends it."]
    elif kind == "done":
        headline = f"Team {name} finished its assignments"
        raw_files = data.get("files")
        files = [str(f) for f in raw_files] if isinstance(raw_files, list) else []
        details = ["Files changed: " + (", ".join(files) if files else "none"),
                   "Tell the user what the team built."]
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
