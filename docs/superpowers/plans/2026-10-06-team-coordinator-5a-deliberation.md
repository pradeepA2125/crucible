# Team Coordinator 5A — Deliberation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 4's immediate-wake policy with a deterministic coordinator that runs a team's deliberation in rounds — explicit stances (tools or report fields, with a one-time redirect), per-member deadlines, transient re-queues, quorum, adoption at round end, deadlock and the main agent's exits — and show it on the board as round strips, verdicts and held footers.

**Architecture:** A pure `TeamStateMachine` (`apply(state, event) → (state, actions)`) holds every rule; an async `TeamCoordinator` (one per live team, held by `ChatController`) feeds it events, persists its state and executes its actions through a small `CoordinatorHost` protocol that `ChatController` implements (start/stop/force a member, milestones, phase broadcasts). Report-field stances are validated inside `ControllerLoop` through a new `report_check` hook; deadlines use a new `force_final()` switch on the loop. The webview's journey builder gains round, verdict and held items built from new activity kinds.

**Tech Stack:** Python 3.12 (FastAPI backend, pydantic, sqlite, pytest + pytest-asyncio), TypeScript (Zod editor-client, React webview, vitest).

**Spec:** `docs/superpowers/specs/2026-10-02-subagents-v2-design.md` §8.1–§8.4, §8.8 (milestone rows `adopted`, `deadlock`, `member_lost`, `ended`), §8.9 (disband), §8.10 (rewind refusal for live teams), §8.11, §3.9 (edit type removed outside implementation), §3.11 (deliberation iteration cap 40), §7.5–§7.6 (round headers, teaching); `docs/superpowers/specs/2026-10-05-team-activity-ui-design.md` §9. Approved mockup: `.superpowers/brainstorm/teams-activity-ui/content/team-activity-ui.html` (the round-2 moment: `.round`, `.verdict`, `.held`, `.sys`).

**Part index (Phase 5 is split in three; this plan is 5A):**

| Part | Scope | This plan |
|---|---|---|
| 5A Deliberation | state machine, coordinator, rounds + deadlines, report-field stances + redirect, adoption, deadlock + `adopt_proposal`/`post_board`, quorum, transient re-queue, milestones (`adopted`, `deadlock`, `member_lost`, `ended`), trace, rewind refusal, round strips / verdicts / held footers / Round-keyed chapters / card progress | ✅ |
| 5B Implementation | approval gate + `team_plan` card, assignment validation at propose time (§8.3: files, ownership, protected paths, can-edit), phase-gated edits + ownership at Check 1, implementation report redirects, stuck detection, budget + `PAUSED` + `resume_team` + the phase-aware budget hint (§3.11), `transient_burst` | later |
| 5C Review + teardown | closing proposal + cycles, objection routing, `done` milestone, rewind deletion, Edits section + assignment rows | later |

**Interim in 5A (replaced in 5B):** an adopted proposal ends the team `DONE` with `end_reason = "adopted"` (the `adopted` milestone tells the main agent what was adopted). There is no implementation phase yet.

## Global Constraints

- Branch `feat/subagents-v2`, base `6942fe9`. Never push.
- Commit format `type(scope): short description`, ending with the two trailer lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz`.
- Pytest: never `-q` (pyproject sets it), never piped; redirect to a file and check `$?`; add `--timeout=120`.
  Example: `pytest tests/test_x.py --color=no --timeout=120 > /tmp/x.txt 2>&1; echo exit=$?; tail -5 /tmp/x.txt`.
- Python venv: `/Users/pradeepkumar/projects/AI editor/services/agentd-py/.venv/bin/python` (from a worktree, run from that worktree's `services/agentd-py`; the worktree's code is imported first).
- Vitest under `perl -e 'alarm N; exec @ARGV' npx vitest run …` (macOS has no `timeout`).
- After an editor-client change: `npm run -w @crucible/editor-client build` before the extension typecheck.
- `CRUCIBLE_TEAMS_ENABLED` stays default **off**; tests opt in with `monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")` and `CRUCIBLE_SUBAGENTS_ENABLED=1`.
- Prompts describe options by what each does and when it fits — never rank one tool or action above another.
- Text from agents reaching another agent is framed with `frame(...)` (spec §3.10); UI author/kind come from columns, never text.
- New env vars (default-off flags need the three opt-in sites; this plan adds only `CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC`, default 900 — a limit, not a flag, so no opt-in sites).
- `/live` team rows carry only fields that change at activation boundaries (the dedup-signature invariant).
- `GET` routes stay read-only (`tests/test_get_routes_read_only.py`); this plan adds no GET route.

## Review Focus

1. **The last reporter starts the next round from inside its own task.** `TeamCoordinator.on_report` runs inside the finishing member's `_activate`; enqueueing that same member again would raise `ActivationInProgress`. Expected: next-round activations are scheduled with `call_soon` and start after the task ends — pinned by `test_round_two_restarts_the_last_reporter` (Task 8).
2. **A post made mid-round never reaches a running member.** Expected: no inbox marker, a `held` activity naming the round it lands in, and the post in that member's next-round delta — `test_mid_round_post_is_held_not_delivered` (Task 8).
3. **A model that resubmits the same invalid report forever.** Expected: byte-identical resubmissions count as malformed; after `_MAX_MALFORMED` the report is accepted with the invalid entries dropped and the member stays in the quorum — `test_report_check_identical_resubmission_exhausts_into_acceptance` (Task 2) and `test_invalid_entries_dropped_on_final` (Task 3).
4. **A member parked at a command card for 20 minutes.** Expected: its deadline clock does not run while it waits (and not while throttled by the limiter) — `test_active_seconds_exclude_gate_and_limiter_waits` (Task 1) and `test_deadline_reschedules_while_paused` (Task 7).
5. **The user stops one member of a two-member team mid-round.** Expected: it leaves the quorum, a `member_lost` milestone, then the team ends `FAILED` (quorum below 2) and the other member is stopped — `test_quorum_below_two_fails_the_team` (Task 5) and `test_user_stop_loses_quorum` (Task 8).

## File Structure

| File | Responsibility |
|---|---|
| `services/agentd-py/agentd/providers/usage.py` | `UsageMeter.peek` (non-destructive read) |
| `services/agentd-py/agentd/subagents/runtime.py` | gate-wait accounting on `AgentHandle`, `paused_seconds`, `AgentSupervisor.keep` |
| `services/agentd-py/agentd/chat/controller_loop.py` | `force_final()`, `ReportVerdict`, `report_check` hook |
| `services/agentd-py/agentd/teams/service.py` | prepare/commit split for stances and proposals, `expected_stances`, round-aware header, evaluation line, `system_post(payload=)` |
| `services/agentd-py/agentd/teams/report_fields.py` (new) | `ReportFields` — validates and posts a member's report fields |
| `services/agentd-py/agentd/chat/controller_prompts.py` | team report schema fields, `_TEAM_BLOCK` / `_TEAMS_MAIN_BLOCK` text |
| `services/agentd-py/agentd/teams/adoption.py` (new) | pure `evaluate_round` |
| `services/agentd-py/agentd/teams/state_machine.py` (new) | pure `TeamStateMachine` |
| `services/agentd-py/agentd/teams/milestones.py` (new) | compact milestone headlines + bodies |
| `services/agentd-py/agentd/teams/trace.py` (new) | `CoordinatorTrace` (jsonl) |
| `services/agentd-py/agentd/teams/coordinator.py` (new) | `TeamCoordinator`, `CoordinatorHost` |
| `services/agentd-py/agentd/teams/{config,models,store,tools}.py` | round timeout, activity kinds, `set_in_quorum` / `live_team_names`, main tools |
| `services/agentd-py/agentd/subagents/notices.py` | team notice author/body |
| `services/agentd-py/agentd/chat/controller.py` | host implementation, wiring, `_activate` changes, live/summary fields, milestones |
| `services/agentd-py/agentd/api/routes.py` | rewind 409 for live teams |
| `apps/editor-client/src/contracts/task-contracts.ts`, `src/client/http-backend-client.ts` | `roundProgress` on live teams, `evaluation` on summaries |
| `apps/vscode-extension/webview-ui/src/{types.ts,teamJourney.ts,teamChapters.ts,teams.ts}` | new item kinds, Round chapters, progress text |
| `apps/vscode-extension/webview-ui/src/components/teams/{Journey,RoundItems,MemberView,TeamCard,PhaseStepper}.tsx` | round strip, verdict, held footer, adopted card, card progress bar |

---

# Part A — Loop and runtime seams

### Task 1: Meter peek, gate-wait accounting, kept leftovers

**Files:**
- Modify: `services/agentd-py/agentd/providers/usage.py`
- Modify: `services/agentd-py/agentd/subagents/runtime.py`
- Test: `services/agentd-py/tests/test_runtime_clock.py` (new)

