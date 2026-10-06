# Team Coordinator 5B — Implementation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a team adopts a plan, run it: an optional user approval card, assignments handed to members, edits bounded by the plan, implementation reports checked, stuck teams surfaced, and budgets/outages pausing the team until the user resumes it — plus "Open board" links on the main agent's team tool pills.

**Architecture:** The pure state machine (`teams/state_machine.py`) gains the approval, implementation, pause and resume rules; the coordinator executes the new actions through five new `CoordinatorHost` methods that `ChatController` implements. Edit ownership is a new protection class at Check 1 (`chat/protected_paths.py`), driven by a pure rule in `teams/scope.py`. Implementation reports are checked in the loop through the existing `report_check` hook (`teams/implementation.py`). The budget is enforced from the loop's per-iteration callback. The `team_plan` gate is one more Class-A gate (backend enum, editor-client Zod enum, webview type) with its own decision route and card.

**Tech Stack:** Python 3.12 (FastAPI, pydantic, sqlite, pytest + pytest-asyncio), TypeScript (Zod editor-client, React webview, vitest).

**Spec:** `docs/superpowers/specs/2026-10-02-subagents-v2-design.md` §8.2 (`PAUSED`, `resume_team`), §8.3 (assignment validation at propose time), §8.5 (approval gate), §8.6 (implementation), §8.8 (milestones `approval_needed`, `member_blocked`, `stuck`, `paused`; `adopted` body), §8.9 (gate removal on disband), §3.9 (phase-gated edits, ownership at Check 1), §3.11 (budget enforcement, phase-aware budget hint), §5.3 (transient-burst wake suppression). Prior part: `docs/superpowers/plans/2026-10-06-team-coordinator-5a-deliberation.md`.

**Part index (Phase 5 is split in three; this plan is 5B):**

| Part | Scope | This plan |
|---|---|---|
| 5A Deliberation | state machine, rounds, stances, adoption, deadlock, quorum, re-queue, trace | done |
| 5B Implementation | approval gate + `team_plan` card, assignment validation, Check-1 ownership + phase rules, implementation report redirects, `member_blocked`, stuck detection, budget + `PAUSED` + `resume_team` + phase-aware hint, `transient_burst`, team usage totals, "Open board" pill links | ✅ |
| 5C Review + teardown | closing proposal + cycles, objection routing, `done` milestone (full), rewind deletion, Edits section + assignment rows | later |

**Interim in 5B (replaced in 5C):** when every assignment is done the team ends `DONE` with `end_reason = "implemented"` and an interim `done` milestone listing the files changed (no review yet). An adopted proposal with no assignments still ends `DONE` with `end_reason = "adopted"` (5A behavior — nothing to implement).

## Global Constraints

