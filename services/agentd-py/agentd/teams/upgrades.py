"""User-approved role changes on an existing quiet team, preserving its history."""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentd.chat.models import GateTeam, PendingGate
from agentd.subagents.definitions import BUILTIN_AGENTS, definition_to_json
from agentd.teams.controller_ops import TeamWorkflowOps
from agentd.teams.validation import TeamInputError, check_text

if TYPE_CHECKING:
    from agentd.chat.controller import ChatController


def _changes(host: ChatController, team_id: str, labels: object) -> list[dict[str, object]]:
    if (not isinstance(labels, list) or not labels or
            any(not isinstance(label, str) for label in labels) or len(set(labels)) != len(labels)):
        raise TeamInputError("members must be a nonempty list of unique member labels")
    target = BUILTIN_AGENTS["general-purpose"]
    changes = []
    for label in labels:
        member = host._store.teams.member(team_id, label)
        record = host._store.get_agent(member.agent_id) if member else None
        if member is None or record is None:
            raise TeamInputError(f"unknown team member {label!r}")
        ctx, _ = host._context_for(
            agent_id=record.agent_id, name=target.name, label=label, depth=record.depth,
            parent_agent_id=record.parent_agent_id, snapshot=target, inherited=record.inherited,
        )
        if "edit" not in ctx.allowed_types:
            raise TeamInputError(f"{label} cannot be upgraded under its inherited restrictions")
        changes.append({
            "member": label, "agent_id": record.agent_id,
            "from": member.definition_override or record.definition,
            "to": definition_to_json(target), "permission": ctx.permission,
            "tools": "Read, edit assigned files, run policy-approved commands",
            "allowed_types": list(ctx.allowed_types), "no_ask": ctx.no_ask,
            "edit_review": ctx.edit_review,
        })
    return changes


async def request_upgrade(host: ChatController, thread_id: str, team_id: str,
                          args: dict[str, object]) -> dict[str, object]:
    ops = TeamWorkflowOps(host)
    team = ops.team(thread_id, team_id, args)
    seq = args.get("result_seq")
    result = ops.store.get_post(team_id, seq) if type(seq) is int else None
    if (team.phase != "DONE" or team.result_seq != seq or result is None
            or result.kind != "result" or result.payload.get("run_id") != team.run_id):
        raise TeamInputError("upgrade requires this run's completed accepted result")
    ops.quiet(team)
    reason = check_text(args.get("reason"), "reason")
    changes = _changes(host, team_id, args.get("members"))
    thread = host._store.get_thread(thread_id)
    for gate in thread.pending_controller_gates if thread else []:
        if (gate.team and gate.team.id == team_id
                and gate.payload.get("action") == "upgrade_members"):
            if gate.payload.get("changes") != changes:
                raise TeamInputError("resolve the existing member-upgrade card first")
            return {"status": "awaiting_user_confirmation", "gate_id": gate.gate_id}
    gate = PendingGate.new("team_plan", {
        "action": "upgrade_members", "team_id": team_id, "team_name": team.name,
        "run_id": team.run_id, "result_seq": team.result_seq, "text": reason,
        "changes": changes,
    }, team=GateTeam(id=team_id, name=team.name))
    host._store.add_controller_gate(thread_id, gate)
    return {"status": "awaiting_user_confirmation", "gate_id": gate.gate_id,
            "note": "Only the user's confirmation card changes roles; no agent was activated."}


def decide_upgrade(host: ChatController, thread_id: str, gate: PendingGate,
                   decision: str) -> dict[str, object]:
    from agentd.chat.controller import TeamPlanConflict, TeamPlanInvalid
    ops = TeamWorkflowOps(host)
    team_id = gate.team.id if gate.team else ""
    team = ops.store.get_team(team_id)
    if (team is None or team.thread_id != thread_id or team.phase != "DONE"
            or team.run_id != gate.payload.get("run_id")
            or team.result_seq != gate.payload.get("result_seq")):
        raise TeamPlanConflict("this upgrade card no longer matches the team's result")
    if decision not in {"approve", "reject"}:
        raise TeamPlanInvalid("member upgrade requires approve or reject")
    try:
        ops.quiet(team)
        if decision == "approve":
            changes = gate.payload.get("changes", [])
            current = _changes(host, team_id, [change["member"] for change in changes])
            if current != changes:
                raise TeamInputError("member capabilities changed; request a fresh confirmation")
            ops.checkpoint(team)
            with ops.store.transaction():
                for change in changes:
                    ops.store.update_member(team_id, str(change["member"]),
                                            definition_override=change["to"])
                post = ops.store.append_post(team_id, author="main", kind="system",
                    text="User approved member roles: " + ", ".join(
                        str(change["member"]) + " → general-purpose" for change in changes),
                    payload={"role_upgrade": True, "run_id": team.run_id,
                             "gate_id": gate.gate_id})
            host._broadcast_post(team, post)
        host._store.remove_controller_gate(thread_id, gate.gate_id)
    except TeamInputError as exc:
        raise TeamPlanConflict(str(exc)) from exc
    host.team_phase_changed(ops.current(team_id))
    host.team_milestone(ops.current(team_id), "member_upgrade", "Team member upgrade " +
        ("approved" if decision == "approve" else "rejected"),
        "User " + ("approved" if decision == "approve" else "rejected") +
        " the member role upgrade. No implementation has started. "
        "The accepted result and member history are unchanged. "
        "If approved, start_team_implementation can use the result in a user-started turn.")
    return {"team_id": team_id, "phase": team.phase, "decision": decision}
