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
        raw_waits = data.get("waits")
        waits = raw_waits if isinstance(raw_waits, dict) else {}

        def idle_line(label: str, status: str) -> str:
            on = waits.get(label)
            return f"- {label}: {status}" + (f" (waiting on {', '.join(on)})" if on else "")
        first = next((str(p[0]) for p in idle if isinstance(p, list) and len(p) == 2
                      and p[1] == "awaiting_peer"), None) or next(
            (str(p[0]) for p in idle if isinstance(p, list) and len(p) == 2), "member")
        details = ["Nobody is working and assignments are open:",
                   *[idle_line(str(p[0]), str(p[1])) for p in idle
                     if isinstance(p, list) and len(p) == 2],
                   f"Stuck {data.get('count')} of 3 times in a row (the third pauses the "
                   "team). Restart a member with post_board mentioning the member, e.g. "
                   f'"@{first} <what changed>; continue your part". Doing a member\'s part '
                   "yourself leaves its assignment open, so the team cannot finish; "
                   "disband_team ends it."]
    elif kind == "paused":
        reason = str(data.get("reason", ""))
        why = {"budget": "its request budget ran out",
               "transient_burst": "the provider kept failing",
               "stuck": "it was stuck three times in a row",
               "quorum lost": "fewer than 2 members were left in the quorum"}.get(reason, reason)
        headline = f"Team {name} paused — {why}"
        progress = (f"; {data.get('done', 0)} of {data.get('total')} assignments done"
                    if data.get("total") else "")
        details = [f"Paused from {data.get('paused_from')}{progress}.",
                   "resume_team continues it (only after the user agrees — it spends more "
                   "requests); disband_team ends it."]
    elif kind == "done":
        reviewed = data.get("reason") == "reviewed"
        headline = (f"Team {name} finished — the review agreed" if reviewed
                    else f"Team {name} finished with unresolved objections")
        raw_files = data.get("files")
        files = [str(f) for f in raw_files] if isinstance(raw_files, list) else []
        raw_abstained = data.get("abstained")
        abstained = [str(a) for a in raw_abstained] if isinstance(raw_abstained, list) else []
        raw_open = data.get("unresolved")
        unresolved = ([u for u in raw_open if isinstance(u, dict)]
                      if isinstance(raw_open, list) else [])
        details = [f"{data.get('adopted')} implemented; closing proposal {data.get('closing')} "
                   f"(review cycle {data.get('cycle')})."]
        if abstained:
            details.append("Abstained: " + ", ".join(abstained))
        if unresolved:
            details.append("Unresolved objections:")
            details += [f"- #{u.get('seq')} {u.get('label')}: {str(u.get('reason', ''))[:160]}"
                        for u in unresolved]
        details.append("Files changed: " + (", ".join(files) if files else "none"))
        details.append("Tell the user what the team built"
                       + (" and what is still open." if unresolved else "."))
        details.append("For a follow-up request, post_board to this team: it reopens with its "
                       "members' context.")
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
            if stances and "object" not in stances:
                # Live 2026-10-08: the main agent asked the team to "adopt the strongest
                # proposal" with post_board three times instead of adopting it.
                details.append(f"{p['id']} has no objection: adopt_proposal adopts it now; "
                               "post_board only runs another round.")
        details = details or ["No open proposals."]
        details.append("Next: post_board to run one more round, adopt_proposal to adopt one "
                       "of its open proposals, or disband_team.")
    elif kind == "member_lost":
        headline = f"Team {name} lost {data['label']} ({data['status']})"
        details = [f"{data['label']} left the quorum; the team continues without it."]
    else:
        headline = f"Team {name} ended — {data.get('reason', team.end_reason or '')}"
        details = []
    # `used` counts running activations too: the persisted total lags until they end.
    used = data.get("used", team.requests)
    lines = [headline, f"phase {team.phase} · round {team.round} · "
             f"{used} / {team.budget} requests", *details,
             "Read more with team_status, or the board in the team window."]
    return headline, "\n".join(lines)