- Branch `feat/subagents-v2`, base `5dc53a5`. Never push.
- Commit format `type(scope): short description`, ending with the two trailer lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz`.
- Pytest: never `-q` (pyproject sets it), never piped; redirect to a file and check `$?`; add `--timeout=120`.
  Example: `pytest tests/test_x.py --color=no --timeout=120 > /tmp/x.txt 2>&1; echo exit=$?; tail -5 /tmp/x.txt`.
- Python venv: `/Users/pradeepkumar/projects/AI editor/services/agentd-py/.venv/bin/python` (from a worktree, run from that worktree's `services/agentd-py`).
- Vitest under `perl -e 'alarm N; exec @ARGV' npx vitest run …` (macOS has no `timeout`).
- After an editor-client change: `npm run -w @crucible/editor-client build` before the extension typecheck.
- `CRUCIBLE_TEAMS_ENABLED` stays default **off**; tests opt in with `monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")` and `CRUCIBLE_SUBAGENTS_ENABLED=1` (the `_make` helper in `tests/test_team_controller.py` does both).
- A new gate kind goes in **all three** enumerations: `chat/models.py::GateKind`, editor-client `PendingGateSchema.kind`, webview `types.ts::LiveGateView.kind` (and the host's `LiveGateView` in `src/controller.ts`).
- Prompts describe options by what each does and when it fits — never rank one tool or action above another.
- Text from agents reaching another agent is framed (`frame(...)`); refusal messages the model sees are system-written strings only.
- `/live` team rows carry only fields that change at activation boundaries (the dedup-signature invariant). Usage stays off `/live`.
- `GET` routes stay read-only; this plan adds only `POST /v1/chat/threads/{id}/team-plan-decision`.
- No new env var: the budget cap is the existing `CRUCIBLE_TEAM_MAX_BUDGET`, now enforced.

## Review Focus

1. **A team gate must not take the composer or block notice turns.** `/live` lists the `team_plan` gate with `agent: null`, so every filter written as `not g.agent` treats it as the main agent's. Expected: the composer stays enabled while a plan waits for approval, and the `approval_needed` milestone still starts a notice turn — `inputAvailability` "a team gate leaves the composer usable" (Task 11) and `test_team_gate_does_not_block_notice_turns` (Task 8).
2. **Stuck detection must exclude the reporting member.** `on_report` runs inside the reporter's own task, so `is_active(reporter)` is still true there. Expected: two members both reporting `awaiting_peer` produce a `stuck` milestone on the second report — `test_two_waiting_members_are_stuck` (Task 7) and `test_stuck_milestone_after_mutual_wait` (Task 8).
3. **A budget-forced report is not the member's work.** Expected: the member's assignment stays open, no `member_blocked`, and `resume_team` re-activates it — `test_budget_pause_forces_final_and_resume_restarts_owing` (Task 7) and `test_budget_exhaustion_pauses_and_resume_continues` (Task 8).
4. **The user rejects or sends feedback on the plan while the main agent is mid-turn.** Expected: feedback closes the proposal (`closed = "feedback"`), posts the user's text on the board and starts round n+1 with `max_rounds` raised; a second click on the removed card gets 404 — `test_feedback_reopens_deliberation` (Task 7), `test_team_plan_decision_route_errors` (Task 9).
5. **A member edits another member's file twice.** Expected: both refused at Check 1 before any gate, the second refusal adds "You were already told…", and nothing reaches the workspace — `test_team_protection_ownership_and_repeat` (Task 6) and `test_member_cannot_edit_anothers_file` (Task 8).

## File Structure

| File | Responsibility |
|---|---|
| `services/agentd-py/agentd/teams/state_machine.py` | approval, implementation, pause/resume, transient burst, stuck rules |
| `services/agentd-py/agentd/teams/milestones.py` | `approval_needed`, `member_blocked`, `stuck`, `paused`, interim `done`; adopted body |
| `services/agentd-py/agentd/teams/store.py` | `set_assignment`, `set_assignment_done`, `reset_wakes`, `add_usage` |
| `services/agentd-py/agentd/teams/validation.py` | `canonical_path_in`, `check_assignments` |
| `services/agentd-py/agentd/teams/service.py` | assignment validation at propose, `ActivationCounters.messaged`, `final_hint`, summary fields |
| `services/agentd-py/agentd/teams/scope.py` (new) | pure `team_edit_refusal` |
| `services/agentd-py/agentd/teams/implementation.py` (new) | `ImplementationReport` report check, `chain_checks` |
| `services/agentd-py/agentd/teams/coordinator.py` | new actions, host methods, budget/stuck/approval/resume |
| `services/agentd-py/agentd/teams/tools.py` | `MainTeamOps.resume`, `resume_team` |
| `services/agentd-py/agentd/domain/models.py` | `PatchFailureCode.TEAM_SCOPE` |
| `services/agentd-py/agentd/chat/protected_paths.py` | `TeamScopeError`, `TeamProtection` |
| `services/agentd-py/agentd/chat/controller_loop.py` | `final_hint` param, `forced_final` in plan_context, TEAM_SCOPE guidance |
| `services/agentd-py/agentd/chat/controller_prompts.py` | forced-final hint, implementation teaching, main TEAMS block |
| `services/agentd-py/agentd/subagents/runtime.py` | `AgentSupervisor.has_wakes` |
| `services/agentd-py/agentd/chat/models.py` | `GateKind` gains `team_plan`, `PendingGate.new(team=)` |
| `services/agentd-py/agentd/chat/storage.py` | `remove_team_gates` |
| `services/agentd-py/agentd/chat/controller.py` | host methods, `_activate` wiring, usage totals, decisions, resume, notice fix |
| `services/agentd-py/agentd/api/routes.py` | `POST …/team-plan-decision` |
| `apps/editor-client/src/contracts/task-contracts.ts`, `src/client/http-backend-client.ts` | `team_plan` kind + `team` on gates, `decideTeamPlan`, summary fields |
| `apps/vscode-extension/src/{controller.ts,chat-panel.ts,extension.ts}` | `team` on gates, `teamPlanDecision` message, `decideTeamPlan` |
| `apps/vscode-extension/webview-ui/src/{types.ts,inputAvailability.ts,teams.ts}` | gate kind + team, composer rule, `teamForRef` |
| `apps/vscode-extension/webview-ui/src/components/{LiveSlot.tsx,messages/gates/TeamPlanGate.tsx,messages/AgentRow.tsx,teams/TeamLinks.tsx,teams/PhaseStepper.tsx}` | the card, team chip, pill links, stepper road |

---

# Part A — Pure rules

### Task 1: State machine — approval, implementation, pause and resume

**Files:**
- Modify: `services/agentd-py/agentd/teams/state_machine.py`
- Test: `services/agentd-py/tests/test_team_state_machine.py`

**Interfaces:**
- Consumes: 5A's `TeamState`, `MemberState`, `apply`, events and actions (unchanged names).
- Produces:
  - `TeamState` fields `approval_gate: bool = False`, `paused_from: str | None = None`, `stuck_count: int = 0`, `transient_streak: int = 0`, `pending_assignees: tuple[str, ...] = ()`.
  - `MemberState` fields `assigned: bool = False`, `done: bool = False`.
  - Event changes: `RoundEvaluated(adopted, assignees=())`, `MainAdopt(proposal_id, assignees=())`, `MemberReported(label, status, stop_reason=None, files=(), report="")`.
  - New events: `Approval(decision: str)`, `BudgetExhausted()`, `Stuck(idle: tuple[tuple[str, str], ...])`, `MainResume()`.
  - Action change: `Adopt(proposal_id, by, close=True)`.
  - New actions: `RaisePlanGate(proposal_id)`, `ClosePlan(reason)`, `StartImplementation(labels)`, `AssignmentDone(label, files)`, `ResumeMembers(labels)`, `CancelTimers()`, `ForceFinalAll()`.
  - `BUDGET_STOP = "budget"` — the `stop_reason` of an interrupted (budget-forced) report.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_state_machine.py` (and extend the import list with `AssignmentDone, Approval, BudgetExhausted, CancelTimers, ClosePlan, ForceFinalAll, MainResume, RaisePlanGate, ResumeMembers, StartImplementation, Stuck`):

```python
def _adopted(state: TeamState, assignees: tuple[str, ...] = ("alice", "bob")) -> TeamState:
    s = _round1(state)
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, _ = apply(s, RoundEvaluated("P1", assignees))
    return s


def test_adoption_without_assignments_still_ends_done() -> None:
    s = _round1(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1", ()))
    assert actions == [Adopt("P1", "team"), End("DONE", "adopted")]


def test_adoption_with_assignments_implements() -> None:
    s = _round1(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1", ("alice",)))
    assert actions == [Adopt("P1", "team"), EnterPhase("IMPLEMENTING", 1),
                       StartImplementation(("alice",))]
    assert s.phase == "IMPLEMENTING"
    assert s.members["alice"].assigned and not s.members["bob"].assigned


def test_approval_gate_waits_then_approve_implements() -> None:
    state = _state()
    state.approval_gate = True
    s = _round1(state)
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1", ("alice", "bob")))
    assert actions == [Adopt("P1", "team", close=False), EnterPhase("AWAITING_APPROVAL", 1),
                       RaisePlanGate("P1"), Milestone("approval_needed", {"proposal_id": "P1"})]
    s, actions = apply(s, Approval("approve"))
    assert actions == [ClosePlan("adopted"), EnterPhase("IMPLEMENTING", 1),
                       StartImplementation(("alice", "bob"))]


def test_feedback_reopens_deliberation() -> None:
    state = _state(max_rounds=1)
    state.approval_gate = True
    s = _adopted(state)
    s, actions = apply(s, Approval("feedback"))
    assert actions[0] == ClosePlan("feedback")
    assert actions[1] == StartRound(2, ("alice", "bob"))
    assert (s.phase, s.round, s.max_rounds) == ("DELIBERATING", 2, 2)


def test_reject_disbands() -> None:
    state = _state()
    state.approval_gate = True
    s, actions = apply(_adopted(state), Approval("reject"))
    assert actions == [End("DISBANDED", "plan rejected")] and s.phase == "DISBANDED"


def test_completed_marks_done_and_last_one_ends_implemented() -> None:
    s = _adopted(_state())
    s, actions = apply(s, MemberReported("alice", "completed", files=("a.py",)))
    assert actions == [AssignmentDone("alice", ("a.py",))]
    s, actions = apply(s, MemberReported("bob", "completed", files=("b.py",)))
    assert actions == [AssignmentDone("bob", ("b.py",)),
                       Milestone("done", {"files": ["b.py"]}),   # widened by the coordinator
                       End("DONE", "implemented")]


def test_partial_is_blocked_and_assignment_stays_open() -> None:
    s = _adopted(_state())
    s, actions = apply(s, MemberReported("alice", "partial", report="x" * 400))
    assert actions == [Milestone("member_blocked",
                                 {"label": "alice", "status": "partial", "report": "x" * 300})]
    assert not s.members["alice"].done and s.members["alice"].in_quorum


def test_awaiting_peer_and_unassigned_reports_change_nothing() -> None:
    s = _adopted(_state(), assignees=("alice",))
    s, actions = apply(s, MemberReported("alice", "awaiting_peer"))
    assert actions == []
    s, actions = apply(s, MemberReported("bob", "completed"))
    assert actions == []


def test_three_final_transients_pause_the_team() -> None:
    s = _adopted(_state("alice", "bob", "carol"), assignees=("alice", "bob", "carol"))
    for label in ("alice", "bob"):
        for _ in range(3):
            s, _ = apply(s, MemberReported(label, "failed_transient"))
    assert s.transient_streak == 2 and s.phase == "IMPLEMENTING"
    for _ in range(2):
        s, actions = apply(s, MemberReported("carol", "failed_transient"))
        assert isinstance(actions[0], Requeue)
    s, actions = apply(s, MemberReported("carol", "failed_transient"))
    assert actions == [CancelTimers(), EnterPhase("PAUSED", 1, "transient_burst"),
                       Milestone("paused", {"reason": "transient_burst",
                                            "paused_from": "IMPLEMENTING"})]
    assert (s.phase, s.paused_from) == ("PAUSED", "IMPLEMENTING")


def test_a_non_transient_outcome_resets_the_streak() -> None:
    s = _adopted(_state())
    for _ in range(3):
        s, _ = apply(s, MemberReported("alice", "failed_transient"))
    s, _ = apply(s, MemberReported("bob", "awaiting_peer"))
    assert s.transient_streak == 0


def test_budget_pause_forces_final_and_resume_restarts_owing() -> None:
    s = _adopted(_state())
    s, actions = apply(s, BudgetExhausted())
    assert actions == [CancelTimers(), EnterPhase("PAUSED", 1, "budget"),
                       Milestone("paused", {"reason": "budget", "paused_from": "IMPLEMENTING"}),
                       ForceFinalAll()]
    s, actions = apply(s, MemberReported("alice", "partial", stop_reason="budget"))
    assert actions == [] and not s.members["alice"].done        # interrupted: still owes
    s, actions = apply(s, MemberReported("bob", "completed", files=("b.py",)))
    assert actions == [AssignmentDone("bob", ("b.py",))]          # real work counts
    s, actions = apply(s, MainResume())
    assert actions == [EnterPhase("IMPLEMENTING", 1, "resumed"), ResumeMembers(("alice",))]


def test_resume_in_deliberation_evaluates_a_complete_round() -> None:
    s = _round1(_state())
    s, _ = apply(s, BudgetExhausted())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, actions = apply(s, MemberReported("bob", "completed"))
    assert actions == []                                          # deferred while paused
    s, actions = apply(s, MainResume())
    assert actions == [EnterPhase("DELIBERATING", 1, "resumed"), EvaluateRound(1)]


def test_resume_in_deliberation_restarts_who_owes() -> None:
    s = _round1(_state())
    s, _ = apply(s, BudgetExhausted())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "partial", stop_reason="budget"))
    s, actions = apply(s, MainResume())
    assert actions == [EnterPhase("DELIBERATING", 1, "resumed"), ResumeMembers(("bob",))]


def test_stuck_twice_is_a_milestone_third_pauses() -> None:
    s = _adopted(_state())
    idle = (("alice", "awaiting_peer"), ("bob", "awaiting_peer"))
    for count in (1, 2):
        s, actions = apply(s, Stuck(idle))
        assert actions == [Milestone("stuck", {"idle": [list(i) for i in idle],
                                               "count": count})]
    s, actions = apply(s, Stuck(idle))
    assert actions[1] == EnterPhase("PAUSED", 1, "stuck")
    s, _ = apply(s, MainResume())
    assert s.stuck_count == 0


def test_a_completed_assignment_resets_stuck() -> None:
    s = _adopted(_state())
    s, _ = apply(s, Stuck((("alice", "awaiting_peer"),)))
    s, _ = apply(s, MemberReported("alice", "completed"))
    assert s.stuck_count == 0


def test_main_adopt_from_deadlock_implements() -> None:
    s = _state(phase="DEADLOCKED")
    s, actions = apply(s, MainAdopt("P2", ("bob",)))
    assert actions == [Adopt("P2", "main"), EnterPhase("IMPLEMENTING", 1),
                       StartImplementation(("bob",))]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_state_machine.py --color=no --timeout=120 > /tmp/sm.txt 2>&1; echo exit=$?; tail -5 /tmp/sm.txt`
Expected: FAIL — `ImportError: cannot import name 'AssignmentDone'`.

- [ ] **Step 3: Implement**

Replace `agentd/teams/state_machine.py` with:

```python
"""The team's rules (spec v2 §8.1–§8.6) — pure, synchronous, no I/O. The coordinator feeds
events in and executes the actions out. 5B covers approval and implementation; when every
assignment is done the team ends DONE until 5C adds review."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

RETRY_BACKOFF_S: tuple[float, ...] = (30.0, 120.0)
LIVE_PHASES = frozenset({"DELIBERATING", "AWAITING_APPROVAL", "IMPLEMENTING", "REVIEWING",
                         "DEADLOCKED", "PAUSED"})
BUDGET_STOP = "budget"          # the stop_reason of a report forced by a budget pause
TRANSIENT_BURST = 3             # consecutive final failed_transient outcomes that pause
STUCK_LIMIT = 3                 # consecutive stuck milestones that pause
_BLOCKED_EXCERPT = 300


@dataclass
class MemberState:
    label: str
    in_quorum: bool = True
    reported: bool = False   # its final outcome for this round is in
    retries: int = 0         # failed_transient re-queues (this round, or this assignment)
    assigned: bool = False   # holds an assignment in the adopted plan
    done: bool = False       # that assignment is done


@dataclass
class TeamState:
    phase: str
    round: int
    max_rounds: int
    members: dict[str, MemberState]
    round_members: list[str] = field(default_factory=list)
    approval_gate: bool = False
    paused_from: str | None = None
    stuck_count: int = 0
    transient_streak: int = 0
    pending_assignees: tuple[str, ...] = ()   # the adopted plan's assignees, awaiting approval

    def quorum(self) -> list[str]:
        return [label for label, m in self.members.items() if m.in_quorum]


# ── events ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Kickoff:
    kind: str                     # "proposal" | "post"
    mentions: tuple[str, ...]


@dataclass(frozen=True)
class MemberReported:
    label: str
    status: str
    stop_reason: str | None = None
    files: tuple[str, ...] = ()
    report: str = ""


@dataclass(frozen=True)
class RoundEvaluated:
    adopted: str | None
    assignees: tuple[str, ...] = ()


@dataclass(frozen=True)
class MainAdopt:
    proposal_id: str
    assignees: tuple[str, ...] = ()


@dataclass(frozen=True)
class MainPost:
    pass


@dataclass(frozen=True)
class Disband:
    pass


@dataclass(frozen=True)
class Approval:
    decision: str                 # "approve" | "feedback" | "reject"


@dataclass(frozen=True)
class BudgetExhausted:
    pass


@dataclass(frozen=True)
class Stuck:
    idle: tuple[tuple[str, str], ...]   # (label, last status) of each idle member


@dataclass(frozen=True)
class MainResume:
    pass


Event = (Kickoff | MemberReported | RoundEvaluated | MainAdopt | MainPost | Disband | Approval
         | BudgetExhausted | Stuck | MainResume)


# ── actions ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StartRound:
    round: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Requeue:
    label: str
    retry: int
    after_s: float


@dataclass(frozen=True)
class EvaluateRound:
    round: int


@dataclass(frozen=True)
class Adopt:
    proposal_id: str
    by: str                       # "team" | "main"
    close: bool = True            # False while the plan waits for the user's approval


@dataclass(frozen=True)
class Milestone:
    kind: str
    data: dict[str, object]


@dataclass(frozen=True)
class SetQuorum:
    label: str
    in_quorum: bool


@dataclass(frozen=True)
class EnterPhase:
    phase: str
    round: int
    reason: str = ""


@dataclass(frozen=True)
class End:
    phase: str                    # DONE | DISBANDED | FAILED
    reason: str


@dataclass(frozen=True)
class RaisePlanGate:
    proposal_id: str


@dataclass(frozen=True)
class ClosePlan:
    reason: str                   # "adopted" | "feedback"


@dataclass(frozen=True)
class StartImplementation:
    labels: tuple[str, ...]


@dataclass(frozen=True)
class AssignmentDone:
    label: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class ResumeMembers:
    labels: tuple[str, ...]


@dataclass(frozen=True)
class CancelTimers:
    pass


@dataclass(frozen=True)
class ForceFinalAll:
    pass


Action = (StartRound | Requeue | EvaluateRound | Adopt | Milestone | SetQuorum | EnterPhase
          | End | RaisePlanGate | ClosePlan | StartImplementation | AssignmentDone
          | ResumeMembers | CancelTimers | ForceFinalAll)


def _start_round(state: TeamState, labels: list[str]) -> list[Action]:
    state.phase = "DELIBERATING"
    state.round_members = labels
    for member in state.members.values():
        member.reported = False
        member.retries = 0
    return [StartRound(state.round, tuple(labels))]


def _implement(state: TeamState, assignees: tuple[str, ...]) -> list[Action]:
    state.phase = "IMPLEMENTING"
    state.pending_assignees = ()
    state.stuck_count = 0
    for label, member in state.members.items():
        member.assigned = label in assignees
        member.done = False
        member.retries = 0
    return [EnterPhase("IMPLEMENTING", state.round), StartImplementation(assignees)]


def _adopt(state: TeamState, proposal_id: str, by: str,
           assignees: tuple[str, ...]) -> list[Action]:
    known = tuple(lb for lb in assignees if lb in state.members)
    if not known:
        # Nothing to implement: the plan is the deliverable (5A's behavior).
        state.phase = "DONE"
        return [Adopt(proposal_id, by), End("DONE", "adopted")]
    if state.approval_gate:
        state.phase = "AWAITING_APPROVAL"
        state.pending_assignees = known
        return [Adopt(proposal_id, by, close=False), EnterPhase("AWAITING_APPROVAL", state.round),
                RaisePlanGate(proposal_id),
                Milestone("approval_needed", {"proposal_id": proposal_id})]
    return [Adopt(proposal_id, by), *_implement(state, known)]


def _pause(state: TeamState, reason: str) -> list[Action]:
    state.paused_from = state.phase
    state.phase = "PAUSED"
    actions: list[Action] = [
        CancelTimers(), EnterPhase("PAUSED", state.round, reason),
        Milestone("paused", {"reason": reason, "paused_from": state.paused_from})]
    if reason == "budget":
        actions.append(ForceFinalAll())
    return actions


def _owing(state: TeamState, phase: str) -> tuple[str, ...]:
    if phase == "DELIBERATING":
        return tuple(lb for lb in state.round_members
                     if state.members[lb].in_quorum and not state.members[lb].reported)
    if phase == "IMPLEMENTING":
        return tuple(lb for lb, m in state.members.items() if m.assigned and not m.done)
    return ()


def _resume(state: TeamState) -> list[Action]:
    phase = state.paused_from or "DELIBERATING"
    state.phase = phase
    state.paused_from = None
    state.stuck_count = 0
    state.transient_streak = 0
    owing = _owing(state, phase)
    actions: list[Action] = [EnterPhase(phase, state.round, "resumed")]
    if phase == "DELIBERATING" and not owing:
        return [*actions, EvaluateRound(state.round)]
    if phase == "IMPLEMENTING" and not owing:
        return [*actions, *_all_done(state, [])]
    return [*actions, ResumeMembers(owing)] if owing else actions


def _all_done(state: TeamState, files: list[str]) -> list[Action]:
    # The coordinator widens `files` to every member's files when it runs the milestone.
    state.phase = "DONE"
    return [Milestone("done", {"files": files}), End("DONE", "implemented")]


def _final_transient(state: TeamState, member: MemberState, event: MemberReported,
                     ) -> tuple[list[Action], bool]:
    """A failed_transient outcome: re-queue it, or count it as final. Returns the actions
    and whether the outcome was final (the caller continues only when it was)."""
    if state.phase == "PAUSED":
        return [], False                     # no re-queue while paused: resume restarts it
    if member.retries < len(RETRY_BACKOFF_S):
        member.retries += 1
        return [Requeue(event.label, member.retries, RETRY_BACKOFF_S[member.retries - 1])], False
    member.retries = 0
    state.transient_streak += 1
    if state.transient_streak >= TRANSIENT_BURST:
        return _pause(state, "transient_burst"), False
    return [], True


def _deliberation_report(state: TeamState, event: MemberReported) -> list[Action]:
    member = state.members.get(event.label)
    if member is None or member.reported or event.label not in state.round_members:
        return []
    actions: list[Action] = []
    if event.status == "failed_transient":
        actions, final = _final_transient(state, member, event)
        if not final:
            return actions
    else:
        state.transient_streak = 0
    paused = state.phase == "PAUSED"
    if event.stop_reason == BUDGET_STOP:
        return actions                       # interrupted: the member still owes its stances
    member.reported = True
    lost = event.status == "failed" or (event.status == "stopped" and event.stop_reason == "user")
    if lost:
        member.in_quorum = False
        actions += [SetQuorum(event.label, False),
                    Milestone("member_lost", {"label": event.label, "status": event.status})]
        if len(state.quorum()) < 2:
            state.phase = "FAILED"
            return [*actions, End("FAILED", "fewer than 2 members left in the quorum")]
    waiting = [lb for lb in state.round_members
               if state.members[lb].in_quorum and not state.members[lb].reported]
    if not waiting and not paused:
        actions.append(EvaluateRound(state.round))
    return actions


def _implementation_report(state: TeamState, event: MemberReported) -> list[Action]:
    member = state.members.get(event.label)
    if member is None or not member.assigned or member.done:
        return []
    actions: list[Action] = []
    if event.status == "failed_transient":
        actions, final = _final_transient(state, member, event)
        if not final:
            return actions
    else:
        state.transient_streak = 0
        member.retries = 0
    if event.stop_reason == BUDGET_STOP:
        return actions                       # interrupted: the assignment stays open
    if event.status == "completed":
        member.done = True
        state.stuck_count = 0
        actions.append(AssignmentDone(event.label, event.files))
        if state.phase == "IMPLEMENTING" and all(m.done for m in state.members.values()
                                                 if m.assigned):
            return [*actions, *_all_done(state, sorted(event.files))]
        return actions
    if event.status in ("partial", "failed", "failed_transient") or (
            event.status == "stopped" and event.stop_reason == "user"):
        actions.append(Milestone("member_blocked", {
            "label": event.label, "status": event.status,
            "report": event.report[:_BLOCKED_EXCERPT]}))
    return actions


def apply(state: TeamState, event: Event) -> tuple[TeamState, list[Action]]:
    s = copy.deepcopy(state)
    if s.phase not in LIVE_PHASES:
        return s, []
    if isinstance(event, Disband):
        s.phase = "DISBANDED"
        return s, [End("DISBANDED", "disbanded")]
    if isinstance(event, Kickoff):
        mentioned = [lb for lb in event.mentions if lb in s.members and s.members[lb].in_quorum]
        labels = mentioned if event.kind == "post" and mentioned else s.quorum()
        return s, _start_round(s, labels)
    if isinstance(event, MainAdopt):
        if s.phase != "DEADLOCKED":
            return s, []
        return s, _adopt(s, event.proposal_id, "main", event.assignees)
    if isinstance(event, MainPost):
        if s.phase != "DEADLOCKED":
            return s, []
        s.round += 1
        s.max_rounds += 1
        return s, _start_round(s, s.quorum())
    if isinstance(event, RoundEvaluated):
        if s.phase != "DELIBERATING":
            return s, []
        if event.adopted is not None:
            return s, _adopt(s, event.adopted, "team", event.assignees)
        if s.round >= s.max_rounds:
            s.phase = "DEADLOCKED"
            return s, [EnterPhase("DEADLOCKED", s.round, "round limit"),
                       Milestone("deadlock", {"round": s.round})]
        s.round += 1
        return s, _start_round(s, s.quorum())
    if isinstance(event, Approval):
        if s.phase != "AWAITING_APPROVAL":
            return s, []
        if event.decision == "approve":
            return s, [ClosePlan("adopted"), *_implement(s, s.pending_assignees)]
        if event.decision == "feedback":
            s.pending_assignees = ()
            s.round += 1
            s.max_rounds += 1
            return s, [ClosePlan("feedback"), *_start_round(s, s.quorum())]
        s.phase = "DISBANDED"
        return s, [End("DISBANDED", "plan rejected")]
    if isinstance(event, BudgetExhausted):
        if s.phase == "PAUSED":
            return s, []
        return s, _pause(s, "budget")
    if isinstance(event, Stuck):
        if s.phase != "IMPLEMENTING":
            return s, []
        s.stuck_count += 1
        if s.stuck_count >= STUCK_LIMIT:
            return s, _pause(s, "stuck")
        return s, [Milestone("stuck", {"idle": [list(i) for i in event.idle],
                                       "count": s.stuck_count})]
    if isinstance(event, MainResume):
        return (s, _resume(s)) if s.phase == "PAUSED" else (s, [])
    assert isinstance(event, MemberReported)
    phase = s.paused_from if s.phase == "PAUSED" else s.phase
    if phase == "DELIBERATING":
        return s, _deliberation_report(s, event)
    if phase == "IMPLEMENTING":
        return s, _implementation_report(s, event)
    return s, []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_state_machine.py --color=no --timeout=120 > /tmp/sm.txt 2>&1; echo exit=$?; tail -5 /tmp/sm.txt`
Expected: PASS (5A's tests unchanged: `RoundEvaluated("P1")` with no assignees still ends `DONE`).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/state_machine.py services/agentd-py/tests/test_team_state_machine.py
git commit -m "feat(teams): approval, implementation, pause and resume rules"
```

### Task 2: Milestone texts and store helpers

**Files:**
- Modify: `services/agentd-py/agentd/teams/milestones.py`
- Modify: `services/agentd-py/agentd/teams/store.py`
- Test: `services/agentd-py/tests/test_team_milestones.py`, `services/agentd-py/tests/test_team_store.py`

**Interfaces:**
- Produces:
  - `milestone_text(team, kind, data)` for kinds `approval_needed`, `member_blocked`, `stuck`, `paused`, `done`; `adopted` accepts `data["next"]` in `{"approval", "implementing", "ended"}`.
  - `TeamStore.set_assignment(team_id, label, assignment: dict | None) -> None` (also clears `assignment_done`), `TeamStore.set_assignment_done(team_id, label) -> None`, `TeamStore.reset_wakes(team_id) -> None`, `TeamStore.add_usage(team_id, usage: Usage) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_milestones.py`:

```python
def test_adopted_body_names_what_happens_next() -> None:
    data = {"proposal_id": "P2", "by": "team",
            "assignments": [{"member": "alice", "part": "api", "files": ["a.py"]}]}
    _, implementing = milestone_text(_team(phase="IMPLEMENTING"), "adopted",
                                     {**data, "next": "implementing"})
    assert "The members are implementing it" in implementing
    _, ended = milestone_text(_team(phase="DONE"), "adopted", {**data, "next": "ended"})
    assert "no assignments" in ended


def test_approval_needed_and_paused_and_stuck() -> None:
    headline, body = milestone_text(_team(phase="AWAITING_APPROVAL"), "approval_needed",
                                    {"proposal_id": "P4"})
    assert headline == "Team 'auth' needs your approval for P4"
    assert "a card is waiting for the user" in body
    headline, body = milestone_text(_team(phase="PAUSED", paused_reason="budget"), "paused",
                                    {"reason": "budget", "paused_from": "IMPLEMENTING",
                                     "done": 1, "total": 3})
    assert headline == "Team 'auth' paused — its request budget ran out"
    assert "1 of 3 assignments done" in body and "resume_team" in body
    _, body = milestone_text(_team(phase="IMPLEMENTING"), "stuck",
                             {"idle": [["alice", "awaiting_peer"], ["bob", "partial"]],
                              "count": 2})
    assert "alice: awaiting_peer" in body and "bob: partial" in body and "2 of 3" in body


def test_member_blocked_quotes_300_chars_and_points_to_the_rest() -> None:
    _, body = milestone_text(_team(phase="IMPLEMENTING"), "member_blocked",
                             {"label": "bob", "status": "partial", "report": "y" * 300})
    assert "bob reported partial" in body and "y" * 300 in body
    assert "post_board" in body


def test_done_lists_files() -> None:
    headline, body = milestone_text(_team(phase="DONE"), "done", {"files": ["a.py", "b.py"]})
    assert headline == "Team 'auth' finished its assignments"
    assert "a.py, b.py" in body
```

Append to `tests/test_team_store.py` (reuse its `_store()` / `_team()` helpers; read the file's top for their names):

```python
def test_assignment_wakes_and_usage(tmp_path) -> None:
    teams = _store(tmp_path)
    team = _team()
    teams.create_team(team)
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="a1", label="alice"))
    teams.set_assignment(team.team_id, "alice", {"member": "alice", "part": "api",
                                                 "files": ["a.py"]})
    teams.bump_wakes(team.team_id, "alice")
    teams.set_assignment_done(team.team_id, "alice")
    member = teams.member(team.team_id, "alice")
    assert member.assignment["files"] == ["a.py"] and member.assignment_done
    teams.reset_wakes(team.team_id)
    assert teams.member(team.team_id, "alice").wakes_this_phase == 0
    teams.set_assignment(team.team_id, "alice", None)
    assert teams.member(team.team_id, "alice").assignment_done is False
    teams.add_usage(team.team_id, Usage(requests=3, prompt_tokens=10, completion_tokens=2))
    after = teams.get_team(team.team_id)
    assert (after.requests, after.prompt_tokens, after.completion_tokens) == (3, 10, 2)
```

(Add `from agentd.providers.usage import Usage` and `TeamMember` to the imports if missing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_milestones.py tests/test_team_store.py --color=no --timeout=120 > /tmp/m.txt 2>&1; echo exit=$?; tail -5 /tmp/m.txt`
Expected: FAIL — `AttributeError: 'TeamStore' object has no attribute 'set_assignment'` and the milestone assertions.

- [ ] **Step 3: Implement**

In `agentd/teams/store.py`, after `bump_wakes`, add (and `from agentd.providers.usage import Usage` at the top):

```python
    def set_assignment(self, team_id: str, label: str, assignment: dict[str, object] | None,
                       ) -> None:
        self._conn.execute(
            "UPDATE team_members SET assignment_json = ?, assignment_done = 0 "
            "WHERE team_id = ? AND label = ?",
            (json.dumps(assignment) if assignment is not None else None, team_id, label))
        self._conn.commit()

    def set_assignment_done(self, team_id: str, label: str) -> None:
        self._conn.execute(
            "UPDATE team_members SET assignment_done = 1 WHERE team_id = ? AND label = ?",
            (team_id, label))
        self._conn.commit()

    def reset_wakes(self, team_id: str) -> None:
        """The wake cap counts per phase (spec v2 §8.6)."""
        self._conn.execute("UPDATE team_members SET wakes_this_phase = 0 WHERE team_id = ?",
                           (team_id,))
        self._conn.commit()

    def add_usage(self, team_id: str, usage: Usage) -> None:
        """A team's usage is the sum over its members and their helpers (spec §3.11)."""
        self._conn.execute(
            "UPDATE teams SET requests = requests + ?, prompt_tokens = prompt_tokens + ?, "
            "completion_tokens = completion_tokens + ? WHERE team_id = ?",
            (usage.requests, usage.prompt_tokens, usage.completion_tokens, team_id))
        self._conn.commit()
```

In `agentd/teams/milestones.py`, replace the `adopted` branch's `details` and add the new kinds before the final `else`:

```python
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
```

(The existing `deadlock`, `member_lost` and `ended` branches stay; keep the `if/elif` chain order: `adopted`, `approval_needed`, `member_blocked`, `stuck`, `paused`, `done`, `deadlock`, `member_lost`, else.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_milestones.py tests/test_team_store.py --color=no --timeout=120 > /tmp/m.txt 2>&1; echo exit=$?; tail -5 /tmp/m.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/milestones.py services/agentd-py/agentd/teams/store.py services/agentd-py/tests/test_team_milestones.py services/agentd-py/tests/test_team_store.py
git commit -m "feat(teams): implementation milestones and assignment store helpers"
```

### Task 3: Assignment validation at propose time

**Files:**
- Modify: `services/agentd-py/agentd/teams/validation.py`
- Modify: `services/agentd-py/agentd/teams/service.py`
- Test: `services/agentd-py/tests/test_team_validation.py`, `services/agentd-py/tests/test_team_service.py`

**Interfaces:**
- Consumes: `agentd.chat.protected_paths.is_protected(key: str) -> bool`.
- Produces:
  - `validation.parse_assignments(raw: object, roster: list[str]) -> list[dict[str, object]]` — the shape check `prepare_propose` already did, moved here (`{member, part, files}`, member lower-cased).
  - `validation.check_assignments(parts, shared, *, workspace: Path, can_edit: Callable[[str], bool]) -> tuple[list[dict[str, object]], list[str]]` — returns the parts and shared files with **canonical** workspace-relative paths, or raises `TeamInputError`.
  - `TeamService(..., can_edit: Callable[[str, str], bool] = lambda _team, _label: True)` — new keyword argument; `prepare_propose` stores canonical paths.

Spec §8.3's "every part that edits lists ≥ 1 file" cannot be checked (a part's text does not say whether it edits); a part with no files is accepted as a non-editing part. Everything else in §8.3's list is checked.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_validation.py`:

