"""TeamCoordinator (spec v2 §8.1): one per live team. Feeds events into the pure state
machine, persists its state, and executes its actions through the host (ChatController).
Every method is synchronous apart from disband, so a caller never interleaves with it."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from agentd.teams import state_machine as sm
from agentd.teams.adoption import evaluate_round
from agentd.teams.milestones import milestone_text
from agentd.teams.models import TeamMember, TeamPost, TeamRecord
from agentd.teams.service import TeamService
from agentd.teams.store import TeamStore
from agentd.teams.trace import CoordinatorTrace

logger = logging.getLogger(__name__)
_FORCE_RETRY_S = 5.0   # the member's loop was not built yet: look again shortly
Delivery = Literal["wake", "notify"]


class CoordinatorHost(Protocol):
    def start_member(self, team_id: str, label: str, extra: str = "") -> None: ...
    async def stop_member(self, team_id: str, label: str, reason: str) -> None: ...
    def force_final(self, team_id: str, label: str) -> bool: ...
    def active_seconds(self, team_id: str, label: str) -> float | None: ...
    def team_milestone(self, team: TeamRecord, kind: str, headline: str, body: str,
                       delivery: Delivery = "wake") -> None: ...
    def team_phase_changed(self, team: TeamRecord) -> None: ...
    def wake_for_post(self, team: TeamRecord, post: TeamPost) -> None: ...
    def raise_plan_gate(self, team: TeamRecord, proposal: TeamPost) -> None: ...
    def force_team_final(self, team_id: str) -> list[str]: ...
    def team_requests(self, team_id: str) -> int: ...
    def is_busy(self, team_id: str, label: str) -> bool: ...
    def has_wakes(self, team_id: str, label: str) -> bool: ...


def assignment_text(assignment: dict[str, Any], shared: list[str]) -> str:
    """A member's implementation input (spec v2 §8.6 step 1)."""
    files = ", ".join(str(f) for f in assignment.get("files") or []) or "none"
    return (f"Your assignment: {assignment.get('part', '')}. Files you own: {files}. "
            f"Shared files: {', '.join(shared) or 'none'}.")


def _member_state(member: TeamMember) -> sm.MemberState:
    return sm.MemberState(member.label, in_quorum=member.in_quorum,
                          assigned=member.assignment is not None,
                          done=member.assignment_done)