**Interfaces:**
- Produces: `UsageMeter.peek(owner: str) -> Usage`; `AgentHandle.gate_wait_s: float`, `AgentHandle.waiting_since: datetime | None`; `paused_seconds(handle: AgentHandle, now: datetime) -> float` (gate waits only); `active_seconds(handle: AgentHandle, now: datetime) -> float | None` (wall time since `started_at` minus gate waits minus the owner's limiter wait; `None` when the activation has not started); `AgentSupervisor.keep(agent_id: str, items: list[InboxItem]) -> None`.

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_runtime_clock.py`:

```python
"""The round deadline counts active time only (spec v2 §8.3)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentd.providers.usage import METER, UsageMeter
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.subagents.inbox import InboxItem
from agentd.subagents.runtime import AgentHandle, AgentSupervisor, active_seconds, paused_seconds


def _handle(agent_id: str = "agent-x") -> AgentHandle:
    ctx = AgentContext(agent_id=agent_id, name="general-purpose", label="alice", depth=1,
                       parent_agent_id=None, permission="default",
                       allowed_types=("tool_call", "edit", "progress", "report"), persona="",
                       max_iters=10)
    return AgentHandle(context=ctx, definition=BUILTIN_AGENTS["general-purpose"], prompt="p",
                       thread_id="t", turn_id="u")


def test_peek_does_not_consume() -> None:
    meter = UsageMeter()
    meter.record("a", requests=2, wait_ms=500)
    assert meter.peek("a").wait_ms == 500
    assert meter.peek("a").requests == 2
    assert meter.take("a").requests == 2
    assert meter.peek("a").requests == 0


def test_gate_waits_accumulate_across_status_changes() -> None:
    sup = AgentSupervisor(1)
    handle = _handle()
    sup.set_status(handle, "running")
    sup.set_status(handle, "waiting")
    handle.waiting_since = datetime.now(UTC) - timedelta(seconds=30)   # parked 30 s
    sup.set_status(handle, "running")
    assert 29.5 < handle.gate_wait_s < 31
    assert handle.waiting_since is None


def test_active_seconds_exclude_gate_and_limiter_waits() -> None:
    handle = _handle("agent-clock")
    now = datetime.now(UTC)
    assert active_seconds(handle, now) is None                 # not started yet
    handle.started_at = now - timedelta(seconds=100)
    handle.gate_wait_s = 20
    handle.waiting_since = now - timedelta(seconds=10)         # parked right now
    METER.record("agent-clock", wait_ms=15_000)                # throttled 15 s
    try:
        assert paused_seconds(handle, now) == 30
        assert abs(active_seconds(handle, now) - 55) < 0.01    # 100 - 30 - 15
    finally:
        METER.take("agent-clock")


def test_keep_puts_items_back_without_waking() -> None:
    woken: list[str] = []
    sup = AgentSupervisor(1, on_leftover=lambda h, items: woken.append(h.agent_id))
    sup.keep("agent-k", [InboxItem(kind="note", text="later", wakes=True)])
    assert woken == []
    assert [i.text for i in sup.drain("agent-k")] == ["later"]
```

- [ ] **Step 2: Run to verify failure**

Run (from `services/agentd-py`): `.venv/bin/python -m pytest tests/test_runtime_clock.py --color=no --timeout=120 > /tmp/t1.txt 2>&1; echo exit=$?; tail -5 /tmp/t1.txt`
Expected: exit≠0 — `ImportError: cannot import name 'active_seconds'`.

- [ ] **Step 3: Implement**

`agentd/providers/usage.py`, add to `UsageMeter` after `record`:

```python
    def peek(self, owner: str) -> Usage:
        """The owner's counts so far, left in place — the deadline clock reads limiter
        waits mid-activation (spec v2 §8.3); `take` still consumes them at its end."""
        usage = self._by_owner.get(owner)
        if usage is None:
            return Usage()
        return Usage(requests=usage.requests, prompt_tokens=usage.prompt_tokens,
                     completion_tokens=usage.completion_tokens, wait_ms=usage.wait_ms)
```

`agentd/subagents/runtime.py`:

1. Add the import `from agentd.providers.usage import METER` with the other imports.
2. Add two fields to `AgentHandle`, after `activation_seq`:

```python
    # Time parked at a user gate (`waiting`) this activation, and the open wait's start —
    # the round deadline clock pauses for both (spec v2 §8.3).
    gate_wait_s: float = 0.0
    waiting_since: datetime | None = None
```

3. Replace `AgentSupervisor.set_status` with:

```python
    def set_status(self, handle: AgentHandle, status: str) -> None:
        now = datetime.now(UTC)
        if status == "waiting" and handle.waiting_since is None:
            handle.waiting_since = now
        elif status != "waiting" and handle.waiting_since is not None:
            handle.gate_wait_s += (now - handle.waiting_since).total_seconds()
            handle.waiting_since = None
        handle.status = status
        if status == "running" and handle.started_at is None:
            handle.started_at = now
        if status in IDLE_STATUSES:
            handle.ended_at = now
        if self._on_status is not None:
            self._on_status(handle)
```

4. Add after `agent_channel`:

```python
def paused_seconds(handle: AgentHandle, now: datetime) -> float:
    """Gate waits so far this activation, including one still open (spec v2 §8.3)."""
    open_wait = (now - handle.waiting_since).total_seconds() if handle.waiting_since else 0.0
    return handle.gate_wait_s + open_wait


def active_seconds(handle: AgentHandle, now: datetime) -> float | None:
    """The round deadline's clock: time since the activation took its slot, minus time
    parked at a user gate and time waiting on the provider rate limiter (spec v2 §8.3).
    None until the activation has started."""
    if handle.started_at is None:
        return None
    limiter = METER.peek(handle.agent_id).wait_ms / 1000
    return (now - handle.started_at).total_seconds() - paused_seconds(handle, now) - limiter
```

5. Add to `AgentSupervisor`, after `drain`:

```python
    def keep(self, agent_id: str, items: list[InboxItem]) -> None:
        """Put items back without waking anyone: a team member's leftovers during
        deliberation wait for its next round (spec v2 §3.6, E5)."""
        self._inboxes.setdefault(agent_id, []).extend(items)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_runtime_clock.py tests/test_subagent_runtime.py --color=no --timeout=120 > /tmp/t1.txt 2>&1; echo exit=$?; tail -5 /tmp/t1.txt`
Expected: exit=0. (If `tests/test_subagent_runtime.py` does not exist, list `tests/ | grep -i runtime` and run those.)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/usage.py services/agentd-py/agentd/subagents/runtime.py services/agentd-py/tests/test_runtime_clock.py
git commit -m "feat(subagents): active-time clock and kept leftovers for team rounds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 2: `ControllerLoop.force_final()` and the `report_check` hook

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py`
- Test: `services/agentd-py/tests/test_controller_loop_report_check.py` (new)

**Interfaces:**
- Consumes: the existing `ControllerLoop(agent=…, phase_sm=ControllerPhaseSM(start="AGENT"))`, `run(...)`.
- Produces:
  - `ControllerLoop.force_final() -> None` — the next iteration is final: types narrow to `["report"]`, the report is accepted (status `partial`), guards and redirects are skipped as on the iteration cap.
  - `@dataclass(frozen=True) class ReportVerdict: message: str | None = None; malformed: bool = False` (module level, exported).
  - `ReportCheck = Callable[[dict[str, object], bool], ReportVerdict]` — called with the report action and `final`; `message is None` accepts.
  - `run(..., report_check: ReportCheck | None = None)`.

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_controller_loop_report_check.py`:

```python
"""Report fields are validated in the loop (spec v2 §8.3); deadlines force the final
iteration (§8.3, §3.11)."""
from __future__ import annotations

import pytest

from agentd.chat.controller_loop import ControllerLoop, ReportVerdict
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.subagents.context import AgentContext
from agentd.tools.aggregating import AggregatingToolRegistry

REPORT = {"type": "report", "thought": "t", "summary": "s", "status": "completed"}
TOOL = {"type": "tool_call", "thought": "look", "tool": "list_directory", "args": {}}


class _Engine:
    def __init__(self, script: list[dict[str, object]]) -> None:
        self.script = script
        self.types: list[list[str]] = []

    async def create_controller_step(self, plan_context, history, tool_definitions,
                                     allowed_types=None, **_k):  # type: ignore[no-untyped-def]
        self.types.append(list(allowed_types or []))
        return self.script[min(len(self.types) - 1, len(self.script) - 1)]


def _loop(engine: _Engine) -> ControllerLoop:
    ctx = AgentContext(agent_id="agent-r", name="general-purpose", label="alice", depth=1,
                       parent_agent_id=None, permission="default",
                       allowed_types=("tool_call", "progress", "report"), persona="",
                       max_iters=10)
    return ControllerLoop(engine, AggregatingToolRegistry([]), EventBroadcaster(),
                          channel_id="c", phase_sm=ControllerPhaseSM(start="AGENT"), agent=ctx)


@pytest.mark.asyncio
async def test_report_check_redirect_then_accept() -> None:
    calls: list[bool] = []

    def check(resp, final):  # type: ignore[no-untyped-def]
        calls.append(final)
        return ReportVerdict(message="state P1 first") if len(calls) == 1 else ReportVerdict()

    engine = _Engine([REPORT])
    outcome = await _loop(engine).run({"goal": "g"}, max_iters=10, report_check=check)
    assert outcome.kind == "report"
    assert calls == [False, False]
    assert "state P1 first" in str(outcome.history)


@pytest.mark.asyncio
async def test_report_check_identical_resubmission_exhausts_into_acceptance() -> None:
    finals: list[bool] = []

    def check(resp, final):  # type: ignore[no-untyped-def]
        finals.append(final)
        if final:
            return ReportVerdict()
        return ReportVerdict(message="bad id P9", malformed=len(finals) > 1)

    engine = _Engine([REPORT])
    outcome = await _loop(engine).run({"goal": "g"}, max_iters=20, report_check=check)
    assert outcome.kind == "report"
    assert finals[-1] is True                     # accepted with invalid entries dropped
    assert len(finals) <= 6                       # bounded by _MAX_MALFORMED, not max_iters


@pytest.mark.asyncio
async def test_force_final_narrows_and_accepts_partial() -> None:
    engine = _Engine([TOOL, REPORT])
    loop = _loop(engine)
    loop.force_final()
    seen: list[bool] = []
    outcome = await loop.run({"goal": "g"}, max_iters=10,
                             report_check=lambda r, f: (seen.append(f), ReportVerdict())[1])
    assert engine.types[0] == ["report"]
    assert outcome.payload == {"status": "partial"}
    assert seen == [True]
```

(`AggregatingToolRegistry` lives where `ChatController` imports it from — check with `grep -rn "class AggregatingToolRegistry" agentd` and fix the import line if it differs.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_controller_loop_report_check.py --color=no --timeout=120 > /tmp/t2.txt 2>&1; echo exit=$?; tail -5 /tmp/t2.txt`
Expected: exit≠0 — `ImportError: cannot import name 'ReportVerdict'`.

- [ ] **Step 3: Implement**

In `agentd/chat/controller_loop.py`:

1. Module level, after the imports (with the other dataclasses/type aliases):

```python
@dataclass(frozen=True)
class ReportVerdict:
    """What a team member's report fields came to (spec v2 §8.3). `message` None accepts
    the report; otherwise it is shown to the model, which reports again. `malformed` marks
    a byte-identical resubmission of a refused report, counted like any malformed action."""
    message: str | None = None
    malformed: bool = False


ReportCheck = Callable[[dict[str, object], bool], ReportVerdict]
```

(Add `from dataclasses import dataclass` if the module does not import it yet.)

2. In `__init__`, next to `self._iteration = 0`: `self._force_final = False`.

3. Add the method after `_allowed_modes_for_current_phase`:

```python
    def force_final(self) -> None:
        """Make the next iteration the final one (spec v2 §8.3 deadline, §3.11 budget): the
        schema narrows to report, and the report is accepted as it stands."""
        self._force_final = True
```

4. In `_allowed_action_types`, change `if self._iteration >= self._max_iters:` to
   `if self._iteration >= self._max_iters or self._force_final:`.

5. Thread the hook: add `report_check: ReportCheck | None = None,` to `run(...)` (after `status_tail`) and to `_iterate(...)` (after `status_tail`), and pass `report_check=report_check,` in `run`'s `_iterate` call.

6. In the `if atype == "report":` branch, change `final = iteration >= max_iters` to
   `final = iteration >= max_iters or self._force_final`, and insert right after the
   `if still_open and not final:` block (before `history.append(assistant_turn(resp))`):

```python
                if report_check is not None:
                    verdict = report_check(resp, final)
                    if verdict.message is not None and verdict.malformed:
                        # A byte-identical resubmission is malformed (spec v2 §8.3) — the
                        # counter was reset for this accepted action, so continue the streak.
                        consecutive_malformed = prev_malformed + 1
                        if consecutive_malformed > _MAX_MALFORMED:
                            # Exhausted by report validation alone: accept with the invalid
                            # entries dropped, so the member stays in the quorum.
                            verdict = report_check(resp, True)
                    if verdict.message is not None:
                        history.append(assistant_turn(resp))
                        history.append({"role": "tool_result", "tool": "",
                                        "content": verdict.message})
                        continue
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_controller_loop_report_check.py tests/test_controller_loop*.py --color=no --timeout=120 > /tmp/t2.txt 2>&1; echo exit=$?; tail -5 /tmp/t2.txt`
Expected: exit=0.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/tests/test_controller_loop_report_check.py
git commit -m "feat(chat): force_final and a report_check hook on the agent loop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 3: Stances and proposals on the report

**Files:**
- Modify: `services/agentd-py/agentd/teams/service.py`
- Create: `services/agentd-py/agentd/teams/report_fields.py`
- Test: `services/agentd-py/tests/test_team_report_fields.py` (new); `tests/test_team_service.py` (header assertions, if any)

**Interfaces:**
- Consumes: `ReportVerdict` (Task 2).
- Produces (`TeamService`):
  - `@dataclass(frozen=True) class PreparedPost: kind: str; text: str; ref_id: str | None; payload: dict[str, Any]; closes: tuple[int, ...] = ()` (module level).
  - `prepare_agree(team_id, author, proposal_id, note=None) -> PreparedPost`
  - `prepare_object(team_id, author, proposal_id, reason, evidence) -> PreparedPost`
  - `prepare_propose(team_id, author, text, assignments, shared_files=None, supersedes=None, *, also_stated: frozenset[int] = frozenset()) -> PreparedPost`
  - `commit(team_id, author, prepared, counters=None, *, count=False) -> TeamPost`
  - `expected_stances(team_id, label) -> list[str]` — open proposals of earlier rounds (`round < team.round`) by others with no stance from `label`; empty outside `DELIBERATING`.
  - `system_post(team_id, text, payload=None) -> TeamPost`.
  - `render_delta_posts(team_id, label, until_seq: int | None = None)` — a round's input stops at the round's cutoff seq (spec v2 E5: a post made after the round started waits for the next round).
  - `agree`, `object_`, `propose` keep their signatures (now `commit(prepare_…)`).
- Produces (`agentd/teams/report_fields.py`): `class ReportFields` — `ReportFields(service, team_id, label, counters, on_dropped=lambda errors: None)`; callable `(resp: dict, final: bool) -> ReportVerdict`.

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_team_report_fields.py`:

```python
"""Stances and proposals carried on a member's report (spec v2 §8.3)."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.report_fields import ReportFields
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
from agentd.teams.store import TeamStore


def _svc(tmp_path: Path) -> tuple[TeamService, TeamStore, str]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    now = datetime.now(UTC)
    store.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                                 max_rounds=3, budget=100, created_turn_id="u",
                                 created_at=now, round=2))
    for label in ("alice", "bob"):
        store.add_member(TeamMember(team_id="team-1", agent_id=f"agent-{label}", label=label))
    store.append_post("team-1", author="main", kind="proposal", text="P1 plan", round=0,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})
    (tmp_path / "a.py").write_text("x = 1\n")
    svc = TeamService(store, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    return svc, store, "team-1"


def _report(**fields):  # type: ignore[no-untyped-def]
    return {"type": "report", "thought": "t", "summary": "s", "status": "completed", **fields}


def test_expected_stances(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    assert svc.expected_stances(tid, "alice") == ["P1"]
    svc.agree(tid, "alice", "P1")
    assert svc.expected_stances(tid, "alice") == []
    store.update_team(tid, phase="DEADLOCKED")
    assert svc.expected_stances(tid, "bob") == []


def test_missing_stance_redirects_once(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    first = check(_report(), False)
    assert first.message is not None and "P1" in first.message and not first.malformed
    assert '"stances"' in first.message
    assert check(_report(), False).message is None          # accepted the second time
    assert [p.kind for p in store.posts(tid)] == ["proposal"]   # nothing posted


def test_valid_stances_and_proposal_are_posted(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    verdict = check(_report(
        stances=[{"proposal_id": "P1", "stance": "object", "reason": "misses a.py",
                  "evidence": {"files": ["a.py"], "line": 1}}],
        proposal={"text": "P2 plan", "assignments": [
            {"member": "alice", "part": "api", "files": ["a.py"]}]}), False)
    assert verdict.message is None
    kinds = [(p.author, p.kind, p.ref_id) for p in store.posts(tid)]
    assert kinds[1:] == [("alice", "object", "P1"), ("alice", "proposal", None)]
    assert store.posts(tid)[-1].round == 2


def test_invalid_entry_refused_then_identical_is_malformed(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    bad = _report(stances=[{"proposal_id": "P9", "stance": "agree"}])
    first = check(bad, False)
    assert first.message is not None and "P9" in first.message and not first.malformed
    assert check(bad, False).malformed is True
    assert len(store.posts(tid)) == 1


def test_invalid_entries_dropped_on_final(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    dropped: list[list[str]] = []
    check = ReportFields(svc, tid, "alice", ActivationCounters(), on_dropped=dropped.append)
    verdict = check(_report(stances=[{"proposal_id": "P9", "stance": "agree"},
                                     {"proposal_id": "P1", "stance": "agree", "note": "ok"}]),
                    True)
    assert verdict.message is None
    assert [(p.kind, p.ref_id) for p in store.posts(tid)][1:] == [("agree", "P1")]
    assert dropped and "P9" in dropped[0][0]


def test_delta_stops_at_the_round_cutoff(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    store.append_post(tid, author="alice", kind="post", text="late news", round=2)
    text, top, handed = svc.render_delta_posts(tid, "bob", until_seq=1)
    assert top == 1 and [p.seq for p in handed] == [1] and "late news" not in text
    text, top, _ = svc.render_delta_posts(tid, "bob")
    assert top == 2 and "late news" in text


def test_round_headers(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    text, _top = svc.render_delta(tid, "bob")
    assert text.startswith("Round 2 of 3: others have posted their views.")
    store.update_team(tid, round=1)
    text, _top = svc.render_delta(tid, "bob")
    assert text.startswith("Round 1 of 3: the main agent proposed P1.")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_report_fields.py --color=no --timeout=120 > /tmp/t3.txt 2>&1; echo exit=$?; tail -5 /tmp/t3.txt`
Expected: exit≠0 — `ModuleNotFoundError: No module named 'agentd.teams.report_fields'`.

- [ ] **Step 3: Implement the service split**

In `agentd/teams/service.py`:

1. Add `from dataclasses import dataclass, field` (keep the existing `dataclass` import) and define after `AgentInfo`:

```python
@dataclass(frozen=True)
class PreparedPost:
    """A validated stance or proposal not yet on the board: a report's fields are all
    checked before any is posted (spec v2 §8.3)."""
    kind: str
    text: str
    ref_id: str | None
    payload: dict[str, Any]
    closes: tuple[int, ...] = ()
```

2. Replace `propose`, `agree` and `object_` with the prepare/commit pair:

```python
    def prepare_propose(self, team_id: str, author: str, text: object, assignments: object,
                        shared_files: object = None, supersedes: object = None, *,
                        also_stated: frozenset[int] = frozenset()) -> PreparedPost:
        team = self._team(team_id)
        self._require_phase(team, ("DELIBERATING",), "team_propose")
        body = check_text(text, "proposal")
        roster = self._roster(team_id)
        stances = self.stances(team_id)
        owed = [p.proposal_id for p in self.open_proposals(team_id)
                if p.author != author and (p.round or 0) < team.round
                and author not in stances.get(p.seq, {}) and p.seq not in also_stated]
        if owed:
            raise TeamInputError(
                f"State your stance on {', '.join(owed)} first (team_agree, with a note for "
                "small changes, or team_object).")
        if not isinstance(assignments, list):
            raise TeamInputError("assignments must be a list of {member, part, files}")
        parts: list[dict[str, object]] = []
        for i, item in enumerate(assignments):
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
        shared = [str(f) for f in shared_files] if isinstance(shared_files, list) else []
        closes: list[TeamPost] = []
        if supersedes is not None:
            if not isinstance(supersedes, list):
                raise TeamInputError("supersedes must be a list of proposal ids")
            closes = [self._open_proposal(team_id, raw) for raw in supersedes]
        return PreparedPost(
            kind="proposal", text=body, ref_id=None,
            payload={"assignments": parts, "shared_files": shared,
                     "supersedes": [p.proposal_id for p in closes]},
            closes=tuple(p.seq for p in closes))

    def prepare_agree(self, team_id: str, author: str, proposal_id: object,
                      note: object = None) -> PreparedPost:
        team = self._team(team_id)
        proposal = self._open_proposal(team_id, proposal_id)
        self._stance_phase(team, proposal, "team_agree")
        if proposal.author == author:
            raise TeamInputError("your own proposal already counts as your agreement")
        payload = {"note": check_text(note, "note")} if note not in (None, "") else {}
        return PreparedPost(kind="agree", text=str(payload.get("note", "")),
                            ref_id=proposal.proposal_id, payload=payload)

    def prepare_object(self, team_id: str, author: str, proposal_id: object, reason: object,
                       evidence: object) -> PreparedPost:
        team = self._team(team_id)
        proposal = self._open_proposal(team_id, proposal_id)
        self._stance_phase(team, proposal, "team_object")
        body = check_text(reason, "reason")
        assigned = {f for a in proposal.payload.get("assignments", []) for f in a.get("files", [])}
        assigned |= set(proposal.payload.get("shared_files", []))
        checked = validate_evidence(
            evidence, workspace=self._workspace, assignment_files=assigned,
            post_exists=lambda seq: self._store.get_post(team_id, seq) is not None)
        return PreparedPost(kind="object", text=body, ref_id=proposal.proposal_id,
                            payload={"evidence": checked})

    def commit(self, team_id: str, author: str, prepared: PreparedPost,
               counters: ActivationCounters | None = None, *, count: bool = False) -> TeamPost:
        team = self._team(team_id)
        if count:
            self._count(counters, [])
        post = self._store.append_post(
            team_id, author=author, kind=prepared.kind, text=prepared.text,
            ref_id=prepared.ref_id, round=team.round, payload=prepared.payload)
        for seq in prepared.closes:
            self._store.close_proposal(team_id, seq, "superseded")
        return self._emit(team, post)

    def propose(self, team_id: str, author: str, text: object, assignments: object,
                shared_files: object = None, supersedes: object = None,
                counters: ActivationCounters | None = None) -> TeamPost:
        return self.commit(team_id, author, self.prepare_propose(
            team_id, author, text, assignments, shared_files, supersedes), counters, count=True)

    def agree(self, team_id: str, author: str, proposal_id: object,
              note: object = None) -> TeamPost:
        return self.commit(team_id, author, self.prepare_agree(team_id, author, proposal_id, note))

    def object_(self, team_id: str, author: str, proposal_id: object, reason: object,
                evidence: object) -> TeamPost:
        return self.commit(team_id, author, self.prepare_object(
            team_id, author, proposal_id, reason, evidence))

    def expected_stances(self, team_id: str, label: str) -> list[str]:
        """Open proposals from earlier rounds that `label` has no stance on (spec v2 §8.3)."""
        team = self._store.get_team(team_id)
        if team is None or team.phase != "DELIBERATING":
            return []
        stances = self.stances(team_id)
        return [p.proposal_id for p in self.open_proposals(team_id)
                if p.author != label and (p.round or 0) < team.round
                and label not in stances.get(p.seq, {})]
```

3. Replace `system_post`:

```python
    def system_post(self, team_id: str, text: str,
                    payload: dict[str, Any] | None = None) -> TeamPost:
        team = self._store.get_team(team_id)
        assert team is not None
        return self._emit(team, self._store.append_post(
            team_id, author="system", kind="system", text=text, payload=payload))
```

4. Give `render_delta_posts` the round cutoff — replace its signature and first lines:

```python
    def render_delta_posts(
        self, team_id: str, label: str, until_seq: int | None = None,
    ) -> tuple[str, int, list[TeamPost]]:
        """The member's inbox delta, its top seq, and the posts by others it covers — what
        the member is 'handed' (the took_up / picked_up record, spec 2026-10-05 §4.2).
        `until_seq` is the round's cutoff: a later post waits for the next round (E5)."""
        team = self._store.get_team(team_id)
        member = self._store.member(team_id, label)
        assert team is not None and member is not None
        visible = self._store.posts(team_id, since_seq=member.delivered_seq, viewer=label)
        if until_seq is not None:
            visible = [p for p in visible if p.seq <= until_seq]
```

(the rest of the method is unchanged).

5. Replace `_header` with the round-aware text (spec §7.6):

```python
    def _header(self, team: TeamRecord, first: bool) -> str:
        n, total = team.round, team.max_rounds
        kickoff = self._store.get_post(team.team_id, 1)
        if team.phase != "DELIBERATING":
            lead = f"Team {team.name!r} — phase {team.phase}."
        elif n == 1 and kickoff is not None and kickoff.kind == "proposal":
            lead = (f"Round 1 of {total}: the main agent proposed {kickoff.proposal_id}. "
                    "Check the claims relevant to your role, then state your stance.")
        elif n == 1:
            lead = (f"Round 1 of {total}: the main agent asked for proposals. Check the code "
                    "your role covers, then propose an approach (team_propose, or the "
                    "proposal field of your report).")
        else:
            lead = (f"Round {n} of {total}: others have posted their views. Check the claims "
                    "relevant to your role, then state your stance on each open proposal.")
        lines = [lead]
        if first:
            lines.append(f"Goal: {team.goal}")
        lines.append("What you post this round reaches the others at the next round. Report "
                     "when your part of this round is done; your stances can ride on the "
                     "report.")
        return "\n".join(lines)
```

- [ ] **Step 4: Create `agentd/teams/report_fields.py`**

```python
"""A team member's report fields (spec v2 §8.3): stances and a proposal, validated before
the report is accepted and posted exactly as the tools would post them."""
from __future__ import annotations

import json
from collections.abc import Callable

from agentd.chat.controller_loop import ReportVerdict
from agentd.teams.service import ActivationCounters, PreparedPost, TeamService
from agentd.teams.validation import TeamInputError, parse_proposal_id

_SHAPE = ('"stances": [{"proposal_id": "P1", "stance": "agree", "note": "what you checked"}] '
          'or [{"proposal_id": "P1", "stance": "object", "reason": "...", '
          '"evidence": {"files": ["path"], "line": 12}}]')


class ReportFields:
    """One per member activation: the missing-stance redirect is used at most once."""

    def __init__(self, service: TeamService, team_id: str, label: str,
                 counters: ActivationCounters,
                 on_dropped: Callable[[list[str]], None] = lambda _errors: None) -> None:
        self._svc = service
        self._team_id = team_id
        self._label = label
        self._counters = counters
        self._on_dropped = on_dropped
        self._redirected = False
        self._last_refused: str | None = None

    def __call__(self, resp: dict[str, object], final: bool) -> ReportVerdict:
        errors: list[str] = []
        prepared: list[PreparedPost] = []
        stated: set[int] = set()
        raw = resp.get("stances") or []
        if not isinstance(raw, list):
            errors.append("stances must be a list")
            raw = []
        for i, entry in enumerate(raw):
            try:
                if not isinstance(entry, dict):
                    raise TeamInputError("must be an object")
                stance = entry.get("stance")
                if stance == "agree":
                    p = self._svc.prepare_agree(self._team_id, self._label,
                                                entry.get("proposal_id"), entry.get("note"))
                elif stance == "object":
                    p = self._svc.prepare_object(self._team_id, self._label,
                                                 entry.get("proposal_id"), entry.get("reason"),
                                                 entry.get("evidence"))
                else:
                    raise TeamInputError('stance must be "agree" or "object"')
                prepared.append(p)
                assert p.ref_id is not None
                stated.add(parse_proposal_id(p.ref_id))
            except TeamInputError as exc:
                ref = entry.get("proposal_id") if isinstance(entry, dict) else entry
                errors.append(f"stances[{i}] ({ref}): {exc}")
        proposal = resp.get("proposal")
        if proposal is not None:
            try:
                if not isinstance(proposal, dict):
                    raise TeamInputError("must be {text, assignments, shared_files?, supersedes?}")
                prepared.append(self._svc.prepare_propose(
                    self._team_id, self._label, proposal.get("text"),
                    proposal.get("assignments"), proposal.get("shared_files"),
                    proposal.get("supersedes"), also_stated=frozenset(stated)))
            except TeamInputError as exc:
                errors.append(f"proposal: {exc}")
        if errors and not final:
            key = json.dumps(resp, sort_keys=True, default=str)
            malformed = key == self._last_refused
            self._last_refused = key
            return ReportVerdict(
                message=("report REFUSED — " + "; ".join(errors)
                         + ". Fix these entries (or leave them out) and report again."),
                malformed=malformed)
        missing = [pid for pid in self._svc.expected_stances(self._team_id, self._label)
                   if parse_proposal_id(pid) not in stated]
        if missing and not final and not self._redirected:
            self._redirected = True
            return ReportVerdict(message=(
                f"Your report has no stance on {', '.join(missing)}. State one for each in "
                f"this report's stances field — {_SHAPE} — then report again. If you could "
                "not check something, agree and say so in the note, or object with what "
                "you found."))
        for p in prepared:
            try:
                self._svc.commit(self._team_id, self._label, p, self._counters,
                                 count=p.kind == "proposal")
            except TeamInputError as exc:
                errors.append(f"{p.kind}: {exc}")
        if errors:
            self._on_dropped(errors)
        return ReportVerdict()
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_team_report_fields.py tests/test_team_service.py tests/test_team_tools.py --color=no --timeout=120 > /tmp/t3.txt 2>&1; echo exit=$?; tail -8 /tmp/t3.txt`
Expected: exit=0. A `test_team_service.py` assertion on the old header text ("Check the claims relevant to your role, then state your stance on each open proposal (team_agree …") must change to the new round header — update the expected string to the one `_header` now produces.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/teams/service.py services/agentd-py/agentd/teams/report_fields.py services/agentd-py/tests/test_team_report_fields.py services/agentd-py/tests/test_team_service.py
git commit -m "feat(teams): stances and proposals on the member's report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 4: Report schema fields and the round teaching

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py`
- Modify: `services/agentd-py/agentd/teams/tools.py` (`BACKGROUND_NOTE`)
- Modify: `services/agentd-py/tests/goldens/controller_prompt_teams.json` (re-captured)
- Test: `services/agentd-py/tests/test_team_prompts.py` (append)

**Interfaces:**
- Produces: `controller_response_schema(..., team_member=True)` → the `report` variant also declares optional `stances` (array of `{proposal_id, stance: agree|object, note?, reason?, evidence?}`) and `proposal` (`{text, assignments, shared_files?, supersedes?}`); flat and tight/anyOf alike. `team_member=False` output is byte-identical to before.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_team_prompts.py`)

```python
from agentd.chat.controller_prompts import controller_response_schema


def test_team_report_schema_has_stances_and_proposal() -> None:
    flat = controller_response_schema(phase="AGENT", team_member=True)
    assert flat["properties"]["stances"]["items"]["properties"]["stance"]["enum"] == [
        "agree", "object"]
    assert "assignments" in flat["properties"]["proposal"]["properties"]
    tight = controller_response_schema(phase="AGENT", tight=True, team_member=True)
    report = next(b for b in tight["oneOf"] if b["properties"]["type"]["const"] == "report")
    assert {"stances", "proposal"} <= set(report["properties"])
    assert "stances" not in report["required"]


def test_lone_agent_schema_unchanged() -> None:
    flat = controller_response_schema(phase="AGENT")
    assert "stances" not in flat["properties"] and "proposal" not in flat["properties"]


def test_member_prompt_teaches_rounds_and_report_stances() -> None:
    from agentd.chat.controller_prompts import _TEAM_BLOCK
    text = _TEAM_BLOCK   # tagged() returns the template string itself
    assert "reach the others at the next round" in text
    assert '"stances":[{"proposal_id":"P3","stance":"agree"' in text
    assert "appears on the board as a one-line notice" not in text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_prompts.py --color=no --timeout=120 > /tmp/t4.txt 2>&1; echo exit=$?; tail -5 /tmp/t4.txt`
Expected: exit≠0 — `KeyError: 'stances'`.

- [ ] **Step 3: Implement the schema**

In `agentd/chat/controller_prompts.py`, after `_TEAM_REPORT_STATUS`:

```python
# A team member's report may carry its stances and a proposal (spec v2 §8.3) — declared
# only for a team member, so every other schema and its schema-in-prompt bytes are unchanged.
_STANCE_ENTRY = {"type": "object", "properties": {
    "proposal_id": _STR, "stance": {"type": "string", "enum": ["agree", "object"]},
    "note": _STR, "reason": _STR, "evidence": _OBJECT}, "required": ["proposal_id", "stance"]}
_REPORT_TEAM_FIELDS = {
    "stances": {"type": "array", "items": _STANCE_ENTRY},
    "proposal": {"type": "object", "properties": {
        "text": _STR, "assignments": {"type": "array", "items": _OBJECT},
        "shared_files": {"type": "array", "items": _STR},
        "supersedes": {"type": "array", "items": _STR}}, "required": ["text", "assignments"]},
}
```

In `controller_response_schema`:
- tight/anyOf branch: inside `if variant == "report" and isinstance(props, dict):` add after the status line:
  ```python
                if team_member:
                    props.update(copy.deepcopy(_REPORT_TEAM_FIELDS))
  ```
- flat branch: inside `if "report" in types:` add after the status line:
  ```python
        if team_member:
            schema["properties"].update(copy.deepcopy(_REPORT_TEAM_FIELDS))  # type: ignore[union-attr]
  ```

- [ ] **Step 4: Implement the teaching**

In `_TEAM_BLOCK`:
- Replace the two lines
  `- Your dispatcher is the main agent that created this team; the user watches the board. Your`
  `  report is recorded and appears on the board as a one-line notice.`
  with
  ```
  - The team works in rounds. In each round every member reads what is new, checks it and
    reports. What you post during a round reaches the others at the next round, so state your
    stances in this round rather than waiting for a reply. The main agent created the team;
    the user watches the board.
  ```
- Replace
  `- Report when your part of this round is done: status "completed", or "awaiting_peer" naming`
  `  whom you wait on.`
  with
  ```
  - Report when your part of this round is done: status "completed", or "awaiting_peer" naming
    whom you wait on. A report can carry your stances ("stances": [{proposal_id, stance, note,
    or reason + evidence}]) and, when you were asked to propose, your proposal ("proposal":
    {text, assignments}) — the same as the separate tool calls, in one action.
  ```
- After the first example's final `{"type":"report",…}` line, add:
  ```
  Example — the same stances carried on the report itself:
  {"type":"report","thought":"checked P3 and P5","summary":"P3 confirmed at api/login.py:42; P5 misses the caller in api/admin.py:17.","status":"completed","stances":[{"proposal_id":"P3","stance":"agree","note":"Confirmed at api/login.py:42."},{"proposal_id":"P5","stance":"object","reason":"Misses the caller in api/admin.py.","evidence":{"files":["api/admin.py"],"line":17}}]}
  ```

In `_TEAMS_MAIN_BLOCK`, replace
```
- After create_team, answer the user: the team runs in the background and the user watches its
  board. post_board is how the user's later requests reach the team. team_status shows where it
  stands when the user asks.
```
with
```
- After create_team, answer the user: the team runs in the background and the user watches its
  board. Milestones (a plan adopted, a deadlock, a member lost) wake you — do not poll
  team_status. post_board is how the user's later requests reach the team; for a DEADLOCKED
  team it runs one more round, and adopt_proposal adopts one of its open proposals.
```

In `agentd/teams/tools.py`, replace `BACKGROUND_NOTE` (and its comment) with:

```python
BACKGROUND_NOTE = ("The team runs in the background and the user can watch its board. "
                   "Answer the user now; milestones will wake you.")
```

- [ ] **Step 5: Re-capture the teams golden and run**

Run (from `services/agentd-py`):
`.venv/bin/python -m tests.test_prompt_goldens_teams && git diff --stat tests/goldens/`
Expected: only `controller_prompt_teams.json` changes (the main golden `controller_prompt_main.json` must not).
Then: `.venv/bin/python -m pytest tests/test_team_prompts.py tests/test_prompt_goldens_teams.py tests/test_prompt_goldens_subagents.py tests/test_prompt_leak_lint.py tests/test_team_tools.py --color=no --timeout=120 > /tmp/t4.txt 2>&1; echo exit=$?; tail -5 /tmp/t4.txt`
Expected: exit=0 (a `test_team_tools.py` assertion on the old `BACKGROUND_NOTE` text updates to the new one).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_prompts.py services/agentd-py/agentd/teams/tools.py services/agentd-py/tests/goldens/controller_prompt_teams.json services/agentd-py/tests/test_team_prompts.py services/agentd-py/tests/test_team_tools.py
git commit -m "feat(teams): report stance fields in the schema and round teaching

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

# Part B — The coordinator

### Task 5: Adoption evaluation and the state machine (pure)

**Files:**
- Create: `services/agentd-py/agentd/teams/adoption.py`
- Create: `services/agentd-py/agentd/teams/state_machine.py`
- Test: `services/agentd-py/tests/test_team_adoption.py`, `services/agentd-py/tests/test_team_state_machine.py` (new)

**Interfaces:**
- Produces (`adoption.py`):
  - `ProposalEvaluation(id: str, round: int, stances: dict[str, str], eligible: bool, adopted: bool, reason: str)` with `as_payload() -> dict[str, object]`.
  - `Evaluation(round: int, proposals: tuple[ProposalEvaluation, ...], adopted: str | None)` with `as_payload()` and `summary() -> str` (e.g. `P1 not adopted: bob has no stance`).
  - `evaluation_text(payload: dict[str, object]) -> str` — the same line from a stored `round_ended` payload.
  - `evaluate_round(posts: list[TeamPost], quorum: list[str], ended_round: int) -> Evaluation`.
- Produces (`state_machine.py`):
  - State: `MemberState(label, in_quorum=True, reported=False, retries=0)`, `TeamState(phase, round, max_rounds, members: dict[str, MemberState], round_members: list[str])`, `TeamState.quorum() -> list[str]`.
  - Events: `Kickoff(kind, mentions)`, `MemberReported(label, status, stop_reason=None)`, `RoundEvaluated(adopted: str | None)`, `MainAdopt(proposal_id)`, `MainPost()`, `Disband()`.
  - Actions: `StartRound(round, labels: tuple[str, ...])`, `Requeue(label, retry, after_s)`, `EvaluateRound(round)`, `Adopt(proposal_id, by)`, `Milestone(kind, data)`, `SetQuorum(label, in_quorum)`, `EnterPhase(phase, round, reason)`, `End(phase, reason)`.
  - `apply(state: TeamState, event: Event) -> tuple[TeamState, list[Action]]` (returns a new state; never mutates the input).
  - `RETRY_BACKOFF_S = (30.0, 120.0)`.

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_team_adoption.py`:

```python
"""Adoption is evaluated only at round end (spec v2 §8.3)."""
from __future__ import annotations

from datetime import UTC, datetime

from agentd.teams.adoption import evaluate_round
from agentd.teams.models import TeamPost


def _post(seq, author, kind, *, ref=None, round_=1, closed=None) -> TeamPost:  # type: ignore[no-untyped-def]
    return TeamPost(team_id="t", seq=seq, author=author, kind=kind, text="x", ref_id=ref,
                    round=round_, closed=closed, created_at=datetime.now(UTC))


def test_kickoff_proposal_adopted_when_every_member_agrees() -> None:
    posts = [_post(1, "main", "proposal", round_=0), _post(2, "alice", "agree", ref="P1"),
             _post(3, "bob", "agree", ref="P1")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted == "P1"
    assert ev.proposals[0].stances == {"alice": "agree", "bob": "agree"}


def test_no_stance_is_not_agreement_and_latest_stance_wins() -> None:
    posts = [_post(1, "main", "proposal", round_=0), _post(2, "alice", "object", ref="P1"),
             _post(3, "alice", "agree", ref="P1")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted is None
    assert ev.proposals[0].stances == {"alice": "agree", "bob": "none"}
    assert ev.summary() == "P1 not adopted: bob has no stance"


def test_proposal_from_this_round_is_not_eligible() -> None:
    posts = [_post(2, "alice", "proposal", round_=1), _post(3, "bob", "agree", ref="P2")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted is None and ev.proposals[0].reason == "posted this round"
    assert evaluate_round(posts, ["alice", "bob"], 2).adopted == "P2"   # author counts as agree


def test_lowest_seq_wins_and_closed_proposals_are_skipped() -> None:
    posts = [_post(1, "main", "proposal", round_=0, closed="superseded"),
             _post(2, "alice", "proposal", round_=0), _post(3, "alice", "proposal", round_=0),
             _post(4, "bob", "agree", ref="P2"), _post(5, "bob", "agree", ref="P3")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted == "P2"
    assert [p.id for p in ev.proposals] == ["P2", "P3"]
    assert ev.as_payload()["adopted"] == "P2"
```

`services/agentd-py/tests/test_team_state_machine.py`:

```python
"""The team's rules, pure (spec v2 §8.1–§8.4)."""
from __future__ import annotations

from agentd.teams.state_machine import (
    Adopt,
    Disband,
    End,
    EnterPhase,
    EvaluateRound,
    Kickoff,
    MainAdopt,
    MainPost,
    MemberReported,
    MemberState,
    Milestone,
    Requeue,
    RoundEvaluated,
    SetQuorum,
    StartRound,
    TeamState,
    apply,
)


def _state(*labels: str, round_: int = 1, max_rounds: int = 3,
           phase: str = "DELIBERATING") -> TeamState:
    labels = labels or ("alice", "bob")
    return TeamState(phase=phase, round=round_, max_rounds=max_rounds,
                     members={lb: MemberState(lb) for lb in labels}, round_members=[])


def _round1(state: TeamState) -> TeamState:
    state, _ = apply(state, Kickoff("proposal", ()))
    return state


def test_kickoff_kinds() -> None:
    _, actions = apply(_state(), Kickoff("proposal", ()))
    assert actions == [StartRound(1, ("alice", "bob"))]
    _, actions = apply(_state(), Kickoff("post", ("alice",)))
    assert actions == [StartRound(1, ("alice",))]
    _, actions = apply(_state(), Kickoff("post", ()))          # nobody mentioned: everyone
    assert actions == [StartRound(1, ("alice", "bob"))]


def test_round_ends_when_every_round_member_reported() -> None:
    s = _round1(_state())
    s, actions = apply(s, MemberReported("alice", "completed"))
    assert actions == []
    s, actions = apply(s, MemberReported("alice", "completed"))   # a stray second report
    assert actions == []
    s, actions = apply(s, MemberReported("bob", "awaiting_peer"))
    assert actions == [EvaluateRound(1)]


def test_input_state_is_not_mutated() -> None:
    s = _round1(_state())
    apply(s, MemberReported("alice", "completed"))
    assert s.members["alice"].reported is False


def test_next_round_then_deadlock() -> None:
    s = _round1(_state(max_rounds=2))
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated(None))
    assert actions == [StartRound(2, ("alice", "bob"))] and s.round == 2
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated(None))
    assert s.phase == "DEADLOCKED"
    assert actions == [EnterPhase("DEADLOCKED", 2, "round limit"),
                       Milestone("deadlock", {"round": 2})]


def test_adoption_ends_the_team_for_now() -> None:
    s = _round1(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, RoundEvaluated("P1"))
    assert actions == [Adopt("P1", "team"), End("DONE", "adopted")]
    assert s.phase == "DONE"


def test_transient_is_requeued_twice_then_final() -> None:
    s = _round1(_state())
    s, actions = apply(s, MemberReported("alice", "failed_transient"))
    assert actions == [Requeue("alice", 1, 30.0)]
    s, actions = apply(s, MemberReported("alice", "failed_transient"))
    assert actions == [Requeue("alice", 2, 120.0)]
    s, actions = apply(s, MemberReported("alice", "failed_transient"))
    assert actions == [] and s.members["alice"].reported and s.members["alice"].in_quorum


def test_deadline_stop_stays_in_quorum_user_stop_leaves() -> None:
    s = _round1(_state("alice", "bob", "carol"))
    s, actions = apply(s, MemberReported("alice", "stopped", "deadline"))
    assert actions == [] and s.members["alice"].in_quorum
    s, actions = apply(s, MemberReported("bob", "stopped", "user"))
    assert actions == [SetQuorum("bob", False),
                       Milestone("member_lost", {"label": "bob", "status": "stopped"})]
    s, actions = apply(s, MemberReported("carol", "completed"))
    assert actions == [EvaluateRound(1)]
    s, actions = apply(s, RoundEvaluated(None))
    assert actions == [StartRound(2, ("alice", "carol"))]


def test_quorum_below_two_fails_the_team() -> None:
    s = _round1(_state())
    s, actions = apply(s, MemberReported("alice", "failed"))
    assert actions == [SetQuorum("alice", False),
                       Milestone("member_lost", {"label": "alice", "status": "failed"}),
                       End("FAILED", "fewer than 2 members left in the quorum")]
    assert s.phase == "FAILED"


def test_deadlock_exits() -> None:
    s = _state(round_=3, phase="DEADLOCKED")
    s2, actions = apply(s, MainPost())
    assert actions == [StartRound(4, ("alice", "bob"))]
    assert (s2.phase, s2.round, s2.max_rounds) == ("DELIBERATING", 4, 4)
    _, actions = apply(s, MainAdopt("P2"))
    assert actions == [Adopt("P2", "main"), End("DONE", "adopted")]
    _, actions = apply(_round1(_state()), MainAdopt("P2"))    # only in DEADLOCKED
    assert actions == []
    _, actions = apply(_round1(_state()), MainPost())          # an ordinary post
    assert actions == []


def test_disband_and_reports_after_the_end() -> None:
    s, actions = apply(_round1(_state()), Disband())
    assert actions == [End("DISBANDED", "disbanded")] and s.phase == "DISBANDED"
    _, actions = apply(s, MemberReported("alice", "stopped", "disband"))
    assert actions == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_adoption.py tests/test_team_state_machine.py --color=no --timeout=120 > /tmp/t5.txt 2>&1; echo exit=$?; tail -5 /tmp/t5.txt`
Expected: exit≠0 — `ModuleNotFoundError: No module named 'agentd.teams.adoption'`.

- [ ] **Step 3: Create `agentd/teams/adoption.py`**

```python
"""Adoption at round end (spec v2 §8.3) — pure, so it is testable without a team."""
from __future__ import annotations

from dataclasses import dataclass

from agentd.teams.models import TeamPost


@dataclass(frozen=True)
class ProposalEvaluation:
    id: str
    round: int
    stances: dict[str, str]   # quorum label -> agree | object | none (the author agrees)
    eligible: bool            # posted before the round that just ended started
    adopted: bool
    reason: str

    def as_payload(self) -> dict[str, object]:
        return {"id": self.id, "round": self.round, "stances": dict(self.stances),
                "eligible": self.eligible, "adopted": self.adopted, "reason": self.reason}


@dataclass(frozen=True)
class Evaluation:
    round: int
    proposals: tuple[ProposalEvaluation, ...]
    adopted: str | None

    def as_payload(self) -> dict[str, object]:
        return {"round": self.round, "adopted": self.adopted,
                "proposals": [p.as_payload() for p in self.proposals]}

    def summary(self) -> str:
        return evaluation_text(self.as_payload())


def evaluation_text(payload: dict[str, object]) -> str:
    """One line for a stored round_ended payload — team_status and the trace read it back."""
    proposals = [p for p in payload.get("proposals") or [] if isinstance(p, dict)]
    if not proposals:
        return "no open proposals"
    return "; ".join(f"{p['id']} adopted" if p.get("adopted") else
                     f"{p['id']} not adopted: {p.get('reason', '')}" for p in proposals)


def evaluate_round(posts: list[TeamPost], quorum: list[str], ended_round: int) -> Evaluation:
    """A proposal is adopted when it is open, was posted before the ended round started,
    and every quorum member's latest stance on it is agree. The lowest seq wins."""
    ordered = sorted(posts, key=lambda p: p.seq)
    latest: dict[str, dict[str, str]] = {}
    for post in ordered:
        if post.kind in ("agree", "object") and post.ref_id:
            latest.setdefault(post.ref_id, {})[post.author] = post.kind
    out: list[ProposalEvaluation] = []
    adopted: str | None = None
    for post in ordered:
        if post.kind != "proposal" or post.closed is not None:
            continue
        pid = post.proposal_id
        stances = {label: "agree" if label == post.author
                   else latest.get(pid, {}).get(label, "none") for label in quorum}
        eligible = (post.round or 0) < ended_round
        blocking = next(((lb, st) for lb, st in stances.items() if st != "agree"), None)
        qualifies = eligible and blocking is None
        wins = qualifies and adopted is None
        if wins:
            adopted = pid
        if wins:
            reason = "adopted"
        elif qualifies:
            reason = "an earlier proposal was adopted"
        elif not eligible:
            reason = "posted this round"
        else:
            assert blocking is not None
            reason = (f"{blocking[0]} objects" if blocking[1] == "object"
                      else f"{blocking[0]} has no stance")
        out.append(ProposalEvaluation(id=pid, round=post.round or 0, stances=stances,
                                      eligible=eligible, adopted=wins, reason=reason))
    return Evaluation(round=ended_round, proposals=tuple(out), adopted=adopted)
```

- [ ] **Step 4: Create `agentd/teams/state_machine.py`**

```python
"""The team's rules (spec v2 §8.1–§8.4) — pure, synchronous, no I/O. The coordinator feeds
events in and executes the actions out. 5A covers deliberation; an adoption ends the team
DONE until 5B adds the approval gate and implementation."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

RETRY_BACKOFF_S: tuple[float, ...] = (30.0, 120.0)
LIVE_PHASES = frozenset({"DELIBERATING", "AWAITING_APPROVAL", "IMPLEMENTING", "REVIEWING",
                         "DEADLOCKED", "PAUSED"})


@dataclass
class MemberState:
    label: str
    in_quorum: bool = True
    reported: bool = False   # its final outcome for this round is in
    retries: int = 0         # failed_transient re-queues this round


@dataclass
class TeamState:
    phase: str
    round: int
    max_rounds: int
    members: dict[str, MemberState]
    round_members: list[str] = field(default_factory=list)

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


@dataclass(frozen=True)
class RoundEvaluated:
    adopted: str | None


@dataclass(frozen=True)
class MainAdopt:
    proposal_id: str


@dataclass(frozen=True)
class MainPost:
    pass


@dataclass(frozen=True)
class Disband:
    pass


Event = Kickoff | MemberReported | RoundEvaluated | MainAdopt | MainPost | Disband


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


Action = StartRound | Requeue | EvaluateRound | Adopt | Milestone | SetQuorum | EnterPhase | End


def _start_round(state: TeamState, labels: list[str]) -> list[Action]:
    state.phase = "DELIBERATING"
    state.round_members = labels
    for member in state.members.values():
        member.reported = False
        member.retries = 0
    return [StartRound(state.round, tuple(labels))]


def _adopt(state: TeamState, proposal_id: str, by: str) -> list[Action]:
    # 5A interim (replaced in 5B): adoption ends the team; the plan is the deliverable.
    state.phase = "DONE"
    return [Adopt(proposal_id, by), End("DONE", "adopted")]


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
        return (s, _adopt(s, event.proposal_id, "main")) if s.phase == "DEADLOCKED" else (s, [])
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
            return s, _adopt(s, event.adopted, "team")
        if s.round >= s.max_rounds:
            s.phase = "DEADLOCKED"
            return s, [EnterPhase("DEADLOCKED", s.round, "round limit"),
                       Milestone("deadlock", {"round": s.round})]
        s.round += 1
        return s, _start_round(s, s.quorum())
    assert isinstance(event, MemberReported)
    member = s.members.get(event.label)
    if (s.phase != "DELIBERATING" or member is None or member.reported
            or event.label not in s.round_members):
        return s, []
    actions: list[Action] = []
    if event.status == "failed_transient" and member.retries < len(RETRY_BACKOFF_S):
        member.retries += 1
        return s, [Requeue(event.label, member.retries, RETRY_BACKOFF_S[member.retries - 1])]
    member.reported = True
    lost = event.status == "failed" or (event.status == "stopped" and event.stop_reason == "user")
    if lost:
        member.in_quorum = False
        actions += [SetQuorum(event.label, False),
                    Milestone("member_lost", {"label": event.label, "status": event.status})]
        if len(s.quorum()) < 2:
            s.phase = "FAILED"
            return s, [*actions, End("FAILED", "fewer than 2 members left in the quorum")]
    waiting = [lb for lb in s.round_members if s.members[lb].in_quorum and not s.members[lb].reported]
    if not waiting:
        actions.append(EvaluateRound(s.round))
    return s, actions
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_team_adoption.py tests/test_team_state_machine.py --color=no --timeout=120 > /tmp/t5.txt 2>&1; echo exit=$?; tail -5 /tmp/t5.txt`
Expected: exit=0.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/teams/adoption.py services/agentd-py/agentd/teams/state_machine.py services/agentd-py/tests/test_team_adoption.py services/agentd-py/tests/test_team_state_machine.py
git commit -m "feat(teams): adoption evaluation and the deliberation state machine

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 6: What the coordinator records — activity kinds, store helpers, milestones, trace, team notices

**Files:**
- Modify: `services/agentd-py/agentd/teams/models.py`, `agentd/teams/store.py`, `agentd/teams/config.py`
- Create: `services/agentd-py/agentd/teams/milestones.py`, `agentd/teams/trace.py`
- Modify: `services/agentd-py/agentd/subagents/notices.py`
- Test: `services/agentd-py/tests/test_team_milestones.py` (new); append to `tests/test_team_store.py`, `tests/test_notice_fold.py`

**Interfaces:**
- Produces:
  - `ACTIVITY_KINDS` gains `round_started`, `round_ended`, `held`, `requeued`, `deadline`.
  - `TeamStore.set_in_quorum(team_id, label, in_quorum: bool) -> None`; `TeamStore.live_team_names(thread_id) -> list[str]`.
  - `team_round_timeout_s() -> int` (`CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC`, default 900, min 1).
  - `milestone_text(team: TeamRecord, kind: str, data: dict[str, object]) -> tuple[str, str]` → `(headline, body)` for `adopted`, `deadlock`, `member_lost`, `ended`.
  - `CoordinatorTrace(path: Path)` with `write(kind: str, **data: object) -> None` (append-only jsonl, best-effort).
  - `notice_author` / `notice_body` render `source_kind == "team"` notices from `payload["team_name"]` / `payload["body"]`.

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_team_milestones.py`:

```python
"""Milestone bodies are compact (spec v2 §8.8); the trace is append-only (§8.11)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from agentd.chat.models import NoticeRecord
from agentd.subagents.notices import notice_author, notice_body
from agentd.teams.milestones import milestone_text
from agentd.teams.models import TeamRecord
from agentd.teams.trace import CoordinatorTrace


def _team(**over) -> TeamRecord:  # type: ignore[no-untyped-def]
    base = dict(team_id="team-1", thread_id="t", name="auth", goal="g", max_rounds=3,
                budget=160, requests=42, created_turn_id="u", created_at=datetime.now(UTC),
                phase="DEADLOCKED", round=3)
    base.update(over)
    return TeamRecord(**base)


def test_deadlock_body_lists_counts_and_next_steps() -> None:
    headline, body = milestone_text(_team(), "deadlock", {
        "round": 3, "proposals": [{"id": "P1", "stances": {"alice": "agree", "bob": "none"}}]})
    assert headline == "Team 'auth' deadlocked after 3 rounds"
    assert "P1: 1 agree, 0 object, 1 no stance" in body
    assert "42 / 160 requests" in body
    assert "adopt_proposal" in body and "post_board" in body


def test_adopted_body_summarizes_assignments() -> None:
    headline, body = milestone_text(_team(phase="DONE"), "adopted", {
        "proposal_id": "P2", "by": "team",
        "assignments": [{"member": "alice", "part": "api", "files": ["a.py", "b.py"]}]})
    assert headline == "Team 'auth' adopted P2"
    assert "alice → api (2 files)" in body


def test_member_lost_and_ended() -> None:
    assert milestone_text(_team(), "member_lost", {"label": "bob", "status": "failed"})[0] == (
        "Team 'auth' lost bob (failed)")
    assert milestone_text(_team(phase="FAILED"), "ended", {"reason": "x"})[0] == (
        "Team 'auth' ended — x")


def test_team_notice_author_and_body() -> None:
    notice = NoticeRecord(notice_id="n", thread_id="t", source_kind="team", source_id="team-1",
                          kind="deadlock", payload={"team_name": "auth", "body": "BODY"},
                          delivery="wake", created_at=datetime.now(UTC))
    assert notice_author(notice) == "team auth"
    assert notice_body(notice) == "BODY"


def test_trace_appends_lines(tmp_path) -> None:
    trace = CoordinatorTrace(tmp_path / "x" / "coordinator.jsonl")
    trace.write("event", name="Kickoff")
    trace.write("evaluation", round=1)
    lines = (tmp_path / "x" / "coordinator.jsonl").read_text().splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["event", "evaluation"]
```

Append to `tests/test_team_store.py` (reuse that file's existing store fixture/helper; if it builds the store inline, build one the same way):

```python
def test_set_in_quorum_and_live_team_names(store_with_team) -> None:  # type: ignore[no-untyped-def]
    store, team_id, thread_id = store_with_team
    label = store.members(team_id)[0].label
    store.set_in_quorum(team_id, label, False)
    assert store.member(team_id, label).in_quorum is False
    name = store.get_team(team_id).name
    assert store.live_team_names(thread_id) == [name]
    store.update_team(team_id, phase="DONE")
    assert store.live_team_names(thread_id) == []
```

(If `tests/test_team_store.py` has no `store_with_team` fixture, add one at the top of the appended block that creates a store on `sqlite3.connect(":memory:")` with `row_factory = sqlite3.Row`, one `TeamRecord` and two `TeamMember`s, and returns `(store, team_id, thread_id)`.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_milestones.py tests/test_team_store.py --color=no --timeout=120 > /tmp/t6.txt 2>&1; echo exit=$?; tail -5 /tmp/t6.txt`
Expected: exit≠0 — `ModuleNotFoundError: No module named 'agentd.teams.milestones'`.

- [ ] **Step 3: Implement**

`agentd/teams/models.py` — extend the activity kinds:

```python
# Spec 2026-10-05 §4.2 and §9 — the lifecycle facts the UI renders; never member input.
ACTIVITY_KINDS = frozenset({
    "phase", "woke", "notified", "took_up", "picked_up", "wrapped_up", "capped",
    "round_started", "round_ended", "held", "requeued", "deadline"})
```

`agentd/teams/store.py` — add to the members section:

```python
    def set_in_quorum(self, team_id: str, label: str, in_quorum: bool) -> None:
        self._conn.execute(
            "UPDATE team_members SET in_quorum = ? WHERE team_id = ? AND label = ?",
            (int(in_quorum), team_id, label))
        self._conn.commit()
```

and to the teams section:

```python
    def live_team_names(self, thread_id: str) -> list[str]:
        marks = ", ".join("?" * len(LIVE_TEAM_PHASES))
        rows = self._conn.execute(
            f"SELECT name FROM teams WHERE thread_id = ? AND phase IN ({marks}) "  # noqa: S608
            "ORDER BY created_at", (thread_id, *sorted(LIVE_TEAM_PHASES))).fetchall()
        return [r[0] for r in rows]
```

`agentd/teams/config.py`:

```python
def team_round_timeout_s() -> int:
    """Active seconds a member gets per round before its loop is forced to report
    (spec v2 §8.3)."""
    return _int_env("CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC", 900, 1)
```

Create `agentd/teams/milestones.py`:

```python
"""Milestone text (spec v2 §8.8): a headline plus ids, counts and pointers — never full
proposal texts, evidence or reports, which stay on the board."""
from __future__ import annotations

from agentd.teams.models import TeamRecord


def milestone_text(team: TeamRecord, kind: str, data: dict[str, object]) -> tuple[str, str]:
    name = repr(team.name)
    if kind == "adopted":
        headline = f"Team {name} adopted {data['proposal_id']}"
        by = " by you (the main agent)" if data.get("by") == "main" else " by the team"
        parts = data.get("assignments") or []
        assigned = [f"{a.get('member')} → {a.get('part')} ({len(a.get('files') or [])} files)"
                    for a in parts if isinstance(a, dict)]
        details = [f"{data['proposal_id']} was adopted{by}.",
                   "Assignments: " + ("; ".join(assigned) if assigned else "none"),
                   "The team has ended with this plan adopted (implementation by the team "
                   "is not available yet): carry it out or tell the user."]
    elif kind == "deadlock":
        headline = f"Team {name} deadlocked after {data['round']} rounds"
        details = []
        for p in data.get("proposals") or []:
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
```

Create `agentd/teams/trace.py`:

```python
"""The coordinator trace (spec v2 §8.11): append-only jsonl, best-effort."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class CoordinatorTrace:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, kind: str, **data: object) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"at": datetime.now(UTC).isoformat(), "kind": kind, **data},
                              default=str)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001 — a trace must never fail the team
            logger.warning("[teams] trace write failed %s", self._path, exc_info=True)
```

`agentd/subagents/notices.py` — make the two renderers team-aware:

```python
def notice_author(notice: NoticeRecord) -> str:
    if notice.source_kind == "team":
        return f"team {notice.payload.get('team_name') or notice.source_id}"
    label = str(notice.payload.get("label") or notice.source_id)
    name = str(notice.payload.get("name") or "")
    return f"{label} ({name})" if name else label


def notice_body(notice: NoticeRecord) -> str:
    """The whole report, never truncated (v1 D8), with what the agent changed. A team
    milestone is already compact (spec v2 §8.8)."""
    if notice.source_kind == "team":
        return str(notice.payload.get("body", ""))
    files = notice.payload.get("files_changed") or []
    lines = [f"status: {notice.payload.get('status', '')}"]
    if files:
        lines.append("files_changed: " + ", ".join(str(f) for f in files))
    lines.append("")
    lines.append(str(notice.payload.get("report", "")))
    return "\n".join(lines)
```

`build_fold` frames each notice as `frame(notice_author(n), "report", …)`; change the kind for team notices so the frame reads `team milestone`:

```python
        kind = "team milestone" if notice.source_kind == "team" else "report"
        block = frame(notice_author(notice), kind, notice_body(notice))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_team_milestones.py tests/test_team_store.py tests/test_notice_fold.py tests/test_team_activity_store.py --color=no --timeout=120 > /tmp/t6.txt 2>&1; echo exit=$?; tail -5 /tmp/t6.txt`
Expected: exit=0.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams services/agentd-py/agentd/subagents/notices.py services/agentd-py/tests/test_team_milestones.py services/agentd-py/tests/test_team_store.py
git commit -m "feat(teams): milestones, coordinator trace and round activity kinds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 7: `TeamCoordinator`

**Files:**
- Create: `services/agentd-py/agentd/teams/coordinator.py`
- Test: `services/agentd-py/tests/test_team_coordinator.py` (new)

**Interfaces:**
- Consumes: Task 5 (`apply`, events, actions, `TeamState`, `MemberState`), Task 5 (`evaluate_round`), Task 6 (`milestone_text`, `CoordinatorTrace`, `set_in_quorum`), `TeamService.record` / `.system_post`.
- Produces:
  - `class CoordinatorHost(Protocol)`:
    - `start_member(team_id: str, label: str) -> None` — schedule an activation with the member's delta (must not start inline: the caller may be the member's own finishing task).
    - `async stop_member(team_id: str, label: str, reason: str) -> None`
    - `force_final(team_id: str, label: str) -> bool` — False when the member's loop is not built yet.
    - `active_seconds(team_id: str, label: str) -> float | None` — None when it is not running.
    - `team_milestone(team: TeamRecord, kind: str, headline: str, body: str) -> None`
    - `team_phase_changed(team: TeamRecord) -> None` — broadcast `team_phase`; drop the coordinator when ended.
    - `wake_for_post(team: TeamRecord, post: TeamPost) -> None` — outside deliberation (5B uses it).
  - `class TeamCoordinator`:
    - `TeamCoordinator(team_id, store: TeamStore, service: TeamService, host: CoordinatorHost, trace: CoordinatorTrace, *, round_timeout_s: float, grace_s: float = 120.0)`
    - `kickoff(kind: str, mentions: list[str]) -> None`
    - `on_post(post: TeamPost) -> None`
    - `on_report(label: str, status: str, stop_reason: str | None) -> None`
    - `on_activation_start(label: str) -> None`
    - `main_adopt(proposal_id: str) -> None`
    - `async disband() -> None`
    - `close() -> None` — cancel every timer.
    - `trace_dropped(label: str, errors: list[str]) -> None`
    - `@property phase: str`
    - `delta_cutoff() -> int | None` — the highest post seq at the current round's start (None outside deliberation); a member's round input stops there.

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_team_coordinator.py`:

```python
"""The coordinator drives rounds through its host (spec v2 §8.1, §8.3)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.teams.coordinator import TeamCoordinator
from agentd.teams.models import TeamMember, TeamPost, TeamRecord
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.store import TeamStore
from agentd.teams.trace import CoordinatorTrace


class _Host:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[tuple[str, str]] = []
        self.forced: list[str] = []
        self.milestones: list[str] = []
        self.phases: list[str] = []
        self.active: dict[str, float | None] = {}
        self.woken: list[int] = []

    def start_member(self, team_id: str, label: str) -> None:
        self.started.append(label)

    async def stop_member(self, team_id: str, label: str, reason: str) -> None:
        self.stopped.append((label, reason))

    def force_final(self, team_id: str, label: str) -> bool:
        self.forced.append(label)
        return True

    def active_seconds(self, team_id: str, label: str) -> float | None:
        return self.active.get(label, 0.0)

    def team_milestone(self, team, kind, headline, body) -> None:  # type: ignore[no-untyped-def]
        self.milestones.append(kind)

    def team_phase_changed(self, team) -> None:  # type: ignore[no-untyped-def]
        self.phases.append(team.phase)

    def wake_for_post(self, team, post) -> None:  # type: ignore[no-untyped-def]
        self.woken.append(post.seq)


def _setup(tmp_path: Path, *, max_rounds: int = 3, timeout: float = 900.0,
           labels: tuple[str, ...] = ("alice", "bob")):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TeamStore(conn)
    store.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                                 max_rounds=max_rounds, budget=100, created_turn_id="u",
                                 created_at=datetime.now(UTC)))
    for label in labels:
        store.add_member(TeamMember(team_id="team-1", agent_id=f"agent-{label}", label=label))
    svc = TeamService(store, tmp_path, lambda _a: AgentInfo("gp", "", "idle"))
    host = _Host()
    trace_path = tmp_path / "coordinator.jsonl"
    coord = TeamCoordinator("team-1", store, svc, host, CoordinatorTrace(trace_path),
                            round_timeout_s=timeout, grace_s=0.05)
    store.append_post("team-1", author="main", kind="proposal", text="P1", round=0,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})
    return store, svc, host, coord, trace_path


def _kinds(store: TeamStore) -> list[str]:
    return [e.kind for e in store.activity("team-1")]


@pytest.mark.asyncio
async def test_kickoff_starts_round_one(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    assert host.started == ["alice", "bob"]
    started = next(e for e in store.activity("team-1") if e.kind == "round_started")
    assert started.payload == {"round": 1, "members": [
        {"label": "alice", "handed": [1]}, {"label": "bob", "handed": [1]}]}
    assert coord.delta_cutoff() == 1
    coord.close()


@pytest.mark.asyncio
async def test_round_end_evaluates_and_adopts(tmp_path) -> None:
    store, svc, host, coord, trace = _setup(tmp_path)
    coord.kickoff("proposal", [])
    svc.agree("team-1", "alice", "P1")
    svc.agree("team-1", "bob", "P1")
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    team = store.get_team("team-1")
    assert (team.phase, team.adopted_proposal_id, team.end_reason) == ("DONE", "P1", "adopted")
    assert store.get_post("team-1", 1).closed == "adopted"
    ended = next(e for e in store.activity("team-1") if e.kind == "round_ended")
    assert ended.payload["adopted"] == "P1"
    system = [p for p in store.posts("team-1") if p.kind == "system"]
    assert system[-1].text.startswith("Adopted P1.") and system[-1].payload["adopted"] == "P1"
    assert host.milestones == ["adopted"] and host.phases[-1] == "DONE"
    kinds = [json.loads(line)["kind"] for line in trace.read_text().splitlines()]
    assert "evaluation" in kinds


@pytest.mark.asyncio
async def test_no_adoption_runs_round_two_then_deadlocks(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, max_rounds=2)
    coord.kickoff("proposal", [])
    for _ in range(2):
        coord.on_report("alice", "completed", None)
        coord.on_report("bob", "completed", None)
    assert host.started == ["alice", "bob", "alice", "bob"]
    assert store.get_team("team-1").phase == "DEADLOCKED"
    phases = [e.payload.get("phase") for e in store.activity("team-1") if e.kind == "phase"]
    assert phases == ["DELIBERATING", "DEADLOCKED"]   # round 2's chapter, then the deadlock
    rounds = [e.payload["round"] for e in store.activity("team-1") if e.kind == "round_started"]
    assert rounds == [1, 2]
    assert host.milestones == ["deadlock"]


@pytest.mark.asyncio
async def test_mid_round_posts_are_held(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    post = svc.post("team-1", "alice", "@bob look at this")
    coord.on_post(post)
    dm = svc.message("team-1", "bob", "alice", "which file?")
    coord.on_post(dm)
    board = svc.post("team-1", "bob", "a general note")
    coord.on_post(board)
    held = [e.payload for e in store.activity("team-1") if e.kind == "held"]
    assert held == [
        {"post_seq": post.seq, "for": ["bob"], "everyone": False, "until_round": 2},
        {"post_seq": dm.seq, "for": ["alice"], "everyone": False, "until_round": 2},
        {"post_seq": board.seq, "for": ["alice"], "everyone": True, "until_round": 2}]
    assert host.woken == []


@pytest.mark.asyncio
async def test_main_post_in_deadlock_runs_one_more_round(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, max_rounds=1)
    coord.kickoff("proposal", [])
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    assert store.get_team("team-1").phase == "DEADLOCKED"
    coord.on_post(svc.post("team-1", "main", "@team please agree on one plan"))
    team = store.get_team("team-1")
    assert (team.phase, team.round, team.max_rounds) == ("DELIBERATING", 2, 2)
    assert host.started[-2:] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_main_adopt_from_deadlock(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, max_rounds=1)
    coord.kickoff("proposal", [])
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    coord.main_adopt("P1")
    team = store.get_team("team-1")
    assert team.phase == "DONE" and team.adopted_proposal_id == "P1"
    assert "by the main agent" in [p for p in store.posts("team-1") if p.kind == "system"][-1].text


@pytest.mark.asyncio
async def test_transient_requeue_after_backoff(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("agentd.teams.state_machine.RETRY_BACKOFF_S", (0.01, 0.01))
    store, _svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    coord.on_report("alice", "failed_transient", None)
    assert "requeued" in _kinds(store)
    await asyncio.sleep(0.05)
    assert host.started == ["alice", "bob", "alice"]
    coord.close()


@pytest.mark.asyncio
async def test_deadline_forces_final_then_stops(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, timeout=0.02)
    coord.kickoff("proposal", [])
    host.active["alice"] = 1.0
    coord.on_activation_start("alice")
    await asyncio.sleep(0.15)
    assert host.forced == ["alice"]
    assert "deadline" in _kinds(store)
    assert ("alice", "deadline") in host.stopped
    coord.close()


@pytest.mark.asyncio
async def test_deadline_reschedules_while_paused(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path, timeout=0.05)
    coord.kickoff("proposal", [])
    host.active["alice"] = 0.0                 # parked at a gate: no active time passes
    coord.on_activation_start("alice")
    await asyncio.sleep(0.12)
    assert host.forced == []
    coord.on_report("alice", "completed", None)   # reporting cancels its clock
    host.active["alice"] = 10.0
    await asyncio.sleep(0.1)
    assert host.forced == []
    coord.close()


@pytest.mark.asyncio
async def test_disband_stops_members_and_records(tmp_path) -> None:
    store, _svc, host, coord, _ = _setup(tmp_path)
    coord.kickoff("proposal", [])
    await coord.disband()
    team = store.get_team("team-1")
    assert team.phase == "DISBANDED"
    assert sorted(host.stopped) == [("alice", "disband"), ("bob", "disband")]
    assert host.milestones == ["ended"] and host.phases[-1] == "DISBANDED"
    assert [p.text for p in store.posts("team-1") if p.kind == "system"] == [
        "The team was disbanded."]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_coordinator.py --color=no --timeout=120 > /tmp/t7.txt 2>&1; echo exit=$?; tail -5 /tmp/t7.txt`
Expected: exit≠0 — `ModuleNotFoundError: No module named 'agentd.teams.coordinator'`.

- [ ] **Step 3: Create `agentd/teams/coordinator.py`**

```python
"""TeamCoordinator (spec v2 §8.1): one per live team. Feeds events into the pure state
machine, persists its state, and executes its actions through the host (ChatController).
Every method is synchronous apart from disband, so a caller never interleaves with it."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

from agentd.teams import state_machine as sm
from agentd.teams.adoption import evaluate_round
from agentd.teams.milestones import milestone_text
from agentd.teams.models import TeamPost, TeamRecord
from agentd.teams.service import TeamService
from agentd.teams.store import TeamStore
from agentd.teams.trace import CoordinatorTrace

logger = logging.getLogger(__name__)
_FORCE_RETRY_S = 5.0   # the member's loop was not built yet: look again shortly


class CoordinatorHost(Protocol):
    def start_member(self, team_id: str, label: str) -> None: ...
    async def stop_member(self, team_id: str, label: str, reason: str) -> None: ...
    def force_final(self, team_id: str, label: str) -> bool: ...
    def active_seconds(self, team_id: str, label: str) -> float | None: ...
    def team_milestone(self, team: TeamRecord, kind: str, headline: str, body: str) -> None: ...
    def team_phase_changed(self, team: TeamRecord) -> None: ...
    def wake_for_post(self, team: TeamRecord, post: TeamPost) -> None: ...


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
            members={m.label: sm.MemberState(m.label, in_quorum=m.in_quorum)
                     for m in store.members(team_id)})
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._evaluation: dict[str, object] = {}   # the latest round_ended payload
        self._cutoff = 0                             # highest post seq at the round's start

    @property
    def phase(self) -> str:
        return self._state.phase

    def delta_cutoff(self) -> int | None:
        """A round's input is the board as it stood when the round started (spec v2 E5)."""
        return self._cutoff if self._state.phase == "DELIBERATING" else None

    # ── inputs ──────────────────────────────────────────────────────────────

    def kickoff(self, kind: str, mentions: list[str]) -> None:
        self._apply(sm.Kickoff(kind, tuple(mentions)))

    def on_post(self, post: TeamPost) -> None:
        team = self._team()
        if post.kind == "system":
            return
        if self._state.phase == "DELIBERATING":
            self._hold(post)
        elif self._state.phase == "DEADLOCKED":
            if post.author == "main" and post.recipient is None:
                self._apply(sm.MainPost())
        elif team.phase in sm.LIVE_PHASES:
            self._host.wake_for_post(team, post)

    def on_report(self, label: str, status: str, stop_reason: str | None) -> None:
        self._cancel(f"deadline:{label}")
        self._cancel(f"grace:{label}")
        self._apply(sm.MemberReported(label, status, stop_reason))

    def on_activation_start(self, label: str) -> None:
        if self._state.phase == "DELIBERATING":
            self._schedule(f"deadline:{label}", self._timeout, lambda: self._check_deadline(label))

    def main_adopt(self, proposal_id: str) -> None:
        self._apply(sm.MainAdopt(proposal_id))

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

    def _apply(self, event: sm.Event) -> None:
        self._state, actions = sm.apply(self._state, event)
        self._trace.write("event", name=type(event).__name__, event=repr(event),
                          actions=[repr(a) for a in actions])
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
                           lambda: self._host.start_member(self._team_id, action.label))
        elif isinstance(action, sm.EvaluateRound):
            self._evaluate(action.round)
        elif isinstance(action, sm.Adopt):
            self._adopt(action)
        elif isinstance(action, sm.Milestone):
            team = self._team()
            data = ({**action.data, "proposals": self._evaluation.get("proposals", [])}
                    if action.kind == "deadlock" else action.data)
            headline, body = milestone_text(team, action.kind, data)
            self._host.team_milestone(team, action.kind, headline, body)
            self._trace.write("milestone", milestone=action.kind, headline=headline)
        elif isinstance(action, sm.SetQuorum):
            self._store.set_in_quorum(self._team_id, action.label, action.in_quorum)
        elif isinstance(action, sm.EnterPhase):
            self._store.update_team(self._team_id, phase=action.phase)
            self._svc.record(self._team_id, "team", "phase", payload={
                "phase": action.phase, "round": action.round, "reason": action.reason})
            self._host.team_phase_changed(self._team())
        elif isinstance(action, sm.End):
            self.close()
            self._store.update_team(self._team_id, phase=action.phase,
                                    end_reason=action.reason, ended_at=datetime.now(UTC))
            team = self._team()
            for label in self._state.round_members:
                if action.phase == "FAILED":
                    asyncio.get_running_loop().create_task(
                        self._host.stop_member(self._team_id, label, "disband"))
            self._ended(team, action.phase, action.reason)

    def _ended(self, team: TeamRecord, phase: str, reason: str) -> None:
        self._svc.record(self._team_id, "team", "phase", payload={
            "phase": phase, "round": team.round, "reason": reason})
        fresh = self._team()
        if phase != "DONE":
            headline, body = milestone_text(fresh, "ended", {"reason": reason})
            self._host.team_milestone(fresh, "ended", headline, body)
        self._host.team_phase_changed(fresh)

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
            self._host.start_member(self._team_id, label)

    def _evaluate(self, ended_round: int) -> None:
        posts = self._store.posts(self._team_id)
        evaluation = evaluate_round(posts, self._state.quorum(), ended_round)
        new_posts = sum(1 for p in posts if p.round == ended_round and p.kind != "system")
        payload = {**evaluation.as_payload(), "new_posts": new_posts}
        self._svc.record(self._team_id, "team", "round_ended", payload=payload)
        self._trace.write("evaluation", **evaluation.as_payload())
        # The deadlock milestone carries the per-proposal counts (spec §8.8).
        self._evaluation = payload
        self._apply(sm.RoundEvaluated(evaluation.adopted))

    def _adopt(self, action: sm.Adopt) -> None:
        seq = int(action.proposal_id.lstrip("Pp"))
        proposal = self._store.get_post(self._team_id, seq)
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
        team = self._team()
        headline, body = milestone_text(team, "adopted", {
            "proposal_id": action.proposal_id, "by": action.by, "assignments": assignments})
        self._host.team_milestone(team, "adopted", headline, body)

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
        self._schedule(f"grace:{label}", self._grace, lambda: asyncio.get_running_loop().create_task(
            self._host.stop_member(self._team_id, label, "deadline")))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_team_coordinator.py --color=no --timeout=120 > /tmp/t7.txt 2>&1; echo exit=$?; tail -8 /tmp/t7.txt`
Expected: exit=0.

- [ ] **Step 5: Lint and type-check the new modules**

Run: `.venv/bin/python -m ruff check agentd/teams && .venv/bin/python -m mypy agentd/teams > /tmp/t7m.txt 2>&1; echo exit=$?; tail -5 /tmp/t7m.txt`
Expected: no new errors in `agentd/teams/*` (compare with `git stash`-free baseline by running the same command at the base commit only if a count looks off).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/teams/coordinator.py services/agentd-py/tests/test_team_coordinator.py
git commit -m "feat(teams): the coordinator — rounds, deadlines, requeues, adoption, deadlock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 8: Wire the coordinator into the controller

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`
- Test: `services/agentd-py/tests/test_team_rounds_controller.py` (new); rewrite parts of `tests/test_team_controller.py` and `tests/test_team_activity_controller.py` (below)

**Interfaces:**
- Consumes: Tasks 1–7 (`active_seconds`, `AgentSupervisor.keep`, `ControllerLoop.force_final`, `report_check`, `ReportFields`, `render_delta_posts(until_seq=)`, `TeamCoordinator`, `CoordinatorTrace`, `team_round_timeout_s`, `notice_author`).
- Produces (on `ChatController`, implementing `CoordinatorHost`): `start_member`, `stop_member`, `force_final`, `active_seconds`, `team_milestone`, `team_phase_changed`, `wake_for_post`; `self._coordinators: dict[str, TeamCoordinator]`; `_broadcast_post(team, post)`; `_team_member_reported(record, result, stop_reason=None)`; took_up and wrapped_up payloads gain `round` during deliberation.

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_team_rounds_controller.py`:

```python
"""Teams run in rounds inside the controller (spec v2 §8.3)."""
from __future__ import annotations

import asyncio

import pytest

from tests.test_team_controller import REPORT, _make, _request, _settle

AGREE = {"type": "report", "thought": "checked", "summary": "P1 holds", "status": "completed",
         "stances": [{"proposal_id": "P1", "stance": "agree", "note": "checked the plan"}]}
POST_TO_BOB = {"type": "tool_call", "thought": "tell bob", "tool": "team_post",
               "args": {"text": "@bob the login route needs a test"}}


def _no_notice_turns(ctrl, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A milestone arms a main-agent notice turn; these tests read the notices directly.
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)


@pytest.mark.asyncio
async def test_agreement_on_the_report_adopts(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.adopted_proposal_id) == ("DONE", "P1")
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["adopted"]
    assert team_id not in ctrl._coordinators


@pytest.mark.asyncio
async def test_round_two_restarts_the_last_reporter(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    rounds = [e.payload["round"] for e in store.teams.activity(team_id)
              if e.kind == "round_started"]
    assert rounds == [1, 2, 3]
    for label in ("alice", "bob"):
        agent = store.get_agent(store.teams.member(team_id, label).agent_id)
        assert agent.activation_count == 3
    assert store.teams.get_team(team_id).phase == "DEADLOCKED"
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["deadlock"]


@pytest.mark.asyncio
async def test_mid_round_post_is_held_not_delivered(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch,
                                     {"alice": [POST_TO_BOB, REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    activity = store.teams.activity(team_id)
    held = [e for e in activity if e.kind == "held"]
    assert held and held[0].payload["for"] == ["bob"] and held[0].payload["until_round"] == 2
    assert not [e for e in activity if e.kind in ("notified", "picked_up", "woke")]
    bob_inputs = [h[-1]["content"] for label, h, _, _ in engine.seen
                  if label == "bob" and h and h[-1].get("role") == "user"]
    assert "the login route needs a test" not in str(bob_inputs[0])     # round 1
    assert any("the login route needs a test" in str(c) for c in bob_inputs[1:])   # round 2


@pytest.mark.asyncio
async def test_deliberation_members_cannot_edit(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _no_notice_turns(ctrl, monkeypatch)
    seen_types: list[list[str]] = []
    original = engine.create_controller_step

    async def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen_types.append(list(kwargs.get("allowed_types") or []))
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", spy)
    await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    assert seen_types and all("edit" not in t for t in seen_types)


@pytest.mark.asyncio
async def test_user_stop_loses_quorum(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def slow(*args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", slow)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await asyncio.sleep(0.05)
    alice = store.teams.member(team_id, "alice")
    await ctrl.stop_agent(tid, alice.agent_id)
    await _settle(ctrl)
    assert store.teams.get_team(team_id).phase == "FAILED"
    assert store.teams.member(team_id, "alice").in_quorum is False
    assert [n.kind for n in store.unclaimed_notices(tid)] == ["member_lost", "ended"]
    wraps = {e.label: e.payload["status"] for e in store.teams.activity(team_id)
             if e.kind == "wrapped_up"}
    assert wraps == {"alice": "stopped", "bob": "stopped"}


@pytest.mark.asyncio
async def test_deadline_forces_a_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC", "1")
    calls = {"n": 0}

    def looking() -> dict[str, object]:   # a fresh path each time: no duplicate-call guard
        calls["n"] += 1
        return {"type": "tool_call", "thought": "keep looking", "tool": "read_file",
                "args": {"path": f"notes-{calls['n']}.txt"}}
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [AGREE], "bob": [AGREE]})
    _no_notice_turns(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def stubborn(*args, **kwargs):  # type: ignore[no-untyped-def]
        label = getattr(kwargs.get("render_ctx"), "agent_label", "")
        if label == "alice" and kwargs.get("allowed_types") != ["report"]:
            await asyncio.sleep(0.2)
            return looking()
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", stubborn)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    for _ in range(40):
        await asyncio.sleep(0.1)
        if any(e.kind == "deadline" for e in store.teams.activity(team_id)):
            break
    await _settle(ctrl)
    assert any(e.kind == "deadline" and e.label == "alice"
               for e in store.teams.activity(team_id))
    alice_wrap = next(e for e in store.teams.activity(team_id)
                      if e.kind == "wrapped_up" and e.label == "alice")
    assert alice_wrap.payload["status"] == "partial"     # the forced-final report
```

Rewrite these Phase 4 tests, which asserted the immediate-wake policy that deliberation no longer has:

`tests/test_team_controller.py`:

```python
@pytest.mark.asyncio
async def test_post_kickoff_round_one_is_only_the_mentions(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    started = [e.payload for e in store.teams.activity(team_id) if e.kind == "round_started"]
    assert [m["label"] for m in started[0]["members"]] == ["alice"]
    assert [m["label"] for m in started[1]["members"]] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_a_mention_reaches_its_member_next_round(tmp_path, monkeypatch) -> None:
    post_to_bob = {"type": "tool_call", "thought": "tell bob", "tool": "team_post",
                   "args": {"text": "@bob the login route needs a test"}}
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch,
                                     {"alice": [post_to_bob, REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    result = await ctrl._create_team(tid, "turn1", _request(kind="post"))
    await _settle(ctrl)
    bob_calls = [h for label, h, _, _ in engine.seen if label == "bob"]
    assert bob_calls, "bob never ran"
    assert "the login route needs a test" in str(bob_calls[0][-1]["content"])
    assert "<<<agent-content author=\"alice (general-purpose)\"" in str(bob_calls[0][-1]["content"])
    assert store.teams.member(str(result["team_id"]), "bob").delivered_seq >= 2


@pytest.mark.asyncio
async def test_nothing_is_pushed_to_a_running_member_between_rounds(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    result = await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    team_id = str(result["team_id"])
    bob = store.teams.member(team_id, "bob")
    monkeypatch.setattr(ctrl._subagents, "is_active", lambda agent_id: agent_id == bob.agent_id)
    ctrl._teams.post(team_id, "alice", "@bob new finding")   # type: ignore[union-attr]
    assert ctrl._drain_member(bob.agent_id, team_id, "bob") == []
    # A marker (live delivery outside deliberation) still renders what is new.
    ctrl._subagents.deliver(bob.agent_id, InboxItem(kind="team", text="", wakes=True))
    items = ctrl._drain_member(bob.agent_id, team_id, "bob")
    assert [i.kind for i in items] == ["team"] and "new finding" in items[0].text


@pytest.mark.asyncio
async def test_wake_cap_stops_ping_pong(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_MAX_WAKES", "1")
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    result = await ctrl._create_team(tid, "turn1", _request(kind="post"))
    await _settle(ctrl)
    team_id = str(result["team_id"])
    post = store.teams.append_post(team_id, author="bob", kind="post", text="@alice again",
                                   mentions=["alice"])
    for _ in range(2):    # outside deliberation a post wakes whom it mentions (5B keeps it)
        ctrl.wake_for_post(store.teams.get_team(team_id), post)
        await _settle(ctrl)
    capped = [e for e in store.teams.activity(team_id) if e.kind == "capped"]
    assert capped and capped[0].label == "alice"
```

(Delete the old `test_post_kickoff_wakes_only_mentions`, `test_mention_wakes_an_idle_member_with_its_delta`, `test_post_to_running_member_lands_at_next_drain` and the old `test_wake_cap_stops_ping_pong`. In `_make`, keep everything as is.)

`tests/test_team_activity_controller.py` — replace these tests:

```python
@pytest.mark.asyncio
async def test_kickoff_records_phase_round_handoffs_and_wrapups(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    events = store.teams.activity(team_id)
    assert events[0].kind == "phase" and events[0].payload["phase"] == "DELIBERATING"
    assert events[1].kind == "round_started" and events[1].payload["round"] == 1
    assert "woke" not in _kinds(store, team_id)            # round strips replace wakes
    took = next(e for e in events if e.kind == "took_up" and e.label == "alice")
    assert took.payload["posts"] == [1] and took.payload["from"] == ["main"]
    assert took.payload["round"] == 1 and took.activation == 1
    wrap = next(e for e in events if e.kind == "wrapped_up" and e.label == "alice")
    assert wrap.payload["status"] == "completed" and wrap.payload["report"] == "done here"
    assert wrap.payload["round"] == 1
    assert {"duration_ms", "tools", "posts", "messages", "stances"} <= set(wrap.payload)
    assert [p.kind for p in store.teams.posts(team_id)] == ["proposal"]


@pytest.mark.asyncio
async def test_mention_mid_round_is_held(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [POST_TO_BOB, REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    held = next(e for e in store.teams.activity(team_id) if e.kind == "held")
    assert held.label == "alice" and held.cause_seq == 2
    assert held.payload == {"post_seq": 2, "for": ["bob"], "everyone": False, "until_round": 2}


@pytest.mark.asyncio
async def test_direct_message_is_held_for_its_recipient(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [DM_TO_BOB, REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    held = next(e for e in store.teams.activity(team_id) if e.kind == "held")
    assert held.payload["for"] == ["bob"] and held.payload["everyone"] is False


@pytest.mark.asyncio
async def test_running_member_is_notified_then_picks_up(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    bob = store.teams.member(team_id, "bob")
    monkeypatch.setattr(ctrl._subagents, "is_active", lambda agent_id: agent_id == bob.agent_id)
    post = store.teams.append_post(team_id, author="alice", kind="post", text="@bob new finding",
                                   mentions=["bob"])
    ctrl.wake_for_post(store.teams.get_team(team_id), post)   # outside deliberation
    ctrl._drain_member(bob.agent_id, team_id, "bob")
    kinds = _kinds(store, team_id, "bob")
    assert kinds[-2:] == ["notified", "picked_up"]
    picked = store.teams.activity(team_id)[-1]
    assert picked.payload["posts"] == [post.seq]


@pytest.mark.asyncio
async def test_wake_cap_is_an_event_not_a_post(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_MAX_WAKES", "1")
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    post = store.teams.append_post(team_id, author="bob", kind="post", text="@alice again",
                                   mentions=["alice"])
    for _ in range(2):
        ctrl.wake_for_post(store.teams.get_team(team_id), post)
        await _settle(ctrl)
    capped = [e for e in store.teams.activity(team_id) if e.kind == "capped"]
    assert len(capped) == 1 and capped[0].payload == {"wakes": 2, "cap": 1}
    assert not [p for p in store.teams.posts(team_id) if p.kind == "system"]


@pytest.mark.asyncio
async def test_divider_carries_activation(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)                                 # three rounds, three activations
    bob = store.get_agent(store.teams.member(team_id, "bob").agent_id)
    dividers = [m.metadata.get("activation") for m in bob.transcript if m.metadata.get("divider")]
    assert dividers == [2, 3]
```

(The old `test_kickoff_records_phase_wakes_handoffs_and_wrapups`, `test_mention_records_a_caused_wake` and `test_direct_message_cause` are replaced by the first three above. `test_disband_closes_open_chapters`, `test_reap_closes_live_members_and_fails_the_team` and `test_activity_is_streamed_on_the_team_channel` stay as they are.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_rounds_controller.py --color=no --timeout=120 > /tmp/t8.txt 2>&1; echo exit=$?; tail -8 /tmp/t8.txt`
Expected: exit≠0 — e.g. `assert 'DELIBERATING' == 'DONE'` in the first test (Phase 4 never adopts).

- [ ] **Step 3: Implement in `agentd/chat/controller.py`**

1. Imports (merge into the existing import blocks):

```python
from agentd.runtime.artifacts import chat_turn_artifacts_root
from agentd.subagents.notices import notice_author
from agentd.subagents.runtime import active_seconds
from agentd.teams.config import team_round_timeout_s
from agentd.teams.coordinator import TeamCoordinator
from agentd.teams.report_fields import ReportFields
from agentd.teams.trace import CoordinatorTrace
```

(Some may already be imported — keep one import per name.)

2. `__init__`, right after `self._teams: TeamService | None = (…)`:

```python
        # One coordinator per live team (spec v2 §8.1). Teams never survive a restart, so
        # nothing is rebuilt at startup: the reap fails them.
        self._coordinators: dict[str, TeamCoordinator] = {}
```

3. `_create_team`: replace the last two statements before `return` —
`self._team_posted(team, kickoff, wake_all=req.kickoff_kind == "proposal")` — with:

```python
        self._broadcast_post(team, kickoff)
        coordinator = TeamCoordinator(
            team.team_id, self._store.teams, self._teams, self,
            CoordinatorTrace(chat_turn_artifacts_root(thread_id, turn_id, self._workspace_path)
                             / "teams" / team.team_id / "coordinator.jsonl"),
            round_timeout_s=team_round_timeout_s())
        self._coordinators[team.team_id] = coordinator
        coordinator.kickoff(req.kickoff_kind, req.kickoff_mentions)
```

4. Replace `_team_posted` (and drop its `wake_all` parameter) with:

```python
    def _broadcast_post(self, team: TeamRecord, post: TeamPost) -> None:
        self._broadcaster.broadcast(team_channel(team.thread_id, team.team_id), {
            "type": "team_post", "seq": post.seq,
            "payload": {"post": post.model_dump(mode="json")}})

    def _team_posted(self, team: TeamRecord, post: TeamPost) -> None:
        """Every stored post: stream it, then let the team's coordinator decide who sees it
        and when (spec v2 §8.3 — during deliberation nothing reaches a member mid-round)."""
        self._broadcast_post(team, post)
        coordinator = self._coordinators.get(team.team_id)
        if coordinator is not None:
            coordinator.on_post(post)
```

5. Add the host methods (a new section after `_team_activity`):

```python
    # ── CoordinatorHost (spec v2 §8.1) ──────────────────────────────────────

    def start_member(self, team_id: str, label: str) -> None:
        """Scheduled, never inline: the round's last reporter calls this from inside its own
        finishing activation, which is still active until that task ends (the same rule
        as _on_leftover)."""
        asyncio.get_running_loop().call_soon(self._start_member_now, team_id, label)

    def _start_member_now(self, team_id: str, label: str) -> None:
        assert self._subagents is not None
        team = self._store.teams.get_team(team_id)
        member = self._store.teams.member(team_id, label)
        if team is None or member is None or team.phase not in LIVE_TEAM_PHASES:
            return
        if self._subagents.is_active(member.agent_id):
            logger.warning("[teams] %s of %s still running at its round start", label, team.name)
            return
        handle = self._handle_from_record(team.thread_id, member.agent_id)
        handle.activation_input = TEAM_DELTA
        self._subagents.enqueue(handle, self._activate)

    def _member_handle(self, team_id: str, label: str) -> AgentHandle | None:
        member = self._store.teams.member(team_id, label)
        if member is None or self._subagents is None:
            return None
        if not self._subagents.is_active(member.agent_id):
            return None
        return self._subagents.registry.get(member.agent_id)

    async def stop_member(self, team_id: str, label: str, reason: str) -> None:
        member = self._store.teams.member(team_id, label)
        if member is not None and self._subagents is not None:
            await self._subagents.stop(member.agent_id, reason)

    def force_final(self, team_id: str, label: str) -> bool:
        handle = self._member_handle(team_id, label)
        if handle is None or not isinstance(handle.loop, ControllerLoop):
            return False
        handle.loop.force_final()
        return True

    def active_seconds(self, team_id: str, label: str) -> float | None:
        handle = self._member_handle(team_id, label)
        return active_seconds(handle, datetime.now(UTC)) if handle is not None else None

    def team_milestone(self, team: TeamRecord, kind: str, headline: str, body: str) -> None:
        """A team milestone for the main agent (spec v2 §8.8): a wake notice, delivered by
        the same paths as an agent's report (§5.2)."""
        notice = NoticeRecord(
            notice_id=uuid4().hex, thread_id=team.thread_id, source_kind="team",
            source_id=team.team_id, kind=kind,
            payload={"team_name": team.name, "team_id": team.team_id, "headline": headline,
                     "body": body, "phase": team.phase},
            delivery="wake", created_at=datetime.now(UTC))
        self._store.insert_notice(notice)
        if team.thread_id in self._active_loops:
            self._main_inbox.setdefault(team.thread_id, []).append(InboxItem(
                kind="note", text=body, wakes=False, source_id=team.team_id,
                author=notice_author(notice), notice_id=notice.notice_id))
            return
        self._rearm_notices(team.thread_id)

    def team_phase_changed(self, team: TeamRecord) -> None:
        self._broadcaster.broadcast(team_channel(team.thread_id, team.team_id), {
            "type": "team_phase", "payload": {"phase": team.phase, "round": team.round,
                                              "paused_reason": team.paused_reason}})
        if team.phase not in LIVE_TEAM_PHASES:
            coordinator = self._coordinators.pop(team.team_id, None)
            if coordinator is not None:
                coordinator.close()

    def wake_for_post(self, team: TeamRecord, post: TeamPost) -> None:
        """Outside deliberation a post wakes whom it concerns: Phase 4's policy, which 5B's
        implementation phase keeps (spec v2 §8.6 live delivery)."""
        members = self._store.teams.members(team.team_id)
        if post.recipient is not None:
            targets = [m for m in members if m.label == post.recipient]
        elif "team" in post.mentions:
            targets = members
        else:
            targets = [m for m in members if m.label in post.mentions]
        for member in targets:
            if member.label != post.author:
                self._wake_member(team, member, cause=wake_cause(post, member.label), post=post)
```

6. `_team_member_reported` — new signature and body:

```python
    def _team_member_reported(self, record: AgentRecord, result: ChildResult,
                              stop_reason: str | None = None) -> None:
        """A member's activation ended: its wrap-up (spec 2026-10-05 §4.2), then the
        coordinator's event (spec v2 §8.3). Written even after the team ended, so a
        disband's stopped activations close their chapters."""
        if self._teams is None or record.team_id is None:
            return
        report = result.report
        if len(report) > 20_000:
            report = report[:20_000] + "\n… (truncated)"
        stats = _activation_stats(record, self._store.teams.posts(record.team_id))
        team = self._store.teams.get_team(record.team_id)
        in_round = ({"round": team.round}
                    if team is not None and team.phase == "DELIBERATING" else {})
        self._teams.record(record.team_id, record.label, "wrapped_up",
                           activation=record.activation_count,
                           payload={"status": result.status, "report": report,
                                    "files_changed": list(result.files_changed), **stats,
                                    **in_round})
        coordinator = self._coordinators.get(record.team_id)
        if coordinator is not None:
            coordinator.on_report(record.label, result.status, stop_reason)
```

and in `_activate`'s `except asyncio.CancelledError:` branch change
`self._team_member_reported(stopped, handle.result)` to
`self._team_member_reported(stopped, handle.result, handle.stop_reason)`.

7. `_activate` — after `membership = self._store.teams.member_for_agent(ctx.agent_id)` and `team_counters = ActivationCounters()`, insert:

```python
        team_row = (self._store.teams.get_team(membership.team_id)
                    if membership is not None else None)
        deliberating = team_row is not None and team_row.phase == "DELIBERATING"
        coordinator = (self._coordinators.get(membership.team_id)
                       if membership is not None else None)
        if team_row is not None and team_row.phase != "IMPLEMENTING":
            # Edits only where the phase allows them (spec v2 §3.9), recomputed at each
            # activation start; deliberation activations are capped at 40 iterations (§3.11).
            ctx = replace(ctx, allowed_types=tuple(t for t in ctx.allowed_types if t != "edit"),
                          max_iters=min(ctx.max_iters, 40) if deliberating else ctx.max_iters)
            handle.context = ctx
```

In the existing `if membership is not None and activation_input.startswith(TEAM_DELTA):` block, pass the cutoff and the round:

```python
            delta, top, handed = self._teams.render_delta_posts(
                membership.team_id, membership.label,
                until_seq=coordinator.delta_cutoff() if coordinator is not None else None)
            self._store.teams.set_delivered_seq(membership.team_id, membership.label, top)
            activation_input = delta + activation_input[len(TEAM_DELTA):]
            self._teams.record(membership.team_id, membership.label, "took_up",
                               activation=activation,
                               payload={"posts": [p.seq for p in handed],
                                        "from": sorted({p.author for p in handed}),
                                        **({"round": team_row.round}
                                           if deliberating and team_row is not None else {})})
```

After `handle.loop = loop`, add:

```python
        if coordinator is not None and membership is not None:
            coordinator.on_activation_start(membership.label)
        report_check = (
            ReportFields(self._teams, membership.team_id, membership.label, team_counters,
                         on_dropped=(partial(coordinator.trace_dropped, membership.label)
                                     if coordinator is not None else (lambda _errors: None)))
            if membership is not None and self._teams is not None else None)
```

and pass `report_check=report_check,` in the `loop.run(...)` call (after `report_guard=…`).

8. `_on_leftover` — after `others = [i for i in items if i.kind != "team"]` and the `membership` lookup, before the activation is scheduled:

```python
        if membership is not None:
            team = self._store.teams.get_team(membership.team_id)
            if team is not None and team.phase == "DELIBERATING":
                # Nothing reaches a member mid-round (spec v2 E5): the next round's delta
                # covers the board, and anything else waits in its inbox for that round.
                assert self._subagents is not None
                self._subagents.keep(handle.agent_id, others)
                return
```

(Move the `membership = …` line above `text = …` if it is below it today.)

9. `disband_team` — at the top of the `if team.phase in LIVE_TEAM_PHASES:` branch:

```python
            coordinator = self._coordinators.pop(team_id, None)
            if coordinator is not None:
                await coordinator.disband()
                return {"team_id": team_id, "phase": "DISBANDED"}
```

(The existing body stays as the fallback for a team without a coordinator.)

10. `handle_notices` — the marker for a team milestone. Replace

```python
        sources = [n for n in pending if n.source_kind == "agent"]
        target = {"agent_id": sources[0].source_id} if len(sources) == 1 else {}
        text = (f"🔔 Agent \"{sources[0].payload.get('label', '')}\" finished — main agent woke"
                if len(sources) == 1 else
                f"🔔 {len(pending)} agents finished — main agent woke")
```

with

```python
        sources = [n for n in pending if n.source_kind == "agent"]
        teams = [n for n in pending if n.source_kind == "team"]
        target: dict[str, str] = {}
        if len(pending) == 1 and teams:
            target = {"team_id": teams[0].source_id}
            text = f"🔔 {teams[0].payload.get('headline', 'Team update')} — main agent woke"
        elif len(pending) == 1:
            target = {"agent_id": sources[0].source_id}
            text = f"🔔 Agent \"{sources[0].payload.get('label', '')}\" finished — main agent woke"
        elif teams:
            text = f"🔔 {len(pending)} updates from agents and teams — main agent woke"
        else:
            text = f"🔔 {len(pending)} agents finished — main agent woke"
```

- [ ] **Step 4: Run the team suites**

Run: `.venv/bin/python -m pytest tests/test_team_rounds_controller.py tests/test_team_controller.py tests/test_team_activity_controller.py tests/test_notice_turns.py tests/test_notice_delivery.py --color=no --timeout=120 > /tmp/t8.txt 2>&1; echo exit=$?; grep -E "FAILED|passed|failed" /tmp/t8.txt | tail -12`
Expected: exit=0. If a test leaks a notice turn after its body (an unawaited task warning or a stray `submit_changes`), stub `_rearm_notices` in that test the way the new tests do.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_team_rounds_controller.py services/agentd-py/tests/test_team_controller.py services/agentd-py/tests/test_team_activity_controller.py
git commit -m "feat(teams): the controller hosts a coordinator per team; rounds replace wakes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 9: The main agent's exits, status, `/live` progress, rewind refusal

**Files:**
- Modify: `services/agentd-py/agentd/teams/tools.py`, `agentd/teams/service.py`, `agentd/chat/controller.py`, `agentd/api/routes.py`
- Test: append to `tests/test_team_rounds_controller.py`, `tests/test_team_tools.py`, `tests/test_team_routes.py`

**Interfaces:**
- Produces:
  - `MainTeamOps.adopt: Callable[[str, str], dict[str, object]]` (team_id, proposal_id).
  - `TeamService.open_proposal(team_id, raw) -> TeamPost` (public wrapper of `_open_proposal`); `summary(...)["evaluation"]` = the latest `round_ended` payload or `None`; `status_text` gains `last round (N): <evaluation_text>`.
  - `ChatController._adopt_proposal(thread_id, team_id, proposal_id) -> dict[str, object]`; `ChatController.live_team_names(thread_id) -> list[str]`.
  - `/live` team rows gain `round_progress: {round, members: [label], reported: [label]} | None` (deliberation only).
  - `POST /chat/threads/{id}/rewind` → 409 while a team has not ended; the preview's `blocked_by_agents` lists `team <name>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_rounds_controller.py`:

```python
@pytest.mark.asyncio
async def test_main_agent_adopts_from_deadlock(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(max_rounds=1)))["team_id"])
    await _settle(ctrl)
    assert store.teams.get_team(team_id).phase == "DEADLOCKED"
    out = ctrl._adopt_proposal(tid, team_id, "P1")
    assert out == {"team_id": team_id, "adopted": "P1", "phase": "DONE"}
    from agentd.teams.validation import TeamInputError
    with pytest.raises(TeamInputError):
        ctrl._adopt_proposal(tid, team_id, "P1")          # the team has ended


@pytest.mark.asyncio
async def test_main_post_in_deadlock_runs_another_round(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(tid, "turn1", _request(max_rounds=1)))["team_id"])
    await _settle(ctrl)
    source = ctrl._main_team_source(tid, "turn2")
    out = await source.execute("post_board", {"team": "auth", "text": "@team settle on one plan"})
    assert '"phase": "DELIBERATING"' in out.output
    await _settle(ctrl)
    rounds = [e.payload["round"] for e in store.teams.activity(team_id)
              if e.kind == "round_started"]
    assert rounds == [1, 2]


@pytest.mark.asyncio
async def test_status_and_live_progress(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    _no_notice_turns(ctrl, monkeypatch)
    original = engine.create_controller_step

    async def slow_bob(*args, **kwargs):  # type: ignore[no-untyped-def]
        if getattr(kwargs.get("render_ctx"), "agent_label", "") == "bob":
            await asyncio.sleep(5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", slow_bob)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    for _ in range(50):
        await asyncio.sleep(0.02)
        live = ctrl.live_teams(tid)[0]
        if live["round_progress"]["reported"]:
            break
    assert live["round_progress"] == {"round": 1, "members": ["alice", "bob"],
                                      "reported": ["alice"]}
    assert ctrl.live_team_names(tid) == ["auth"]
    await ctrl.disband_team(tid, team_id)
    await _settle(ctrl)
    assert ctrl.live_team_names(tid) == []


def test_status_text_names_the_last_evaluation(tmp_path) -> None:
    import sqlite3
    from datetime import UTC, datetime

    from agentd.teams.models import TeamMember, TeamRecord
    from agentd.teams.service import AgentInfo, TeamService
    from agentd.teams.store import TeamStore
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    st = TeamStore(conn)
    st.create_team(TeamRecord(team_id="team-1", thread_id="t", name="auth", goal="g",
                              max_rounds=3, budget=1, created_turn_id="u",
                              created_at=datetime.now(UTC)))
    st.add_member(TeamMember(team_id="team-1", agent_id="a", label="alice"))
    st.append_activity("team-1", label="team", kind="round_ended", payload={
        "round": 1, "adopted": None,
        "proposals": [{"id": "P1", "adopted": False, "reason": "bob has no stance"}]})
    svc = TeamService(st, tmp_path, lambda _a: AgentInfo("gp", "", "idle"))
    assert "last round (1): P1 not adopted: bob has no stance" in svc.status_text("team-1", "alice")
    assert svc.summary("team-1")["evaluation"]["round"] == 1
```

Append to `tests/test_team_routes.py`:

```python
@pytest.mark.asyncio
async def test_rewind_refused_while_a_team_is_live(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from agentd.chat.models import ChatMessage
    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    message_id = store.append_message(tid, ChatMessage(role="user", content="hi"))
    ctrl._rewind = SimpleNamespace()        # rewind wired; the guard answers before it is used
    async with _client(tmp_path, ctrl) as client:
        r = await client.post(f"/v1/chat/threads/{tid}/rewind", json={"message_id": message_id})
    assert r.status_code == 409 and "auth" in r.json()["detail"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_rounds_controller.py tests/test_team_routes.py --color=no --timeout=120 > /tmp/t9.txt 2>&1; echo exit=$?; grep -E "FAILED|Error" /tmp/t9.txt | head`
Expected: exit≠0 — `AttributeError: 'ChatController' object has no attribute '_adopt_proposal'`.

- [ ] **Step 3: Implement**

`agentd/teams/service.py`:

```python
    def open_proposal(self, team_id: str, raw: object) -> TeamPost:
        """An open proposal on this board, or a TeamInputError saying why not."""
        return self._open_proposal(team_id, raw)

    def latest_evaluation(self, team_id: str) -> dict[str, Any] | None:
        ended = [e for e in self._store.activity(team_id) if e.kind == "round_ended"]
        return ended[-1].payload if ended else None
```

In `status_text`, after the open-proposals loop:

```python
        evaluation = self.latest_evaluation(team_id)
        if evaluation is not None:
            lines.append(f"last round ({evaluation.get('round')}): "
                         f"{evaluation_text(evaluation)}")
```

(import `from agentd.teams.adoption import evaluation_text`), and in `summary` add the key
`"evaluation": self.latest_evaluation(team_id),`.

`agentd/teams/tools.py`:
- `MainTeamOps` gains a field `adopt: Callable[[str, str], dict[str, object]]` (after `disband`).
- The `adopt_proposal` description becomes `"Adopt one of a DEADLOCKED team's open proposals, as if every member agreed."`
- In `execute`, replace the `adopt_proposal` branch with:

```python
            if tool == "adopt_proposal":
                if status.get("phase") != "DEADLOCKED":
                    raise TeamInputError(f"adopt_proposal is only for a DEADLOCKED team; this "
                                         f"team is {status.get('phase')}")
                return _ok(self._ops.adopt(team_id, str(args.get("proposal_id", ""))))
```

`agentd/chat/controller.py`:

- `_main_team_source`: make `post` report the phase after posting, and wire `adopt`:

```python
        def post(team_id: str, text: str, mentions: object) -> dict[str, object]:
            p = teams.post(team_id, "main", text, mentions)
            after = self._store.teams.get_team(team_id)
            return {"seq": p.seq, "mentions": p.mentions,
                    "phase": after.phase if after is not None else "", 
                    "round": after.round if after is not None else 0}

        return MainTeamToolSource(self._agent_catalog(), MainTeamOps(
            create=partial(self._create_team, thread_id, turn_id),
            resolve=partial(self._resolve_team, thread_id),
            post=post, status=teams.summary,
            disband=partial(self.disband_team, thread_id),
            adopt=partial(self._adopt_proposal, thread_id)),
            first_turn_team_ids=set())
```

- New methods:

```python
    def _adopt_proposal(self, thread_id: str, team_id: str, proposal_id: str) -> dict[str, object]:
        """adopt_proposal (spec v2 §8.4): only for a DEADLOCKED team, only an open proposal."""
        assert self._teams is not None
        team = self._store.teams.get_team(team_id)
        coordinator = self._coordinators.get(team_id)
        if team is None or team.thread_id != thread_id or coordinator is None \
                or coordinator.phase != "DEADLOCKED":
            raise TeamInputError("adopt_proposal is only for a DEADLOCKED team")
        proposal = self._teams.open_proposal(team_id, proposal_id)
        coordinator.main_adopt(proposal.proposal_id)
        after = self._store.teams.get_team(team_id)
        return {"team_id": team_id, "adopted": proposal.proposal_id,
                "phase": after.phase if after is not None else "DONE"}

    def live_team_names(self, thread_id: str) -> list[str]:
        """Teams that have not ended block a rewind (spec v2 §8.10)."""
        return self._store.teams.live_team_names(thread_id)
```

- `live_teams`: add `"round_progress": _round_progress(team, self._store.teams.activity(team.team_id)),` to each row, with the module-level helper:

```python
def _round_progress(team: TeamRecord, activity: list[TeamActivity]) -> dict[str, object] | None:
    """The card's progress bar (spec 2026-10-05 §9): whom the round started and who has its
    final report in. Changes only at activation boundaries, so it is safe in /live."""
    if team.phase != "DELIBERATING":
        return None
    starts = [e for e in activity
              if e.kind == "round_started" and e.payload.get("round") == team.round]
    if not starts:
        return None
    start = starts[-1]
    labels = [str(m.get("label")) for m in start.payload.get("members", [])
              if isinstance(m, dict)]
    last: dict[str, str] = {}
    for e in activity:
        if e.aseq > start.aseq and e.kind in ("wrapped_up", "requeued") and e.label in labels:
            last[e.label] = e.kind
    return {"round": team.round, "members": labels,
            "reported": [lb for lb in labels if last.get(lb) == "wrapped_up"]}
```

`agentd/api/routes.py`:

- In `get_rewind_preview`, after `preview.blocked_by_agents = …`:

```python
            _team_names = getattr(_chat_agent, "live_team_names", None)
            if _team_names is not None:
                preview.blocked_by_agents = [*preview.blocked_by_agents,
                                             *(f"team {n}" for n in _team_names(thread_id))]
```

- In `post_rewind`, right after the running-agents 409:

```python
            _team_names = getattr(_chat_agent, "live_team_names", None)
            live_teams = _team_names(thread_id) if _team_names is not None else []
            if live_teams:
                raise HTTPException(
                    status_code=409,
                    detail=(f"Team {', '.join(live_teams)} has not ended — disband it before "
                            "rewinding."))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_team_rounds_controller.py tests/test_team_routes.py tests/test_team_tools.py tests/test_team_service.py tests/test_get_routes_read_only.py tests/test_rewind_agents.py --color=no --timeout=120 > /tmp/t9.txt 2>&1; echo exit=$?; grep -E "FAILED|passed|failed" /tmp/t9.txt | tail -8`
Expected: exit=0. (`test_team_tools.py` builds `MainTeamOps(...)` — add `adopt=lambda _t, _p: {}` there.)

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd services/agentd-py/tests
git commit -m "feat(teams): adopt from deadlock, round progress on /live, rewind waits for teams

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

# Part C — Client

### Task 10: editor-client — round progress on `/live` teams

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`
- Test: append to `apps/editor-client/test/team-activity-contracts.test.ts`

**Interfaces:**
- Produces: `TeamLive.roundProgress: { round: number; members: string[]; reported: string[] } | null` (default `null`), mapped from `round_progress`.

- [ ] **Step 1: Write the failing test** (append inside the `describe` block)

```ts
  it("maps /live round_progress, null when absent", async () => {
    const live = (teams: unknown[]) => new HttpBackendClient({ baseUrl: "http://x",
      fetchFn: vi.fn().mockResolvedValue({ ok: true, json: async () => ({
        active_task_id: null, status: null, pending_gates: [], plan: null, teams }) }) });
    const base = { team_id: "team-1", name: "auth", phase: "DELIBERATING", round: 2,
      max_rounds: 3, paused_reason: null, members: [] };
    const withProgress = await live([{ ...base,
      round_progress: { round: 2, members: ["alice", "bob"], reported: ["alice"] } }])
      .getThreadLiveState("t");
    expect(withProgress.teams![0].roundProgress).toEqual(
      { round: 2, members: ["alice", "bob"], reported: ["alice"] });
    const without = await live([base]).getThreadLiveState("t");
    expect(without.teams![0].roundProgress).toBeNull();
  });
```

- [ ] **Step 2: Run to verify failure**

Run (from `apps/editor-client`): `perl -e 'alarm 120; exec @ARGV' npx vitest run test/team-activity-contracts.test.ts > /tmp/t10.txt 2>&1; echo exit=$?; tail -8 /tmp/t10.txt`
Expected: exit≠0 — `expected undefined to deeply equal { round: 2, … }`.

- [ ] **Step 3: Implement**

In `TeamLiveSchema` (after `counts`):

```ts
  // Who the current round started and who has its final report in (spec 2026-10-05 §9).
  roundProgress: z.object({
    round: z.number(), members: z.array(z.string()), reported: z.array(z.string()),
  }).nullable().default(null),
```

In `HttpBackendClient.toTeamLive`, read `const rp = t["round_progress"] as Record<string, unknown> | null | undefined;` next to `counts`, and add to the returned object:

```ts
      roundProgress: rp ? { round: rp["round"], members: rp["members"] ?? [],
                            reported: rp["reported"] ?? [] } : null,
```

- [ ] **Step 4: Run, build, commit**

Run: `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/t10.txt 2>&1; echo exit=$?; tail -4 /tmp/t10.txt` then (from the repo root) `npm run -w @crucible/editor-client build > /tmp/t10b.txt 2>&1; echo exit=$?`.
Expected: both exit=0.

```bash
git add apps/editor-client/src apps/editor-client/test/team-activity-contracts.test.ts
git commit -m "feat(editor-client): round progress on /live teams

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 11: The journey builder — round strips, verdicts, held footers, the adopted card

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`, `apps/vscode-extension/webview-ui/src/teamJourney.ts`
- Test: append to `apps/vscode-extension/webview-ui/src/test/teamJourney.test.ts`

**Interfaces:**
- Produces (`types.ts`): `TeamRoundProgressView { round: number; members: string[]; reported: string[] }`; `roundProgress?: TeamRoundProgressView | null` on `TeamSummaryView` and `TeamLiveView`.
- Produces (`teamJourney.ts`):
  - `HeldNote { for: string[]; everyone: boolean; untilRound: number }`; `PostFooter` gains `held: HeldNote | null`.
  - `RoundMember { label: string; handed: number[]; reportedAt: string | null; status: string | null }`.
  - `VerdictProposal { id: string; stances: Record<string, string>; adopted: boolean }`.
  - New `JourneyItem` kinds: `round` (`round, members, ended`), `verdict` (`round, proposals, adopted, nextRound, newPosts`), `adopted` (`post`).
  - Folding: `held` events fold into their post's footer; `took_up` events carrying `payload.round` fold into that round's strip.

- [ ] **Step 1: Write the failing tests** (append to `src/test/teamJourney.test.ts`)

```ts
import { buildJourney as build5a } from "../teamJourney";
import type { TeamActivityView as Act, TeamPostView as Post } from "../types";

const T0 = Date.parse("2026-10-06T10:00:00Z");
const at = (s: number) => new Date(T0 + s * 1000).toISOString();
const post5 = (seq: number, author: string, kind: string, s: number, over: Partial<Post> = {}): Post => ({
  teamId: "t", seq, author, kind, recipient: null, text: `text ${seq}`, mentions: [], refId: null,
  round: 1, payload: {}, closed: null, createdAt: at(s), ...over });
const act5 = (aseq: number, label: string, kind: string, s: number, payload: Record<string, unknown> = {},
              over: Partial<Act> = {}): Act => ({
  teamId: "t", aseq, at: at(s), label, kind, activation: null, causeSeq: null, payload, ...over });

describe("journey with rounds (spec 2026-10-05 §9)", () => {
  const posts = [
    post5(1, "main", "proposal", 0, { round: 0 }),
    post5(2, "alice", "post", 10, { mentions: ["bob"] }),
    post5(3, "system", "system", 50, { round: null, text: "Adopted P1.",
      payload: { adopted: "P1", assignments: [{ member: "alice", part: "api", files: ["a.py"] }] } }),
  ];
  const activity = [
    act5(1, "team", "phase", 0, { phase: "DELIBERATING", round: 1 }),
    act5(2, "team", "round_started", 1, { round: 1, members: [
      { label: "alice", handed: [1] }, { label: "bob", handed: [1] }] }),
    act5(3, "alice", "took_up", 2, { posts: [1], from: ["main"], round: 1 }, { activation: 1 }),
    act5(4, "alice", "held", 10, { post_seq: 2, for: ["bob"], everyone: false, until_round: 2 },
         { causeSeq: 2 }),
    act5(5, "alice", "wrapped_up", 20, { status: "completed", report: "r", round: 1 }, { activation: 1 }),
    act5(6, "bob", "requeued", 21, { retry: 1, of: 2, after_ms: 30000 }),
    act5(7, "bob", "wrapped_up", 25, { status: "completed", report: "r", round: 1 }, { activation: 1 }),
    act5(8, "team", "round_ended", 26, { round: 1, adopted: null, new_posts: 1, proposals: [
      { id: "P1", stances: { alice: "agree", bob: "none" }, adopted: false, reason: "bob has no stance" }] }),
    act5(9, "team", "phase", 27, { phase: "DELIBERATING", round: 2 }),
    act5(10, "team", "round_started", 28, { round: 2, members: [
      { label: "alice", handed: [] }, { label: "bob", handed: [2] }] }),
    act5(11, "bob", "deadline", 40, {}),
    act5(12, "team", "round_ended", 49, { round: 2, adopted: "P1", new_posts: 0, proposals: [
      { id: "P1", stances: { alice: "agree", bob: "agree" }, adopted: true, reason: "adopted" }] }),
    act5(13, "team", "phase", 51, { phase: "DONE", round: 2, reason: "adopted" }),
  ];
  const items = build5a(posts, activity, ["alice", "bob"]);

  it("lays out chapters, strips, verdicts and the adopted card in order", () => {
    expect(items.filter((i) => i.kind !== "gap").map((i) => i.kind)).toEqual([
      "chapter", "post", "chapter", "round", "post", "wrap", "beat", "wrap", "verdict",
      "chapter", "round", "beat", "verdict", "adopted", "chapter"]);
  });

  it("folds held into the post footer and took_up into the strip", () => {
    const card = items.find((i) => i.kind === "post" && i.post.seq === 2);
    expect(card && card.kind === "post" && card.footer.held).toEqual(
      { for: ["bob"], everyone: false, untilRound: 2 });
    expect(items.some((i) => i.kind === "beat" && i.event.kind === "took_up")).toBe(false);
  });

  it("round strips know who reported and when", () => {
    const strip = items.find((i) => i.kind === "round" && i.round === 1);
    expect(strip && strip.kind === "round" && strip.members).toEqual([
      { label: "alice", handed: [1], reportedAt: at(20), status: "completed" },
      { label: "bob", handed: [1], reportedAt: at(25), status: "completed" }]);
    expect(strip && strip.kind === "round" && strip.ended).toBe(true);
  });

  it("verdicts name the next round or the adoption", () => {
    const verdicts = items.filter((i) => i.kind === "verdict");
    expect(verdicts.map((v) => v.kind === "verdict" && [v.adopted, v.nextRound, v.newPosts])).toEqual([
      [null, 2, 1], ["P1", null, 0]]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `webview-ui`): `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamJourney.test.ts > /tmp/t11.txt 2>&1; echo exit=$?; tail -12 /tmp/t11.txt`
Expected: exit≠0 — the order assertion fails (`round_started` renders as a beat).

- [ ] **Step 3: Implement**

`types.ts` — after `TeamCountsView`:

```ts
/** Whom the current round started and who has its final report in (spec 2026-10-05 §9). */
export interface TeamRoundProgressView {
  round: number;
  members: string[];
  reported: string[];
}
```

and add `roundProgress?: TeamRoundProgressView | null;` to both `TeamSummaryView` and `TeamLiveView`.

`teamJourney.ts`:

1. Replace the `PostFooter` line and add the new types:

```ts
export interface HeldNote { for: string[]; everyone: boolean; untilRound: number }
export interface PostFooter { woke: string[]; queued: string[]; held: HeldNote | null }
export interface RoundMember { label: string; handed: number[]; reportedAt: string | null; status: string | null }
export interface VerdictProposal { id: string; stances: Record<string, string>; adopted: boolean }
```

2. Add three members to the `JourneyItem` union:

```ts
  | { kind: "round"; key: string; at: string; round: number; members: RoundMember[]; ended: boolean }
  | { kind: "verdict"; key: string; at: string; round: number; proposals: VerdictProposal[];
      adopted: string | null; nextRound: number | null; newPosts: number }
  | { kind: "adopted"; key: string; at: string; post: TeamPostView }
```

3. Add helpers above `buildJourney`:

```ts
const EMPTY_FOOTER = (): PostFooter => ({ woke: [], queued: [], held: null });
const roundOf = (e: TeamActivityView): number | null =>
  typeof e.payload.round === "number" ? e.payload.round : null;

function roundItem(e: TeamActivityView, activity: TeamActivityView[]): JourneyItem {
  const round = Number(e.payload.round ?? 0);
  const raw = Array.isArray(e.payload.members) ? e.payload.members as { label?: string; handed?: number[] }[] : [];
  const members = raw.map((m): RoundMember => {
    const label = String(m.label ?? "");
    const wrap = activity.find((x) => x.kind === "wrapped_up" && x.label === label && roundOf(x) === round);
    return { label, handed: Array.isArray(m.handed) ? m.handed : [],
             reportedAt: wrap?.at ?? null, status: wrap ? String(wrap.payload.status ?? "completed") : null };
  });
  const ended = activity.some((x) => x.kind === "round_ended" && roundOf(x) === round);
  return { kind: "round", key: `a${e.aseq}`, at: e.at, round, members, ended };
}

function verdictItem(e: TeamActivityView, activity: TeamActivityView[]): JourneyItem {
  const round = Number(e.payload.round ?? 0);
  const raw = Array.isArray(e.payload.proposals) ? e.payload.proposals as Record<string, unknown>[] : [];
  const proposals = raw.map((p): VerdictProposal => ({
    id: String(p.id ?? ""), stances: (p.stances as Record<string, string>) ?? {}, adopted: p.adopted === true }));
  const next = activity.some((x) => x.kind === "round_started" && roundOf(x) === round + 1);
  return { kind: "verdict", key: `a${e.aseq}`, at: e.at, round, proposals,
           adopted: typeof e.payload.adopted === "string" ? e.payload.adopted : null,
           nextRound: next ? round + 1 : null, newPosts: Number(e.payload.new_posts ?? 0) };
}
```

4. In `buildJourney`'s folding loop, extend Rule 3 to `held` and round-carrying `took_up`:

```ts
  for (const e of activity) {
    if (e.kind === "took_up" && roundOf(e) !== null) {
      folded.add(e.aseq);                               // the round strip shows what it handed
      continue;
    }
    if (e.kind !== "woke" && e.kind !== "notified" && e.kind !== "held") continue;
    if (e.causeSeq === null) continue;
    const cause = bySeq.get(e.causeSeq);
    if (!cause || !cardShown(cause)) continue;
    const footer = footers.get(cause.seq) ?? EMPTY_FOOTER();
    if (e.kind === "held") {
      footer.held = { for: (e.payload.for as string[]) ?? [], everyone: e.payload.everyone === true,
                      untilRound: Number(e.payload.until_round ?? 0) };
    } else {
      (e.kind === "woke" ? footer.woke : footer.queued).push(e.label);
    }
    footers.set(cause.seq, footer);
    folded.add(e.aseq);
  }
```

(This replaces the existing loop that handled only `woke`/`notified`.) Replace both remaining `{ woke: [], queued: [] }` literals with `EMPTY_FOOTER()`.

5. In `postItem`, make an adoption's system post its own item:

```ts
    if (p.kind === "system") {
      timed(typeof p.payload.adopted === "string"
        ? { kind: "adopted", key: `p${p.seq}`, at: p.createdAt, post: p }
        : { kind: "system", key: `p${p.seq}`, at: p.createdAt, post: p });
    } else if …
```

6. In the entries loop, right after the `phase` branch and before the `if (!filters.activity …` line:

```ts
    if (e.kind === "round_started") {
      timed(roundItem(e, activity));
      continue;
    }
    if (e.kind === "round_ended") {
      timed(verdictItem(e, activity));
      continue;
    }
```

7. The `timed` helper's parameter type excludes only `chapter` and `gap`, so the three new kinds pass through — no change there.

- [ ] **Step 4: Run, typecheck, commit**

Run: `perl -e 'alarm 300; exec @ARGV' npx vitest run src/test/teamJourney.test.ts > /tmp/t11.txt 2>&1; echo exit=$?; tail -4 /tmp/t11.txt` and `npm run typecheck > /tmp/t11t.txt 2>&1; echo exit=$?`.
Expected: exit=0 for both. Typecheck errors in `Journey.tsx` (a `switch` with no case for the new kinds) are fixed in Task 12 — if `tsc` reports a non-exhaustive switch here, add `case "round": case "verdict": case "adopted": return null;` to `Journey.tsx`'s switch now and let Task 12 replace it.

```bash
git add apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/webview-ui/src/teamJourney.ts apps/vscode-extension/webview-ui/src/test/teamJourney.test.ts apps/vscode-extension/webview-ui/src/components/teams/Journey.tsx
git commit -m "feat(webview): journey items for rounds, verdicts, held posts and the adoption

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 12: Board components — round strip, verdict, held footer, adopted card, new beats

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/teams/RoundItems.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/teams/Journey.tsx`
- Test: `apps/vscode-extension/webview-ui/src/test/roundItems.test.tsx` (new)

**Interfaces:**
- Consumes: Task 11 item kinds; `useAgentsUi`, `useTeamsUi`, `useNow`, `elapsedMs`, `formatElapsed`, `Avatar`, `identityFor`.
- Produces: `RoundStrip({ item, teamId, roster })`, `Verdict({ item, roster })`, `AdoptedCard({ post, roster })` (all from `RoundItems.tsx`); `heldText(held: HeldNote): string`; `beatText` handles `requeued` and `deadline`; `WrapRow` says `wrapped up round N` when the event carries a round.

- [ ] **Step 1: Write the failing tests**

`src/test/roundItems.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { beatText } from "../components/teams/Journey";
import { AdoptedCard, RoundStrip, Verdict, heldText } from "../components/teams/RoundItems";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentSummaryView, TeamPostView, TeamSummaryView } from "../types";

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 2, maxRounds: 3,
  pausedReason: null, openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "",
  members: [{ label: "alice", agentId: "agent-a", status: "completed" },
            { label: "bob", agentId: "agent-b", status: "running" }],
};
const row = (id: string, label: string, status: string): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "gp", label, status, now: "", toolCount: 0,
  filesChangedCount: 0, startedAt: new Date(Date.now() - 60_000).toISOString(), endedAt: null,
  reportPreview: "", activationStartedAt: new Date(Date.now() - 42_000).toISOString(),
  activationEndedAt: null,
});

function wrap(node: ReactNode) {
  const agentsUi: AgentsUi = {
    agents: { "agent-a": row("agent-a", "alice", "completed"), "agent-b": row("agent-b", "bob", "running") },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, views: {}, openTeam: vi.fn() };
  return render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>{node}</TeamsContext.Provider></AgentsContext.Provider>);
}

describe("round items", () => {
  it("a running round: who woke, what each was handed, progress and who it waits on", () => {
    wrap(<RoundStrip teamId="team-1" roster={["alice", "bob"]} item={{
      kind: "round", key: "a1", at: "2026-10-06T10:00:00Z", round: 2, ended: false, members: [
        { label: "alice", handed: [2, 5], reportedAt: "2026-10-06T10:03:00Z", status: "completed" },
        { label: "bob", handed: [3], reportedAt: null, status: null }] }} />);
    const strip = screen.getByTestId("round-2");
    expect(strip).toHaveTextContent("Round 2 started — both members woke");
    expect(strip).toHaveTextContent("#2");
    expect(strip).toHaveTextContent("✓ reported");
    expect(strip).toHaveTextContent(/1 of 2 reported · waiting on bob \(4\ds\)/);
  });

  it("verdicts: not adopted with the next round, then adopted", () => {
    const { rerender } = wrap(<Verdict roster={["alice", "bob"]} item={{
      kind: "verdict", key: "a2", at: "x", round: 1, adopted: null, nextRound: 2, newPosts: 3,
      proposals: [{ id: "P1", stances: { alice: "agree", bob: "object" }, adopted: false }] }} />);
    expect(screen.getByTestId("verdict-1")).toHaveTextContent(
      "Round 1 ended · P1: alice ✓ bob ✗ — not adopted · round 2 next, with 3 new posts");
    rerender(<Verdict roster={["alice", "bob"]} item={{
      kind: "verdict", key: "a3", at: "x", round: 2, adopted: "P1", nextRound: null, newPosts: 0,
      proposals: [{ id: "P1", stances: { alice: "agree", bob: "agree" }, adopted: true }] }} />);
    expect(screen.getByTestId("verdict-2")).toHaveTextContent("Round 2 ended · P1: alice ✓ bob ✓ — adopted");
  });

  it("the adopted card lists assignments", () => {
    const post: TeamPostView = { teamId: "t", seq: 9, author: "system", kind: "system", recipient: null,
      text: "Adopted P1.", mentions: [], refId: null, round: null, closed: null, createdAt: "x",
      payload: { adopted: "P1", assignments: [{ member: "alice", part: "api", files: ["a.py"] }],
                 shared_files: ["c.py"] } };
    wrap(<AdoptedCard post={post} roster={["alice", "bob"]} />);
    const card = screen.getByTestId("adopted-p9");
    expect(card).toHaveTextContent("Adopted P1");
    expect(card).toHaveTextContent("alice → api");
    expect(card).toHaveTextContent("a.py");
    expect(card).toHaveTextContent("shared: c.py");
  });

  it("held footers and the new beats", () => {
    expect(heldText({ for: ["bob"], everyone: false, untilRound: 2 })).toBe(
      "for bob · held for round 2 — nothing reaches a member mid-round");
    expect(heldText({ for: ["alice", "bob"], everyone: true, untilRound: 3 })).toBe(
      "for everyone · held for round 3");
    const ev = (kind: string, payload: Record<string, unknown>) => ({ teamId: "t", aseq: 1, at: "x",
      label: "bob", kind, activation: null, causeSeq: null, payload });
    expect(beatText(ev("requeued", { retry: 1, of: 2 })).text).toBe(
      "bob restarted after a provider error (retry 1 of 2)");
    expect(beatText(ev("deadline", {})).text).toBe(
      "bob hit the round's time limit — reporting what it has");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/roundItems.test.tsx > /tmp/t12.txt 2>&1; echo exit=$?; tail -6 /tmp/t12.txt`
Expected: exit≠0 — `Failed to resolve import "../components/teams/RoundItems"`.

- [ ] **Step 3: Create `components/teams/RoundItems.tsx`**

```tsx
import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import { identityFor } from "../../teamIdentity";
import type { HeldNote, JourneyItem } from "../../teamJourney";
import type { TeamPostView } from "../../types";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Avatar } from "./Avatar";
import { useTeamsUi } from "./TeamsContext";

// The deliberation's structure (spec 2026-10-05 §9; mockup .round / .verdict / .sys).

const clock = (at: string) => new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Name({ label, roster }: { label: string; roster: string[] }) {
  return <span className="font-semibold" style={{ color: identityFor(label, roster).color }}>{label}</span>;
}

export function heldText(held: HeldNote): string {
  return held.everyone
    ? `for everyone · held for round ${held.untilRound}`
    : `for ${held.for.join(" · ")} · held for round ${held.untilRound} — nothing reaches a member mid-round`;
}

function wokeText(count: number, total: number, labels: string[]): string {
  if (count === total && total === 2) return "both members woke";
  if (count === total) return `all ${total} members woke`;
  return `${labels.join(", ")} woke`;
}

export function RoundStrip({ item, teamId, roster }: {
  item: Extract<JourneyItem, { kind: "round" }>; teamId: string; roster: string[];
}) {
  const agentsUi = useAgentsUi();
  const team = useTeamsUi().teams[teamId];
  const rowFor = (label: string) => {
    const member = team?.members.find((m) => m.label === label);
    return member ? agentsUi.agents[member.agentId] : undefined;
  };
  const pending = item.members.filter((m) => m.reportedAt === null);
  const live = !item.ended && pending.some((m) => { const r = rowFor(m.label); return r && !isTerminalAgent(r.status); });
  const now = useNow(live);
  const reported = item.members.length - pending.length;
  const waiting = pending.find((m) => { const r = rowFor(m.label); return r && !isTerminalAgent(r.status); });
  const waitingRow = waiting ? rowFor(waiting.label) : undefined;
  const pct = item.members.length ? Math.round((reported / item.members.length) * 100) : 0;
  return (
    <div data-testid={`round-${item.round}`} className="relative grid gap-1.5 rounded-[10px] border px-3 py-2.5"
      style={{ borderColor: "var(--accent-brd)", background: "linear-gradient(180deg, var(--accent-bg), transparent 70%), var(--color-surface)" }}>
      <span aria-hidden="true" className="absolute left-[-22px] top-2 grid h-4 w-4 place-items-center rounded-full border-[1.5px] text-[10px]"
        style={{ background: "var(--color-surface-2)", borderColor: "var(--color-amber)", color: "var(--color-amber)" }}>⚡</span>
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <strong className="font-semibold">Round {item.round} started — {wokeText(item.members.length, roster.length, item.members.map((m) => m.label))}</strong>
        <span className="ml-auto text-[10.5px] tabular-nums text-text-3">{clock(item.at)}</span>
      </div>
      <div className="grid gap-1">
        {item.members.map((m) => {
          const r = rowFor(m.label);
          const working = m.reportedAt === null && r !== undefined && !isTerminalAgent(r.status);
          return (
            <div key={m.label} className="grid grid-cols-[auto_52px_minmax(0,1fr)_auto] items-center gap-2 text-[11.5px] text-text-2">
              <Avatar label={m.label} roster={roster} size="sm" />
              <Name label={m.label} roster={roster} />
              <span className="flex flex-wrap items-center gap-1">
                {m.handed.map((s) => <span key={s} className="font-mono text-[10.5px] font-semibold text-text-3">#{s}</span>)}
                <span className="text-text-3">{m.handed.length ? `${m.handed.length} post${m.handed.length === 1 ? "" : "s"}` : "nothing new"}</span>
              </span>
              <span className="whitespace-nowrap text-[10.5px]" style={{ color: m.reportedAt ? "var(--color-green)" : "var(--color-accent-ink)" }}>
                {m.reportedAt ? `✓ reported ${clock(m.reportedAt)}` : working && r ? `● working · ${formatElapsed(elapsedMs(r, now) ?? 0)}` : "waiting"}
              </span>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-2.5 text-[11px] text-text-2">
        <span>{reported} of {item.members.length} reported{!item.ended && waiting && waitingRow
          ? ` · waiting on ${waiting.label} (${formatElapsed(elapsedMs(waitingRow, now) ?? 0)})` : ""}</span>
        <span className="h-[5px] max-w-[200px] flex-1 overflow-hidden rounded-full" style={{ background: "var(--color-surface-2)" }}>
          <i className="block h-full rounded-full" style={{ width: `${pct}%`, background: item.ended ? "var(--color-green)" : "var(--color-accent)" }} />
        </span>
      </div>
    </div>
  );
}

const MARK: Record<string, string> = { agree: "✓", object: "✗", none: "–" };

export function Verdict({ item, roster }: { item: Extract<JourneyItem, { kind: "verdict" }>; roster: string[] }) {
  const yes = item.adopted !== null;
  return (
    <div data-testid={`verdict-${item.round}`} className="relative flex flex-wrap items-center gap-2 rounded-lg px-2.5 py-1.5 text-[11.5px]"
      style={yes
        ? { border: "1px solid var(--green-brd)", background: "var(--green-bg)", color: "var(--color-text)" }
        : { border: "1px dashed var(--color-border-strong)", color: "var(--color-text-2)" }}>
      <span aria-hidden="true" className="absolute left-[-19px] top-1/2 -mt-[4.5px] h-[9px] w-[9px] rotate-45"
        style={{ background: yes ? "var(--color-green)" : "var(--color-amber)", boxShadow: "0 0 0 3px var(--color-surface-2)" }} />
      {/* Spaces are explicit text: the row is flex, and the line must read as one sentence. */}
      <strong>Round {item.round} ended</strong>
      {item.proposals.length === 0 && <span>{" · no open proposals"}</span>}
      {item.proposals.map((p) => (
        <span key={p.id}>
          {` · ${p.id}: `}
          {Object.entries(p.stances).map(([label, stance], i) => (
            <span key={label}>{i > 0 && " "}<Name label={label} roster={roster} />{` ${MARK[stance] ?? "–"}`}</span>
          ))}
          {" — "}{p.adopted ? <strong style={{ color: "var(--color-green)" }}>adopted</strong> : "not adopted"}
        </span>
      ))}
      {!yes && item.nextRound !== null && (
        <span className="text-text-3">{` · round ${item.nextRound} next, with ${item.newPosts} new post${item.newPosts === 1 ? "" : "s"}`}</span>
      )}
      {!yes && item.nextRound === null && <span className="text-text-3">{" · the round limit is reached"}</span>}
    </div>
  );
}

export function AdoptedCard({ post, roster }: { post: TeamPostView; roster: string[] }) {
  const assignments = Array.isArray(post.payload.assignments)
    ? post.payload.assignments as { member?: string; part?: string; files?: string[] }[] : [];
  const shared = Array.isArray(post.payload.shared_files) ? post.payload.shared_files as string[] : [];
  return (
    <div data-testid={`adopted-p${post.seq}`} className="relative grid gap-1 rounded-[10px] border border-border px-3 py-2 text-[12px]"
      style={{ background: "var(--color-panel)" }}>
      <span aria-hidden="true" className="absolute left-[-19px] top-[13px] h-[9px] w-[9px] rounded-full"
        style={{ background: "var(--color-green)", boxShadow: "0 0 0 3px var(--color-surface-2)" }} />
      <div><strong>Adopted {String(post.payload.adopted)}{post.payload.by === "main" ? " by the main agent" : ""}.</strong></div>
      {assignments.map((a, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5 text-text-2">
          <Avatar label={String(a.member)} roster={roster} size="sm" />
          <Name label={String(a.member)} roster={roster} /> → {a.part}
          {(a.files ?? []).map((f) => <code key={f} className="font-mono text-[11px] text-[var(--color-code)]">{f}</code>)}
        </div>
      ))}
      {shared.length > 0 && <div className="text-[11px] text-text-3">shared: {shared.join(", ")}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Wire into `Journey.tsx`**

1. Imports: `import { AdoptedCard, RoundStrip, Verdict, heldText } from "./RoundItems";`
2. In `PostCard`, add the held footer inside the footer block, and show the footer when any of the three parts is present — replace the footer's opening condition
   `{(footer.woke.length > 0 || footer.queued.length > 0) && (`
   with
   `{(footer.woke.length > 0 || footer.queued.length > 0 || footer.held) && (`
   and add as the block's last child:

```tsx
          {footer.held && <><span>→</span><span className="text-text-3">{heldText(footer.held)}</span></>}
```

3. In `beatText`'s `switch`, before `default`:

```tsx
    case "requeued":
      return { icon: "↻", tone: "var(--color-amber)", text: `${e.label} restarted after a provider error (retry ${String(p.retry)} of ${String(p.of)})` };
    case "deadline":
      return { icon: "⏱", tone: "var(--color-amber)", text: `${e.label} hit the round's time limit — reporting what it has` };
```

4. In `WrapRow`, replace `<span>wrapped up</span>` with
   `<span>{typeof p.round === "number" ? `wrapped up round ${p.round}` : "wrapped up"}</span>`.
5. `Journey` takes `teamId` already; in its `switch`, add (replacing any placeholder `return null` cases from Task 11):

```tsx
            case "round": return <RoundStrip key={item.key} item={item} teamId={teamId} roster={roster} />;
            case "verdict": return <Verdict key={item.key} item={item} roster={roster} />;
            case "adopted": return <AdoptedCard key={item.key} post={item.post} roster={roster} />;
```

- [ ] **Step 5: Run, typecheck, commit**

Run: `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/t12.txt 2>&1; echo exit=$?; grep -E "Tests " /tmp/t12.txt` and `npm run typecheck > /tmp/t12t.txt 2>&1; echo exit=$?`.
Expected: exit=0 for both (existing Journey/teamWindow tests keep passing: the footer object gained a field only).

```bash
git add apps/vscode-extension/webview-ui/src/components/teams/RoundItems.tsx apps/vscode-extension/webview-ui/src/components/teams/Journey.tsx apps/vscode-extension/webview-ui/src/test/roundItems.test.tsx
git commit -m "feat(webview): round strips, verdicts, held footers and the adopted card

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

### Task 13: Round chapters, the card's progress bar, the stepper's deadlock and adoption

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/teamChapters.ts`, `src/components/teams/MemberView.tsx`, `src/teams.ts`, `src/components/teams/TeamCard.tsx`, `src/components/teams/PhaseStepper.tsx`
- Test: append to `src/test/teamChapters.test.ts`, `src/test/teams.test.ts`, `src/test/teamCard.test.tsx`

**Interfaces:**
- Produces: `ChapterWhy.round?: number | null`; `chapterHeading(c: MemberChapter, isProposal: (seq: number) => boolean): { title: string; why: string }`; `roundProgressText(progress: TeamRoundProgressView | null | undefined, team: TeamSummaryView, agents: Record<string, AgentSummaryView>, now: number): { text: string; pct: number } | null`; `PhaseStepper` shows `Deadlocked · round N of M` and ends `DONE` as `Plan adopted` (5A: a team's only way to DONE).

- [ ] **Step 1: Write the failing tests**

Append to `src/test/teamChapters.test.ts`:

```ts
import { buildChapters as chapters5a, chapterHeading } from "../teamChapters";

describe("round chapters (spec 2026-10-05 §9)", () => {
  it("a deliberation activation is titled by its round and lists what it was handed", () => {
    const took = { teamId: "t", aseq: 3, at: "2026-10-06T10:00:02Z", label: "alice", kind: "took_up",
      activation: 1, causeSeq: null, payload: { posts: [1], from: ["main"], round: 1 } };
    const [c] = chapters5a("alice", [], [took], [], { working: false, fallbackStatus: "completed" });
    expect(c.why).toEqual({ cause: "round", by: null, postSeq: null, round: 1 });
    expect(chapterHeading(c, (s) => s === 1)).toEqual({ title: "Round 1", why: "handed P1" });
  });

  it("other chapters keep the Chapter title", () => {
    const woke = { teamId: "t", aseq: 3, at: "x", label: "alice", kind: "woke", activation: 1,
      causeSeq: 5, payload: { cause: "mention", by: "bob", post_seq: 5 } };
    const [c] = chapters5a("alice", [], [woke], [], { working: false, fallbackStatus: "completed" });
    expect(chapterHeading(c, () => false)).toEqual({ title: "Chapter 1", why: "woken by bob's post #5" });
  });
});
```

Append to `src/test/teams.test.ts` (merge `roundProgressText` into the existing `../teams` import):

```ts
describe("round progress on the card", () => {
  it("counts reports and names whom it waits on", () => {
    const now = Date.parse("2026-10-06T10:01:00Z");
    const team = { ...SUMMARY, round: 2, members: [
      { label: "alice", agentId: "a", status: "completed" }, { label: "bob", agentId: "b", status: "running" }] };
    const agents = { b: { agentId: "b", parentAgentId: null, depth: 1, name: "gp", label: "bob",
      status: "running", now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null,
      reportPreview: "", activationStartedAt: "2026-10-06T10:00:18Z", activationEndedAt: null } };
    expect(roundProgressText({ round: 2, members: ["alice", "bob"], reported: ["alice"] }, team, agents, now))
      .toEqual({ text: "Round 2 · 1 of 2 reported · waiting on bob (42s)", pct: 50 });
    expect(roundProgressText(null, team, agents, now)).toBeNull();
  });
});
```

Append to `src/test/teamCard.test.tsx` (inside its `describe`):

```tsx
  it("shows the round's progress bar while deliberating", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />, {
      teams: { "team-1": { ...TEAM, roundProgress: { round: 1, members: ["review", "impl"], reported: ["review"] } } } });
    expect(screen.getByTestId("round-progress")).toHaveTextContent(/Round 1 · 1 of 2 reported · waiting on impl/);
  });

  it("a deadlocked team reads as such on the stepper", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r"]} />, {
      teams: { "team-1": { ...TEAM, phase: "DEADLOCKED", round: 3 } } });
    expect(screen.getByTestId("team-card")).toHaveTextContent("Deadlocked · round 3 of 3");
  });

  it("an adopted (DONE) team ends its road with the plan", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r"]} />, {
      teams: { "team-1": { ...TEAM, phase: "DONE" } } });
    expect(screen.getByTestId("team-card")).toHaveTextContent("Plan adopted");
    expect(screen.getByTestId("team-card")).not.toHaveTextContent("Implementing");
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamChapters.test.ts src/test/teams.test.ts src/test/teamCard.test.tsx > /tmp/t13.txt 2>&1; echo exit=$?; tail -10 /tmp/t13.txt`
Expected: exit≠0 — `chapterHeading is not a function` / `roundProgressText is not a function`.

- [ ] **Step 3: Implement**

`teamChapters.ts`:
- `export interface ChapterWhy { cause: string; by: string | null; postSeq: number | null; round?: number | null }`
- In `buildChapters`, replace the `why:` property with:

```ts
      why: woke ? { cause: String(woke.payload.cause ?? ""), by: (woke.payload.by as string | null) ?? null,
                    postSeq: (woke.payload.post_seq as number | null) ?? woke.causeSeq }
        : typeof took?.payload.round === "number"
          ? { cause: "round", by: null, postSeq: null, round: took.payload.round }
          : null,
```

- Add at the end:

```ts
/** A chapter's header: deliberation activations are rounds (spec 2026-10-05 §9). */
export function chapterHeading(c: MemberChapter, isProposal: (seq: number) => boolean): { title: string; why: string } {
  if (c.why?.cause === "round") {
    const handed = c.handed.map((s) => (isProposal(s) ? `P${s}` : `#${s}`)).join(" ");
    return { title: `Round ${c.why.round}`, why: handed ? `handed ${handed}` : "nothing new on the board" };
  }
  return { title: `Chapter ${c.n}`, why: whyText(c.why, isProposal) };
}
```

`MemberView.tsx` — import `chapterHeading` with `buildChapters`, and replace the two header lines

```tsx
        <strong>Chapter {c.n}</strong>{" "}
        <span className="text-[12px] text-text-2">· {whyText(c.why, isProposal)}</span>
```

with

```tsx
        <strong>{chapterHeading(c, isProposal).title}</strong>{" "}
        <span className="text-[12px] text-text-2">· {chapterHeading(c, isProposal).why}</span>
```

(drop the `whyText` import if nothing else in the file uses it).

`teams.ts` — merge `TeamRoundProgressView` into the type import, and add:

```ts
/** The card's progress line (spec 2026-10-05 §9): the round's reports, and the member it
 * waits on with that member's active time, ticked by the caller's useNow. */
export function roundProgressText(
  progress: TeamRoundProgressView | null | undefined, team: TeamSummaryView,
  agents: Record<string, AgentSummaryView>, now: number,
): { text: string; pct: number } | null {
  if (!progress || progress.members.length === 0) return null;
  const done = progress.reported.length;
  let text = `Round ${progress.round} · ${done} of ${progress.members.length} reported`;
  const pending = progress.members.filter((label) => !progress.reported.includes(label));
  for (const label of pending) {
    const member = team.members.find((m) => m.label === label);
    const row = member ? agents[member.agentId] : undefined;
    if (!row || isTerminalAgent(row.status)) continue;
    const ms = elapsedMs(row, now);
    text += ` · waiting on ${label}${ms !== null ? ` (${formatElapsed(ms)})` : ""}`;
    break;
  }
  return { text, pct: Math.round((done / progress.members.length) * 100) };
}
```

`TeamCard.tsx` — import `roundProgressText`, compute `const progress = roundProgressText(team.roundProgress, team, agentsUi.agents, now);` after `counts`, and render right after the `PhaseStepper` line:

```tsx
        {progress && (
          <div data-testid="round-progress" className="flex items-center gap-2.5 text-[11px] text-text-2">
            <span>{progress.text}</span>
            <span className="h-[5px] max-w-[200px] flex-1 overflow-hidden rounded-full" style={{ background: "var(--color-surface-2)" }}>
              <i className="block h-full rounded-full" style={{ width: `${progress.pct}%`, background: "var(--color-accent)" }} />
            </span>
          </div>
        )}
```

`PhaseStepper.tsx`:
- `const ended = isTerminalTeam(phase);` (DONE included: in 5A a team reaches DONE only by adopting a plan; 5B restores the full road once implementation exists).
- `label`: `i === 1 && at === 1 && !ended ? (phase === "DEADLOCKED" ? `Deadlocked · round ${round} of ${maxRounds}` : `Deliberating · round ${round} of ${maxRounds}`) : step`.
- `done`: `const done = i < at || (ended && i <= 1);`, `now`: `const now = i === at && !ended;`.
- The ended suffix: colour green for DONE, red for FAILED, grey otherwise; text `phase === "FAILED" ? "Failed" : phase === "DONE" ? "Plan adopted" : "Disbanded"`:

```tsx
      {ended && (
        <span className="inline-flex items-center whitespace-nowrap">
          <span className={mini ? "mx-1 h-px w-2.5" : "mx-1.5 h-px w-[18px]"} style={{ background: "var(--color-border-strong)" }} />
          <span className="mr-1.5 h-2 w-2 rounded-full" style={{ background: phase === "FAILED" ? "var(--color-red)" : phase === "DONE" ? "var(--color-green)" : "var(--color-text-3)" }} />
          <span style={{ color: phase === "FAILED" ? "var(--color-red)" : phase === "DONE" ? "var(--color-green)" : "var(--color-text-2)", fontWeight: 600 }}>
            {phase === "FAILED" ? "Failed" : phase === "DONE" ? "Plan adopted" : "Disbanded"}
          </span>
        </span>
      )}
```

If an existing test asserts the old DONE rendering (`grep -rn '"DONE"' src/test`), update it to `Plan adopted`.

- [ ] **Step 4: Run, typecheck, build, commit**

Run: `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/t13.txt 2>&1; echo exit=$?; grep -E "Tests " /tmp/t13.txt`, `npm run typecheck > /tmp/t13t.txt 2>&1; echo exit=$?`, then from `apps/vscode-extension`: `npm run build > /tmp/t13b.txt 2>&1; echo exit=$?`.
Expected: three exit=0.

```bash
git add apps/vscode-extension/webview-ui/src
git commit -m "feat(webview): round chapters, card round progress, deadlock and adoption on the stepper

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz"
```

---

# Part D — Docs, suites, smoke

### Task 14: CLAUDE.md, full suites, live smoke

- [ ] **Step 1: CLAUDE.md** — in the **Agent teams — foundations (v2 Phase 4)** bullet, replace the sentence that begins `The **interim activation policy** lives in one method, `ChatController._team_posted`…` up to `…and wakes nobody.` with:

```
**Coordinator — 5A deliberation (spec v2 §8.1–§8.4; plan `docs/superpowers/plans/2026-10-06-team-coordinator-5a-deliberation.md`):** `teams/state_machine.py` (pure `apply(state, event) → (state, actions)`; events `Kickoff`/`MemberReported`/`RoundEvaluated`/`MainAdopt`/`MainPost`/`Disband`) and `teams/coordinator.py` (`TeamCoordinator`, one per live team in `ChatController._coordinators`; executes actions through the `CoordinatorHost` methods `ChatController` implements). Deliberation runs in rounds: a proposal kickoff starts every member, a post kickoff its mentions (the rest join at round 2); a round's input is the board up to the round's **cutoff seq** (`delta_cutoff`), so a post made mid-round is recorded as `held` and reaches members next round — nothing is pushed mid-round (`_on_leftover` keeps a member's leftovers with `AgentSupervisor.keep`). Next-round activations are scheduled with `call_soon` (`start_member`): the last reporter is still inside its own task. A round ends when every quorum member has its final report: `failed_transient` re-queues after 30 s / 120 s; `failed` or a user ■ leaves the quorum (`member_lost`), and fewer than 2 ends the team `FAILED`; a deadline stop stays in. Per-member deadline `CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC` (900) counts **active** time only (`subagents/runtime.py::active_seconds` — minus gate waits and `METER.peek` limiter waits); at expiry `ControllerLoop.force_final()` narrows to `report`, then a 120 s grace before a `deadline` stop. Stances ride the report too (`stances`/`proposal` fields in the team-member schema only), validated in the loop through `report_check` (`teams/report_fields.py::ReportFields`): an invalid entry is refused (a byte-identical resubmission counts as malformed; exhaustion accepts with invalid entries dropped), a missing expected stance gets one redirect. `teams/adoption.py::evaluate_round` adopts at round end (open, posted before the round, every quorum member's latest stance `agree`; lowest seq wins); round limit → `DEADLOCKED`, where `post_board` runs one more round and `adopt_proposal` adopts. **5A interim:** adoption ends the team `DONE` (`end_reason "adopted"`); 5B adds the approval gate and implementation. Milestones (`adopted`, `deadlock`, `member_lost`, `ended`) are wake notices with `source_kind "team"` (compact bodies, `teams/milestones.py`); trace `chat/<thread>/<created_turn>/teams/<team>/coordinator.jsonl`. Members lose the `edit` type outside `IMPLEMENTING` and are capped at 40 iterations in deliberation. Activity gains `round_started`/`round_ended`/`held`/`requeued`/`deadline`; `/live` teams gain `round_progress`; rewind 409s while a team has not ended. Board: round strips, verdicts, held footers, the adopted card (`RoundItems.tsx`); member chapters titled `Round n`.
```

Commit: `git add CLAUDE.md && git commit -m "docs(claude): the team coordinator, deliberation part" ` (with the trailers).

- [ ] **Step 2: Full suites** (from the worktree root)

```bash
cd services/agentd-py && .venv/bin/python -m pytest --color=no --timeout=120 > /tmp/py.txt 2>&1; echo exit=$?; tail -3 /tmp/py.txt
.venv/bin/python -m ruff check agentd tests > /tmp/ruff.txt 2>&1; echo exit=$?; tail -1 /tmp/ruff.txt
.venv/bin/python -m mypy agentd > /tmp/mypy.txt 2>&1; echo exit=$?; tail -1 /tmp/mypy.txt
cd ../.. && npm run build > /tmp/build.txt 2>&1; echo exit=$?
perl -e 'alarm 900; exec @ARGV' npm run test > /tmp/ts.txt 2>&1; echo exit=$?; grep -E "Tests " /tmp/ts.txt
npm run typecheck > /tmp/tc.txt 2>&1; echo exit=$?
cd apps/vscode-extension/webview-ui && perl -e 'alarm 600; exec @ARGV' npx vitest run > /tmp/web.txt 2>&1; echo exit=$?; tail -4 /tmp/web.txt
```

Known pre-existing: `test_command_only_step_runs_command_and_verifies` fails on the base commit too; ruff and mypy carry pre-existing errors — compare the error lists (not counts) with the base commit, path-normalised, and accept only an empty diff.

- [ ] **Step 3: Live smoke** on the dev host (second VS Code instance `vscode-p4`, CDP 9335), relaunched as in Phase 4 with `CRUCIBLE_TEAMS_ENABLED=1` and, to see deadlines quickly, `CRUCIBLE_TEAM_ROUND_TIMEOUT_SEC=300`. Stop the old instance and its managed backend first (the lockfile names the backend pid). Wait for the backend's CPU to settle before the first message (the MPS model-load race can wedge a fresh backend — kill and let it respawn if it sits at ~300 % CPU).

1. A proposal kickoff both members can agree with (a small, clearly correct plan): the card shows `Round 1 · n of 2 reported · waiting on …`; the board shows the Round 1 strip with handed chips, wrap-ups `wrapped up round 1`, a verdict, then `Adopted P1` and a Done chapter; the stepper reads `Plan adopted`; the main agent is woken by the `adopted` milestone (a 🔔 marker naming the team).
2. During a round, one member mentions the other: the post's footer reads `→ for <label> · held for round 2 …`, and round 2's strip lists that post among the member's handed chips.
3. A plan the members disagree on with `max_rounds` 2: rounds 1–2, verdicts `not adopted`, a `Deadlocked` chapter, the main agent woken by `deadlock`; ask it to adopt one proposal (`adopt_proposal`) — or to post — and check the board.
4. Member tab: chapters titled `Round 1`, `Round 2` with `handed …`.
5. Reload the webview: same board, no duplicates.
6. Stop one member of a two-member team (■): `member_lost`, then `Failed`; the other member's chapter closes `■ stopped`.
7. Try a rewind while a team is live: the UI shows the 409 naming the team.

Record any failure as a finding, fix it with a regression test, re-run the step.