```python
from agentd.teams.validation import check_assignments, parse_assignments


def _parts(*items):  # type: ignore[no-untyped-def]
    return [{"member": m, "part": "p", "files": list(f)} for m, f in items]


def test_parse_assignments_checks_shape_and_roster() -> None:
    assert parse_assignments([{"member": "@Alice", "part": "api", "files": ["a.py"]}],
                             ["alice"]) == [{"member": "alice", "part": "api", "files": ["a.py"]}]
    with pytest.raises(TeamInputError, match="unknown member 'carol'"):
        parse_assignments([{"member": "carol", "part": "x", "files": []}], ["alice"])
    with pytest.raises(TeamInputError, match="must be a list"):
        parse_assignments("nope", ["alice"])


def test_check_assignments_canonicalizes(tmp_path) -> None:
    parts, shared = check_assignments(_parts(("alice", ["src/../a.py"])), ["./b.py"],
                                      workspace=tmp_path, can_edit=lambda _m: True)
    assert parts[0]["files"] == ["a.py"] and shared == ["b.py"]


@pytest.mark.parametrize("parts,shared,message", [
    (_parts(("alice", ["a.py"]), ("bob", ["a.py"])), [], "assigned to both alice and bob"),
    (_parts(("alice", ["a.py"])), ["a.py"], "both assigned to alice and shared"),
    (_parts(("alice", ["../x.py"])), [], "outside the workspace"),
    (_parts(("alice", [".crucible/mcp.json"])), [], "protected"),
    (_parts(("alice", ["a.py"]), ("alice", ["b.py"])), [], "one assignment per member"),
])
def test_check_assignments_refusals(tmp_path, parts, shared, message) -> None:
    with pytest.raises(TeamInputError, match=message):
        check_assignments(parts, shared, workspace=tmp_path, can_edit=lambda _m: True)


def test_a_read_only_member_cannot_own_files(tmp_path) -> None:
    with pytest.raises(TeamInputError, match="review cannot edit files"):
        check_assignments(_parts(("review", ["a.py"])), [], workspace=tmp_path,
                          can_edit=lambda m: m != "review")
    # A part without files is fine for a read-only member.
    check_assignments(_parts(("review", [])), [], workspace=tmp_path,
                      can_edit=lambda m: m != "review")
```

Append to `tests/test_team_service.py` (its `_setup(tmp_path)` returns `(service, team_id, teams, posted)` with members `alice`, `bob`, `carol`):

```python
def test_propose_stores_canonical_paths_and_checks_can_edit(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    service = TeamService(teams, tmp_path / "ws", service._agent_info,
                          can_edit=lambda _team, label: label != "bob")
    post = service.propose(tid, "alice", "plan",
                           [{"member": "alice", "part": "api", "files": ["./src/auth.py"]}])
    assert post.payload["assignments"][0]["files"] == ["src/auth.py"]
    with pytest.raises(TeamInputError, match="bob cannot edit files"):
        service.propose(tid, "alice", "plan 2",
                        [{"member": "bob", "part": "tests", "files": ["t.py"]}],
                        supersedes=[post.proposal_id])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_validation.py tests/test_team_service.py --color=no --timeout=120 > /tmp/v.txt 2>&1; echo exit=$?; tail -5 /tmp/v.txt`
Expected: FAIL — `ImportError: cannot import name 'check_assignments'`.

- [ ] **Step 3: Implement**

In `agentd/teams/validation.py` add (imports: `from agentd.chat.protected_paths import is_protected`; `Callable` is already imported):

```python
def parse_assignments(raw: object, roster: list[str]) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        raise TeamInputError("assignments must be a list of {member, part, files}")
    parts: list[dict[str, object]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TeamInputError(f"assignments[{i}] must be an object")
        member = str(item.get("member", "")).lstrip("@").casefold()
        if member not in roster:
            raise TeamInputError(
                f"assignments[{i}]: unknown member {item.get('member')!r}; "
                f"the team is: {', '.join(roster)}")
        files = item.get("files") or []
        if not isinstance(files, list):
            raise TeamInputError(f"assignments[{i}].files must be a list of paths")
        parts.append({"member": member, "part": check_text(item.get("part"), "part"),
                      "files": [str(f) for f in files]})
    return parts


def _assignable(workspace: Path, raw: str) -> str:
    root = workspace.resolve()
    target = (root / raw).resolve()
    if target == root or root not in target.parents:
        raise TeamInputError(f"{raw!r} is outside the workspace")
    key = target.relative_to(root).as_posix()
    if is_protected(key):
        raise TeamInputError(f"{key} is a protected Crucible configuration file; no "
                             "assignment can include it")
    return key


def check_assignments(
    parts: list[dict[str, object]], shared: list[str], *, workspace: Path,
    can_edit: Callable[[str], bool],
) -> tuple[list[dict[str, object]], list[str]]:
    """Spec v2 §8.3: canonical paths inside the workspace, never protected, one owner per
    file, no file both owned and shared, and only members that can edit own files."""
    owner: dict[str, str] = {}
    seen_members: set[str] = set()
    out: list[dict[str, object]] = []
    for part in parts:
        member = str(part["member"])
        if member in seen_members:
            raise TeamInputError(f"one assignment per member: merge {member}'s parts into one")
        seen_members.add(member)
        raw_files = part.get("files")
        files = [_assignable(workspace, str(f)) for f in
                 (raw_files if isinstance(raw_files, list) else [])]
        if files and not can_edit(member):
            raise TeamInputError(
                f"{member} cannot edit files (its agent definition is read-only); give its "
                "files to a member that can edit, or give it a part without files")
        for key in files:
            if key in owner and owner[key] != member:
                raise TeamInputError(
                    f"{key} is assigned to both {owner[key]} and {member}; each file has one "
                    "owner (list it under shared_files to let both edit it)")
            owner[key] = member
        out.append({**part, "files": sorted(set(files), key=files.index)})
    shared_keys = [_assignable(workspace, str(f)) for f in shared]
    for key in shared_keys:
        if key in owner:
            raise TeamInputError(f"{key} is both assigned to {owner[key]} and shared; pick one")
    return out, sorted(set(shared_keys), key=shared_keys.index)
```

In `agentd/teams/service.py`:
- `TeamService.__init__` gains `can_edit: Callable[[str, str], bool] = lambda _team, _label: True` (keyword, after `on_activity`), stored as `self._can_edit`.
- In `prepare_propose`, replace the block from `if not isinstance(assignments, list):` through `shared = [str(f) for f in shared_files] if isinstance(shared_files, list) else []` with:

```python
        parts = parse_assignments(assignments, roster)
        raw_shared = [str(f) for f in shared_files] if isinstance(shared_files, list) else []
        parts, shared = check_assignments(
            parts, raw_shared, workspace=self._workspace,
            can_edit=lambda member: self._can_edit(team_id, member))
```

