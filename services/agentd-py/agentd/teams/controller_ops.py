"""Controller-side team workflow operations; lifecycle rules stay in the coordinator."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agentd.subagents.framing import frame
from agentd.subagents.inbox import InboxItem
from agentd.teams.config import team_budget_per_member, team_max_budget, team_max_live_per_thread
from agentd.teams.models import LIVE_TEAM_PHASES, TeamRecord
from agentd.teams.validation import TeamInputError, check_assignments, check_text, parse_assignments

if TYPE_CHECKING:
    from agentd.chat.controller import ChatController
    from agentd.teams.coordinator import TeamCoordinator


class TeamWorkflowOps:
    def __init__(self, host: ChatController) -> None:
        self.host = host
        self.store = host._store.teams

    def current(self, team_id: str) -> TeamRecord:
        team = self.store.get_team(team_id)
        assert team is not None
        return team

    def team(self, thread_id: str, team_id: str, args: dict[str, object]) -> TeamRecord:
        team = self.store.get_team(team_id)
        if team is None or team.thread_id != thread_id:
            raise TeamInputError("team is not in this thread")
        expected = args.get("expected_run_id")
        if type(expected) is not int or expected != team.run_id:
            raise TeamInputError(f"stale team run: expected_run_id must be {team.run_id}")
        return team

    def checkpoint(self, team: TeamRecord) -> None:
        self.store.save_snapshot(
            team.team_id, self.host._store.current_checkpoint_seq(team.thread_id)
        )

    def capacity(self, team: TeamRecord) -> None:
        if team.phase not in LIVE_TEAM_PHASES and not team.pending_lead_request_seq:
            if self.store.count_live_teams(team.thread_id) >= team_max_live_per_thread():
                raise TeamInputError("live team limit reached; finish another team first")

    def quiet(self, team: TeamRecord) -> None:
        if team.pending_lead_request_seq:
            raise TeamInputError("cancel or finish the pending lead request before changing runs")
        if any(self.host.is_busy(team.team_id, m.label) for m in self.store.members(team.team_id)):
            raise TeamInputError("team members are still finishing; retry after they report")

    def coordinator(self, team: TeamRecord) -> TeamCoordinator:
        from agentd.runtime.artifacts import chat_turn_artifacts_root
        from agentd.teams.config import team_round_timeout_s
        from agentd.teams.coordinator import TeamCoordinator
        from agentd.teams.trace import CoordinatorTrace

        assert self.host._teams is not None
        coordinator = TeamCoordinator(
            team.team_id,
            self.store,
            self.host._teams,
            self.host,
            CoordinatorTrace(
                chat_turn_artifacts_root(
                    team.thread_id, team.created_turn_id, self.host._workspace_path
                )
                / "teams"
                / team.team_id
                / "coordinator.jsonl"
            ),
            round_timeout_s=team_round_timeout_s(),
        )
        self.host._coordinators[team.team_id] = coordinator
        return coordinator

    async def start_implementation(
        self, thread_id: str, team_id: str, args: dict[str, object]
    ) -> dict[str, object]:
        if self.host.turn_kind(thread_id) != "user":
            raise TeamInputError("start implementation only in a user-started turn requesting it")
        team = self.store.get_team(team_id)
        # A retried successful start references the previous run, not the new one.
        if team is not None and team.thread_id == thread_id and team.mode == "implement":
            plan = (
                self.store.get_post(team_id, int(team.adopted_proposal_id[1:]))
                if team.adopted_proposal_id
                else None
            )
            if (
                plan
                and plan.payload.get("source_result_seq") == args.get("result_seq")
                and plan.payload.get("source_run_id") == args.get("expected_run_id")
            ):
                supplied = args.get("assignments")
                shared_raw = args.get("shared_files", plan.payload.get("shared_files", []))
                if not isinstance(shared_raw, list):
                    raise TeamInputError("shared_files must be a list")
                repeated_parts, repeated_shared = check_assignments(
                    parse_assignments(
                        supplied if supplied is not None else plan.payload.get("assignments", []),
                        [m.label for m in self.store.members(team_id)],
                    ),
                    [str(f) for f in shared_raw],
                    workspace=Path(self.host._workspace_path),
                    can_edit=lambda _: True,
                )
                if repeated_parts != plan.payload.get("assignments"):
                    raise TeamInputError(
                        "this result was already started with different assignments"
                    )
                if repeated_shared != plan.payload.get("shared_files"):
                    raise TeamInputError(
                        "this result was already started with different shared files"
                    )
                if args.get("approval_gate") is True and not team.approval_gate:
                    raise TeamInputError("this result was already started without an approval gate")
                return {
                    "team_id": team_id,
                    "phase": team.phase,
                    "run_id": team.run_id,
                    "already_started": True,
                }
        team = self.team(thread_id, team_id, args)
        if team.phase != "DONE":
            raise TeamInputError(
                "implementation extension requires a completed deliberation result"
            )
        self.quiet(team)
        self.capacity(team)
        seq = args.get("result_seq")
        result = self.store.get_post(team_id, seq) if type(seq) is int else None
        if (
            result is None
            or result.kind != "result"
            or team.result_seq != seq
            or result.payload.get("run_id") != team.run_id
        ):
            raise TeamInputError("result_seq must reference this run's accepted result")
        if result.payload.get("outcome") != "actionable":
            raise TeamInputError(
                "the result requires a decision or recommends no change; deliberate first"
            )
        from agentd.teams.finalization import changed_fingerprints, fingerprint_files

        snapshot = result.payload.get("file_fingerprints", {})
        changed = changed_fingerprints(Path(self.host._workspace_path), snapshot)
        if changed:
            raise TeamInputError(
                "stale_plan: files changed; deliberate again: " + ", ".join(changed)
            )
        basis_id = result.payload.get("basis_proposal_id")
        basis = self.store.get_post(team_id, int(str(basis_id)[1:])) if basis_id else None
        payload = basis.payload if basis else {}
        roster = [m.label for m in self.store.members(team_id)]
        raw = args.get("assignments", payload.get("assignments", []))
        parts = parse_assignments(raw, roster)
        shared_raw = args.get("shared_files", payload.get("shared_files", []))
        if not isinstance(shared_raw, list):
            raise TeamInputError("shared_files must be a list")
        parts, shared = check_assignments(
            parts,
            [str(f) for f in shared_raw],
            workspace=Path(self.host._workspace_path),
            can_edit=lambda lb: self.host._member_can_edit(team_id, lb),
        )
        if not parts:
            raise TeamInputError("implementation requires nonempty executable assignments")
        paths = sorted(
            {
                str(f)
                for part in parts
                for f in (part["files"] if isinstance(part["files"], list) else [])
            }
            | set(shared)
        )
        fingerprints = fingerprint_files(Path(self.host._workspace_path), paths)
        self.checkpoint(team)
        fresh = min(team_budget_per_member() * len(roster), team_max_budget())
        with self.store.transaction():
            instruction = self.store.append_post(
                team_id,
                author="main",
                kind="proposal",
                text=result.text,
                payload={
                    "assignments": parts,
                    "shared_files": shared,
                    "source_result_seq": seq,
                    "source_run_id": team.run_id,
                    "execution_instruction": True,
                    "file_fingerprints": fingerprints,
                },
            )
            self.store.update_team(
                team_id,
                phase="DELIBERATING",
                mode="implement",
                run_id=team.run_id + 1,
                run_request_start=team.requests,
                budget=team.requests + fresh,
                approval_gate=team.approval_gate or args.get("approval_gate") is True,
                adopted_proposal_id=None,
                closing_proposal_id=None,
                review_cycles=0,
                paused_reason=None,
                resume_phase=None,
                end_reason=None,
                ended_at=None,
            )
            for member in self.store.members(team_id):
                self.store.set_assignment(team_id, member.label, None)
                self.store.set_in_quorum(team_id, member.label, True)
        current = self.current(team_id)
        self.coordinator(current).start_execution(instruction.proposal_id)
        current = self.current(team_id)
        return {
            "team_id": team_id,
            "phase": current.phase,
            "run_id": current.run_id,
            "reopened": True,
        }

    async def set_mode(
        self, thread_id: str, team_id: str, args: dict[str, object]
    ) -> dict[str, object]:
        team = self.team(thread_id, team_id, args)
        if args.get("mode") != "deliberate":
            raise TeamInputError(
                "only narrowing to deliberate is allowed; use start_team_implementation"
            )
        reason = check_text(args.get("reason"), "reason")
        if team.phase not in LIVE_TEAM_PHASES:
            raise TeamInputError("mode narrowing requires a live team")
        if team.pending_lead_request_seq:
            raise TeamInputError("cancel the pending lead request before changing mode")
        self.checkpoint(team)
        self.store.update_team(team_id, mode="deliberate")  # fence new writes before awaits
        coordinator = self.host._coordinators.get(team_id)
        if team.phase in {"DELIBERATING", "FINALIZING", "DEADLOCKED"}:
            if coordinator:
                coordinator.narrow_mode()
            self.host.team_phase_changed(self.current(team_id))
            return {
                "team_id": team_id,
                "mode": "deliberate",
                "phase": team.phase,
                "run_id": team.run_id,
            }
        if coordinator:
            coordinator.close()
        self.host._coordinators.pop(team_id, None)
        self.store.update_team(team_id, phase="PAUSED", paused_reason="mode narrowed")
        self.host._store.remove_team_gates(thread_id, team_id)
        runtime = self.host._subagents
        assert runtime is not None
        for _member, agent_id in self.host._team_agent_ids(team_id):
            await runtime.stop(agent_id, "mode changed")
        current = self.current(team_id)
        if current.run_id != team.run_id or current.phase != "PAUSED":
            raise TeamInputError(
                "team changed while stopping; inspect its current state before retrying"
            )
        self.store.update_team(team_id, phase="DONE", end_reason="mode narrowed")
        return self.host._reopen_team(
            thread_id,
            self.current(team_id),
            "Implementation stopped; consider the current files and report what remains. " + reason,
            [],
        )

    async def message_lead(
        self, thread_id: str, team_id: str, args: dict[str, object]
    ) -> dict[str, object]:
        team = self.team(thread_id, team_id, args)
        if not team.lead:
            raise TeamInputError(
                "this legacy team has no lead; create a team with an explicit lead"
            )
        if team.phase in {"DISBANDED", "FAILED"}:
            raise TeamInputError("team is closed or requires lifecycle recovery first")
        retry = args.get("retry_request_seq")
        if team.pending_lead_request_seq:
            if retry != team.pending_lead_request_seq:
                return {
                    "team_id": team_id,
                    "request_seq": team.pending_lead_request_seq,
                    "status": team.lead_exchange_status,
                    "already_pending": True,
                }
            if team.lead_exchange_status != "blocked":
                return {
                    "team_id": team_id,
                    "request_seq": retry,
                    "status": team.lead_exchange_status,
                }
        elif retry is not None:
            raise TeamInputError("there is no blocked request with that sequence")
        self.capacity(team)
        extra = args.get("extra_budget")
        allowance = team.budget
        if extra is not None:
            if self.host.turn_kind(thread_id) != "user" or type(extra) is not int or extra < 1:
                raise TeamInputError(
                    "extra_budget must be positive and supplied in a user-started turn"
                )
            maximum = team.run_request_start + team_max_budget()
            allowance = min(maximum, team.budget + extra)
        if self.host.team_requests(team_id) >= allowance:
            raise TeamInputError(
                "team budget exhausted; explicitly add allowance or cancel the request"
            )
        text = check_text(args.get("text"), "text") if retry is None else ""
        self.checkpoint(team)
        with self.store.transaction():
            if retry is None:
                post = self.store.append_post(
                    team_id,
                    author="main",
                    kind="post",
                    recipient=team.lead,
                    text=text,
                    payload={"channel": "main_lead", "run_id": team.run_id},
                )
                seq = post.seq
            else:
                assert team.pending_lead_request_seq is not None
                seq = team.pending_lead_request_seq
            self.store.update_team(
                team_id,
                pending_lead_request_seq=seq,
                lead_exchange_status="pending",
                budget=allowance,
            )
        self.host.team_phase_changed(self.current(team_id))
        if retry is None:
            self.host._broadcast_post(team, post)
        self.deliver_request(team_id)
        return {
            "team_id": team_id,
            "request_seq": seq,
            "run_id": team.run_id,
            "status": self.current(team_id).lead_exchange_status,
        }

    def deliver_request(self, team_id: str) -> None:
        team = self.store.get_team(team_id)
        if (
            not team
            or not team.lead
            or not team.pending_lead_request_seq
            or team.lead_exchange_status != "pending"
        ):
            return
        if team.phase in {"PAUSED", "AWAITING_APPROVAL", "FAILED", "DISBANDED"}:
            return
        member = self.store.member(team_id, team.lead)
        if member is None:
            return
        post = self.store.get_post(team_id, team.pending_lead_request_seq)
        if post is None:
            return
        text = frame("main", "direct request", post.text, seq=post.seq)
        text += (
            f"\nReply with team_reply_main(text, request_seq={post.seq}). "
            "This message does not authorize implementation or change your current obligation."
        )
        runtime = self.host._subagents
        assert runtime is not None
        if runtime.is_active(member.agent_id):
            runtime.deliver(
                member.agent_id,
                InboxItem(
                    kind="note",
                    text=text,
                    wakes=False,
                    source_id=f"team-private:{team_id}",
                    author="main",
                ),
            )
        else:
            if member.pending_start_json:
                return  # lifecycle start will attach this request at activation entry
            handle = self.host._handle_from_record(team.thread_id, member.agent_id)
            handle.activation_input = text
            handle.team_activation_kind = "lead_reply"
            handle.team_run_id = team.run_id
            handle.team_request_seq = post.seq
            handle.team_phase = team.phase
            runtime.enqueue(handle, self.host._activate)
        self.store.update_member(team_id, member.label, private_delivered_seq=post.seq)
        self.store.update_team(team_id, lead_exchange_status="running")

    def reply_main(
        self, team_id: str, label: str, run_id: int, text: str, request_seq: int | None
    ) -> dict[str, object]:
        team = self.store.get_team(team_id)
        if not team or team.run_id != run_id or label != team.lead:
            raise TeamInputError("only this run's lead can reply to main")
        if team.phase in {"DISBANDED", "FAILED"}:
            raise TeamInputError("this team is closed")
        if request_seq is not None and request_seq != team.pending_lead_request_seq:
            earlier = next(
                (
                    p
                    for p in self.store.posts(team_id)
                    if p.payload.get("channel") == "main_lead"
                    and p.recipient == "main"
                    and p.payload.get("reply_to") == request_seq
                    and p.payload.get("run_id") == run_id
                ),
                None,
            )
            if earlier:
                return {"seq": earlier.seq, "already_replied": True}
            raise TeamInputError("request_seq does not match the pending request")
        if request_seq is None and team.pending_lead_request_seq:
            raise TeamInputError("reference the pending request_seq when replying")
        with self.store.transaction():
            post = self.store.append_post(
                team_id,
                author=label,
                kind="post",
                recipient="main",
                text=check_text(text, "reply"),
                payload={"channel": "main_lead", "run_id": run_id, "reply_to": request_seq},
            )
            if request_seq is not None:
                self.store.update_team(
                    team_id, pending_lead_request_seq=None, lead_exchange_status=None
                )
        self.host._broadcast_post(team, post)
        self.host.team_milestone(
            team,
            "lead_reply",
            f"Team {team.name!r}: lead replied",
            f"Lead {label} replied to main (#{post.seq}):\n{post.text}",
            logical_id=f"team-reply:{team_id}:{post.seq}",
        )
        self.host.team_phase_changed(self.current(team_id))
        return {"seq": post.seq, "to": "main"}

    async def cancel_lead_request(
        self, thread_id: str, team_id: str, args: dict[str, object]
    ) -> dict[str, object]:
        team = self.team(thread_id, team_id, args)
        assert team.lead is not None
        if (
            args.get("request_seq") != team.pending_lead_request_seq
            or not team.pending_lead_request_seq
        ):
            raise TeamInputError("request_seq does not match the pending request")
        with self.store.transaction():
            post = self.store.append_post(
                team_id,
                author="system",
                kind="system",
                text="Lead request cancelled.",
                payload={
                    "channel": "main_lead",
                    "cancelled": team.pending_lead_request_seq,
                    "run_id": team.run_id,
                },
                recipient=team.lead,
            )
            self.store.update_team(
                team_id, pending_lead_request_seq=None, lead_exchange_status=None
            )
        handle = self.host._member_handle(team_id, team.lead)
        self.host._broadcast_post(team, post)
        if handle and handle.team_activation_kind == "lead_reply":
            await self.host.stop_member(team_id, team.lead, "request cancelled")
        self.host.team_phase_changed(self.current(team_id))
        return {"team_id": team_id, "cancelled": True, "phase": team.phase}