class TeamCoordinator:
    def __init__(self, team_id: str, store: TeamStore, service: TeamService,
                 host: CoordinatorHost, trace: CoordinatorTrace, *,
                 round_timeout_s: float, grace_s: float = 120.0) -> None:
        self._team_id = team_id
        self._store = store
        self._svc = service
        self._host = host
        self._trace = trace
        self._timeout = round_timeout_s
        self._grace = grace_s
        team = store.get_team(team_id)
        assert team is not None
        self._state = sm.TeamState(
            phase=team.phase, round=team.round, max_rounds=team.max_rounds,
            members={m.label: _member_state(m) for m in store.members(team_id)},
            approval_gate=team.approval_gate, stuck_count=team.stuck_count)
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._evaluation: dict[str, object] = {}   # the latest round_ended payload
        self._cutoff = 0                             # highest post seq at the round's start
        self._stops: set[asyncio.Task[None]] = set()
        self._starting: set[str] = set()     # start scheduled, activation not begun yet
        self._interrupted: set[str] = set()  # forced to report by a budget pause
        self._last_status: dict[str, str] = {}
        self._files: set[str] = set()        # every finished assignment's files
        self._suppress_wakes = False         # after a transient burst, until the user speaks

    @property
    def phase(self) -> str:
        return self._state.phase

    @property
    def thread_id(self) -> str:
        return self._team().thread_id

    def delta_cutoff(self) -> int | None:
        """A round's input is the board as it stood when the round started (spec v2 E5)."""
        return self._cutoff if self._state.phase == "DELIBERATING" else None

    # ── inputs ──────────────────────────────────────────────────────────────

    def kickoff(self, kind: str, mentions: list[str]) -> None:
        self._apply(sm.Kickoff(kind, tuple(mentions)))

    def on_post(self, post: TeamPost) -> None:
        if post.kind == "system":
            return
        phase = self._state.phase
        if phase == "DELIBERATING":
            self._hold(post)
        elif phase == "DEADLOCKED":
            if post.author == "main" and post.recipient is None:
                self._apply(sm.MainPost())
        elif phase == "IMPLEMENTING":
            self._host.wake_for_post(self._team(), post)
        # AWAITING_APPROVAL and PAUSED: an ordinary post, read at the next activation.

    def on_report(self, label: str, status: str, stop_reason: str | None, *,
                  files: tuple[str, ...] | list[str] = (), report: str = "") -> None:
        self._cancel(f"deadline:{label}")
        self._cancel(f"grace:{label}")
        self._starting.discard(label)
        if label in self._interrupted:
            self._interrupted.discard(label)
            if status == "partial":
                # The forced final iteration always reports partial; a member that finished
                # before the forcing took effect reported for real (spec v2 §8.2).
                stop_reason = sm.BUDGET_STOP
        self._last_status[label] = status
        self._apply(sm.MemberReported(label, status, stop_reason, tuple(files), report))
        self._check_stuck(label)

    def on_activation_start(self, label: str) -> None:
        self._starting.discard(label)
        if self._state.phase == "DELIBERATING":
            self._schedule(f"deadline:{label}", self._timeout, lambda: self._check_deadline(label))

    def main_adopt(self, proposal_id: str) -> None:
        self._apply(sm.MainAdopt(proposal_id, self._assignees(proposal_id)))

    def approval(self, decision: str, feedback: str | None) -> None:
        """The user's decision on the team_plan card (spec v2 §8.5)."""
        if decision == "feedback" and feedback:
            self._svc.post(self._team_id, "user", feedback)
        self._apply(sm.Approval(decision))

    def check_budget(self) -> None:
        """Called at every member and helper iteration top (spec v2 §3.11)."""
        if self._state.phase not in sm.LIVE_PHASES or self._state.phase == "PAUSED":
            return
        team = self._team()
        used = self._host.team_requests(self._team_id)
        if used >= team.budget:
            self._trace.write("budget", used=used, budget=team.budget)
            self._apply(sm.BudgetExhausted())

    def resume(self, extra_budget: int) -> None:
        team = self._team()
        self._store.update_team(self._team_id, budget=team.budget + extra_budget)
        self._trace.write("resume", extra_budget=extra_budget)
        self._apply(sm.MainResume())

    def mark_interrupted(self, label: str) -> None:
        self._interrupted.add(label)

    def user_spoke(self) -> None:
        self._suppress_wakes = False

    async def disband(self) -> None:
        self.close()
        team = self._team()
        # Ended first, so a member's stopped report changes nothing.
        self._state, actions = sm.apply(self._state, sm.Disband())
        self._store.update_team(self._team_id, phase="DISBANDED", end_reason="disbanded",
                                ended_at=datetime.now(UTC))
        for member in self._store.members(self._team_id):
            await self._host.stop_member(self._team_id, member.label, "disband")
        self._svc.system_post(self._team_id, "The team was disbanded.")
        self._trace.write("event", name="Disband", actions=[repr(a) for a in actions])
        self._ended(team, "DISBANDED", "disbanded")

    def close(self) -> None:
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()

    def trace_dropped(self, label: str, errors: list[str]) -> None:
        self._trace.write("report_fields_dropped", label=label, errors=errors)

    # ── the state machine ─────────────────────────────────────────────────────

    def _team(self) -> TeamRecord:
        team = self._store.get_team(self._team_id)
        assert team is not None
        return team

    def _assignees(self, proposal_id: str) -> tuple[str, ...]:
        proposal = self._store.get_post(self._team_id, int(proposal_id.lstrip("Pp")))
        parts = (proposal.payload if proposal else {}).get("assignments", [])
        return tuple(str(a.get("member")) for a in parts if isinstance(a, dict))

    def _adopted(self) -> TeamPost | None:
        team = self._team()
        if team.adopted_proposal_id is None:
            return None
        return self._store.get_post(self._team_id, int(team.adopted_proposal_id.lstrip("Pp")))

    def _apply(self, event: sm.Event) -> None:
        before = self._state.stuck_count
        self._state, actions = sm.apply(self._state, event)
        self._trace.write("event", name=type(event).__name__, event=repr(event),
                          actions=[repr(a) for a in actions])
        if self._state.stuck_count != before:
            self._store.update_team(self._team_id, stuck_count=self._state.stuck_count)
        for action in actions:
            self._execute(action)

    def _execute(self, action: sm.Action) -> None:
        if isinstance(action, sm.StartRound):
            self._start_round(action)
        elif isinstance(action, sm.Requeue):
            self._svc.record(self._team_id, action.label, "requeued", payload={
                "retry": action.retry, "of": len(sm.RETRY_BACKOFF_S),
                "after_ms": int(action.after_s * 1000)})
            self._schedule(f"requeue:{action.label}", action.after_s,
                           lambda: self._start(action.label))
        elif isinstance(action, sm.EvaluateRound):
            self._evaluate(action.round)
        elif isinstance(action, sm.Adopt):
            self._adopt(action)
        elif isinstance(action, sm.Milestone):
            self._milestone(action)
        elif isinstance(action, sm.SetQuorum):
            self._store.set_in_quorum(self._team_id, action.label, action.in_quorum)
        elif isinstance(action, sm.EnterPhase):
            self._store.update_team(
                self._team_id, phase=action.phase,
                paused_reason=action.reason if action.phase == "PAUSED" else None)
            self._svc.record(self._team_id, "team", "phase", payload={
                "phase": action.phase, "round": action.round, "reason": action.reason})
            self._host.team_phase_changed(self._team())
        elif isinstance(action, sm.RaisePlanGate):
            proposal = self._store.get_post(self._team_id, int(action.proposal_id.lstrip("Pp")))
            if proposal is not None:
                self._host.raise_plan_gate(self._team(), proposal)
        elif isinstance(action, sm.ClosePlan):
            self._close_plan(action.reason)
        elif isinstance(action, sm.StartImplementation):
            self._start_implementation(action.labels)
        elif isinstance(action, sm.AssignmentDone):
            self._assignment_done(action)
        elif isinstance(action, sm.ResumeMembers):
            for label in action.labels:
                self._start(label)
        elif isinstance(action, sm.CancelTimers):
            self.close()
        elif isinstance(action, sm.ForceFinalAll):
            forced = self._host.force_team_final(self._team_id)
            self._interrupted |= set(forced)
            self._trace.write("budget_forced", labels=sorted(forced))
        elif isinstance(action, sm.End):
            self.close()
            self._store.update_team(self._team_id, phase=action.phase,
                                    end_reason=action.reason, ended_at=datetime.now(UTC))
            team = self._team()
            if action.phase == "FAILED":
                for label in self._state.round_members:
                    self._spawn_stop(label, "disband")
            self._ended(team, action.phase, action.reason)

    def _milestone(self, action: sm.Milestone) -> None:
        team = self._team()
        data: dict[str, object] = dict(action.data)
        if action.kind == "deadlock":
            data["proposals"] = self._evaluation.get("proposals", [])
        elif action.kind == "done":
            raw = data.get("files")
            data["files"] = sorted(self._files | {str(f) for f in
                                                  (raw if isinstance(raw, list) else [])})
        elif action.kind == "paused":
            assigned = [m for m in self._state.members.values() if m.assigned]
            data.update(done=sum(m.done for m in assigned), total=len(assigned))
            if data.get("reason") == "transient_burst":
                # A provider outage or an exhausted daily quota: waking the main agent
                # would only burn more failing turns (spec v2 §5.3).
                self._suppress_wakes = True
        delivery: Delivery = "notify" if self._suppress_wakes else "wake"
        headline, body = milestone_text(team, action.kind, data)
        self._host.team_milestone(team, action.kind, headline, body, delivery)
        self._trace.write("milestone", milestone=action.kind, headline=headline,
                          delivery=delivery)

    def _ended(self, team: TeamRecord, phase: str, reason: str) -> None:
        self._svc.record(self._team_id, "team", "phase", payload={
            "phase": phase, "round": team.round, "reason": reason})
        fresh = self._team()
        if phase != "DONE":
            headline, body = milestone_text(fresh, "ended", {"reason": reason})
            self._host.team_milestone(fresh, "ended", headline, body,
                                      "notify" if self._suppress_wakes else "wake")
        self._host.team_phase_changed(fresh)

    def _start(self, label: str, extra: str = "") -> None:
        self._starting.add(label)
        self._host.start_member(self._team_id, label, extra)

    def _start_round(self, action: sm.StartRound) -> None:
        now = datetime.now(UTC)
        self._store.update_team(self._team_id, phase="DELIBERATING", round=action.round,
                                max_rounds=self._state.max_rounds, round_started_at=now)
        if action.round > 1:
            self._svc.record(self._team_id, "team", "phase",
                             payload={"phase": "DELIBERATING", "round": action.round})
            self._host.team_phase_changed(self._team())
        self._cutoff = max((p.seq for p in self._store.posts(self._team_id)), default=0)
        members = []
        for label in action.labels:
            member = self._store.member(self._team_id, label)
            seen = member.delivered_seq if member is not None else 0
            handed = [p.seq for p in self._store.posts(self._team_id, since_seq=seen, viewer=label)
                      if p.author != label and p.seq <= self._cutoff]
            members.append({"label": label, "handed": handed})
        self._svc.record(self._team_id, "team", "round_started",
                         payload={"round": action.round, "members": members})
        for label in action.labels:
            self._start(label)

    def _evaluate(self, ended_round: int) -> None:
        posts = self._store.posts(self._team_id)
        evaluation = evaluate_round(posts, self._state.quorum(), ended_round)
        new_posts = sum(1 for p in posts if p.round == ended_round and p.kind != "system")
        payload = {**evaluation.as_payload(), "new_posts": new_posts}
        self._svc.record(self._team_id, "team", "round_ended", payload=payload)
        self._trace.write("evaluation", **evaluation.as_payload())
        # The deadlock milestone carries the per-proposal counts (spec §8.8).
        self._evaluation = payload
        adopted = evaluation.adopted
        self._apply(sm.RoundEvaluated(adopted, self._assignees(adopted) if adopted else ()))

    def _adopt(self, action: sm.Adopt) -> None:
        seq = int(action.proposal_id.lstrip("Pp"))
        proposal = self._store.get_post(self._team_id, seq)
        if action.close:
            self._store.close_proposal(self._team_id, seq, "adopted")
        self._store.update_team(self._team_id, adopted_proposal_id=action.proposal_id)
        assignments = list((proposal.payload if proposal else {}).get("assignments", []))
        shared = list((proposal.payload if proposal else {}).get("shared_files", []))
        parts = "; ".join(f"{a.get('member')} → {a.get('part')} ({', '.join(a.get('files', []))})"
                          for a in assignments)
        by = " by the main agent" if action.by == "main" else ""
        text = f"Adopted {action.proposal_id}{by}." + (f" Assignments: {parts}." if parts else "")
        if shared:
            text += f" Shared: {', '.join(shared)}."
        self._svc.system_post(self._team_id, text, payload={
            "adopted": action.proposal_id, "by": action.by,
            "assignments": assignments, "shared_files": shared})
        if not action.close:
            return       # the approval_needed milestone tells the main agent instead
        team = self._team()
        headline, body = milestone_text(team, "adopted", {
            "proposal_id": action.proposal_id, "by": action.by, "assignments": assignments,
            "next": "implementing" if assignments else "ended"})
        self._host.team_milestone(team, "adopted", headline, body,
                                  "notify" if self._suppress_wakes else "wake")

    def _close_plan(self, reason: str) -> None:
        team = self._team()
        if team.adopted_proposal_id is None:
            return
        pid = team.adopted_proposal_id
        self._store.close_proposal(self._team_id, int(pid.lstrip("Pp")), reason)
        if reason == "adopted":
            self._svc.system_post(self._team_id, f"The user approved {pid}.")
        else:
            self._store.update_team(self._team_id, adopted_proposal_id=None)
            self._svc.system_post(self._team_id, f"The user sent {pid} back for another round.")

    def _start_implementation(self, labels: tuple[str, ...]) -> None:
        proposal = self._adopted()
        payload = proposal.payload if proposal is not None else {}
        by_member = {str(a.get("member")): a for a in payload.get("assignments", [])
                     if isinstance(a, dict)}
        shared = [str(f) for f in payload.get("shared_files", [])]
        self._store.reset_wakes(self._team_id)
        for member in self._store.members(self._team_id):
            self._store.set_assignment(self._team_id, member.label, by_member.get(member.label))
        for label in labels:
            if label in by_member:
                self._start(label, assignment_text(by_member[label], shared))

    def _assignment_done(self, action: sm.AssignmentDone) -> None:
        self._store.set_assignment_done(self._team_id, action.label)
        self._files |= set(action.files)
        member = self._store.member(self._team_id, action.label)
        part = (member.assignment or {}).get("part", "") if member is not None else ""
        files = ", ".join(action.files) or "none"
        self._svc.system_post(self._team_id, f'● {action.label} finished "{part}" — files: {files}')

    def _check_stuck(self, reporter: str) -> None:
        """Spec v2 §8.6 step 5. The reporter is excluded from the busy check: on_report runs
        inside its own activation, which is still active here."""
        if self._state.phase != "IMPLEMENTING":
            return
        if any(key.startswith("requeue:") for key in self._timers):
            return
        others_busy = any(self._host.is_busy(self._team_id, lb) or lb in self._starting
                          for lb in self._state.members if lb != reporter)
        if others_busy or self._host.has_wakes(self._team_id, reporter):
            return
        idle = tuple((lb, self._last_status.get(lb, "idle")) for lb in self._state.members)
        self._trace.write("stuck_check", idle=[list(i) for i in idle])
        self._apply(sm.Stuck(idle))

    def _hold(self, post: TeamPost) -> None:
        """Posts made during round N reach members at round N+1 (spec v2 E5, §8.3)."""
        if post.kind in ("agree", "object", "withdraw"):
            return
        others = [lb for lb in self._state.quorum() if lb != post.author]
        if post.recipient is not None:
            targets, everyone = [post.recipient], False
        elif "team" in post.mentions or not post.mentions:
            targets, everyone = others, True
        else:
            targets, everyone = [lb for lb in others if lb in post.mentions], False
        self._svc.record(self._team_id, post.author, "held", cause_seq=post.seq, payload={
            "post_seq": post.seq, "for": targets, "everyone": everyone,
            "until_round": self._state.round + 1})

    # ── timers ──────────────────────────────────────────────────────────────

    def _schedule(self, key: str, delay: float, callback) -> None:  # type: ignore[no-untyped-def]
        self._cancel(key)

        def fire() -> None:
            self._timers.pop(key, None)
            try:
                callback()
            except Exception:  # noqa: BLE001 — a timer must never kill the event loop
                logger.exception("[teams] coordinator timer %s failed", key)

        self._timers[key] = asyncio.get_running_loop().call_later(max(0.0, delay), fire)

    def _spawn_stop(self, label: str, reason: str) -> None:
        """Stop a member from synchronous code; the task is held until it finishes, since
        asyncio keeps only a weak reference to a task nobody awaits."""
        task = asyncio.get_running_loop().create_task(
            self._host.stop_member(self._team_id, label, reason))
        self._stops.add(task)
        task.add_done_callback(self._stops.discard)

    def _cancel(self, key: str) -> None:
        handle = self._timers.pop(key, None)
        if handle is not None:
            handle.cancel()

    def _check_deadline(self, label: str) -> None:
        """The clock counts active time only (spec v2 §8.3): re-arm for what is left."""
        active = self._host.active_seconds(self._team_id, label)
        if active is None or self._state.phase != "DELIBERATING":
            return
        if active < self._timeout:
            self._schedule(f"deadline:{label}", max(0.01, self._timeout - active),
                           lambda: self._check_deadline(label))
            return
        if not self._host.force_final(self._team_id, label):
            self._schedule(f"deadline:{label}", _FORCE_RETRY_S, lambda: self._check_deadline(label))
            return
        self._svc.record(self._team_id, label, "deadline", payload={})
        self._trace.write("deadline", label=label, active_s=active)
        self._schedule(f"grace:{label}", self._grace,
                       lambda: self._spawn_stop(label, "deadline"))