and import `check_assignments, parse_assignments` from `agentd.teams.validation`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_validation.py tests/test_team_service.py tests/test_team_report_fields.py --color=no --timeout=120 > /tmp/v.txt 2>&1; echo exit=$?; tail -5 /tmp/v.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/validation.py services/agentd-py/agentd/teams/service.py services/agentd-py/tests/test_team_validation.py services/agentd-py/tests/test_team_service.py
git commit -m "feat(teams): validate assignments when a plan is proposed"
```

---

# Part B — Loop and edit seams

### Task 4: Phase-aware budget hint, message tracking, inbox wakes

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py`
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py`
- Modify: `services/agentd-py/agentd/subagents/runtime.py`
- Modify: `services/agentd-py/agentd/teams/service.py`
- Test: `services/agentd-py/tests/test_controller_loop_report_check.py`, `services/agentd-py/tests/test_team_prompts.py`, `services/agentd-py/tests/test_team_service.py`

**Interfaces:**
- Produces:
  - `ControllerLoop.run(..., final_hint: Callable[[], str] | None = None)` (and `_iterate`); each iteration sets `plan_context["forced_final"] = self._force_final`, and on the final iteration `plan_context["team_final_hint"] = final_hint()` when given.
  - AGENT payload: on `iteration >= max_iters` **or** `plan_context["forced_final"]`, the hint is `⚠ BUDGET REACHED: emit type='report' NOW — <team_final_hint>.` when a team hint is present, else the existing text.
  - `AgentSupervisor.has_wakes(agent_id: str) -> bool`.
  - `ActivationCounters.messaged: list[tuple[str, int]]` (recipient label, post seq), appended by `TeamService.message` when counters are given.
  - `TeamService.final_hint(team_id: str, label: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_controller_loop_report_check.py`:

```python
@pytest.mark.asyncio
async def test_forced_final_carries_the_team_hint() -> None:
    contexts: list[dict[str, object]] = []

    class _Seeing(_Engine):
        async def create_controller_step(self, plan_context, history, tool_definitions,  # type: ignore[no-untyped-def]
                                         allowed_types=None, **k):
            contexts.append(dict(plan_context))
            return await super().create_controller_step(
                plan_context, history, tool_definitions, allowed_types, **k)

    loop = _loop(_Seeing([REPORT]))
    loop.force_final()
    await loop.run({"goal": "g"}, max_iters=10, final_hint=lambda: "post your proposal now")
    assert contexts[0]["forced_final"] is True
    assert contexts[0]["team_final_hint"] == "post your proposal now"
```

Append to `tests/test_team_prompts.py`:

```python
def test_forced_final_uses_the_team_hint() -> None:
    payload = build_controller_step_payload(
        {"goal": "g", "iteration": 3, "max_iters": 40, "forced_final": True,
         "team_final_hint": "state your stances now on P1"},
        [{"role": "user", "content": "x"}], [], phase="AGENT")
    assert "BUDGET REACHED: emit type='report' NOW — state your stances now on P1." in (
        payload["instruction"])
    plain = build_controller_step_payload(
        {"goal": "g", "iteration": 3, "max_iters": 40, "forced_final": True},
        [{"role": "user", "content": "x"}], [], phase="AGENT")
    assert "list what is unfinished under 'Unfinished'" in plain["instruction"]
```

Append to `tests/test_team_service.py`:

```python
def test_message_is_tracked_and_final_hint_by_phase(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    counters = ActivationCounters()
    post = service.message(tid, "alice", "bob", "which file?", counters)
    assert counters.messaged == [("bob", post.seq)]
    teams.update_team(tid, phase="IMPLEMENTING")
    assert service.final_hint(tid, "alice").startswith("finish or report partial")
    teams.update_team(tid, phase="DELIBERATING", round=2)
    teams.append_post(tid, author="bob", kind="proposal", text="P", round=1,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})
    assert "state your stances now" in service.final_hint(tid, "alice")
    assert service.final_hint(tid, "bob") == ""          # its own proposal: nothing owed
```

Append to `tests/test_agent_supervisor.py`:

```python
def test_has_wakes() -> None:
    sup = AgentSupervisor(max_concurrent=2)
    sup.keep("a1", [InboxItem(kind="team", text="", wakes=False)])
    assert sup.has_wakes("a1") is False
    sup.keep("a1", [InboxItem(kind="team", text="", wakes=True)])
    assert sup.has_wakes("a1") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_controller_loop_report_check.py tests/test_team_prompts.py tests/test_team_service.py tests/test_agent_supervisor.py --color=no --timeout=120 > /tmp/l.txt 2>&1; echo exit=$?; tail -5 /tmp/l.txt`
Expected: FAIL — `unexpected keyword argument 'final_hint'` and the hint assertion.

- [ ] **Step 3: Implement**

`agentd/chat/controller_loop.py`: add `final_hint: Callable[[], str] | None = None` to both `run(...)` and `_iterate(...)` signatures (next to `status_tail`), pass it through in `run`'s call to `_iterate`, and right after the `status_tail` block in `_iterate` add:

```python
            # Spec v2 §3.11: a forced final (deadline or budget) tells the model what its
            # phase needs before it reports.
            plan_context["forced_final"] = self._force_final
            if final_hint is not None and (iteration >= max_iters or self._force_final):
                plan_context["team_final_hint"] = final_hint()
```

`agentd/chat/controller_prompts.py`, AGENT branch — replace

```python
        elif iteration >= max_iters:
            hint = (
                "⚠ BUDGET REACHED: emit type='report' NOW with everything you found and changed, "
                "and list what is unfinished under 'Unfinished'.")
```

with

```python
        elif iteration >= max_iters or plan_context.get("forced_final"):
            team_hint = plan_context.get("team_final_hint")
            hint = (
                f"⚠ BUDGET REACHED: emit type='report' NOW — {team_hint}."
                if isinstance(team_hint, str) and team_hint else
                "⚠ BUDGET REACHED: emit type='report' NOW with everything you found and changed, "
                "and list what is unfinished under 'Unfinished'.")
```

`agentd/subagents/runtime.py`, after `has_pending_report`:

```python
    def has_wakes(self, agent_id: str) -> bool:
        """Input waiting that will re-activate the agent when its activation ends (§3.6)."""
        return any(i.wakes for i in self._inboxes.get(agent_id, []))
```

`agentd/teams/service.py`:
- `ActivationCounters` gains `messaged: list[tuple[str, int]] = field(default_factory=list)` (import `field` from `dataclasses`).
- In `message(...)`, after the post is stored and before `return`, record it:

```python
        post = self._store.append_post(
            team_id, author=author, kind="post", text=body, recipient=target,
            mentions=[target])
        if counters is not None:
            counters.messaged.append((target, post.seq))
        return self._emit(team, post)
```

- Add:

```python
    def final_hint(self, team_id: str, label: str) -> str:
        """What the phase needs before a forced report (spec v2 §3.11)."""
        team = self._store.get_team(team_id)
        if team is None:
            return ""
        if team.phase == "IMPLEMENTING":
            return ("finish or report partial, listing what is left under 'Unfinished' — "
                    "your assignment stays open for the next activation")
        if team.phase != "DELIBERATING":
            return ""
        kickoff = self._store.get_post(team_id, 1)
        asked = (team.round == 1 and kickoff is not None and kickoff.kind == "post"
                 and label in kickoff.mentions
                 and not any(p.author == label and p.kind == "proposal"
                             for p in self._store.posts(team_id)))
        if asked:
            return "post your proposal now, in this report's proposal field"
        owed = self.expected_stances(team_id, label)
        if owed:
            return (f"state your stances now on {', '.join(owed)}, in this report's "
                    "stances field")
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_controller_loop_report_check.py tests/test_team_prompts.py tests/test_team_service.py tests/test_prompt_goldens_teams.py --color=no --timeout=120 > /tmp/l.txt 2>&1; echo exit=$?; tail -5 /tmp/l.txt`
Expected: PASS (the goldens pin the system prompt, which this task does not change).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/agentd/subagents/runtime.py services/agentd-py/agentd/teams/service.py services/agentd-py/tests/
git commit -m "feat(teams): phase-aware budget hint, message tracking, inbox wakes"
```

### Task 5: Implementation report checks

**Files:**
- Create: `services/agentd-py/agentd/teams/implementation.py`
- Test: `services/agentd-py/tests/test_team_implementation_report.py`

**Interfaces:**
- Consumes: `ReportVerdict` (loop), `TeamStore`, `ActivationCounters.messaged` (Task 4).
- Produces:
  - `ImplementationReport(store: TeamStore, team_id: str, label: str, counters: ActivationCounters, changed_files: Callable[[], set[str]])` — a `report_check`; acts only while the team is `IMPLEMENTING` and the iteration is not final; redirects at most once per activation.
  - `chain_checks(*checks: ReportCheck) -> ReportCheck` — the first refusal wins.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_implementation_report.py`:

```python
"""Implementation reports are checked before they are accepted (spec v2 §8.6 step 4)."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from agentd.chat.controller_loop import ReportVerdict
from agentd.teams.implementation import ImplementationReport, chain_checks
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import ActivationCounters
from agentd.teams.store import TeamStore

COMPLETED = {"type": "report", "summary": "s", "status": "completed"}
WAITING = {"type": "report", "summary": "s", "status": "awaiting_peer"}


def _store() -> TeamStore:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    store.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                                 max_rounds=3, budget=100, created_turn_id="u",
                                 created_at=datetime.now(UTC), phase="IMPLEMENTING"))
    for label in ("alice", "bob"):
        store.add_member(TeamMember(team_id="team-1", agent_id=f"a-{label}", label=label))
    store.set_assignment("team-1", "alice", {"member": "alice", "part": "api",
                                             "files": ["a.py"]})
    store.set_assignment("team-1", "bob", {"member": "bob", "part": "tests", "files": ["t.py"]})
    return store


def test_completed_with_unchanged_files_is_redirected_once() -> None:
    store = _store()
    check = ImplementationReport(store, "team-1", "alice", ActivationCounters(), lambda: set())
    first = check(COMPLETED, False)
    assert first.message is not None and "Your assignment's files are unchanged" in first.message
    assert check(COMPLETED, False) == ReportVerdict()          # second attempt accepted


def test_completed_with_changes_is_accepted() -> None:
    check = ImplementationReport(_store(), "team-1", "alice", ActivationCounters(),
                                 lambda: {"a.py"})
    assert check(COMPLETED, False) == ReportVerdict()


def test_completed_while_waiting_on_a_reply_is_redirected() -> None:
    store = _store()
    counters = ActivationCounters()
    sent = store.append_post("team-1", author="alice", kind="post", text="?",
                             recipient="bob", mentions=["bob"])
    counters.messaged.append(("bob", sent.seq))
    check = ImplementationReport(store, "team-1", "alice", counters, lambda: {"a.py"})
    verdict = check(COMPLETED, False)
    assert verdict.message is not None and "You are waiting on bob" in verdict.message


def test_a_reply_clears_the_wait() -> None:
    store = _store()
    counters = ActivationCounters()
    sent = store.append_post("team-1", author="alice", kind="post", text="?",
                             recipient="bob", mentions=["bob"])
    counters.messaged.append(("bob", sent.seq))
    store.append_post("team-1", author="bob", kind="post", text="done", recipient="alice",
                      mentions=["alice"])
    check = ImplementationReport(store, "team-1", "alice", counters, lambda: {"a.py"})
    assert check(COMPLETED, False) == ReportVerdict()


def test_awaiting_peer_with_nobody_to_wait_on_is_redirected() -> None:
    store = _store()
    store.set_assignment_done("team-1", "bob")
    check = ImplementationReport(store, "team-1", "alice", ActivationCounters(), lambda: set())
    verdict = check(WAITING, False)
    assert verdict.message is not None and "You are not waiting on anyone" in verdict.message


def test_awaiting_peer_while_another_member_works_is_accepted() -> None:
    check = ImplementationReport(_store(), "team-1", "alice", ActivationCounters(),
                                 lambda: set())
    assert check(WAITING, False) == ReportVerdict()


def test_final_iteration_and_other_phases_pass() -> None:
    store = _store()
    check = ImplementationReport(store, "team-1", "alice", ActivationCounters(), lambda: set())
    assert check(COMPLETED, True) == ReportVerdict()
    store.update_team("team-1", phase="DELIBERATING")
    assert check(COMPLETED, False) == ReportVerdict()


def test_chain_takes_the_first_refusal() -> None:
    calls: list[str] = []

    def no(_r, _f):  # type: ignore[no-untyped-def]
        calls.append("no")
        return ReportVerdict(message="no")

    def yes(_r, _f):  # type: ignore[no-untyped-def]
        calls.append("yes")
        return ReportVerdict()

    assert chain_checks(yes, no)(COMPLETED, False).message == "no"
    assert chain_checks(no, yes)(COMPLETED, False).message == "no"
    assert calls == ["yes", "no", "no"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_team_implementation_report.py --color=no --timeout=120 > /tmp/i.txt 2>&1; echo exit=$?; tail -5 /tmp/i.txt`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.teams.implementation'`.

- [ ] **Step 3: Implement**

Create `agentd/teams/implementation.py`:

```python
"""Implementation reports (spec v2 §8.6 step 4): a `completed` that changed none of the
member's files, or that leaves a direct message unanswered, and an `awaiting_peer` with
nobody to wait on, are sent back once. Redirects are not malformed actions."""
from __future__ import annotations

from collections.abc import Callable

from agentd.chat.controller_loop import ReportVerdict
from agentd.teams.service import ActivationCounters
from agentd.teams.store import TeamStore

Check = Callable[[dict[str, object], bool], ReportVerdict]


def chain_checks(*checks: Check) -> Check:
    def run(resp: dict[str, object], final: bool) -> ReportVerdict:
        for check in checks:
            verdict = check(resp, final)
            if verdict.message is not None:
                return verdict
        return ReportVerdict()
    return run


class ImplementationReport:
    """One per member activation; the redirect is used at most once."""

    def __init__(self, store: TeamStore, team_id: str, label: str,
                 counters: ActivationCounters, changed_files: Callable[[], set[str]]) -> None:
        self._store = store
        self._team_id = team_id
        self._label = label
        self._counters = counters
        self._changed = changed_files
        self._redirected = False

    def _waiting_on(self) -> list[str]:
        posts = self._store.posts(self._team_id)
        waiting: list[str] = []
        for to, seq in self._counters.messaged:
            replied = any(p.author == to and p.seq > seq and (
                p.recipient == self._label or self._label in p.mentions) for p in posts)
            if not replied and to not in waiting:
                waiting.append(to)
        return waiting

    def __call__(self, resp: dict[str, object], final: bool) -> ReportVerdict:
        team = self._store.get_team(self._team_id)
        if final or self._redirected or team is None or team.phase != "IMPLEMENTING":
            return ReportVerdict()
        member = self._store.member(self._team_id, self._label)
        if member is None:
            return ReportVerdict()
        status = str(resp.get("status") or "completed")
        waiting = self._waiting_on()
        if status == "completed" and member.assignment and not member.assignment_done:
            owned = {str(f) for f in member.assignment.get("files", [])}
            unchanged = bool(owned) and not (owned & self._changed())
            if waiting or unchanged:
                self._redirected = True
                reason = (f"You are waiting on {', '.join(waiting)}" if waiting
                          else "Your assignment's files are unchanged")
                return ReportVerdict(message=(
                    f"{reason} — report awaiting_peer or partial instead, or finish the "
                    "work, then report again."))
        if status == "awaiting_peer" and not waiting:
            others_open = any(m.assignment and not m.assignment_done
                              for m in self._store.members(self._team_id)
                              if m.label != self._label)
            if not others_open:
                self._redirected = True
                return ReportVerdict(message=(
                    "You are not waiting on anyone — finish your part or report partial."))
        return ReportVerdict()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_team_implementation_report.py --color=no --timeout=120 > /tmp/i.txt 2>&1; echo exit=$?; tail -5 /tmp/i.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/implementation.py services/agentd-py/tests/test_team_implementation_report.py
git commit -m "feat(teams): check implementation reports before accepting them"
```

### Task 6: Edits bounded by the team's phase and plan (Check 1)

**Files:**
- Create: `services/agentd-py/agentd/teams/scope.py`
- Modify: `services/agentd-py/agentd/domain/models.py`
- Modify: `services/agentd-py/agentd/chat/protected_paths.py`
- Modify: `services/agentd-py/agentd/chat/controller_loop.py`
- Test: `services/agentd-py/tests/test_team_scope.py`

**Interfaces:**
- Produces:
  - `scope.team_edit_refusal(team: TeamRecord, members: list[TeamMember], label: str, path: str, shared: set[str]) -> str | None`.
  - `PatchFailureCode.TEAM_SCOPE = "team_scope"` with guidance in `_EDIT_GUIDANCE_BY_CODE`.
  - `protected_paths.TeamScopeError(PatchPreflightFailed)` (`.path`), and `TeamProtection(rule: Callable[[str], str | None])` — an `AgentProtection` whose `check_apply` also applies `rule` per canonical key; a repeated ownership refusal for the same path adds `You were already told this file is owned by <label>. Do not retry the edit.`

Messages are spec §3.9/§8.6 verbatim: `team phase <X> does not allow edits`, `<path> is owned by <label> — team_message <label> instead`, `<path> is not in the approved plan — tell the main agent`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_scope.py`:

```python
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
    rule = lambda key: f"{key} is owned by bob — team_message bob instead" if key == "t.py" else None  # noqa: E731
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_team_scope.py --color=no --timeout=120 > /tmp/s.txt 2>&1; echo exit=$?; tail -5 /tmp/s.txt`
Expected: FAIL — `ImportError: cannot import name 'TeamProtection'`.

- [ ] **Step 3: Implement**

`agentd/domain/models.py`, in `PatchFailureCode` after `PROTECTED_PATH`:

```python
    TEAM_SCOPE = "team_scope"
```

Create `agentd/teams/scope.py`:

```python
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
```

`agentd/chat/protected_paths.py` — add `from collections.abc import Callable` and, after `AgentProtection`:

```python
class TeamScopeError(PatchPreflightFailed):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(message, [PatchPreflightIssue(
            code=PatchFailureCode.TEAM_SCOPE, file=path, message=message)])
        self.path = path


class TeamProtection(AgentProtection):
    """A team member or its helper (spec v2 §3.9, §8.6): protected paths first, then the
    team's phase and plan, checked at Check 1 before any edit gate. One per activation, so
    a repeated ownership refusal for the same path can say so."""

    def __init__(self, rule: Callable[[str], str | None]) -> None:
        self._rule = rule
        self._told: set[str] = set()

    def check_apply(self, keys: list[str]) -> None:
        super().check_apply(keys)
        for key in keys:
            message = self._rule(key)
            if message is None:
                continue
            if " is owned by " in message:
                if key in self._told:
                    owner = message.split(" is owned by ", 1)[1].split(" ", 1)[0]
                    message += (f" You were already told this file is owned by {owner}. "
                                "Do not retry the edit.")
                self._told.add(key)
            raise TeamScopeError(key, message)
```

`agentd/chat/controller_loop.py`, in `_EDIT_GUIDANCE_BY_CODE` after the `PROTECTED_PATH` entry:

```python
    PatchFailureCode.TEAM_SCOPE: (
        "Your team's plan decides who edits which file. Send the change to the file's owner "
        "with team_message, or tell the main agent with team_post, instead of editing."),
```

(The apply-time failure branch already appends `_edit_failure_guidance(exc)` to `PATCH FAILED: …`, so no loop change beyond the entry is needed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_team_scope.py tests/test_protected_paths.py --color=no --timeout=120 > /tmp/s.txt 2>&1; echo exit=$?; tail -5 /tmp/s.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/scope.py services/agentd-py/agentd/domain/models.py services/agentd-py/agentd/chat/protected_paths.py services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/tests/test_team_scope.py
git commit -m "feat(teams): bound member edits by the team's phase and plan"
```

---

# Part C — The coordinator

### Task 7: Coordinator — approval, implementation, stuck, budget, pause and resume

**Files:**
- Modify: `services/agentd-py/agentd/teams/coordinator.py`
- Test: `services/agentd-py/tests/test_team_coordinator.py`

**Interfaces:**
- Consumes: Task 1's events/actions, Task 2's store helpers and milestone kinds.
- Produces (`CoordinatorHost` gains, `ChatController` implements them in Task 8):
  - `start_member(team_id, label, extra: str = "") -> None` — `extra` is appended after the board delta in the activation input.
  - `team_milestone(team, kind, headline, body, delivery: str = "wake") -> None`.
  - `raise_plan_gate(team: TeamRecord, proposal: TeamPost) -> None`.
  - `force_team_final(team_id) -> list[str]` — forces every running member and helper loop of the team; returns the member labels whose own loop was forced.
  - `team_requests(team_id) -> int` — persisted team requests plus the in-flight requests of its running agents.
  - `is_busy(team_id, label) -> bool` — the member is queued/running or has waking input in its inbox.
  - `has_wakes(team_id, label) -> bool` — waking input waits in the member's inbox.
- Produces (`TeamCoordinator`):
  - `thread_id` property; `on_report(label, status, stop_reason, *, files=(), report="")`; `approval(decision: str, feedback: str | None)`; `check_budget()`; `resume(extra_budget: int)`; `user_spoke()`.
  - `assignment_text(assignment, shared) -> str` (module function): `Your assignment: <part>. Files you own: <files>. Shared files: <files>.`

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_coordinator.py`, replace `_Host` and `_setup` with:

```python
class _Host:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.extras: dict[str, str] = {}
        self.stopped: list[tuple[str, str]] = []
        self.forced: list[str] = []
        self.milestones: list[str] = []
        self.deliveries: list[tuple[str, str]] = []
        self.phases: list[str] = []
        self.active: dict[str, float | None] = {}
        self.woken: list[int] = []
        self.gates: list[str] = []
        self.busy: set[str] = set()
        self.wakes: set[str] = set()
        self.requests = 0

    def start_member(self, team_id: str, label: str, extra: str = "") -> None:
        self.started.append(label)
        self.extras[label] = extra

    async def stop_member(self, team_id: str, label: str, reason: str) -> None:
        self.stopped.append((label, reason))

    def force_final(self, team_id: str, label: str) -> bool:
        self.forced.append(label)
        return True

    def active_seconds(self, team_id: str, label: str) -> float | None:
        return self.active.get(label, 0.0)

    def team_milestone(self, team, kind, headline, body, delivery="wake") -> None:  # type: ignore[no-untyped-def]
        self.milestones.append(kind)
        self.deliveries.append((kind, delivery))

    def team_phase_changed(self, team) -> None:  # type: ignore[no-untyped-def]
        self.phases.append(team.phase)

    def wake_for_post(self, team, post) -> None:  # type: ignore[no-untyped-def]
        self.woken.append(post.seq)

    def raise_plan_gate(self, team, proposal) -> None:  # type: ignore[no-untyped-def]
        self.gates.append(proposal.proposal_id)

    def force_team_final(self, team_id: str) -> list[str]:
        return sorted(self.busy)

    def team_requests(self, team_id: str) -> int:
        return self.requests

    def is_busy(self, team_id: str, label: str) -> bool:
        return label in self.busy

    def has_wakes(self, team_id: str, label: str) -> bool:
        return label in self.wakes


def _setup(tmp_path: Path, *, max_rounds: int = 3, timeout: float = 900.0,
           labels: tuple[str, ...] = ("alice", "bob"), approval_gate: bool = False,
           assignments: list[dict[str, object]] | None = None, shared: list[str] | None = None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    store.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                                 max_rounds=max_rounds, budget=100, created_turn_id="u",
                                 approval_gate=approval_gate, created_at=datetime.now(UTC)))
    for label in labels:
        store.add_member(TeamMember(team_id="team-1", agent_id=f"agent-{label}", label=label))
    svc = TeamService(store, tmp_path, lambda _a: AgentInfo("gp", "", "idle"))
    host = _Host()
    trace_path = tmp_path / "coordinator.jsonl"
    coord = TeamCoordinator("team-1", store, svc, host, CoordinatorTrace(trace_path),
                            round_timeout_s=timeout, grace_s=0.05)
    store.append_post("team-1", author="main", kind="proposal", text="P1", round=0,
                      payload={"assignments": assignments or [], "shared_files": shared or [],
                               "supersedes": []})
    return store, svc, host, coord, trace_path


PARTS = [{"member": "alice", "part": "api", "files": ["a.py"]},
         {"member": "bob", "part": "tests", "files": ["t.py"]}]


def _adopt_round_one(store, svc, coord, labels=("alice", "bob")) -> None:  # type: ignore[no-untyped-def]
    coord.kickoff("proposal", [])
    for label in labels:
        svc.agree("team-1", label, "P1")
    for label in labels:
        coord.on_report(label, "completed", None)
```

Append the tests:

```python
@pytest.mark.asyncio
async def test_adoption_hands_out_assignments(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, shared=["s.py"])
    _adopt_round_one(store, svc, coord)
    team = store.get_team("team-1")
    assert team.phase == "IMPLEMENTING" and store.get_post("team-1", 1).closed == "adopted"
    assert store.member("team-1", "alice").assignment["files"] == ["a.py"]
    assert host.started[-2:] == ["alice", "bob"]
    assert host.extras["alice"] == ("Your assignment: api. Files you own: a.py. "
                                    "Shared files: s.py.")
    assert host.milestones == ["adopted"]
    coord.close()


@pytest.mark.asyncio
async def test_approval_gate_waits_then_approve(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True)
    _adopt_round_one(store, svc, coord)
    team = store.get_team("team-1")
    assert (team.phase, team.adopted_proposal_id) == ("AWAITING_APPROVAL", "P1")
    assert store.get_post("team-1", 1).closed is None
    assert host.gates == ["P1"] and host.milestones == ["approval_needed"]
    coord.approval("approve", None)
    assert store.get_team("team-1").phase == "IMPLEMENTING"
    assert store.get_post("team-1", 1).closed == "adopted"
    assert any(p.text == "The user approved P1." for p in store.posts("team-1"))
    coord.close()


@pytest.mark.asyncio
async def test_feedback_reopens_deliberation(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True,
                                        max_rounds=1)
    _adopt_round_one(store, svc, coord)
    coord.approval("feedback", "Use redis, not memory")
    team = store.get_team("team-1")
    assert (team.phase, team.round, team.max_rounds, team.adopted_proposal_id) == (
        "DELIBERATING", 2, 2, None)
    assert store.get_post("team-1", 1).closed == "feedback"
    feedback = next(p for p in store.posts("team-1") if p.author == "user")
    assert feedback.text == "Use redis, not memory"
    assert coord.delta_cutoff() >= feedback.seq                 # round 2's input includes it
    coord.close()


@pytest.mark.asyncio
async def test_reject_disbands(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True)
    _adopt_round_one(store, svc, coord)
    coord.approval("reject", None)
    team = store.get_team("team-1")
    assert (team.phase, team.end_reason) == ("DISBANDED", "plan rejected")


@pytest.mark.asyncio
async def test_assignments_done_end_implemented(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    coord.on_report("alice", "completed", None, files=("a.py",))
    assert store.member("team-1", "alice").assignment_done
    assert any(p.text == '● alice finished "api" — files: a.py' for p in store.posts("team-1"))
    coord.on_report("bob", "completed", None, files=("t.py",))
    team = store.get_team("team-1")
    assert (team.phase, team.end_reason) == ("DONE", "implemented")
    assert host.milestones[-1] == "done"


@pytest.mark.asyncio
async def test_two_waiting_members_are_stuck(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    host.busy = {"bob"}                       # bob still running when alice reports
    coord.on_report("alice", "awaiting_peer", None)
    assert "stuck" not in host.milestones
    host.busy = {"bob"}                       # bob is the reporter: still inside its task
    coord.on_report("bob", "awaiting_peer", None)
    assert host.milestones[-1] == "stuck"
    assert store.get_team("team-1").stuck_count == 1
    coord.close()


@pytest.mark.asyncio
async def test_a_waking_reporter_is_not_stuck(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    host.busy = {"bob"}
    coord.on_report("alice", "awaiting_peer", None)
    host.busy, host.wakes = {"bob"}, {"bob"}   # a message reached bob while it ran
    coord.on_report("bob", "awaiting_peer", None)
    assert "stuck" not in host.milestones
    coord.close()


@pytest.mark.asyncio
async def test_budget_pause_forces_final_and_resume_restarts_owing(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    _adopt_round_one(store, svc, coord)
    host.requests, host.busy = 100, {"alice", "bob"}
    coord.check_budget()
    team = store.get_team("team-1")
    assert (team.phase, team.paused_reason) == ("PAUSED", "budget")
    assert host.milestones[-1] == "paused"
    coord.on_report("alice", "partial", None)                   # forced → interrupted
    coord.on_report("bob", "completed", None, files=("t.py",))  # finished for real
    assert "member_blocked" not in host.milestones
    assert not store.member("team-1", "alice").assignment_done
    assert store.member("team-1", "bob").assignment_done
    host.started.clear()
    coord.resume(50)
    team = store.get_team("team-1")
    assert (team.phase, team.budget, team.paused_reason) == ("IMPLEMENTING", 150, None)
    assert host.started == ["alice"]
    coord.close()


@pytest.mark.asyncio
async def test_transient_burst_pauses_with_a_notify(tmp_path) -> None:
    labels = ("alice", "bob", "carol")
    parts = [{"member": lb, "part": lb, "files": [f"{lb}.py"]} for lb in labels]
    store, svc, host, coord, _ = _setup(tmp_path, assignments=parts, labels=labels)
    _adopt_round_one(store, svc, coord, labels)
    for label in labels:
        for _ in range(3):
            coord.on_report(label, "failed_transient", None)
    team = store.get_team("team-1")
    assert (team.phase, team.paused_reason) == ("PAUSED", "transient_burst")
    assert host.deliveries[-1] == ("paused", "notify")
    coord.user_spoke()
    coord.resume(10)
    assert store.get_team("team-1").phase == "IMPLEMENTING"
    coord.close()


@pytest.mark.asyncio
async def test_posts_wake_only_while_implementing(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, approval_gate=True)
    _adopt_round_one(store, svc, coord)
    coord.on_post(store.append_post("team-1", author="main", kind="post", text="@alice hi",
                                    mentions=["alice"]))
    assert host.woken == []                                     # awaiting approval
    coord.approval("approve", None)
    post = store.append_post("team-1", author="main", kind="post", text="@alice hi",
                             mentions=["alice"])
    coord.on_post(post)
    assert host.woken == [post.seq]
    coord.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_coordinator.py --color=no --timeout=120 > /tmp/c.txt 2>&1; echo exit=$?; tail -5 /tmp/c.txt`
Expected: FAIL — `AttributeError: 'TeamCoordinator' object has no attribute 'approval'` (and the new tests' assertions).

- [ ] **Step 3: Implement**

Replace `agentd/teams/coordinator.py` with:

```python
"""TeamCoordinator (spec v2 §8.1): one per live team. Feeds events into the pure state
machine, persists its state, and executes its actions through the host (ChatController).
Every method is synchronous apart from disband, so a caller never interleaves with it."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from agentd.teams import state_machine as sm
from agentd.teams.adoption import evaluate_round
from agentd.teams.milestones import milestone_text
from agentd.teams.models import TeamMember, TeamPost, TeamRecord
from agentd.teams.service import TeamService
from agentd.teams.store import TeamStore
from agentd.teams.trace import CoordinatorTrace

logger = logging.getLogger(__name__)
_FORCE_RETRY_S = 5.0   # the member's loop was not built yet: look again shortly


class CoordinatorHost(Protocol):
    def start_member(self, team_id: str, label: str, extra: str = "") -> None: ...
    async def stop_member(self, team_id: str, label: str, reason: str) -> None: ...
    def force_final(self, team_id: str, label: str) -> bool: ...
    def active_seconds(self, team_id: str, label: str) -> float | None: ...
    def team_milestone(self, team: TeamRecord, kind: str, headline: str, body: str,
                       delivery: str = "wake") -> None: ...
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
        delivery = "notify" if self._suppress_wakes else "wake"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_coordinator.py tests/test_team_state_machine.py --color=no --timeout=120 > /tmp/c.txt 2>&1; echo exit=$?; tail -5 /tmp/c.txt`
Expected: PASS (5A's coordinator tests are unchanged: their kickoff proposals have no assignments, so adoption still ends `DONE`).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/coordinator.py services/agentd-py/tests/test_team_coordinator.py
git commit -m "feat(teams): coordinator runs approval, implementation, pauses and resume"
```

---

# Part D — Wiring

### Task 8: The controller runs implementation — host methods, edits, reports, budget, resume, the plan card

**Files:**
- Modify: `services/agentd-py/agentd/chat/models.py`
- Modify: `services/agentd-py/agentd/chat/storage.py`
- Modify: `services/agentd-py/agentd/teams/validation.py`
- Modify: `services/agentd-py/agentd/teams/tools.py`
- Modify: `services/agentd-py/agentd/chat/controller.py`
- Test: `services/agentd-py/tests/test_team_implementation_controller.py` (new)

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces:
  - `GateKind` gains `"team_plan"`; `PendingGate.new(kind, payload, agent=None, team=None)`.
  - `ChatThreadStore.remove_team_gates(thread_id: str, team_id: str) -> None`.
  - `validation.TeamPlanConflict(ValueError)` (→ 409) and `validation.TeamPlanInvalid(ValueError)` (→ 422).
  - `MainTeamOps.resume: Callable[[str, int | None], dict[str, object]]` (defaults to a refusal, so existing constructions keep working); `resume_team` calls it.
  - `ChatController.decide_team_plan(thread_id, gate_id, decision, feedback) -> dict[str, object]` (sync; raises `GateNotFoundError`, `TeamPlanConflict`, `TeamPlanInvalid`).
  - `ChatController` implements the Task 7 host methods and `_team_membership_of(agent_id) -> TeamMember | None` (walks `parent_agent_id`, so a member's helper finds its team).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_team_implementation_controller.py`:

```python
"""A team implements its adopted plan inside the controller (spec v2 §8.5, §8.6, §8.2)."""
from __future__ import annotations

import pytest

from agentd.chat.models import GateNotFoundError
from agentd.providers.usage import METER, Usage
from agentd.teams.validation import TeamInputError, TeamPlanConflict, TeamPlanInvalid
from tests.test_team_controller import REPORT, _make, _request, _settle

AGREE = {"type": "report", "thought": "ok", "summary": "P1 holds", "status": "completed",
         "stances": [{"proposal_id": "P1", "stance": "agree", "note": "checked"}]}
DONE = {"type": "report", "thought": "done", "summary": "part done", "status": "completed"}
WAITING = {"type": "report", "thought": "w", "summary": "waiting", "status": "awaiting_peer"}
PARTIAL = {"type": "report", "thought": "f", "summary": "out of budget", "status": "partial"}
LOOK = {"type": "tool_call", "thought": "look", "tool": "list_directory", "args": {}}
PARTS = [{"member": "alice", "part": "api", "files": ["a.py"]},
         {"member": "bob", "part": "tests", "files": ["b.py"]}]


def _edit(path: str, content: str) -> dict[str, object]:
    return {"type": "edit", "thought": f"write {path}", "patch_ops": [
        {"op": "create_file", "file": path, "content": content, "reason": "part"}]}


def _quiet(ctrl, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)


@pytest.mark.asyncio
async def test_adoption_runs_implementation_and_ends_implemented(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("a.py", "A = 1\n"), DONE],
        "bob": [AGREE, _edit("b.py", "B = 1\n"), DONE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(
        tid, "turn1", _request(kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason) == ("DONE", "implemented")
    ws = tmp_path / "ws"
    assert (ws / "a.py").read_text() == "A = 1\n" and (ws / "b.py").read_text() == "B = 1\n"
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["adopted", "done"]
    alice_inputs = [h[-1]["content"] for label, h, _, _ in engine.seen
                    if label == "alice" and h and h[-1].get("role") == "user"]
    assert any("Your assignment: api. Files you own: a.py." in str(c) for c in alice_inputs)


@pytest.mark.asyncio
async def test_member_cannot_edit_anothers_file(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("b.py", "x\n"), _edit("b.py", "y\n"), _edit("a.py", "A\n"), DONE],
        "bob": [AGREE, _edit("b.py", "B\n"), DONE]})
    _quiet(ctrl, monkeypatch)
    await ctrl._create_team(tid, "turn1", _request(kickoff_assignments=PARTS))
    await _settle(ctrl)
    assert (tmp_path / "ws" / "b.py").read_text() == "B\n"
    alice_history = [str(m.get("content")) for label, h, _, _ in engine.seen
                     if label == "alice" for m in h]
    assert any("b.py is owned by bob — team_message bob instead" in c for c in alice_history)
    assert any("You were already told this file is owned by bob" in c for c in alice_history)


@pytest.mark.asyncio
async def test_unassigned_files_are_refused_with_an_approval_gate(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("other.py", "x\n"), _edit("a.py", "A\n"), DONE],
        "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS[:1])))["team_id"])
    await _settle(ctrl)
    gate = store.get_thread(tid).pending_controller_gates[0]
    ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)
    await _settle(ctrl)
    assert not (tmp_path / "ws" / "other.py").exists()
    history = [str(m.get("content")) for label, h, _, _ in engine.seen
               if label == "alice" for m in h]
    assert any("other.py is not in the approved plan — tell the main agent" in c
               for c in history)
    assert store.teams.get_team(team_id).phase == "DONE"


@pytest.mark.asyncio
async def test_team_gate_does_not_block_notice_turns(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    gates = store.get_thread(tid).pending_controller_gates
    assert [g.kind for g in gates] == ["team_plan"]
    assert gates[0].team.id == team_id and gates[0].agent is None
    assert gates[0].payload["proposal_id"] == "P1"
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["approval_needed"]
    assert ctrl._notice_blocked(tid) is False


@pytest.mark.asyncio
async def test_plan_decisions(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    gate = store.get_thread(tid).pending_controller_gates[0]
    with pytest.raises(GateNotFoundError):
        ctrl.decide_team_plan(tid, "nope", "approve", None)
    with pytest.raises(TeamPlanInvalid):
        ctrl.decide_team_plan(tid, gate.gate_id, "feedback", "x" * 8001)
    out = ctrl.decide_team_plan(tid, gate.gate_id, "reject", None)
    assert out == {"team_id": team_id, "phase": "DISBANDED"}
    assert store.get_thread(tid).pending_controller_gates == []
    with pytest.raises(GateNotFoundError):
        ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)


@pytest.mark.asyncio
async def test_a_stale_card_conflicts(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    gate = store.get_thread(tid).pending_controller_gates[0]
    store.teams.update_team(team_id, adopted_proposal_id="P9")
    with pytest.raises(TeamPlanConflict):
        ctrl.decide_team_plan(tid, gate.gate_id, "approve", None)


@pytest.mark.asyncio
async def test_disband_removes_the_plan_card(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(
        approval_gate=True, kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    await ctrl.disband_team(tid, team_id)
    assert store.get_thread(tid).pending_controller_gates == []


@pytest.mark.asyncio
async def test_stuck_milestone_after_mutual_wait(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE, WAITING],
                                                         "bob": [AGREE, WAITING]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(
        tid, "turn1", _request(kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    assert "stuck" in [n.kind for n in store.unclaimed_notices(tid)]
    assert store.teams.get_team(team_id).stuck_count == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_pauses_and_resume_continues(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, LOOK, _edit("a.py", "A\n"), DONE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def forced_reports_partial(*args, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("allowed_types") == ["report"]:
            return PARTIAL
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", forced_reports_partial)
    # Over budget only once implementing: every activation's end also checks the budget,
    # so a constant would pause the team in round 1 already.
    monkeypatch.setattr(ctrl, "team_requests", lambda team_id: (
        60 if store.teams.get_team(team_id).phase == "IMPLEMENTING" else 0))
    team_id = str((await ctrl._create_team(
        tid, "turn1", _request(budget=50, kickoff_assignments=PARTS[:1])))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.paused_reason) == ("PAUSED", "budget")
    assert not store.teams.member(team_id, "alice").assignment_done
    assert "member_blocked" not in [n.kind for n in store.unclaimed_notices(tid)]
    with pytest.raises(TeamInputError, match="runs only in a turn the user started"):
        ctrl._resume_team(tid, team_id, None)
    monkeypatch.setattr(ctrl, "turn_kind", lambda _thread_id: "user")
    out = ctrl._resume_team(tid, team_id, None)
    assert out["budget"] == 50 + 160
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason) == ("DONE", "implemented")
    assert (tmp_path / "ws" / "a.py").read_text() == "A\n"


@pytest.mark.asyncio
async def test_team_usage_is_the_sum_of_its_activations(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _quiet(ctrl, monkeypatch)
    monkeypatch.setattr(METER, "take", lambda _owner: Usage(requests=1, prompt_tokens=5))
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.requests, team.prompt_tokens) == (2, 10)


@pytest.mark.asyncio
async def test_kickoff_assignments_are_validated(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    with pytest.raises(TeamInputError, match="protected"):
        await ctrl._create_team(tid, "turn1", _request(kickoff_assignments=[
            {"member": "alice", "part": "cfg", "files": [".crucible/mcp.json"]}]))
    assert store.teams.list_teams(tid) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_implementation_controller.py --color=no --timeout=120 > /tmp/ic.txt 2>&1; echo exit=$?; tail -15 /tmp/ic.txt`
Expected: FAIL — `ImportError: cannot import name 'TeamPlanConflict'`.

- [ ] **Step 3: Gate kind, gate removal, decision errors, resume op**

`agentd/chat/models.py`:

```python
GateKind = Literal["command", "step", "scope", "validation", "mode", "edit", "clarify", "mcp_tool",
                   "team_plan"]
```

and

```python
    @classmethod
    def new(cls, kind: GateKind, payload: dict[str, Any],
            agent: GateAgent | None = None, team: GateTeam | None = None) -> PendingGate:
        return cls(gate_id=uuid4().hex, kind=kind, payload=payload, agent=agent, team=team)
```

`agentd/chat/storage.py`, after `clear_main_gates`:

```python
    def remove_team_gates(self, thread_id: str, team_id: str) -> None:
        """An ended team's cards can never be answered (spec v2 §8.9)."""
        self._write_gates(thread_id, [g for g in self._read_gates(thread_id)
                                      if g.team is None or g.team.id != team_id])
```

`agentd/teams/validation.py`:

```python
class TeamPlanConflict(ValueError):
    """The team_plan card no longer matches the team (route → 409)."""


class TeamPlanInvalid(ValueError):
    """A team_plan decision the route cannot accept as sent (route → 422)."""
```

`agentd/teams/tools.py`:
- Above `MainTeamOps`:

```python
def _resume_unavailable(_team_id: str, _extra: int | None) -> dict[str, object]:
    raise TeamInputError("resume_team is not available here")
```

- `MainTeamOps` gains the last field `resume: Callable[[str, int | None], dict[str, object]] = _resume_unavailable`.
- In `MainTeamToolSource.execute`, replace the `resume_team` branch with:

```python
            if tool == "resume_team":
                extra = args.get("extra_budget")
                return _ok(self._ops.resume(team_id, extra if isinstance(extra, int) else None))
```

- [ ] **Step 4: The controller**

In `agentd/chat/controller.py` (imports: add `GateTeam`, `PendingGate` from `agentd.chat.models` if missing; `TeamProtection` from `agentd.chat.protected_paths`; `Usage` from `agentd.providers.usage`; `team_budget_per_member`, `team_max_budget` from `agentd.teams.config`; `ImplementationReport`, `chain_checks` from `agentd.teams.implementation`; `team_edit_refusal` from `agentd.teams.scope`; `TEXT_MAX`, `TeamPlanConflict`, `TeamPlanInvalid`, `check_assignments`, `parse_assignments` from `agentd.teams.validation`):

1. **Service wiring.** Where `TeamService(...)` is built in `__init__`, pass `can_edit=self._member_can_edit`, and add:

```python
    def _member_can_edit(self, team_id: str, label: str) -> bool:
        """Spec v2 §8.3: a member assigned files must be able to edit (permission not plan,
        and its definition keeps the edit action)."""
        team = self._store.teams.get_team(team_id)
        member = self._store.teams.member(team_id, label)
        if team is None or member is None:
            return False
        handle = self._handle_from_record(team.thread_id, member.agent_id)
        return "edit" in handle.context.allowed_types

    def _team_membership_of(self, agent_id: str) -> TeamMember | None:
        """The team an agent works for: its own membership, or its dispatcher's (a member's
        helpers follow the team's edit, budget and usage rules)."""
        current: str | None = agent_id
        for _ in range(subagent_max_depth() + 1):
            if current is None:
                return None
            membership = self._store.teams.member_for_agent(current)
            if membership is not None:
                return membership
            record = self._store.get_agent(current)
            current = record.parent_agent_id if record is not None else None
        return None

    def _team_edit_rule(self, team_id: str, label: str, key: str) -> str | None:
        team = self._store.teams.get_team(team_id)
        if team is None:
            return "the team no longer exists"
        shared: set[str] = set()
        if team.adopted_proposal_id is not None:
            plan = self._store.teams.get_post(team_id, int(team.adopted_proposal_id.lstrip("Pp")))
            shared = {str(f) for f in (plan.payload if plan else {}).get("shared_files", [])}
        return team_edit_refusal(team, self._store.teams.members(team_id), label, key, shared)
```

2. **Notice turns are blocked only by the main agent's own cards.** In `_notice_blocked`:

```python
        return any(g.is_main() for g in thread.pending_controller_gates)
```

3. **The user speaking lifts a team's wake suppression.** At the end of `_user_spoke`, and after `self._wakes_suppressed.discard(thread_id)` in the queued-message path (the second site, near the `append_message` for a queued user message):

```python
        for coordinator in self._coordinators.values():
            if coordinator.thread_id == thread_id:
                coordinator.user_spoke()
```

4. **Host methods.** Replace `start_member` / `_start_member_now` and `team_milestone`, and add the rest of the host:

```python
    def start_member(self, team_id: str, label: str, extra: str = "") -> None:
        """Scheduled, never inline: the round's last reporter calls this from inside its own
        finishing activation, which is still active until that task ends (the same rule
        as _on_leftover)."""
        asyncio.get_running_loop().call_soon(self._start_member_now, team_id, label, extra)

    def _start_member_now(self, team_id: str, label: str, extra: str = "") -> None:
        assert self._subagents is not None
        team = self._store.teams.get_team(team_id)
        member = self._store.teams.member(team_id, label)
        if team is None or member is None or team.phase not in LIVE_TEAM_PHASES:
            return
        if self._subagents.is_active(member.agent_id):
            logger.warning("[teams] %s of %s still running at its start", label, team.name)
            return
        handle = self._handle_from_record(team.thread_id, member.agent_id)
        handle.activation_input = TEAM_DELTA + (f"\n\n{extra}" if extra else "")
        self._subagents.enqueue(handle, self._activate)

    def team_milestone(self, team: TeamRecord, kind: str, headline: str, body: str,
                       delivery: str = "wake") -> None:
        """A team milestone for the main agent (spec v2 §8.8): a notice, delivered by the
        same paths as an agent's report (§5.2). `notify` never starts a notice turn."""
        notice = NoticeRecord(
            notice_id=uuid4().hex, thread_id=team.thread_id, source_kind="team",
            source_id=team.team_id, kind=kind,
            payload={"team_name": team.name, "team_id": team.team_id, "headline": headline,
                     "body": body, "phase": team.phase},
            delivery=delivery, created_at=datetime.now(UTC))
        self._store.insert_notice(notice)
        if team.thread_id in self._active_loops:
            self._main_inbox.setdefault(team.thread_id, []).append(InboxItem(
                kind="note", text=body, wakes=False, source_id=team.team_id,
                author=notice_author(notice), notice_id=notice.notice_id))
            return
        self._rearm_notices(team.thread_id)

    def raise_plan_gate(self, team: TeamRecord, proposal: TeamPost) -> None:
        """Spec v2 §8.5: a team's card — not the main agent's, so a user turn keeps it."""
        self._store.add_controller_gate(team.thread_id, PendingGate.new(
            "team_plan",
            {"team_id": team.team_id, "team_name": team.name,
             "proposal_id": proposal.proposal_id, "text": proposal.text,
             "assignments": proposal.payload.get("assignments", []),
             "shared_files": proposal.payload.get("shared_files", [])},
            team=GateTeam(id=team.team_id, name=team.name)))

    def _team_agent_ids(self, team_id: str) -> list[tuple[TeamMember, str]]:
        return [(member, agent_id) for member in self._store.teams.members(team_id)
                for agent_id in self._store.subtree_agent_ids(member.agent_id)]

    def force_team_final(self, team_id: str) -> list[str]:
        """Budget pause (spec v2 §3.11): every running member and helper loop of the team
        reports at its next iteration top."""
        assert self._subagents is not None
        forced: list[str] = []
        for member, agent_id in self._team_agent_ids(team_id):
            handle = self._subagents.registry.get(agent_id)
            if (handle is None or not self._subagents.is_active(agent_id)
                    or not isinstance(handle.loop, ControllerLoop)):
                continue
            handle.loop.force_final()
            if agent_id == member.agent_id:
                forced.append(member.label)
        return forced

    def team_requests(self, team_id: str) -> int:
        """Persisted usage plus what running activations have used so far."""
        assert self._subagents is not None
        team = self._store.teams.get_team(team_id)
        if team is None:
            return 0
        live = sum(METER.peek(agent_id).requests for _m, agent_id in self._team_agent_ids(team_id)
                   if self._subagents.is_active(agent_id))
        return team.requests + live

    def is_busy(self, team_id: str, label: str) -> bool:
        assert self._subagents is not None
        member = self._store.teams.member(team_id, label)
        return member is not None and (self._subagents.is_active(member.agent_id)
                                       or self._subagents.has_wakes(member.agent_id))

    def has_wakes(self, team_id: str, label: str) -> bool:
        assert self._subagents is not None
        member = self._store.teams.member(team_id, label)
        return member is not None and self._subagents.has_wakes(member.agent_id)
```

In `team_phase_changed`, inside `if team.phase not in LIVE_TEAM_PHASES:` add first:

```python
            self._store.remove_team_gates(team.thread_id, team.team_id)
```

5. **Leftovers wait while no work may start.** In `_on_leftover`, change the condition to:

```python
            if team is not None and team.phase in ("DELIBERATING", "AWAITING_APPROVAL", "PAUSED"):
                # Nothing reaches a member mid-round (spec v2 E5), and nothing starts while a
                # plan waits for approval or the team is paused (§8.2): the next activation's
                # delta covers the board, and anything else waits in the inbox.
```

6. **Reports carry files and text.** In `_team_member_reported`, change the coordinator call to:

```python
            coordinator.on_report(record.label, result.status, stop_reason,
                                  files=tuple(result.files_changed), report=result.report)
```

7. **`_activate`.** Replace the membership block at the top with:

```python
        membership = self._store.teams.member_for_agent(ctx.agent_id)
        # A member's helpers live under its team's edit, budget and usage rules too
        # (spec v2 §3.9, §3.11).
        team_of = membership or self._team_membership_of(ctx.agent_id)
        team_counters = ActivationCounters()
        team_row = (self._store.teams.get_team(team_of.team_id)
                    if team_of is not None else None)
        deliberating = (membership is not None and team_row is not None
                        and team_row.phase == "DELIBERATING")
        coordinator = (self._coordinators.get(team_of.team_id)
                       if team_of is not None else None)
```

(the existing `if team_row is not None and team_row.phase != "IMPLEMENTING":` block now also removes `edit` for helpers — keep it as it is). Before `edit_session_factory = (`, add:

```python
        protection = (TeamProtection(partial(self._team_edit_rule, team_of.team_id,
                                             team_of.label))
                      if team_of is not None else AgentProtection())
```

and in the factory replace `protection=AgentProtection()` with `protection=protection`. After `handle.loop = loop`, add:

```python
        if team_row is not None and team_row.phase == "PAUSED" and membership is not None \
                and coordinator is not None:
            # Queued before the pause took effect: report at once, owing its work.
            loop.force_final()
            coordinator.mark_interrupted(membership.label)
```

Move the `report_check = (...)` assignment to just after `def subtree_files()` and make it:

```python
        report_check = (
            chain_checks(
                ImplementationReport(self._store.teams, membership.team_id, membership.label,
                                     team_counters, lambda: set(subtree_files())),
                ReportFields(self._teams, membership.team_id, membership.label, team_counters,
                             on_dropped=(partial(coordinator.trace_dropped, membership.label)
                                         if coordinator is not None
                                         else (lambda _errors: None))))
            if membership is not None and self._teams is not None else None)

        def persist_and_meter(history: list[dict[str, object]]) -> None:
            self._store.set_agent_history(ctx.agent_id, history)
            if coordinator is not None:
                coordinator.check_budget()   # spec v2 §3.11: enforced, not advisory
```

In the `loop.run(...)` call, replace `iteration_cb=partial(self._store.set_agent_history, ctx.agent_id)` with `iteration_cb=persist_and_meter`, and add:

```python
                final_hint=(
                    partial(self._teams.final_hint, membership.team_id, membership.label)
                    if membership is not None and self._teams is not None else None),
```

Add to `TeamCoordinator` (Task 7's file) the one-liner used above:

```python
    def mark_interrupted(self, label: str) -> None:
        self._interrupted.add(label)
```

8. **Team usage.** In `_close_child`, replace `self._store.add_agent_usage(ctx.agent_id, METER.take(ctx.agent_id))` with:

```python
        usage = METER.take(ctx.agent_id)
        self._store.add_agent_usage(ctx.agent_id, usage)
        team_of = self._team_membership_of(ctx.agent_id)
        if team_of is not None:
            # A team's usage is the sum over its members and their helpers (spec §3.11).
            self._store.teams.add_usage(team_of.team_id, usage)
```

9. **resume_team, decisions, kickoff validation.** In `_main_team_source`, add `resume=partial(self._resume_team, thread_id)` to `MainTeamOps(...)`, and add:

```python
    def _resume_team(self, thread_id: str, team_id: str, extra: int | None) -> dict[str, object]:
        """resume_team (spec v2 §8.2): only from a turn the user started — it spends more of
        the user's requests."""
        if self.turn_kind(thread_id) != "user":
            raise TeamInputError(
                "resume_team spends more requests, so it runs only in a turn the user started: "
                "tell the user why the team paused and ask whether to resume it")
        team = self._store.teams.get_team(team_id)
        coordinator = self._coordinators.get(team_id)
        if team is None or team.thread_id != thread_id:
            raise TeamInputError(f"no team {team_id!r} in this thread")
        if coordinator is None or coordinator.phase != "PAUSED":
            raise TeamInputError(f"resume_team is only for a PAUSED team; this team is "
                                 f"{team.phase}")
        cap = team_max_budget()
        members = len(self._store.teams.members(team_id))
        amount = extra if extra is not None else min(team_budget_per_member() * members, cap)
        if amount < 1:
            raise TeamInputError("extra_budget must be at least 1")
        amount = min(amount, cap - team.budget)
        used = self.team_requests(team_id)
        if team.budget + amount <= used:
            raise TeamInputError(
                f"the team has used {used} requests and its budget cannot rise above {cap} "
                "(CRUCIBLE_TEAM_MAX_BUDGET): disband it, or ask the user to raise that limit")
        coordinator.resume(amount)
        after = self._store.teams.get_team(team_id)
        assert after is not None
        return {"team_id": team_id, "phase": after.phase, "budget": after.budget}

    def decide_team_plan(self, thread_id: str, gate_id: str, decision: str,
                         feedback: str | None) -> dict[str, object]:
        """The user's decision on a team_plan card (spec v2 §8.5). Order: unknown gate → 404,
        a card that no longer matches → 409, invalid feedback → 422; then the gate goes
        first, so a second click finds nothing."""
        thread = self._store.get_thread(thread_id)
        gate = next((g for g in (thread.pending_controller_gates if thread else [])
                     if g.gate_id == gate_id), None)
        if gate is None:
            raise GateNotFoundError(f"no pending gate {gate_id!r} on this thread")
        team_id = gate.team.id if gate.team is not None else ""
        team = self._store.teams.get_team(team_id)
        coordinator = self._coordinators.get(team_id)
        if (gate.kind != "team_plan" or team is None or coordinator is None
                or team.phase != "AWAITING_APPROVAL"
                or team.adopted_proposal_id != gate.payload.get("proposal_id")):
            raise TeamPlanConflict("this card no longer matches the team's plan")
        text = (feedback or "").strip()
        if decision == "feedback" and not 1 <= len(text) <= TEXT_MAX:
            raise TeamPlanInvalid(f"feedback must be 1 to {TEXT_MAX} characters")
        if decision not in ("approve", "feedback", "reject"):
            raise TeamPlanInvalid("decision must be approve, feedback or reject")
        self._store.remove_controller_gate(thread_id, gate_id)
        pid = team.adopted_proposal_id
        crumb = {"approve": f"✓ Approved {pid} of team {team.name!r}",
                 "feedback": f"↻ Sent {pid} of team {team.name!r} back for another round",
                 "reject": f"✗ Rejected {pid} of team {team.name!r}"}[decision]
        self._write_breadcrumb(thread_id, f"chat:{thread_id}", crumb)
        coordinator.approval(decision, text or None)
        after = self._store.teams.get_team(team_id)
        return {"team_id": team_id, "phase": after.phase if after is not None else None}
```

In `_create_team`, after the two limit checks and before `now = datetime.now(UTC)`, build the contexts first and validate the kickoff plan:

```python
        built = [(spec, *self._context_for(
            agent_id=new_agent_id(), name=spec.agent.name, label=spec.label, depth=1,
            parent_agent_id=None, snapshot=spec.agent, inherited={})) for spec in req.members]
        if req.kickoff_kind == "proposal":
            # The main agent's plan is validated like any proposal (spec v2 §8.3).
            edit_ok = {ctx.label: "edit" in ctx.allowed_types for _s, ctx, _d in built}
            parts, shared = check_assignments(
                parse_assignments(req.kickoff_assignments, [s.label for s in req.members]),
                req.kickoff_shared_files, workspace=Path(self._workspace_path),
                can_edit=lambda label: edit_ok.get(label, False))
            req = replace(req, kickoff_assignments=parts, kickoff_shared_files=shared)
```

and change the member loop header from `for spec in req.members:` plus its `context, definition = self._context_for(...)` statement to `for spec, context, definition in built:`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_team_implementation_controller.py tests/test_team_controller.py tests/test_team_rounds_controller.py tests/test_team_activity_controller.py tests/test_team_tools.py --color=no --timeout=120 > /tmp/ic.txt 2>&1; echo exit=$?; tail -15 /tmp/ic.txt`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/ services/agentd-py/tests/test_team_implementation_controller.py
git commit -m "feat(teams): run the adopted plan — assignments, ownership, budget, resume, plan card"
```

### Task 9: The decision route, summary fields, and the teaching

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py`
- Modify: `services/agentd-py/agentd/teams/service.py`
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py`
- Modify: `services/agentd-py/tests/goldens/controller_prompt_teams.json` (re-captured)
- Test: `services/agentd-py/tests/test_team_routes.py`, `services/agentd-py/tests/test_team_service.py`, `services/agentd-py/tests/test_team_prompts.py`

**Interfaces:**
- Consumes: `ChatController.decide_team_plan` (Task 8).
- Produces:
  - `POST /v1/chat/threads/{thread_id}/team-plan-decision {gate_id, decision: "approve"|"feedback"|"reject", feedback?}` → `200 {team_id, phase}`; 404 unknown gate; 409 stale card; 422 invalid feedback.
  - `TeamService.summary` gains `end_reason`, `approval_gate`, `adopted_proposal_id`, and per member `assignment` (object or null) and `assignment_done`.

Spec §8.5 says a gate belonging to another thread is a 409. The controller looks gates up on the thread in the path, so another thread's gate id is simply unknown there (404) — the safer answer, since it reveals nothing about other threads.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_routes.py`:

```python
@pytest.mark.asyncio
async def test_team_plan_decision_route_errors(tmp_path: Path, monkeypatch) -> None:
    from agentd.chat.models import GateNotFoundError
    from agentd.teams.validation import TeamPlanConflict, TeamPlanInvalid

    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    outcomes: list[object] = [GateNotFoundError("x"), TeamPlanConflict("y"),
                              TeamPlanInvalid("z"), {"team_id": "team-1", "phase": "IMPLEMENTING"}]
    seen: list[tuple[str, str, str, str | None]] = []

    def decide(thread_id, gate_id, decision, feedback):  # type: ignore[no-untyped-def]
        seen.append((thread_id, gate_id, decision, feedback))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ctrl, "decide_team_plan", decide)
    async with _client(tmp_path, ctrl) as client:
        url = f"/v1/chat/threads/{tid}/team-plan-decision"
        body = {"gate_id": "g1", "decision": "approve"}
        assert (await client.post(url, json=body)).status_code == 404
        assert (await client.post(url, json=body)).status_code == 409
        assert (await client.post(url, json={**body, "decision": "feedback",
                                             "feedback": "x"})).status_code == 422
        ok = await client.post(url, json=body)
        assert ok.status_code == 200 and ok.json() == {"team_id": "team-1",
                                                       "phase": "IMPLEMENTING"}
        assert (await client.post(url, json={**body, "decision": "maybe"})).status_code == 422
    assert seen[2] == (tid, "g1", "feedback", "x")
```

Append to `tests/test_team_service.py`:

```python
def test_summary_carries_assignments_and_end(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    teams.set_assignment(tid, "alice", {"member": "alice", "part": "api", "files": ["a.py"]})
    teams.set_assignment_done(tid, "alice")
    teams.update_team(tid, phase="DONE", end_reason="implemented", adopted_proposal_id="P1")
    summary = service.summary(tid)
    assert (summary["end_reason"], summary["adopted_proposal_id"], summary["approval_gate"]) == (
        "implemented", "P1", False)
    alice = next(m for m in summary["members"] if m["label"] == "alice")
    assert alice["assignment"]["part"] == "api" and alice["assignment_done"] is True
    bob = next(m for m in summary["members"] if m["label"] == "bob")
    assert bob["assignment"] is None and bob["assignment_done"] is False
```

Append to `tests/test_team_prompts.py`:

```python
def test_teaching_covers_implementation_and_pauses() -> None:
    member = format_controller_system_prompt(
        [{"name": "team_post"}], task_subsystem_enabled=False, memory_enabled=False,
        render_ctx=_ctx("Team 'auth'. Goal: add login."), persona="")
    assert "Edit only\n  your own files and the shared files" in member
    tools = [d.model_dump() for d in MainTeamToolSource(BUILTIN_AGENTS, MainTeamOps(
        create=_never, resolve=lambda r: r, post=lambda *a: {}, status=lambda t: {},
        disband=_never, adopt=lambda *a: {}), first_turn_team_ids=set()).definitions()]
    main = format_controller_system_prompt(tools, task_subsystem_enabled=False,
                                           memory_enabled=False)
    assert "approval_gate: true" in main and "resume_team continues it" in main
    assert "only after\n  the user agrees" in main
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_routes.py tests/test_team_service.py tests/test_team_prompts.py --color=no --timeout=120 > /tmp/r.txt 2>&1; echo exit=$?; tail -15 /tmp/r.txt`
Expected: FAIL — 404 from FastAPI for the unknown route (the first `== 404` passes by accident; the `409` assertion fails), plus the summary `KeyError` and the teaching assertions.

- [ ] **Step 3: Implement**

`agentd/api/routes.py` — next to the other chat request models:

```python
class TeamPlanDecisionRequest(BaseModel):
    gate_id: str
    decision: Literal["approve", "feedback", "reject"]
    feedback: str | None = None
```

(import `Literal` from `typing` if the file does not already) and, after the `/teams/{team_id}/disband` route:

```python
        @router.post("/chat/threads/{thread_id}/team-plan-decision")
        async def post_team_plan_decision(
            thread_id: str, request: TeamPlanDecisionRequest,
        ) -> dict[str, object]:
            # Spec v2 §8.5. Plain JSON: approval starts members in the background.
            decide = getattr(_chat_agent, "decide_team_plan", None)
            if decide is None:
                raise HTTPException(status_code=404, detail="Teams are not available")
            try:
                result: dict[str, object] = decide(
                    thread_id, request.gate_id, request.decision, request.feedback)
            except GateNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except TeamPlanConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except TeamPlanInvalid as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return result
```

(import `TeamPlanConflict, TeamPlanInvalid` alongside `TeamInputError` from `agentd.teams.validation`).

`agentd/teams/service.py`, in `summary`:

```python
            "end_reason": team.end_reason, "approval_gate": team.approval_gate,
            "adopted_proposal_id": team.adopted_proposal_id,
            "members": [{"label": m.label, "agent_id": m.agent_id,
                         "name": (info := self._agent_info(m.agent_id)).name,
                         "description": info.description, "status": info.status,
                         "assignment": m.assignment, "assignment_done": m.assignment_done}
                        for m in self._store.members(team_id)],
```

(replacing the existing `"members": [...]` entry and adding the three keys before it). In `status_text`, replace the existing two-line `if member.assignment:` block with:

```python
        if member.assignment:
            done = " (done)" if member.assignment_done else ""
            lines.append(f"your assignment{done}: {json.dumps(member.assignment)}")
```

`agentd/chat/controller_prompts.py`:
- In `_TEAM_BLOCK`, after the bullet that starts `- Report when your part of this round is done:`, add the bullet:

```
- Once a plan is adopted the team implements it, and team_status shows your assignment. Edit only
  your own files and the shared files: a file another member owns is theirs, so send them the
  change with team_message. Report completed when your part is done and checked, awaiting_peer
  naming the member you wait on, or partial when you cannot finish — your assignment then stays
  open and the main agent can restart you.
```

- In `_TEAMS_MAIN_BLOCK`, replace the sentence `Milestones (a plan adopted, a deadlock, a member lost) wake you — do not poll team_status.` with `Milestones (a plan adopted or waiting for approval, a deadlock, a member lost or blocked, the team stuck, paused or finished) wake you — do not poll team_status.` and add these bullets before `Example — you have a plan and want it checked:`:

```
- approval_gate: true makes an adopted plan wait for the user on a card: the user approves it,
  sends feedback (one more round of deliberation), or rejects it (the team ends).
- A team pauses when its request budget runs out, the provider keeps failing, or it is stuck
  three times in a row. resume_team continues it and spends more requests, so call it only after
  the user agrees — in the turn that answers you.
```

Re-capture the teams golden, then run the prompt tests:

```bash
cd services/agentd-py && .venv/bin/python -m tests.test_prompt_goldens_teams
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_routes.py tests/test_team_service.py tests/test_team_prompts.py tests/test_prompt_goldens_teams.py tests/test_prompt_goldens_subagents.py tests/test_prompt_leak_lint.py tests/test_get_routes_read_only.py --color=no --timeout=120 > /tmp/r.txt 2>&1; echo exit=$?; tail -15 /tmp/r.txt`
Expected: PASS. (`controller_prompt_main.json` must be unchanged — the TEAMS block is only rendered with teams on; if `test_prompt_goldens_subagents` or the main golden fails, the edit leaked outside `<<team>>`/`<<main>>` and must be fixed, not re-captured.)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/agentd/teams/service.py services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/tests/
git commit -m "feat(teams): plan decision route, assignment summaries, implementation teaching"
```

---

# Part E — Frontend

### Task 10: editor-client — the `team_plan` gate, its decision, summary fields

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`
- Modify: `apps/editor-client/src/client/http-backend-client.ts`
- Test: `apps/editor-client/test/team-contracts.test.ts`

**Interfaces:**
- Produces:
  - `PendingGateSchema.kind` gains `"team_plan"`; `PendingGateSchema` gains `team: z.object({ id: z.string(), name: z.string() }).nullable().default(null)`.
  - `TeamSummarySchema` gains `endReason: string | null`, `approvalGate: boolean`, `adoptedProposalId: string | null`; members gain `assignment: {member, part, files} | null` and `assignmentDone: boolean` (all defaulted, so older payloads parse).
  - `BackendTaskClient.decideTeamPlan(threadId: string, gateId: string, decision: "approve" | "feedback" | "reject", feedback?: string): Promise<{ teamId: string; phase: string | null }>`.

- [ ] **Step 1: Write the failing test**

Append to `test/team-contracts.test.ts`:

```ts
import { PendingGateSchema } from "../src/contracts/task-contracts";

describe("team plan gate and implementation fields", () => {
  it("parses a team_plan gate with its team", async () => {
    const live = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, plan: null, turn_active: false,
      pending_gates: [{ gate_id: "g1", kind: "team_plan", agent: null,
                        team: { id: "team-1", name: "auth" },
                        payload: { proposal_id: "P1", text: "plan", assignments: [] } }],
    }) });
    const state = await live.getThreadLiveState("t");
    expect(state.pendingGates[0]).toMatchObject({
      gateId: "g1", kind: "team_plan", agent: null, team: { id: "team-1", name: "auth" } });
    expect(PendingGateSchema.parse({ gateId: "g", kind: "edit", payload: {} }).team).toBeNull();
  });

  it("posts a plan decision", async () => {
    const fetchFn = respond({ team_id: "team-1", phase: "DELIBERATING" });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    expect(await c.decideTeamPlan("t", "g1", "feedback", "use redis")).toEqual(
      { teamId: "team-1", phase: "DELIBERATING" });
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/chat/threads/t/team-plan-decision");
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual(
      { gate_id: "g1", decision: "feedback", feedback: "use redis" });
  });

  it("maps assignment and end fields on summaries", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({ teams: [{
      ...SUMMARY, phase: "DONE", end_reason: "implemented", approval_gate: true,
      adopted_proposal_id: "P1",
      members: [{ label: "alice", agent_id: "agent-a", status: "completed",
                  assignment: { member: "alice", part: "api", files: ["a.py"] },
                  assignment_done: true }] }] }) });
    const [team] = await c.listTeams("t");
    expect(team).toMatchObject({ endReason: "implemented", approvalGate: true,
      adoptedProposalId: "P1",
      members: [{ assignment: { part: "api", files: ["a.py"] }, assignmentDone: true }] });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/editor-client && perl -e 'alarm 120; exec @ARGV' npx vitest run test/team-contracts.test.ts`
Expected: FAIL — `c.decideTeamPlan is not a function` and the Zod enum rejecting `team_plan`.

- [ ] **Step 3: Implement**

`src/contracts/task-contracts.ts`:

```ts
export const GateTeamSchema = z.object({ id: z.string(), name: z.string() });

export const PendingGateSchema = z.object({
  gateId: z.string(),
  kind: z.enum(["command", "step", "scope", "validation", "mode", "edit", "clarify", "mcp_tool",
                "team_plan"]),
  payload: z.record(z.unknown()).default({}),
  agent: GateAgentSchema.nullable().default(null),
  // A team's card (spec v2 §8.5): not the main agent's, so it never takes the composer.
  team: GateTeamSchema.nullable().default(null),
});
```

In `TeamSummarySchema`, the member object gains

```ts
    assignment: z.object({ member: z.string(), part: z.string(), files: z.array(z.string()) })
      .nullable().default(null),
    assignmentDone: z.boolean().default(false),
```

and the summary gains

```ts
  endReason: z.string().nullable().default(null),
  approvalGate: z.boolean().default(false),
  adoptedProposalId: z.string().nullable().default(null),
```

and the interface gains

```ts
  decideTeamPlan(threadId: string, gateId: string, decision: "approve" | "feedback" | "reject",
                 feedback?: string): Promise<{ teamId: string; phase: string | null }>;
```

`src/client/http-backend-client.ts`:
- `toGate` adds `team: g["team"] ?? null,`.
- `toTeamSummary`'s member mapping adds `assignment: m["assignment"] ?? null, assignmentDone: m["assignment_done"] === true`, and the summary adds `endReason: t["end_reason"] ?? null, approvalGate: t["approval_gate"] === true, adoptedProposalId: t["adopted_proposal_id"] ?? null,`.
- After `disbandTeam`:

```ts
  async decideTeamPlan(
    threadId: string, gateId: string, decision: "approve" | "feedback" | "reject",
    feedback?: string,
  ): Promise<{ teamId: string; phase: string | null }> {
    const body: Record<string, unknown> = { gate_id: gateId, decision };
    if (feedback !== undefined) body.feedback = feedback;
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/team-plan-decision`,
      { method: "POST", body: JSON.stringify(body) }
    ) as Record<string, unknown>;
    return { teamId: String(raw["team_id"] ?? ""),
             phase: typeof raw["phase"] === "string" ? raw["phase"] : null };
  }
```

- [ ] **Step 4: Run tests and build**

Run: `cd apps/editor-client && perl -e 'alarm 300; exec @ARGV' npx vitest run && npm run build`
Expected: PASS, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add apps/editor-client
git commit -m "feat(editor-client): team_plan gate, plan decision, assignment summaries"
```

### Task 11: The plan card, its team chip, and the composer rule

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/messages/gates/TeamPlanGate.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`
- Modify: `apps/vscode-extension/webview-ui/src/components/LiveSlot.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/inputAvailability.ts`
- Modify: `apps/vscode-extension/src/controller.ts`
- Modify: `apps/vscode-extension/src/chat-panel.ts`
- Modify: `apps/vscode-extension/src/extension.ts`
- Test: `apps/vscode-extension/webview-ui/src/test/gates.test.tsx`, `apps/vscode-extension/webview-ui/src/test/views.test.tsx`, `apps/vscode-extension/test/controller.test.ts`

**Interfaces:**
- Consumes: Task 10's `team` on gates and `decideTeamPlan`.
- Produces:
  - webview `LiveGateView.kind` gains `"team_plan"`, and `team?: { id: string; name: string } | null`; host `LiveGateView` (src/controller.ts) the same.
  - webview → host message `{ type: "teamPlanDecision", threadId, gateId, decision, feedback? }`.
  - `CrucibleController.decideTeamPlan(threadId: string, gateId: string, decision: "approve" | "feedback" | "reject", feedback?: string): Promise<void>` — a 404/409 is the benign lost race.

- [ ] **Step 1: Write the failing tests**

Append to `webview-ui/src/test/gates.test.tsx` (and add `import { TeamPlanGate } from "../components/messages/gates/TeamPlanGate";`):

```tsx
describe("TeamPlanGate", () => {
  const payload = {
    team_id: "team-1", team_name: "auth", proposal_id: "P3", text: "Add a limiter.",
    assignments: [{ member: "alice", part: "limiter", files: ["api/limiter.py"] },
                  { member: "bob", part: "tests", files: [] }],
    shared_files: ["api/routes.py"],
  };

  it("shows the plan with its assignments", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    expect(screen.getByText(/Team auth adopted P3/)).toBeInTheDocument();
    expect(screen.getByText("Add a limiter.")).toBeInTheDocument();
    expect(screen.getByText(/alice → limiter/)).toBeInTheDocument();
    expect(screen.getByText(/api\/limiter\.py/)).toBeInTheDocument();
    expect(screen.getByText(/Shared: api\/routes\.py/)).toBeInTheDocument();
  });

  it("approves once", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByText("Approved"));
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith({ type: "teamPlanDecision", threadId: "thread-1",
                                               gateId: "g1", decision: "approve" });
  });

  it("sends feedback only with text", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(postMessage).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Feedback for the team"),
                     { target: { value: "use redis" } });
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "teamPlanDecision", threadId: "thread-1",
                                               gateId: "g1", decision: "feedback",
                                               feedback: "use redis" });
  });

  it("rejects", () => {
    render(<TeamPlanGate gateId="g1" taskId="thread-1" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ decision: "reject" }));
  });
});
```

Append to `webview-ui/src/test/views.test.tsx`, inside the `inputAvailability` describe:

```tsx
    it("a team gate leaves the composer usable", () => {
      const r = inputAvailability({
        inputEnabled: true, liveStatus: null, workbar: null, turnActive: false,
        liveGates: [{ gateId: "g", kind: "team_plan", taskId: "t", payload: {}, agent: null,
                      team: { id: "team-1", name: "auth" } }],
      });
      expect(r.disabled).toBe(false);
    });
```

Append to `test/controller.test.ts`, next to the mixed-gate mapping test (reusing its `createStubBackend`, `createUi`, `MemorySessionStore`, `createSettings` set-up):

```ts
  test("a team gate keeps its team and a lost-race decision is swallowed", async () => {
    const state = { submitPayloads: [], getTaskCalls: [], acceptCalls: [], rejectCalls: [],
                    getResultCalls: [], planFeedbackCalls: [] };
    const renders: LiveGateView[][] = [];
    const errors: string[] = [];
    const calls: unknown[][] = [];
    const backend: BackendTaskClient = {
      ...createStubBackend(state),
      decideTeamPlan: async (...args: unknown[]) => {
        calls.push(args);
        throw Object.assign(new Error("gone"), { status: 404 });
      },
    };
    const controller = new CrucibleController(
      () => backend, new MemorySessionStore(), createSettings(),
      createUi({ renderLiveGates: (g) => { renders.push(g); },
                 showError: (m: string) => { errors.push(m); } }),
      { openDiff: async (_entry: ReviewFileEntry) => {} },
      () => "2026-05-11T00:00:00.000Z");
    await controller.decideTeamPlan("chat-1", "g1", "approve");
    expect(calls).toEqual([["chat-1", "g1", "approve", undefined]]);
    expect(errors).toEqual([]);
    controller.dispose();
  });
```

(Adapt the `state` literal to whatever `createStubBackend` takes in this file — copy it from the neighboring "lost the race" test.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && perl -e 'alarm 300; exec @ARGV' npx vitest run src/test/gates.test.tsx src/test/views.test.tsx`
Expected: FAIL — cannot resolve `TeamPlanGate`, and the composer test disabled (`team_plan` is filtered as a main gate).

- [ ] **Step 3: Implement the card**

Create `webview-ui/src/components/messages/gates/TeamPlanGate.tsx`:

```tsx
import { useState } from "react";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../shared/buttons";

interface Props {
  gateId: string;
  /** The thread id — a team's card belongs to the thread, not a task. */
  taskId: string;
  payload: Record<string, unknown>;
}

interface Assignment { member: string; part: string; files: string[] }

function assignmentsOf(payload: Record<string, unknown>): Assignment[] {
  const raw = Array.isArray(payload.assignments) ? payload.assignments : [];
  return raw.filter((a): a is Record<string, unknown> => typeof a === "object" && a !== null)
    .map((a) => ({ member: String(a.member ?? ""), part: String(a.part ?? ""),
                   files: Array.isArray(a.files) ? a.files.map(String) : [] }));
}

/** The user's approval of a team's adopted plan (spec v2 §8.5): approve, send feedback for
 * one more round, or reject (the team ends). One decision per card. */
export function TeamPlanGate({ gateId, taskId, payload }: Props) {
  const [resolved, setResolved] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const team = String(payload.team_name ?? "");
  const proposal = String(payload.proposal_id ?? "");
  const shared = Array.isArray(payload.shared_files) ? payload.shared_files.map(String) : [];

  function decide(decision: "approve" | "feedback" | "reject") {
    if (resolved !== null) return;
    const text = feedback.trim();
    if (decision === "feedback" && text === "") return;
    setResolved({ approve: "Approved", feedback: "Feedback sent", reject: "Rejected" }[decision]);
    vscode.postMessage({ type: "teamPlanDecision", threadId: taskId, gateId, decision,
                         ...(decision === "feedback" ? { feedback: text } : {}) });
  }

  return (
    <CardShell
      icon="check"
      title={`Team ${team} adopted ${proposal} — approve the plan?`}
      subtitle="The members implement it after you approve"
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      <div className="max-h-60 overflow-auto border-t border-border px-2.5 py-2 text-[12px] text-text-2">
        <p className="whitespace-pre-wrap">{String(payload.text ?? "")}</p>
        <ul className="mt-2 grid gap-0.5">
          {assignmentsOf(payload).map((a) => (
            <li key={a.member}>
              <span className="font-semibold text-text">{a.member} → {a.part}</span>
              {a.files.length > 0 && <span className="text-text-3"> ({a.files.join(", ")})</span>}
            </li>
          ))}
        </ul>
        {shared.length > 0 && <p className="mt-1 text-text-3">Shared: {shared.join(", ")}</p>}
      </div>
      {resolved === null ? (
        <div className="grid gap-1.5 border-t border-border px-2.5 py-2">
          <textarea aria-label="Feedback for the team" rows={2} value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="What should the team change? (sends the plan back for another round)"
            className="w-full resize-y rounded border border-border bg-panel px-2 py-1 text-[12px]" />
          <div className="flex flex-wrap items-center gap-1.5">
            <BtnPrimary onClick={() => decide("approve")}>Approve</BtnPrimary>
            <BtnGhost onClick={() => decide("feedback")}>Send feedback</BtnGhost>
            <BtnDanger onClick={() => decide("reject")}>Reject</BtnDanger>
          </div>
        </div>
      ) : (
        <div className="border-t border-border px-2.5 py-2 text-[12px] text-text-3">{resolved}</div>
      )}
    </CardShell>
  );
}
```

- [ ] **Step 4: Types, dispatch, chip, composer**

`webview-ui/src/types.ts`:

```ts
export interface GateTeamView { id: string; name: string }

export interface LiveGateView {
  gateId: string;
  kind: "command" | "scope" | "validation" | "step" | "mode" | "edit" | "clarify" | "mcp_tool"
    | "doc_write" | "team_plan";
  taskId: string;
  payload: Record<string, unknown>;  // pending_* payload, snake_case
  agent?: GateAgentView | null;      // the sub-agent that raised it; absent/null = main
  team?: GateTeamView | null;        // the team whose card it is (spec v2 §8.5)
}
```

and in the `WebviewMessage` union, next to `mcpDecision`:

```ts
  // A team's plan card (spec v2 §8.5): approve / feedback / reject.
  | { type: "teamPlanDecision"; threadId: string; gateId: string;
      decision: "approve" | "feedback" | "reject"; feedback?: string }
```

`webview-ui/src/components/LiveSlot.tsx`: import `TeamPlanGate`; add `case "team_plan": return <TeamPlanGate gateId={gateId} taskId={taskId} payload={payload} />;` to `GateCard`; give `GateDispatchProps` a `team: GateTeamView | null` and render it like the agent chip:

```tsx
function GateDispatch({ agent, team, ...card }: GateDispatchProps) {
  if (agent === null && team === null) return <GateCard {...card} />;
  return (
    <div className="flex flex-col gap-1">
      {agent !== null ? (
        <span className="self-start" title={`Raised by sub-agent ${agent.label} (${agent.name})`}>
          <AgentChip name={agent.name} label={agent.label} />
        </span>
      ) : team !== null && (
        <span className="self-start rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
          style={{ background: "var(--accent-bg)", color: "var(--color-accent-ink)" }}
          title={`Raised by team ${team.name}`}>
          team {team.name}
        </span>
      )}
      <GateCard {...card} />
    </div>
  );
}
```

and pass `team={gate.team ?? null}` where `GateDispatch` is rendered.

`webview-ui/src/inputAvailability.ts`:

```ts
  // Composer rules key on the MAIN agent's gates only (spec §6): a background agent's or a
  // team's card waits above without taking the composer away.
  const mainGates = liveGates.filter((g) => !g.agent && !g.team);
```

- [ ] **Step 5: Host plumbing**

`src/controller.ts`:
- Host `LiveGateView` kind union gains `"team_plan"`, plus `team: { id: string; name: string } | null;`.
- In `pollThreadLiveState`'s `renderLiveGates` mapping add `team: g.team,`.
- Next to `disbandTeam`:

```ts
  async decideTeamPlan(
    threadId: string, gateId: string, decision: "approve" | "feedback" | "reject",
    feedback?: string,
  ): Promise<void> {
    try {
      await this.clientForChat().decideTeamPlan(threadId, gateId, decision, feedback);
    } catch (err) {
      if (this.isBenignGateMiss(err)) return;   // the card was already answered or stale
      this.ui.showError(`Failed to send the plan decision: ${formatError(err)}`);
      return;
    }
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
  }
```

(If `isBenignGateMiss` only accepts 404, extend it to 409 as well: a stale card is the same lost race.)

`src/chat-panel.ts`: add a trailing constructor parameter `private readonly onTeamPlanDecision: (threadId: string, gateId: string, decision: "approve" | "feedback" | "reject", feedback?: string) => Promise<void> = async () => {}` after `onDisbandTeam`, and a branch next to `disbandTeam`:

```ts
      } else if (m["type"] === "teamPlanDecision") {
        const d = m["decision"];
        if (d === "approve" || d === "feedback" || d === "reject") {
          p = this.onTeamPlanDecision(String(m["threadId"] ?? ""), String(m["gateId"] ?? ""), d,
                                      typeof m["feedback"] === "string" ? m["feedback"] : undefined);
        }
```

`src/extension.ts`: after `(teamId) => controller.disbandTeam(teamId)` in the `ChatPanel` constructor call, add `, (threadId, gateId, decision, feedback) => controller.decideTeamPlan(threadId, gateId, decision, feedback)`.

Every test stub implementing `BackendTaskClient` that the typecheck flags gets `decideTeamPlan: async () => ({ teamId: "", phase: null }),`.

- [ ] **Step 6: Run tests and typecheck**

Run:
```bash
npm run -w @crucible/editor-client build
cd apps/vscode-extension && npm run typecheck && perl -e 'alarm 300; exec @ARGV' npx vitest run test/controller.test.ts
cd webview-ui && perl -e 'alarm 300; exec @ARGV' npx vitest run
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension
git commit -m "feat(webview): the team plan card, its team chip, and a usable composer"
```

### Task 12: "Open board" links on team tool pills, and the stepper's implementation road

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/teams/TeamLinks.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/teams.ts`
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/teams/PhaseStepper.tsx`
- Test: `apps/vscode-extension/webview-ui/src/test/teams.test.ts`, `apps/vscode-extension/webview-ui/src/test/teamCard.test.tsx`

**Interfaces:**
- Produces:
  - `teams.ts::TEAM_TOOLS: ReadonlySet<string>` = `create_team`, `post_board`, `team_status`, `adopt_proposal`, `resume_team`, `disband_team`.
  - `teams.ts::teamForPill(event: ToolEventView, teams: Record<string, TeamSummaryView>): TeamSummaryView | null` — `create_team` resolves by the `team_id` in its output; the others by `args.team` (an id or a name; the newest team with that name wins).
  - `TeamLinks({ events })` — one "Open board" button per distinct team the pills name.
  - `PhaseStepper`: `DONE` with `endReason === "adopted"` keeps the "Plan adopted" ending; any other `DONE` walks the whole road and ends "Done"; `PAUSED` labels its step "Paused".

The links matter most for an **ended** team: its card has scrolled away and the pinned `TeamStrip` hides ended teams, so the main agent's `team_status` pill is the nearest way back to its board.

- [ ] **Step 1: Write the failing tests**

Add `TEAM_TOOLS` and `teamForPill` to the existing `../teams` import of `webview-ui/src/test/teams.test.ts`, then append:

```ts
describe("team tool pills", () => {
  const team = (id: string, name: string, createdAt: string) => ({
    teamId: id, name, goal: "", phase: "DONE", round: 1, maxRounds: 3, pausedReason: null,
    members: [], openProposals: [], usage: { requests: 0, budget: 0 }, createdAt });
  const teams = { "team-1": team("team-1", "auth", "2026-10-06T01:00:00Z"),
                  "team-2": team("team-2", "auth", "2026-10-06T02:00:00Z") };
  const pill = (tool: string, args: Record<string, unknown>, output?: string) => ({
    id: "p", tool, args, source: "execution" as const, output, done: true });

  it("resolves by id, by newest name, and by create_team's output", () => {
    expect(TEAM_TOOLS.has("post_board")).toBe(true);
    expect(teamForPill(pill("team_status", { team: "team-1" }), teams)?.teamId).toBe("team-1");
    expect(teamForPill(pill("post_board", { team: "auth" }), teams)?.teamId).toBe("team-2");
    expect(teamForPill(pill("create_team", {}, '{"team_id": "team-1"}'), teams)?.teamId)
      .toBe("team-1");
    expect(teamForPill(pill("read_file", { team: "auth" }), teams)).toBeNull();
    expect(teamForPill(pill("create_team", {}, "Error: limit"), teams)).toBeNull();
  });
});
```

Append to `webview-ui/src/test/teamCard.test.tsx` (it already renders team UI inside `TeamsContext` — reuse its provider helper):

```tsx
import { TeamLinks } from "../components/teams/TeamLinks";
import { PhaseStepper } from "../components/teams/PhaseStepper";

describe("team links and the stepper", () => {
  it("opens an ended team's board from a pill", () => {
    const openTeam = vi.fn();
    render(
      <TeamsContext.Provider value={{ teams: { "team-1": { ...TEAM, phase: "DONE" } },
                                          views: {}, openTeam }}>
        <TeamLinks events={[
          { id: "a", tool: "team_status", args: { team: "team-1" }, source: "execution", done: true },
          { id: "b", tool: "post_board", args: { team: "team-1" }, source: "execution", done: true },
        ]} />
      </TeamsContext.Provider>);
    const buttons = screen.getAllByRole("button", { name: `Open board of ${TEAM.name}` });
    expect(buttons).toHaveLength(1);                         // one link per team
    fireEvent.click(buttons[0]);
    expect(openTeam).toHaveBeenCalledWith("team-1");
  });

  it("shows the implemented road and pauses", () => {
    const { rerender } = render(
      <PhaseStepper phase="DONE" endReason="implemented" round={1} maxRounds={3} />);
    expect(screen.getByText("Implementing")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
    rerender(<PhaseStepper phase="DONE" endReason="adopted" round={1} maxRounds={3} />);
    expect(screen.getByText("Plan adopted")).toBeInTheDocument();
    rerender(<PhaseStepper phase="PAUSED" round={1} maxRounds={3} />);
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });
});
```

(`TEAM` is the fixture this file already defines; if it is named differently, use that one.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && perl -e 'alarm 300; exec @ARGV' npx vitest run src/test/teams.test.ts src/test/teamCard.test.tsx`
Expected: FAIL — `teamForPill` is not exported; `TeamLinks` cannot be resolved.

- [ ] **Step 3: Implement**

`webview-ui/src/types.ts` — `TeamSummaryView` gains `endReason?: string | null;`.

`webview-ui/src/teams.ts`:

```ts
export const TEAM_TOOLS: ReadonlySet<string> = new Set([
  "create_team", "post_board", "team_status", "adopt_proposal", "resume_team", "disband_team"]);

/** The team a main-agent tool pill is about, if the thread knows it. */
export function teamForPill(
  event: ToolEventView, teams: Record<string, TeamSummaryView>,
): TeamSummaryView | null {
  if (!TEAM_TOOLS.has(event.tool)) return null;
  if (event.tool === "create_team") {
    const match = /"team_id":\s*"([^"]+)"/.exec(event.output ?? "");
    return match ? teams[match[1]] ?? null : null;
  }
  const ref = typeof event.args.team === "string" ? event.args.team : "";
  if (ref === "") return null;
  if (teams[ref]) return teams[ref];
  const named = Object.values(teams).filter((t) => t.name === ref)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return named[0] ?? null;
}
```

(import `ToolEventView` from `./types` if `teams.ts` does not already).

Create `webview-ui/src/components/teams/TeamLinks.tsx`:

```tsx
import type { ToolEventView } from "../../types";
import { teamForPill } from "../../teams";
import { useTeamsUi } from "./TeamsContext";

/** "Open board" for each team the main agent's team tool pills name (post_board,
 * team_status, adopt_proposal, create_team, …) — the way back to an ended team's board. */
export function TeamLinks({ events }: { events: ToolEventView[] }) {
  const teamsUi = useTeamsUi();
  const seen = new Map<string, string>();
  for (const event of events) {
    const team = teamForPill(event, teamsUi.teams);
    if (team !== null) seen.set(team.teamId, team.name);
  }
  if (seen.size === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1.5">
      {[...seen].map(([teamId, name]) => (
        <button key={teamId} type="button" aria-label={`Open board of ${name}`}
          onClick={() => teamsUi.openTeam(teamId)}
          className="rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
          style={{ background: "var(--accent-bg)", color: "var(--color-accent-ink)" }}>
          Open board · {name}
        </button>
      ))}
    </div>
  );
}
```

`webview-ui/src/components/messages/AgentRow.tsx`: import `TeamLinks` and render it right after the track:

```tsx
        {pills.length > 0 && <ToolTrack events={pills} />}
        {pills.length > 0 && <TeamLinks events={pills} />}
```

`webview-ui/src/components/teams/PhaseStepper.tsx`: add an optional `endReason?: string | null` prop and replace the ended handling:

```tsx
export function PhaseStepper({ phase, round, maxRounds, endReason = null, mini = false }: {
  phase: string; round: number; maxRounds: number; endReason?: string | null; mini?: boolean;
}) {
  const at = INDEX[phase] ?? 1;
  // A plan adopted with nothing to implement ends after deliberation; an implemented team
  // walks the whole road (5C adds the review step's own state).
  const adoptedOnly = phase === "DONE" && endReason === "adopted";
  const ended = isTerminalTeam(phase) && !(phase === "DONE" && !adoptedOnly);
  const label = (step: string, i: number) =>
    i === 1 && at === 1 && !ended
      ? (phase === "DEADLOCKED" ? `Deadlocked · round ${round} of ${maxRounds}`
        : phase === "PAUSED" ? "Paused"
        : phase === "AWAITING_APPROVAL" ? "Awaiting approval"
        : `Deliberating · round ${round} of ${maxRounds}`)
      : step;
```

Leave the rest of the component as it is, except that the final badge text reads `phase === "FAILED" ? "Failed" : adoptedOnly ? "Plan adopted" : "Disbanded"` (an implemented `DONE` is no longer `ended`, so it renders the `Done` step instead of a badge; `done` for every step `i < at` already marks the road green, and `now` lights `Done` because `at === 4`). Pass `endReason={team.endReason}` from `TeamCard` and `TeamWindow`. (Interim until 5C: an implemented team's road shows the Review step as passed — 5C gives review its own state.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && perl -e 'alarm 300; exec @ARGV' npx vitest run && cd .. && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui
git commit -m "feat(webview): open a team's board from its tool pills; implementation road"
```

---

# Part F — Docs and verification

### Task 13: CLAUDE.md, full suites, live smoke

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

In the "Agent teams — foundations" bullet, replace the sentence that starts `**5A interim:** adoption ends the team` with:

```markdown
**Implementation (5B, plan `docs/superpowers/plans/2026-10-06-team-coordinator-5b-implementation.md`):** an adopted plan with assignments goes to `AWAITING_APPROVAL` when `approval_gate` is set — a `team_plan` Class-A gate (`PendingGate.team` set, `agent` null, so it never takes the composer or blocks notice turns: filter with `is_main()` / `!g.agent && !g.team`), resolved by `POST /v1/chat/threads/{id}/team-plan-decision {gate_id, decision: approve|feedback|reject, feedback?}` (404 unknown gate, 409 stale card, 422 bad feedback; feedback closes the proposal and runs one more round) — else straight to `IMPLEMENTING`, where each assignee starts with `Your assignment: … Files you own: … Shared files: …`. Check 1 (`TeamProtection` + `teams/scope.py`) refuses edits outside `IMPLEMENTING`, another member's files (`team_message <owner> instead`, a second try adds "already told"), and — with an approval gate — files outside the plan; helpers follow their member's team (`_team_membership_of`). Implementation reports are checked once in the loop (`teams/implementation.py`: `completed` with untouched files or an unanswered DM, `awaiting_peer` with nobody to wait on). `partial`/`failed` raise `member_blocked`; a report with every other member idle raises `stuck` (the reporter is excluded — it is still inside its own task); the third consecutive `stuck`, an exhausted budget (checked at every member/helper iteration top via `iteration_cb`, forcing every running loop to its final iteration; those `partial` reports are *interrupted* and owe their work) or three consecutive final `failed_transient` outcomes pause the team (`PAUSED`, timers cancelled; a transient burst's milestones become `notify` until the user speaks). `resume_team` runs only in a user turn (`turn_kind == "user"`), raises the budget (default per-member × members, capped at `CRUCIBLE_TEAM_MAX_BUDGET`) and restarts whoever owes work. **5B interim:** when every assignment is done the team ends `DONE` (`end_reason "implemented"`, an interim `done` milestone listing the files); a plan with no assignments still ends `DONE` (`"adopted"`). Team usage is summed in `_close_child` over members and helpers. Pills of `create_team`/`post_board`/`team_status`/`adopt_proposal`/`resume_team`/`disband_team` get "Open board" links (`TeamLinks`), the way back to an ended team's board.
```

- [ ] **Step 2: Full suites**

```bash
cd services/agentd-py && .venv/bin/pytest --color=no --timeout=120 > /tmp/py.txt 2>&1; echo exit=$?; tail -5 /tmp/py.txt
cd services/agentd-py && .venv/bin/ruff check agentd tests && .venv/bin/mypy agentd
npm run build && npm run typecheck && npm run test
cd apps/vscode-extension/webview-ui && perl -e 'alarm 600; exec @ARGV' npx vitest run
```

Expected: all green except the known pre-existing `test_command_only_step_runs_command_and_verifies`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): team implementation, plan approval, pauses and resume"
```

- [ ] **Step 4: Live smoke (dev host `vscode-p4`, CDP 9335, NIM nemotron-3-ultra, `CRUCIBLE_TEAMS_ENABLED=1`)**

After rebuilding, run **Developer: Reload Webviews** (and Reload Window for host changes — it restarts the backend and reaps live teams). Scenarios:

1. **Approval → implement → done:** ask for a two-member team with `approval_gate` on a small change with assignments. Expect the plan card above the composer (composer still usable), the 🔔 notice turn telling you, Approve, both members editing their own files, `● … finished` posts, the team ending `DONE` with the `done` milestone, and the stepper walking to Done.
2. **Feedback:** repeat, send feedback on the card. Expect your text on the board as `user`, the proposal closed, round n+1 starting.
3. **Ownership:** in a team whose plan gives `routes.py` to one member, ask the other (via `post_board`) to change `routes.py`. Expect `PATCH FAILED: routes.py is owned by … — team_message … instead` in its tab and a message to the owner.
4. **Budget pause + resume:** create a team with `budget: 6`. Expect `PAUSED — its request budget ran out`, interrupted members, then "resume the team" from you → `resume_team` → members continue.
5. **Open board from a pill:** after a team ends, scroll to an older `team_status` pill and click "Open board · <name>".

Record findings in the memory file `project_subagents_v2_spec.md`.
