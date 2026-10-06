# Team Coordinator 5C — Review and Teardown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a team finishes its assignments it reviews its own work: a closing proposal every member verifies, objections routed to the file's owner (or the objector) as fix assignments, at most a bounded number of fix cycles, then `DONE` with a full `done` milestone — and a rewind deletes the teams it rewinds past.

**Architecture:** Two pure functions in a new `teams/review.py` (`evaluate_review`, `route_objection`) decide what a review cycle came to. The state machine gains a review loop (`OpenReview → ReviewStarted → EvaluateReview → ReviewEvaluated → StartFixes → … → OpenReview`) that reuses the round mechanics (round members, quorum, re-queues, deadlines) and the implementation mechanics (assigned/done) it already has. The coordinator executes it: it posts each closing proposal, carries forward the stances of members who are not re-asked, routes objections, and hands fixers their objections. The webview gains an Edits section per member chapter, the member's assignment, a closing-proposal label and the review state; three small UI issues from the 5B smoke ride along.

**Tech Stack:** Python 3.12 (FastAPI, pydantic, sqlite, pytest + pytest-asyncio), TypeScript (Zod editor-client, React webview, vitest).

**Spec:** `docs/superpowers/specs/2026-10-02-subagents-v2-design.md` §8.7 (review), §8.8 (`done` milestone), §8.10 (what a rewind deletes), §3.11 (review iteration cap 25), §9 (team UI); `docs/superpowers/specs/2026-10-05-team-activity-ui-design.md` §7 (member tab) and §9 (Edits section, assignment rows). Prior parts: `docs/superpowers/plans/2026-10-06-team-coordinator-5a-deliberation.md`, `…-5b-implementation.md`.

**Part index:**

| Part | Scope | This plan |
|---|---|---|
| 5A Deliberation | rounds, stances, adoption, deadlock, quorum | done |
| 5B Implementation | approval gate, assignments, ownership, budget/pause/resume, revive (+ smoke changes) | done |
| 5C Review + teardown | closing proposal + cycles, objection routing, `done` milestone, rewind deletion, Edits section + assignment row + review UI | ✅ |

**Replaces 5B's interim:** "every assignment done → `DONE` (`implemented`)" becomes "every assignment done → `REVIEWING`". A plan with no assignments still ends `DONE` (`adopted`).

## Global Constraints

- Branch `feat/subagents-v2`, base `4a91eca`. Never push.
- Commit format `type(scope): short description`, ending with the two trailer lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01BZuqWJip34C3E9wRSeNhHz`.
- Pytest: never `-q`, never piped; redirect to a file and check `$?`; add `--timeout=120`.
- Python venv: `/Users/pradeepkumar/projects/AI editor/services/agentd-py/.venv/bin/python` (from a worktree, run from that worktree's `services/agentd-py`).
- Vitest under `perl -e 'alarm N; exec @ARGV' npx vitest run …`.
- After an editor-client change: `npm run -w @crucible/editor-client build` before the extension typecheck.
- `CRUCIBLE_TEAMS_ENABLED` stays default **off**; tests opt in through `tests/test_team_controller.py::_make`.
- New env var: `CRUCIBLE_TEAM_REVIEW_CYCLES` (default 2) — a limit, not a flag, so no opt-in sites.
- Text from agents reaching another agent is framed with `frame(...)` (spec §3.10): an objection handed to a fixer is framed.
- Prompts describe options by what each does — never rank one above another.
- `/live` team rows keep only fields that change at activation boundaries.

## Review Focus

1. **A member objects but cites no file** (evidence is a command and its output). Expected: the objection cannot be routed, so it goes to the main agent in a `member_blocked` milestone, and if nothing else was routed the team ends `DONE` listing it as unresolved — `test_unroutable_objection_goes_to_main` (Task 4).
2. **Fix cycles never converge** (a reviewer keeps objecting). Expected: at most `CRUCIBLE_TEAM_REVIEW_CYCLES` closing proposals after the first, then `DONE` with the unresolved objections — `test_review_cycles_are_capped` (Task 2) and `test_cycle_cap_ends_with_unresolved` (Task 4).
3. **A member reports in review without a stance, twice.** Expected: one redirect naming the closing proposal, then it counts as abstaining, and the `done` milestone lists it — `test_missing_review_stance_redirects_then_abstains` (Task 5).
4. **The second review cycle re-asks only who it must.** Expected: the objector, the fixer, and the owners of files the fix changed are activated; everyone else's previous agree is carried to the new closing proposal (`payload.carried`) and an unrouted objection becomes an abstention — `test_second_cycle_carries_stances` (Task 4).
5. **A rewind past the turn that created an ended team.** Expected: the team, its members, posts and activity, and its notices are deleted — `test_rewind_deletes_teams_in_the_span` (Task 6).

## File Structure

| File | Responsibility |
|---|---|
| `services/agentd-py/agentd/teams/review.py` (new) | pure `evaluate_review`, `route_objection` |
| `services/agentd-py/agentd/teams/state_machine.py` | review loop events/actions; implementation end opens review |
| `services/agentd-py/agentd/teams/config.py` | `team_review_cycles()` |
| `services/agentd-py/agentd/teams/milestones.py` | full `done` body |
| `services/agentd-py/agentd/teams/service.py` | `closing_proposal`, review stances owed, review header and hint |
| `services/agentd-py/agentd/teams/coordinator.py` | open/evaluate review, carry stances, route, fixes |
| `services/agentd-py/agentd/teams/store.py` | `delete_teams_from_checkpoint`, `delete_teams_for_turns` |
| `services/agentd-py/agentd/chat/controller.py` | review iteration cap, leftovers in review, `waiting_on` in wrap-up, rewind deletes teams |
| `apps/editor-client/src/…` | `waitingOn` on a member's last event |
| `apps/vscode-extension/webview-ui/src/{teams.ts,teamChapters.ts,types.ts}` | latest-line words, waiting phrase, chapter edits |
| `apps/vscode-extension/webview-ui/src/components/teams/{MemberView,Journey}.tsx` | Edits section, assignment row, closing proposal label, handed underscores |

---

# Part A — Pure rules

### Task 1: What a review cycle came to — `evaluate_review` and `route_objection`

**Files:**
- Create: `services/agentd-py/agentd/teams/review.py`
- Test: `services/agentd-py/tests/test_team_review.py`

**Interfaces:**
- Produces:
  - `Objection(label: str, seq: int, reason: str, files: tuple[str, ...])` (frozen dataclass).
  - `ReviewOutcome(stances: dict[str, str], objections: list[Objection], abstained: list[str])` — `stances` maps each reviewer to `"agree" | "object" | "none"` (its **latest** stance on the closing proposal).
  - `evaluate_review(posts: list[TeamPost], closing_id: str, reviewers: list[str]) -> ReviewOutcome`.
  - `Routing(objection: Objection, fixer: str | None, synthetic: bool, why: str)` — `fixer` None means the objection goes to the main agent.
  - `route_objection(objection, members: list[TeamMember], *, workspace: Path, approval_gate: bool, plan_files: set[str], can_edit: Callable[[str], bool]) -> Routing`.

Routing (spec §8.7 step 3): to the **owner** of the first cited file that is assigned; otherwise to the objection's **author** with a synthetic assignment over the cited files — when every cited file exists inside the workspace, none is protected, the author can edit, and (with an approval gate) every file is in the approved plan. Anything else goes to the main agent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_review.py`:

```python
"""A review cycle's outcome and where each objection goes (spec v2 §8.7)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentd.teams.models import TeamMember, TeamPost
from agentd.teams.review import Objection, evaluate_review, route_objection


def _post(seq: int, author: str, kind: str, ref: str | None = None, text: str = "",
          payload: dict | None = None) -> TeamPost:
    return TeamPost(team_id="t", seq=seq, author=author, kind=kind, ref_id=ref, text=text,
                    payload=payload or {}, created_at=datetime.now(UTC))


MEMBERS = [
    TeamMember(team_id="t", agent_id="a", label="api",
               assignment={"member": "api", "part": "api", "files": ["a.py"]}),
    TeamMember(team_id="t", agent_id="b", label="tests",
               assignment={"member": "tests", "part": "tests", "files": ["t.py"]}),
    TeamMember(team_id="t", agent_id="c", label="review"),
]


def test_latest_stance_wins_and_silence_abstains() -> None:
    posts = [
        _post(9, "system", "proposal"),
        _post(10, "api", "object", "P9", "too slow", {"evidence": {"files": ["a.py"], "line": 3}}),
        _post(11, "api", "agree", "P9"),
        _post(12, "tests", "object", "P9", "missing case", {"evidence": {"files": ["t.py"]}}),
        _post(13, "tests", "agree", "P3"),           # another proposal: ignored
    ]
    outcome = evaluate_review(posts, "P9", ["api", "tests", "review"])
    assert outcome.stances == {"api": "agree", "tests": "object", "review": "none"}
    assert outcome.objections == [Objection("tests", 12, "missing case", ("t.py",))]
    assert outcome.abstained == ["review"]


def _route(objection: Objection, tmp_path: Path, **over):  # type: ignore[no-untyped-def]
    args = dict(workspace=tmp_path, approval_gate=False, plan_files={"a.py", "t.py"},
                can_edit=lambda _label: True)
    args.update(over)
    return route_objection(objection, MEMBERS, **args)


def test_an_owned_file_goes_to_its_owner(tmp_path) -> None:
    routing = _route(Objection("review", 5, "bug", ("docs.md", "a.py")), tmp_path)
    assert (routing.fixer, routing.synthetic) == ("api", False)


def test_an_unowned_file_goes_to_the_objector(tmp_path) -> None:
    (tmp_path / "util.py").write_text("x = 1\n")
    routing = _route(Objection("review", 5, "bug", ("util.py",)), tmp_path)
    assert (routing.fixer, routing.synthetic) == ("review", True)


def test_unroutable_objections_go_to_main(tmp_path) -> None:
    (tmp_path / "util.py").write_text("x = 1\n")
    cases = [
        (Objection("review", 5, "bug", ()), {}, "cites no file"),
        (Objection("review", 5, "bug", ("gone.py",)), {}, "does not exist"),
        (Objection("review", 5, "bug", ("util.py",)), {"can_edit": lambda _l: False},
         "cannot edit"),
        (Objection("review", 5, "bug", ("util.py",)), {"approval_gate": True},
         "outside the approved plan"),
    ]
    for objection, over, why in cases:
        routing = _route(objection, tmp_path, **over)
        assert routing.fixer is None and why in routing.why, (why, routing)


def test_protected_files_go_to_main(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("x\n")
    routing = _route(Objection("review", 5, "bug", ("AGENTS.md",)), tmp_path)
    assert routing.fixer is None and "protected" in routing.why
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_team_review.py --color=no --timeout=120 > /tmp/r.txt 2>&1; echo exit=$?; tail -5 /tmp/r.txt`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.teams.review'`.

- [ ] **Step 3: Implement**

Create `agentd/teams/review.py`:

```python
"""Review (spec v2 §8.7) — pure: what a review cycle came to, and where each objection
goes. The coordinator does the posting and waking."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentd.chat.protected_paths import is_protected
from agentd.teams.models import TeamMember, TeamPost


@dataclass(frozen=True)
class Objection:
    label: str
    seq: int
    reason: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class ReviewOutcome:
    stances: dict[str, str]          # reviewer → agree | object | none
    objections: list[Objection]
    abstained: list[str]


@dataclass(frozen=True)
class Routing:
    objection: Objection
    fixer: str | None                # None → the main agent, in a member_blocked milestone
    synthetic: bool                  # the objector fixes files nobody owns
    why: str


def evaluate_review(posts: list[TeamPost], closing_id: str,
                    reviewers: list[str]) -> ReviewOutcome:
    latest: dict[str, TeamPost] = {}
    for post in sorted(posts, key=lambda p: p.seq):
        if post.kind in ("agree", "object") and post.ref_id == closing_id \
                and post.author in reviewers:
            latest[post.author] = post
    stances = {r: latest[r].kind if r in latest else "none" for r in reviewers}
    objections = []
    for label in reviewers:
        post = latest.get(label)
        if post is not None and post.kind == "object":
            evidence = post.payload.get("evidence") or {}
            files = tuple(str(f) for f in (evidence.get("files") or []))
            objections.append(Objection(label, post.seq, post.text, files))
    return ReviewOutcome(stances, objections, [r for r in reviewers if stances[r] == "none"])


def route_objection(
    objection: Objection, members: list[TeamMember], *, workspace: Path, approval_gate: bool,
    plan_files: set[str], can_edit: Callable[[str], bool],
) -> Routing:
    owners = {str(f): m.label for m in members if m.assignment
              for f in (m.assignment.get("files") or [])}
    for path in objection.files:
        if path in owners:
            return Routing(objection, owners[path], False, f"{path} is {owners[path]}'s file")
    if not objection.files:
        return Routing(objection, None, False, "the objection cites no file")
    root = workspace.resolve()
    for path in objection.files:
        if not (root / path).is_file():
            return Routing(objection, None, False, f"{path} does not exist")
        if is_protected(path):
            return Routing(objection, None, False, f"{path} is a protected file")
        if approval_gate and path not in plan_files:
            return Routing(objection, None, False, f"{path} is outside the approved plan")
    if not can_edit(objection.label):
        return Routing(objection, None, False, f"{objection.label} cannot edit files")
    return Routing(objection, objection.label, True, "nobody owns the files: the objector fixes them")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_team_review.py --color=no --timeout=120 > /tmp/r.txt 2>&1; echo exit=$?; tail -5 /tmp/r.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/review.py services/agentd-py/tests/test_team_review.py
git commit -m "feat(teams): evaluate a review cycle and route its objections"
```

### Task 2: The review loop in the state machine

**Files:**
- Modify: `services/agentd-py/agentd/teams/state_machine.py`
- Modify: `services/agentd-py/agentd/teams/config.py`
- Test: `services/agentd-py/tests/test_team_state_machine.py`

**Interfaces:**
- Produces:
  - `TeamState` fields `review_cycle: int = 0` and `max_review_cycles: int = 2` (closing proposals **after the first**).
  - Events `ReviewStarted(cycle: int, labels: tuple[str, ...])`, `ReviewEvaluated(fixers: tuple[str, ...], objections: int)`.
  - Actions `OpenReview(cycle: int)`, `StartReviewRound(cycle: int, labels: tuple[str, ...])`, `EvaluateReview(cycle: int)`, `StartFixes(fixers: tuple[str, ...])`.
  - The `done` milestone is now `Milestone("done", {"reason": <end reason>, "files": []})`, and `End("DONE", "reviewed")` or `End("DONE", "unresolved objections")`.
  - `config.team_review_cycles() -> int` (`CRUCIBLE_TEAM_REVIEW_CYCLES`, default 2, minimum 0).

The loop: the last assignment done → `OpenReview(cycle + 1)` (the coordinator posts the closing proposal and applies `ReviewStarted` with whom it asks). A review cycle is a round: it ends when every asked member has its final report (re-queues, quorum loss and budget interruption work as in deliberation) → `EvaluateReview`. The coordinator evaluates and routes, then applies `ReviewEvaluated`: no objections → `DONE (reviewed)`; routed fixes and another closing proposal allowed → `IMPLEMENTING` for the fixers (`StartFixes`), whose completion opens the next cycle; otherwise → `DONE (unresolved objections)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_state_machine.py`, add `EvaluateReview, OpenReview, ReviewEvaluated, ReviewStarted, StartFixes, StartReviewRound` to the import list (sorted), replace `test_completed_marks_done_and_last_one_ends_implemented` with:

```python
def test_the_last_assignment_opens_review() -> None:
    s = _adopted(_state())
    s, actions = apply(s, MemberReported("alice", "completed", files=("a.py",)))
    assert actions == [AssignmentDone("alice", ("a.py",))]
    s, actions = apply(s, MemberReported("bob", "completed", files=("b.py",)))
    assert actions == [AssignmentDone("bob", ("b.py",)), OpenReview(1)]
    assert s.phase == "IMPLEMENTING"                  # until the coordinator starts it
```

and append:

```python
def _reviewing(cycle: int = 1, labels: tuple[str, ...] = ("alice", "bob")) -> TeamState:
    s = _adopted(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, _ = apply(s, ReviewStarted(cycle, labels))
    return s


def test_review_round_then_clean_done() -> None:
    s = _adopted(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, ReviewStarted(1, ("alice", "bob")))
    assert actions == [EnterPhase("REVIEWING", 1, "cycle 1"),
                       StartReviewRound(1, ("alice", "bob"))]
    s, actions = apply(s, MemberReported("alice", "completed"))
    assert actions == []
    s, actions = apply(s, MemberReported("bob", "completed"))
    assert actions == [EvaluateReview(1)]
    s, actions = apply(s, ReviewEvaluated((), 0))
    assert actions == [Milestone("done", {"reason": "reviewed", "files": []}),
                       End("DONE", "reviewed")]


def test_routed_objection_runs_fixes_then_the_next_cycle() -> None:
    s = _reviewing()
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, ReviewEvaluated(("alice",), 1))
    assert actions == [EnterPhase("IMPLEMENTING", 1, "fixing"), StartFixes(("alice",))]
    assert s.members["alice"].assigned and not s.members["alice"].done
    assert s.members["bob"].done
    s, actions = apply(s, MemberReported("alice", "completed", files=("a.py",)))
    assert actions == [AssignmentDone("alice", ("a.py",)), OpenReview(2)]


def test_only_unrouted_objections_end_unresolved() -> None:
    s = _reviewing()
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, ReviewEvaluated((), 2))
    assert actions[-1] == End("DONE", "unresolved objections")


def test_review_cycles_are_capped() -> None:
    state = _state()
    state.max_review_cycles = 1                       # one closing proposal after the first
    s = _adopted(state)
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, _ = apply(s, ReviewStarted(2, ("alice",)))     # the second (last) closing proposal
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, actions = apply(s, ReviewEvaluated(("alice",), 1))
    assert actions[-1] == End("DONE", "unresolved objections")


def test_nobody_to_ask_evaluates_at_once() -> None:
    s = _adopted(_state())
    s, _ = apply(s, MemberReported("alice", "completed"))
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, ReviewStarted(2, ()))
    assert actions == [EnterPhase("REVIEWING", 1, "cycle 2"), EvaluateReview(2)]


def test_review_survives_a_transient_and_a_pause() -> None:
    s = _reviewing()
    s, actions = apply(s, MemberReported("alice", "failed_transient"))
    assert isinstance(actions[0], Requeue)
    s, _ = apply(s, BudgetExhausted())
    s, _ = apply(s, MemberReported("bob", "completed"))
    s, actions = apply(s, MainResume())
    assert actions == [EnterPhase("REVIEWING", 1, "resumed"), ResumeMembers(("alice",))]
    s, actions = apply(s, MemberReported("alice", "completed"))
    assert actions == [EvaluateReview(1)]


def test_revive_from_review_reopens_it() -> None:
    failed = _state(phase="FAILED")
    failed.review_cycle = 1
    s, actions = apply(failed, Revive("REVIEWING"))
    assert actions == [OpenReview(1)] and s.phase == "REVIEWING"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_state_machine.py --color=no --timeout=120 > /tmp/sm.txt 2>&1; echo exit=$?; tail -5 /tmp/sm.txt`
Expected: FAIL — `ImportError: cannot import name 'EvaluateReview'`.

- [ ] **Step 3: Implement**

`agentd/teams/config.py`:

```python
def team_review_cycles() -> int:
    """Closing proposals after the first one (spec v2 §8.7)."""
    return _int_env("CRUCIBLE_TEAM_REVIEW_CYCLES", 2, 0)
```

`agentd/teams/state_machine.py`:

1. Module docstring: replace its last sentence with `5C adds review: the last assignment done opens a closing proposal, and fixes run until the team agrees or the cycles run out.`
2. `TeamState` gains, after `pending_assignees`:

```python
    review_cycle: int = 0            # the current closing proposal's cycle (1 = the first)
    max_review_cycles: int = 2       # closing proposals allowed after the first
```

3. After `MainResume`, add the events (and extend `Event`):

```python
@dataclass(frozen=True)
class ReviewStarted:
    """The coordinator posted closing proposal `cycle` and asks `labels` to verify it."""
    cycle: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class ReviewEvaluated:
    fixers: tuple[str, ...]          # members with a routed objection to fix
    objections: int                  # every objection, routed or not
```

```python
Event = (Kickoff | MemberReported | RoundEvaluated | MainAdopt | MainPost | Disband | Approval
         | BudgetExhausted | Stuck | MainResume | Revive | ReviewStarted | ReviewEvaluated)
```

4. After `ForceFinalAll`, add the actions (and extend `Action`):

```python
@dataclass(frozen=True)
class OpenReview:
    cycle: int


@dataclass(frozen=True)
class StartReviewRound:
    cycle: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class EvaluateReview:
    cycle: int


@dataclass(frozen=True)
class StartFixes:
    fixers: tuple[str, ...]
```

```python
Action = (StartRound | Requeue | EvaluateRound | Adopt | Milestone | SetQuorum | EnterPhase
          | End | RaisePlanGate | ClosePlan | StartImplementation | AssignmentDone
          | ResumeMembers | CancelTimers | ForceFinalAll | OpenReview | StartReviewRound
          | EvaluateReview | StartFixes)
```

5. Replace `_all_done` with:

```python
def _finish(state: TeamState, reason: str) -> list[Action]:
    # The coordinator fills the milestone in (adopted plan, stances, files) when it runs it.
    state.phase = "DONE"
    return [Milestone("done", {"reason": reason, "files": []}), End("DONE", reason)]
```

and in `_resume` and `_revive` replace `_all_done(state, [])` with `[OpenReview(state.review_cycle + 1)]` (in `_resume`: `return [*actions, OpenReview(state.review_cycle + 1)]`; in `_revive`: `*([ResumeMembers(owing)] if owing else [OpenReview(state.review_cycle + 1)])`).

6. `_owing`: treat `REVIEWING` like `DELIBERATING`:

```python
    if phase in ("DELIBERATING", "REVIEWING"):
```

7. `_resume`, after the `DELIBERATING` early return, add:

```python
    if phase == "REVIEWING" and not owing:
        return [*actions, EvaluateReview(state.review_cycle)]
```

8. `_revive`, before the deliberation fallback, add:

```python
    if phase == "REVIEWING":
        state.phase = "REVIEWING"
        return [*restored, OpenReview(max(state.review_cycle, 1))]
```

9. Rename `_deliberation_report` to `_round_report` and end it with the right evaluation:

```python
    waiting = [lb for lb in state.round_members
               if state.members[lb].in_quorum and not state.members[lb].reported]
    if not waiting and not paused:
        reviewing = (state.paused_from if paused else state.phase) == "REVIEWING"
        actions.append(EvaluateReview(state.review_cycle) if reviewing
                       else EvaluateRound(state.round))
    return actions
```

10. In `_implementation_report`, replace `return [*actions, *_all_done(state, sorted(event.files))]` with `return [*actions, OpenReview(state.review_cycle + 1)]`.

11. In `apply`, before `if isinstance(event, MainResume):` add:

```python
    if isinstance(event, ReviewStarted):
        if s.phase not in ("IMPLEMENTING", "REVIEWING"):
            return s, []
        s.phase = "REVIEWING"
        s.review_cycle = event.cycle
        s.stuck_count = 0
        s.round_members = [lb for lb in event.labels
                           if lb in s.members and s.members[lb].in_quorum]
        for member in s.members.values():
            member.reported = False
            member.retries = 0
        entered: list[Action] = [EnterPhase("REVIEWING", s.round, f"cycle {event.cycle}")]
        if not s.round_members:
            return s, [*entered, EvaluateReview(event.cycle)]
        return s, [*entered, StartReviewRound(event.cycle, tuple(s.round_members))]
    if isinstance(event, ReviewEvaluated):
        if s.phase != "REVIEWING":
            return s, []
        if event.objections == 0:
            return s, _finish(s, "reviewed")
        if event.fixers and s.review_cycle < 1 + s.max_review_cycles:
            s.phase = "IMPLEMENTING"
            for label, member in s.members.items():
                if label in event.fixers:
                    member.assigned, member.done, member.retries = True, False, 0
                else:
                    member.done = True
            return s, [EnterPhase("IMPLEMENTING", s.round, "fixing"), StartFixes(event.fixers)]
        return s, _finish(s, "unresolved objections")
```

and at the end of `apply` route review reports:

```python
    if phase in ("DELIBERATING", "REVIEWING"):
        return s, _round_report(s, event)
```

(replacing the `if phase == "DELIBERATING": return s, _deliberation_report(s, event)` line).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_state_machine.py --color=no --timeout=120 > /tmp/sm.txt 2>&1; echo exit=$?; tail -5 /tmp/sm.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/state_machine.py services/agentd-py/agentd/teams/config.py services/agentd-py/tests/test_team_state_machine.py
git commit -m "feat(teams): the review loop — closing proposal, fixes, bounded cycles"
```

### Task 3: The `done` milestone, the closing proposal post, review stances owed

**Files:**
- Modify: `services/agentd-py/agentd/teams/milestones.py`
- Modify: `services/agentd-py/agentd/teams/service.py`
- Test: `services/agentd-py/tests/test_team_milestones.py`, `services/agentd-py/tests/test_team_service.py`

**Interfaces:**
- Consumes: `TeamRecord.closing_proposal_id`, `TeamRecord.review_cycles` (existing columns).
- Produces:
  - `milestone_text(team, "done", data)` with `data = {"reason", "files", "adopted", "closing", "cycle", "abstained": [label…], "unresolved": [{"seq", "label", "reason"}…]}` (all optional except `reason`).
  - `TeamService.closing_proposal(team_id: str, text: str, payload: dict) -> TeamPost` — a `system`-authored `proposal`, streamed like any post.
  - `TeamService.expected_stances` in `REVIEWING` returns `[closing_proposal_id]` until the member has a stance on it; the review header and the forced-final hint name it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_milestones.py`, replace `test_done_lists_files` with:

```python
def test_done_body_names_the_review() -> None:
    headline, body = milestone_text(_team(phase="DONE"), "done", {
        "reason": "reviewed", "files": ["a.py", "b.py"], "adopted": "P3", "closing": "P9",
        "cycle": 2, "abstained": ["review"], "unresolved": []})
    assert headline == "Team 'auth' finished — the review agreed"
    assert "P3 implemented; closing proposal P9 (review cycle 2)." in body
    assert "Abstained: review" in body and "a.py, b.py" in body
    headline, body = milestone_text(_team(phase="DONE"), "done", {
        "reason": "unresolved objections", "files": [], "adopted": "P3", "closing": "P9",
        "cycle": 1, "abstained": [],
        "unresolved": [{"seq": 12, "label": "tests", "reason": "refresh is unlimited"}]})
    assert headline == "Team 'auth' finished with unresolved objections"
    assert "- #12 tests: refresh is unlimited" in body
    assert "what is still open" in body
```

Append to `tests/test_team_service.py`:

```python
def test_review_owes_a_stance_on_the_closing_proposal(tmp_path: Path) -> None:
    service, tid, teams, posted = _setup(tmp_path)
    closing = service.closing_proposal(tid, "Implementation complete — verify.",
                                       {"assignments": [], "closing": True, "cycle": 1})
    assert (closing.author, closing.kind) == ("system", "proposal") and posted[-1] == closing
    teams.update_team(tid, phase="REVIEWING", closing_proposal_id=closing.proposal_id,
                      review_cycles=1)
    assert service.expected_stances(tid, "alice") == [closing.proposal_id]
    text, _top = service.render_delta(tid, "alice")
    assert f"state your stance on {closing.proposal_id}" in text
    assert service.final_hint(tid, "alice") == (
        f"state your stance on {closing.proposal_id} now, in this report's stances field")
    service.agree(tid, "alice", closing.proposal_id)
    assert service.expected_stances(tid, "alice") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_milestones.py tests/test_team_service.py --color=no --timeout=120 > /tmp/m.txt 2>&1; echo exit=$?; tail -5 /tmp/m.txt`
Expected: FAIL — `AttributeError: 'TeamService' object has no attribute 'closing_proposal'` and the headline assertion.

- [ ] **Step 3: Implement**

`agentd/teams/milestones.py` — replace the `done` branch with:

```python
    elif kind == "done":
        reviewed = data.get("reason") == "reviewed"
        headline = (f"Team {name} finished — the review agreed" if reviewed
                    else f"Team {name} finished with unresolved objections")
        raw_files = data.get("files")
        files = [str(f) for f in raw_files] if isinstance(raw_files, list) else []
        raw_abstained = data.get("abstained")
        abstained = [str(a) for a in raw_abstained] if isinstance(raw_abstained, list) else []
        raw_open = data.get("unresolved")
        unresolved = [u for u in raw_open if isinstance(u, dict)] if isinstance(raw_open, list) else []
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
```

`agentd/teams/service.py`:

- After `system_post`, add:

```python
    def closing_proposal(self, team_id: str, text: str, payload: dict[str, Any]) -> TeamPost:
        """The review's closing proposal (spec v2 §8.7): system-written, so its text is never
        model text; members state stances on it like on any proposal."""
        team = self._store.get_team(team_id)
        assert team is not None
        return self._emit(team, self._store.append_post(
            team_id, author="system", kind="proposal", text=text, payload=payload))
```

- In `expected_stances`, before the `DELIBERATING` check, add:

```python
        if team is not None and team.phase == "REVIEWING" and team.closing_proposal_id:
            seq = parse_proposal_id(team.closing_proposal_id)
            return [] if label in self.stances(team_id).get(seq, {}) else [
                team.closing_proposal_id]
```

- In `final_hint`, after the `IMPLEMENTING` branch, add:

```python
        if team.phase == "REVIEWING":
            owed = self.expected_stances(team_id, label)
            return (f"state your stance on {owed[0]} now, in this report's stances field"
                    if owed else "")
```

- In `_header`, after the `IMPLEMENTING` line branch, add:

```python
        elif team.phase == "REVIEWING" and team.closing_proposal_id:
            lines.append(f"Review: verify the implementation (run tests, read the changes), "
                         f"then state your stance on {team.closing_proposal_id} — team_agree, "
                         "team_object with evidence, or your report's stances field. Posts "
                         "reach the others right away.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_milestones.py tests/test_team_service.py tests/test_team_report_fields.py --color=no --timeout=120 > /tmp/m.txt 2>&1; echo exit=$?; tail -5 /tmp/m.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/milestones.py services/agentd-py/agentd/teams/service.py services/agentd-py/tests/
git commit -m "feat(teams): closing proposals, review stances owed, the full done milestone"
```

---

# Part B — The coordinator and the controller

### Task 4: The coordinator runs the review

**Files:**
- Modify: `services/agentd-py/agentd/teams/coordinator.py`
- Modify: `services/agentd-py/agentd/teams/service.py` (two public accessors)
- Test: `services/agentd-py/tests/test_team_coordinator.py`

**Interfaces:**
- Consumes: Task 1 (`evaluate_review`, `route_objection`, `Routing`), Task 2 (review events/actions, `team_review_cycles`), Task 3 (`closing_proposal`, the `done` body).
- Produces:
  - `TeamService.workspace` (property → `Path`) and `TeamService.member_can_edit(team_id: str, label: str) -> bool` (the `can_edit` callback it was built with).
  - Coordinator behavior: closing proposal text `Implementation complete — verify.` (`payload: {assignments, shared_files, files_changed: {label: [files]}, closing: true, cycle, supersedes}`); review input `Verify the implementation (run tests, read the changes). Respond to P<n> with team_agree or team_object, or put your stance in your report's stances field.`; a `system` post summarising each cycle (`Review of P<n>: agree …; object …; abstained …`); fix input `Fix the objection(s) on P<n> raised in review, in your files, then report completed:` followed by the framed objections; carried stances are `agree` posts with `payload.carried = true`; the closing proposal is closed `superseded` by the next and `reviewed` at the end.

Whom a later cycle asks (spec §8.7 step 1): the objectors whose objections were routed, the fixers, and the owners of files the fixes changed — intersected with the quorum. Everyone else inherits: a previous `agree` is carried; an abstention or an unrouted objection stays an abstention. After a restart (revive), nothing is remembered, so the whole quorum is asked.

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_coordinator.py`, replace `test_assignments_done_end_implemented` with the tests below (they reuse `_setup`, `PARTS` and `_adopt_round_one`):

```python
def _implemented(store, svc, coord):  # type: ignore[no-untyped-def]
    _adopt_round_one(store, svc, coord)
    coord.on_report("alice", "completed", None, files=("a.py",))
    coord.on_report("bob", "completed", None, files=("t.py",))
    return store.get_team("team-1").closing_proposal_id


@pytest.mark.asyncio
async def test_review_after_implementation_agrees(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    closing = _implemented(store, svc, coord)
    team = store.get_team("team-1")
    assert (team.phase, team.review_cycles) == ("REVIEWING", 1)
    post = store.get_post("team-1", int(closing[1:]))
    assert (post.author, post.kind, post.text) == ("system", "proposal",
                                                   "Implementation complete — verify.")
    assert post.payload["files_changed"] == {"alice": ["a.py"], "bob": ["t.py"]}
    assert f"Respond to {closing} with team_agree" in host.extras["alice"]
    svc.agree("team-1", "alice", closing)
    svc.agree("team-1", "bob", closing)
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    team = store.get_team("team-1")
    assert (team.phase, team.end_reason) == ("DONE", "reviewed")
    assert store.get_post("team-1", int(closing[1:])).closed == "reviewed"
    assert host.milestones[-1] == "done"
    assert any(p.text.startswith(f"Review of {closing}: agree alice, bob")
               for p in store.posts("team-1"))


def _object(svc, label, closing, files):  # type: ignore[no-untyped-def]
    evidence = {"files": files, "line": 1} if files else {"command": "pytest", "output": "FAILED"}
    svc.object_("team-1", label, closing, "the limiter is skipped", evidence)


@pytest.mark.asyncio
async def test_objection_routes_to_the_owner_and_runs_a_fix(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    closing = _implemented(store, svc, coord)
    svc.agree("team-1", "alice", closing)
    _object(svc, "bob", closing, ["a.py"])
    host.started.clear()
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    assert store.get_team("team-1").phase == "IMPLEMENTING"
    assert host.started == ["alice"]
    assert "Fix the objection on" in host.extras["alice"]
    assert "the limiter is skipped" in host.extras["alice"]
    assert store.member("team-1", "alice").assignment["fix"]
    coord.on_report("alice", "completed", None, files=("a.py",))
    team = store.get_team("team-1")
    assert (team.phase, team.review_cycles) == ("REVIEWING", 2)
    assert store.get_post("team-1", int(closing[1:])).closed == "superseded"
    assert sorted(host.started[-2:]) == ["alice", "bob"]          # objector + fixer
    coord.close()


@pytest.mark.asyncio
async def test_second_cycle_carries_stances(tmp_path) -> None:
    labels = ("alice", "bob", "carol")
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS, labels=labels)
    _adopt_round_one(store, svc, coord, labels)
    coord.on_report("alice", "completed", None, files=("a.py",))
    coord.on_report("bob", "completed", None, files=("t.py",))
    closing = store.get_team("team-1").closing_proposal_id
    svc.agree("team-1", "alice", closing)
    svc.agree("team-1", "carol", closing)
    _object(svc, "bob", closing, ["a.py"])
    for label in labels:
        coord.on_report(label, "completed", None)
    coord.on_report("alice", "completed", None, files=("a.py",))
    second = store.get_team("team-1").closing_proposal_id
    carried = [p for p in store.posts("team-1") if p.ref_id == second]
    assert [(p.author, p.kind, p.payload.get("carried")) for p in carried] == [
        ("carol", "agree", True)]
    coord.close()


@pytest.mark.asyncio
async def test_unroutable_objection_goes_to_main(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    closing = _implemented(store, svc, coord)
    svc.agree("team-1", "alice", closing)
    _object(svc, "bob", closing, [])                      # command evidence, no file
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    team = store.get_team("team-1")
    assert (team.phase, team.end_reason) == ("DONE", "unresolved objections")
    assert host.milestones[-2:] == ["member_blocked", "done"]


@pytest.mark.asyncio
async def test_cycle_cap_ends_with_unresolved(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_REVIEW_CYCLES", "0")
    store, svc, host, coord, _ = _setup(tmp_path, assignments=PARTS)
    closing = _implemented(store, svc, coord)
    svc.agree("team-1", "alice", closing)
    _object(svc, "bob", closing, ["a.py"])
    coord.on_report("alice", "completed", None)
    coord.on_report("bob", "completed", None)
    assert store.get_team("team-1").end_reason == "unresolved objections"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_coordinator.py --color=no --timeout=120 > /tmp/c.txt 2>&1; echo exit=$?; tail -8 /tmp/c.txt`
Expected: FAIL — the new tests (no closing proposal is posted; `OpenReview` is not handled).

- [ ] **Step 3: Implement**

`agentd/teams/service.py` — after `__init__`:

```python
    @property
    def workspace(self) -> Path:
        return self._workspace

    def member_can_edit(self, team_id: str, label: str) -> bool:
        return self._can_edit(team_id, label)
```

`agentd/teams/coordinator.py`:

1. Imports: `from agentd.subagents.framing import frame`, `from agentd.teams.config import team_review_cycles`, `from agentd.teams.review import Routing, evaluate_review, route_objection`, and `PreparedPost` from `agentd.teams.service` (next to `TeamService`).
2. In `__init__`, set the review state on the machine and add the review fields:

```python
        self._state.review_cycle = team.review_cycles
        self._state.max_review_cycles = team_review_cycles()
```

(after `self._state = sm.TeamState(...)`) and, with the other fields:

```python
        self._member_files: dict[str, set[str]] = {}   # label → files its parts changed
        self._fixes: dict[str, list[Routing]] = {}     # fixer → the objections it fixes
        self._fixing: set[str] = set()
        self._fix_changed: set[str] = set()            # files the current fixes changed
        self._next_reviewers: set[str] = set()
        self._review: dict[str, object] = {}           # the last cycle's outcome, for `done`
```

3. In `on_activation_start` and `_check_deadline`, the deadline applies to review too: replace `self._state.phase == "DELIBERATING"` with `self._state.phase in ("DELIBERATING", "REVIEWING")` in `on_activation_start`, and `self._state.phase != "DELIBERATING"` with `self._state.phase not in ("DELIBERATING", "REVIEWING")` in `_check_deadline`.
4. In `_execute`, before the `End` branch:

```python
        elif isinstance(action, sm.OpenReview):
            self._open_review(action.cycle)
        elif isinstance(action, sm.StartReviewRound):
            self._start_review_round(action)
        elif isinstance(action, sm.EvaluateReview):
            self._evaluate_review(action.cycle)
        elif isinstance(action, sm.StartFixes):
            self._start_fixes(action.fixers)
```

and in the `End` branch, before `self._store.update_team(...)`:

```python
            closing = self._team().closing_proposal_id
            if action.phase == "DONE" and closing is not None:
                self._store.close_proposal(self._team_id, int(closing.lstrip("Pp")), "reviewed")
```

5. In `_milestone`, before `elif action.kind == "stuck":`, enrich `done`:

```python
        elif action.kind == "done":
            raw = data.get("files")
            data["files"] = sorted(self._files | {str(f) for f in
                                                  (raw if isinstance(raw, list) else [])})
            data.update(adopted=team.adopted_proposal_id, closing=team.closing_proposal_id,
                        cycle=team.review_cycles, abstained=self._review.get("abstained", []),
                        unresolved=(self._review.get("objections", [])
                                    if data.get("reason") != "reviewed" else []))
```

(replacing the existing `elif action.kind == "done":` branch that only widened `files`).

6. In `_assignment_done`, record files per member and per fix:

```python
        self._member_files.setdefault(action.label, set()).update(action.files)
        if action.label in self._fixing:
            self._fix_changed |= set(action.files)
```

7. Add the review methods:

```python
    def _open_review(self, cycle: int) -> None:
        """Post closing proposal `cycle` and ask who must verify it (spec v2 §8.7 step 1)."""
        team = self._team()
        plan = self._adopted()
        plan_payload = plan.payload if plan is not None else {}
        quorum = self._state.quorum()
        if cycle == 1 or not self._next_reviewers:
            asked = list(quorum)
        else:
            owners = {m.label for m in self._store.members(self._team_id) if m.assignment
                      and self._fix_changed & {str(f) for f in m.assignment.get("files") or []}}
            asked = [lb for lb in quorum if lb in (self._next_reviewers | owners)]
        previous = team.closing_proposal_id
        post = self._svc.closing_proposal(
            self._team_id, "Implementation complete — verify.",
            {"assignments": plan_payload.get("assignments", []),
             "shared_files": plan_payload.get("shared_files", []),
             "files_changed": {lb: sorted(f) for lb, f in sorted(self._member_files.items())},
             "closing": True, "cycle": cycle, "supersedes": [previous] if previous else []})
        if previous is not None:
            prev_seq = int(previous.lstrip("Pp"))
            self._store.close_proposal(self._team_id, prev_seq, "superseded")
            before = self._svc.stances(self._team_id).get(prev_seq, {})
            for label in quorum:
                if label not in asked and before.get(label) == "agree":
                    # Not asked again: its agreement carries over (spec v2 §8.7 step 1).
                    self._svc.commit(self._team_id, label, PreparedPost(
                        kind="agree", text="(carried over from the previous cycle)",
                        ref_id=post.proposal_id, payload={"carried": True}))
        self._store.update_team(self._team_id, closing_proposal_id=post.proposal_id,
                                review_cycles=cycle)
        self._fix_changed.clear()
        self._fixing.clear()
        self._next_reviewers.clear()
        self._trace.write("review_opened", cycle=cycle, closing=post.proposal_id, asked=asked)
        self._apply(sm.ReviewStarted(cycle, tuple(asked)))

    def _start_review_round(self, action: sm.StartReviewRound) -> None:
        closing = self._team().closing_proposal_id
        text = ("Verify the implementation (run tests, read the changes). Respond to "
                f"{closing} with team_agree or team_object, or put your stance in your "
                "report's stances field.")
        for label in action.labels:
            self._start(label, text)

    def _evaluate_review(self, cycle: int) -> None:
        team = self._team()
        closing = team.closing_proposal_id or ""
        outcome = evaluate_review(self._store.posts(self._team_id), closing,
                                  self._state.quorum())
        plan = self._adopted()
        plan_payload = plan.payload if plan is not None else {}
        plan_files = {str(f) for a in plan_payload.get("assignments", [])
                      for f in (a.get("files") or [])} | {
            str(f) for f in plan_payload.get("shared_files", [])}
        members = self._store.members(self._team_id)
        routings = [route_objection(
            o, members, workspace=self._svc.workspace, approval_gate=team.approval_gate,
            plan_files=plan_files,
            can_edit=lambda label: self._svc.member_can_edit(self._team_id, label))
            for o in outcome.objections]
        self._fixes = {}
        for routing in routings:
            self._trace.write("objection_routed", seq=routing.objection.seq,
                              by=routing.objection.label, fixer=routing.fixer, why=routing.why)
            if routing.fixer is None:
                self._milestone(sm.Milestone("member_blocked", {
                    "label": routing.objection.label, "status": "objected",
                    "report": (f"objection #{routing.objection.seq} on {closing} could not be "
                               f"routed ({routing.why}): {routing.objection.reason}")[:300]}))
            else:
                self._fixes.setdefault(routing.fixer, []).append(routing)
        self._next_reviewers = ({r.objection.label for r in routings if r.fixer is not None}
                                | set(self._fixes))
        self._review = {
            "abstained": outcome.abstained,
            "objections": [{"seq": o.seq, "label": o.label, "reason": o.reason}
                           for o in outcome.objections]}
        verdict = "; ".join(part for part in (
            "agree " + ", ".join(lb for lb, s in outcome.stances.items() if s == "agree"),
            "object " + ", ".join(f"{o.label} (#{o.seq})" for o in outcome.objections),
            "abstained " + ", ".join(outcome.abstained)) if not part.endswith(" "))
        self._svc.system_post(self._team_id, f"Review of {closing}: {verdict or 'no stances'}.")
        self._apply(sm.ReviewEvaluated(tuple(sorted(self._fixes)), len(outcome.objections)))

    def _start_fixes(self, fixers: tuple[str, ...]) -> None:
        closing = self._team().closing_proposal_id
        for label in fixers:
            routes = self._fixes.get(label, [])
            member = self._store.member(self._team_id, label)
            assignment = dict((member.assignment if member is not None else None)
                              or {"member": label, "part": "fix", "files": []})
            files = [str(f) for f in assignment.get("files") or []]
            for routing in routes:
                if routing.synthetic:
                    files += [f for f in routing.objection.files if f not in files]
            assignment.update(files=files, fix=[f"#{r.objection.seq}" for r in routes])
            self._store.set_assignment(self._team_id, label, assignment)
            self._fixing.add(label)
            framed = "\n\n".join(
                frame(r.objection.label, f"objection on {closing}", r.objection.reason,
                      seq=r.objection.seq) for r in routes)
            plural = "s" if len(routes) > 1 else ""
            self._start(label, f"Fix the objection{plural} on {closing} raised in review, in "
                               f"your files, then report completed:\n\n{framed}")
        self._svc.system_post(self._team_id, "Fixing: " + "; ".join(
            f"{lb} → " + ", ".join(f"#{r.objection.seq}" for r in self._fixes.get(lb, []))
            for lb in fixers) + ".")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_coordinator.py tests/test_team_state_machine.py tests/test_team_review.py --color=no --timeout=120 > /tmp/c.txt 2>&1; echo exit=$?; tail -5 /tmp/c.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/coordinator.py services/agentd-py/agentd/teams/service.py services/agentd-py/tests/test_team_coordinator.py
git commit -m "feat(teams): the coordinator reviews — closing proposals, routing, fixes, carried stances"
```

### Task 5: The controller in review — iteration cap, leftovers, named waits on the card

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller.py`
- Test: `services/agentd-py/tests/test_team_implementation_controller.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: review activations capped at 25 iterations (spec §3.11) with no `edit` type; leftovers kept while `REVIEWING` (nothing reaches a reviewer mid-cycle); the `wrapped_up` activity payload and `/live` `members[].last` gain `waiting_on` (the named wait, for the card's "waiting on api").

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_implementation_controller.py`, change the three `("DONE", "implemented"` assertions (in `test_adoption_runs_implementation_and_ends_implemented`, `test_budget_exhaustion_pauses_and_resume_continues` and `test_a_named_wait_is_answered_when_the_peer_finishes`) to `"reviewed"` — those members' scripts end on a report with no stance, which in review is redirected once and then counts as abstaining — and rename the first test to `test_adoption_runs_implementation_and_review`. Then append:

```python
@pytest.mark.asyncio
async def test_missing_review_stance_redirects_then_abstains(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("a.py", "A\n"), DONE],
        "bob": [AGREE, _edit("b.py", "B\n"), DONE]})
    _quiet(ctrl, monkeypatch)
    team_id = str((await ctrl._create_team(
        tid, "turn1", _request(kickoff_assignments=PARTS)))["team_id"])
    await _settle(ctrl)
    team = store.teams.get_team(team_id)
    assert (team.phase, team.end_reason, team.review_cycles) == ("DONE", "reviewed", 1)
    alice_history = [str(m.get("content")) for label, h, _, _ in engine.seen
                     if label == "alice" for m in h]
    assert any(f"no stance on {team.closing_proposal_id}" in c for c in alice_history)
    done = [n for n in store.unclaimed_notices(tid) if n.kind == "done"][0]
    assert "Abstained: alice, bob" in done.payload["body"]
    wraps = [e for e in store.teams.activity(team_id) if e.kind == "wrapped_up"]
    assert all("waiting_on" in e.payload for e in wraps)


@pytest.mark.asyncio
async def test_reviewers_cannot_edit_and_are_capped(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {
        "alice": [AGREE, _edit("a.py", "A\n"), DONE],
        "bob": [AGREE, _edit("b.py", "B\n"), DONE]})
    _quiet(ctrl, monkeypatch)
    seen: list[tuple[str, list[str], object]] = []
    original = engine.create_controller_step

    async def spy(plan_context, *args, **kwargs):  # type: ignore[no-untyped-def]
        team = store.teams.list_teams(tid)[0] if store.teams.list_teams(tid) else None
        seen.append((team.phase if team else "", list(kwargs.get("allowed_types") or []),
                     plan_context.get("max_iters")))
        return await original(plan_context, *args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", spy)
    await ctrl._create_team(tid, "turn1", _request(kickoff_assignments=PARTS))
    await _settle(ctrl)
    review = [(types, cap) for phase, types, cap in seen if phase == "REVIEWING"]
    assert review and all("edit" not in types and cap == 25 for types, cap in review)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_team_implementation_controller.py --color=no --timeout=120 > /tmp/ic.txt 2>&1; echo exit=$?; tail -8 /tmp/ic.txt`
Expected: FAIL — the review cap (`cap == 40`/100) and `waiting_on` assertions.

- [ ] **Step 3: Implement**

In `agentd/chat/controller.py`:

1. In `_activate`, where the non-implementing phase removes `edit`:

```python
            reviewing = membership is not None and team_row.phase == "REVIEWING"
            # Edits only where the phase allows them (spec v2 §3.9), recomputed at each
            # activation start; deliberation activations are capped at 40 iterations and
            # review activations at 25 (§3.11).
            cap = 40 if deliberating else 25 if reviewing else ctx.max_iters
            ctx = replace(ctx, allowed_types=tuple(t for t in ctx.allowed_types if t != "edit"),
                          max_iters=min(ctx.max_iters, cap))
```

2. In `_on_leftover`, add `"REVIEWING"` to the kept phases: `team.phase in ("DELIBERATING", "AWAITING_APPROVAL", "PAUSED", "REVIEWING")`.
3. In `_team_member_reported`, the `wrapped_up` payload gains the named wait — read it before `on_report` pops it:

```python
        waiting_on = self._member_waits.pop(record.agent_id, [])
        self._teams.record(record.team_id, record.label, "wrapped_up",
                           activation=record.activation_count,
                           payload={"status": result.status, "report": report,
                                    "files_changed": list(result.files_changed), **stats,
                                    "waiting_on": waiting_on, **in_round})
```

and pass `waiting_on=waiting_on` to `coordinator.on_report(...)` (instead of popping there).
4. `_last_view` adds `"waiting_on": event.payload.get("waiting_on") or []`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest $(ls tests/test_team_*.py) tests/test_prompt_goldens_teams.py --color=no --timeout=120 > /tmp/ic.txt 2>&1; echo exit=$?; tail -5 /tmp/ic.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_team_implementation_controller.py
git commit -m "feat(teams): review activations in the controller; named waits on the card"
```

### Task 6: A rewind deletes the teams it rewinds past

**Files:**
- Modify: `services/agentd-py/agentd/teams/store.py`
- Modify: `services/agentd-py/agentd/chat/controller.py`
- Test: `services/agentd-py/tests/test_team_controller.py`

**Interfaces:**
- Produces: `TeamStore.teams_from_checkpoint(thread_id: str, seq: int) -> list[str]`, `TeamStore.teams_for_turns(thread_id: str, turn_ids: list[str]) -> list[str]`, `TeamStore.delete_teams(team_ids: list[str]) -> None` (teams, members, posts, activity). `ChatController.forget_rewound_agents` deletes them and their notices (spec §8.10). A live team blocks the rewind already (409), so only ended teams reach this.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_team_controller.py`:

```python
@pytest.mark.asyncio
async def test_rewind_deletes_teams_in_the_span(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    monkeypatch.setattr(ctrl, "_rearm_notices", lambda _thread_id: None)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    await ctrl.disband_team(tid, team_id)
    seq = store.teams.get_team(team_id).checkpoint_seq
    assert store.unclaimed_notices(tid)                     # its milestones
    ctrl.forget_rewound_agents(tid, ["turn1"], seq)
    assert store.teams.get_team(team_id) is None
    assert store.teams.posts(team_id) == [] and store.teams.activity(team_id) == []
    assert store.teams.members(team_id) == []
    assert [n for n in store.unclaimed_notices(tid) if n.source_id == team_id] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_team_controller.py::test_rewind_deletes_teams_in_the_span --color=no --timeout=120 > /tmp/rw.txt 2>&1; echo exit=$?; tail -5 /tmp/rw.txt`
Expected: FAIL — the team row is still there.

- [ ] **Step 3: Implement**

`agentd/teams/store.py`, after `fail_live_teams`:

```python
    def teams_from_checkpoint(self, thread_id: str, seq: int) -> list[str]:
        return [r["team_id"] for r in self._conn.execute(
            "SELECT team_id FROM teams WHERE thread_id = ? AND checkpoint_seq >= ?",
            (thread_id, seq)).fetchall()]

    def teams_for_turns(self, thread_id: str, turn_ids: list[str]) -> list[str]:
        if not turn_ids:
            return []
        marks = ", ".join("?" * len(turn_ids))
        return [r["team_id"] for r in self._conn.execute(
            f"SELECT team_id FROM teams WHERE thread_id = ? AND created_turn_id IN ({marks})",  # noqa: S608 — placeholders only
            (thread_id, *turn_ids)).fetchall()]

    def delete_teams(self, team_ids: list[str]) -> None:
        """A rewind past a team removes it whole (spec v2 §8.10)."""
        if not team_ids:
            return
        marks = ", ".join("?" * len(team_ids))
        for table in ("team_activity", "team_posts", "team_members", "teams"):
            self._conn.execute(
                f"DELETE FROM {table} WHERE team_id IN ({marks})", team_ids)  # noqa: S608 — fixed table names
        self._conn.commit()
```

`agentd/chat/controller.py`, in `forget_rewound_agents` right after `self._store.delete_notices_for_sources(thread_id, agent_ids)`:

```python
        team_ids = sorted(set(
            (self._store.teams.teams_from_checkpoint(thread_id, from_seq)
             if from_seq is not None else [])
            + self._store.teams.teams_for_turns(thread_id, turn_ids)))
        for team_id in team_ids:
            coordinator = self._coordinators.pop(team_id, None)
            if coordinator is not None:
                coordinator.close()
        self._store.teams.delete_teams(team_ids)
        self._store.delete_notices_for_sources(thread_id, team_ids)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_controller.py tests/test_rewind_agents.py tests/test_rewind_routes.py tests/test_rewind_scenarios.py tests/test_subagent_rewind.py --color=no --timeout=120 > /tmp/rw.txt 2>&1; echo exit=$?; tail -5 /tmp/rw.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/teams/store.py services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_team_controller.py
git commit -m "feat(teams): a rewind deletes the teams it rewinds past"
```

---

# Part C — UI and wrap-up

### Task 7: Edits, the assignment, the closing proposal, and three 5B-smoke fixes in the webview

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`, `teams.ts`, `teamChapters.ts`
- Modify: `apps/vscode-extension/webview-ui/src/components/teams/{MemberView,Journey,PhaseStepper}.tsx`
- Test: `apps/editor-client/test/team-contracts.test.ts`, `apps/vscode-extension/webview-ui/src/test/{teams.test.ts,teamChapters.test.ts,memberView.test.tsx,teamWindow.test.tsx}`

**Interfaces:**
- Consumes: `/live` `members[].last.waiting_on` and summary `members[].assignment` (with `fix`) / `assignment_done` (Tasks 4–5; 5B).
- Produces:
  - editor-client: `TeamMemberLastSchema.waitingOn: string[]` (default `[]`); the summary assignment object keeps an optional `fix: string[]`.
  - webview: `TeamMemberLastView.waitingOn?: string[]`; `TeamMemberView.assignment?: {member, part, files, fix?} | null`, `assignmentDone?: boolean`; `MemberChapter.edits: {path, additions, deletions, status}[]` (from the chapter's `diff_card` messages; `status` = `metadata.resolved`).
  - `memberPhrase` says `⏳ waiting on api` when the wait is named; `latestText` words a phase (`team is waiting for your approval`, not `team awaiting_approval`).
  - Member tab: an **Assignment** line under the profile stats, an **Edits** section in each chapter that edited; the **Handed** chips keep underscores (`round_price`).
  - Board: a closing proposal is labelled `closing proposal · review cycle n` and lists the files each member changed; the stepper's Done step reads `Done · unresolved` for a team that ended with unresolved objections.

- [ ] **Step 1: Write the failing tests**

`apps/editor-client/test/team-contracts.test.ts` — append:

```ts
describe("named waits and fix assignments", () => {
  it("maps waiting_on on a member's last event and fix on its assignment", async () => {
    const live = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, pending_gates: [], plan: null, turn_active: false,
      teams: [{ team_id: "team-1", name: "auth", phase: "IMPLEMENTING", round: 1, max_rounds: 3,
                paused_reason: null, members: [{ label: "tests", agent_id: "a", status: "awaiting_peer",
                last: { kind: "wrapped_up", at: "2026-10-07T00:00:00Z", status: "awaiting_peer",
                        waiting_on: ["api"], activation: 2 } }] }],
    }) });
    expect((await live.getThreadLiveState("t")).teams?.[0].members[0].last?.waitingOn).toEqual(["api"]);
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({ teams: [{
      ...SUMMARY, members: [{ label: "alice", agent_id: "a", status: "completed",
        assignment: { member: "alice", part: "api", files: ["a.py"], fix: ["#12"] },
        assignment_done: false }] }] }) });
    expect((await c.listTeams("t"))[0].members[0].assignment?.fix).toEqual(["#12"]);
  });
});
```

`webview-ui/src/test/teams.test.ts` — add `latestText` and `memberPhrase` to the `../teams` import if missing, then append:

```ts
describe("5C wording", () => {
  it("names whom a member waits on and words a phase", () => {
    const member = { label: "tests", agentId: "a", status: "awaiting_peer",
      last: { kind: "wrapped_up", at: new Date().toISOString(), causeSeq: null, by: null,
              status: "awaiting_peer", activation: 2, waitingOn: ["api"] } };
    expect(memberPhrase(member, undefined, Date.now()).text).toBe("⏳ waiting on api");
    expect(latestText({ kind: "activity", event: "phase", label: "team",
                        text: "AWAITING_APPROVAL", at: "" } as never))
      .toBe("team is waiting for your approval");
  });
});
```

`webview-ui/src/test/teamChapters.test.ts` — append:

```ts
describe("edits (spec 2026-10-05 §9)", () => {
  it("a chapter lists the files its edits touched", () => {
    const diff = (path: string, resolved: string): ChatMsg => ({ role: "agent", content: "",
      type: "diff_card", timestamp: T(5),
      metadata: { resolved, diff_entries: [{ path, additions: 3, deletions: 1 }] } });
    const chapters = buildChapters("review", [diff("a.py", "applied"), diff("b.py", "discarded")],
                                   [], [], { working: false, fallbackStatus: "completed" });
    expect(chapters[0].edits).toEqual([
      { path: "a.py", additions: 3, deletions: 1, status: "applied" },
      { path: "b.py", additions: 3, deletions: 1, status: "discarded" }]);
  });
});
```

`webview-ui/src/test/memberView.test.tsx` — append:

```tsx
describe("MemberView in implementation and review", () => {
  it("shows the assignment, the edits, and handed chips with underscores", () => {
    const team: TeamSummaryView = { ...TEAM, phase: "REVIEWING", members: [
      { ...TEAM.members[0], assignment: { member: "review", part: "add round_price", files: ["a.py"], fix: ["#12"] },
        assignmentDone: false }, TEAM.members[1]] };
    const messages = [{ role: "agent", content: "", type: "diff_card", timestamp: T(3),
      metadata: { resolved: "applied", diff_entries: [{ path: "a.py", additions: 4, deletions: 0 }] } } as ChatMsg];
    const posts = [{ ...POSTS[0], text: "add round_price to shop/pricing.py" }];
    const agentsUi: AgentsUi = {
      agents: { "agent-r": { ...DETAIL, status: "completed" } }, expanded: new Set(),
      toggleExpanded: vi.fn(), openWindow: vi.fn(),
      views: { "agent-r": { detail: DETAIL, messages, live: [], callIds: {}, nextId: 1 } },
    };
    const teamsUi: TeamsUi = { teams: { "team-1": team }, openTeam: vi.fn(), views: { "team-1": {
      posts, lastSeq: 1, lastAseq: 1, activity: [ev(1, 1, "took_up", 1, { posts: [1], from: ["main"] })] } } };
    render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>
      <MemberView teamId="team-1" agentId="agent-r" />
    </TeamsContext.Provider></AgentsContext.Provider>);
    expect(screen.getByTestId("member-assignment")).toHaveTextContent("add round_price");
    expect(screen.getByTestId("member-assignment")).toHaveTextContent("fixing #12");
    expect(screen.getByTestId("edits-1")).toHaveTextContent("a.py");
    expect(screen.getByTestId("edits-1")).toHaveTextContent("+4");
    expect(screen.getByTestId("handed-1")).toHaveTextContent("round_price");
  });
});
```

`webview-ui/src/test/teamWindow.test.tsx` — give `renderWindow` a third parameter `posts = POSTS` (and use it for `views["team-1"].posts`), then append inside `describe("TeamWindow board", …)`:

```tsx
  it("labels a closing proposal and its changed files", () => {
    const closing = post(9, 40, { author: "system", kind: "proposal", round: null,
      text: "Implementation complete — verify.",
      payload: { closing: true, cycle: 2, assignments: [], files_changed: { impl: ["a.py"] } } });
    renderWindow("board", { ...TEAM, phase: "REVIEWING" }, [...POSTS, closing]);
    expect(screen.getByText("closing proposal · review cycle 2")).toBeInTheDocument();
    expect(screen.getByTestId("closing-files-9")).toHaveTextContent("impl: a.py");
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/editor-client && perl -e 'alarm 120; exec @ARGV' npx vitest run test/team-contracts.test.ts` and `cd apps/vscode-extension/webview-ui && perl -e 'alarm 300; exec @ARGV' npx vitest run src/test/teams.test.ts src/test/teamChapters.test.ts src/test/memberView.test.tsx src/test/teamWindow.test.tsx`
Expected: FAIL — `waitingOn` undefined, `edits` undefined, missing test ids.

- [ ] **Step 3: Implement**

editor-client `task-contracts.ts`: `TeamMemberLastSchema` gains `waitingOn: z.array(z.string()).default([]),`; the summary member `assignment` object gains `fix: z.array(z.string()).optional(),`. `http-backend-client.ts` `toTeamLive`'s `last` mapping gains `waitingOn: last["waiting_on"] ?? [],`. Then `npm run -w @crucible/editor-client build`.

webview `types.ts`: `TeamMemberLastView` gains `waitingOn?: string[];`; `TeamMemberView` gains `assignment?: { member: string; part: string; files: string[]; fix?: string[] } | null;` and `assignmentDone?: boolean;`.

`teams.ts`:

```ts
const PHASE_WORDS: Record<string, string> = {
  DELIBERATING: "is deliberating", AWAITING_APPROVAL: "is waiting for your approval",
  IMPLEMENTING: "is implementing", REVIEWING: "is reviewing", DEADLOCKED: "is deadlocked",
  PAUSED: "is paused", DONE: "is done", DISBANDED: "was disbanded", FAILED: "failed",
};
```

and in `latestText` replace `return \`team ${latest.text.toLowerCase()}\`;` with
`return \`team ${PHASE_WORDS[latest.text] ?? latest.text.toLowerCase()}\`;`. In `memberPhrase`, the `awaiting_peer` case becomes:

```ts
      case "awaiting_peer": return {
        text: last.waitingOn?.length ? `⏳ waiting on ${last.waitingOn.join(", ")}` : "⏳ waiting on a teammate",
        tone: "idle" };
```

`teamChapters.ts`: `MemberChapter` gains `edits: { path: string; additions: number; deletions: number; status: string }[];` and the chapter builder sets

```ts
      edits: seg.messages.filter((m) => m.type === "diff_card").flatMap((m) => {
        const entries = Array.isArray(m.metadata?.diff_entries) ? m.metadata.diff_entries as Record<string, unknown>[] : [];
        return entries.map((e) => ({ path: String(e.path ?? ""), additions: Number(e.additions ?? 0),
                                     deletions: Number(e.deletions ?? 0), status: String(m.metadata?.resolved ?? "applied") }));
      }),
```

`MemberView.tsx`:
- `firstLine` keeps underscores: `text.split("\n").map((l) => l.replace(/^[#>\-*\s]+/, "").replace(/\*\*|`/g, "").trim()).find((l) => l) ?? ""`.
- After the stats row in the profile:

```tsx
          {member.assignment && (
            <div data-testid="member-assignment" className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-text-2">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Assignment</span>
              <span>{member.assignment.part}</span>
              {member.assignment.files.map((f) => <code key={f} className="font-mono text-[11px] text-[var(--color-code)]">{f}</code>)}
              <span className="text-text-3">· {member.assignment.fix?.length && !member.assignmentDone
                ? `fixing ${member.assignment.fix.join(", ")}` : member.assignmentDone ? "✓ done" : "open"}</span>
            </div>
          )}
```

- In `Chapter`, after the Work section:

```tsx
          {c.edits.length > 0 && (
            <div className="grid gap-1">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Edits</span>
              <div data-testid={`edits-${c.n}`} className="grid gap-0.5">
                {c.edits.map((e, i) => (
                  <div key={`${e.path}-${i}`} className="flex items-center gap-2 text-[11.5px]">
                    <code className="font-mono text-[11px] text-[var(--color-code)]">{e.path}</code>
                    <span style={{ color: "var(--color-green)" }}>+{e.additions}</span>
                    <span style={{ color: "var(--color-red)" }}>−{e.deletions}</span>
                    <span className="text-text-3">{e.status === "applied" ? "applied" : "not applied"}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
```

`Journey.tsx` `PostCard`: the kind label is `post.payload.closing === true ? \`closing proposal · review cycle ${String(post.payload.cycle)}\` : post.kind === "proposal" ? "proposal" : …`, and after the assignments block:

```tsx
      {post.payload.closing === true && typeof post.payload.files_changed === "object" && post.payload.files_changed !== null && (
        <div data-testid={`closing-files-${post.seq}`} className="grid gap-0.5 text-[11.5px] text-text-2">
          {Object.entries(post.payload.files_changed as Record<string, string[]>).map(([lb, files]) => (
            <div key={lb}>{lb}: {files.join(", ") || "no files"}</div>
          ))}
        </div>
      )}
```

`PhaseStepper.tsx`: the Done step's label reads `Done · unresolved` when `phase === "DONE" && endReason === "unresolved objections"` (extend the `label` function: `i === 4 && phase === "DONE" && endReason === "unresolved objections" ? "Done · unresolved" : …`).

- [ ] **Step 4: Run tests, typecheck**

Run: `npm run -w @crucible/editor-client build && cd apps/vscode-extension && npm run typecheck && cd webview-ui && perl -e 'alarm 300; exec @ARGV' npx vitest run && npx tsc --noEmit -p .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/editor-client apps/vscode-extension/webview-ui
git commit -m "feat(webview): edits, assignment and closing proposal; named waits and phase wording"
```

### Task 8: CLAUDE.md, full suites, live smoke

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

In the "Agent teams" bullet, replace the sentence that starts `**5B interim:** when every assignment is done the team ends` with:

```markdown
**Review (5C, plan `docs/superpowers/plans/2026-10-07-team-coordinator-5c-review.md`):** the last assignment done opens a **closing proposal** (`system`-authored `proposal`, `payload.closing`, `files_changed`, `cycle`; `teams.closing_proposal_id`/`review_cycles`) that every quorum member verifies in a round (deadline as in deliberation, 25 iterations, no `edit`); a member reporting without a stance is redirected once, then abstains. `teams/review.py` is pure: `evaluate_review` (latest stance per reviewer) and `route_objection` — to the owner of the first cited assigned file, else the objector with a synthetic assignment (files must exist, not protected, the objector can edit, inside the approved plan when gated), else the main agent (`member_blocked`). Fixers run in `IMPLEMENTING` with the framed objection (`assignment.fix`), then the next closing proposal asks only objectors, fixers and owners of files the fix changed; the rest carry their `agree` (`payload.carried`). No objections → `DONE (reviewed)`; nothing routable or past `CRUCIBLE_TEAM_REVIEW_CYCLES` (2) extra closing proposals → `DONE (unresolved objections)`; the `done` milestone names the plan, the closing proposal, abstentions, unresolved objections and files. A plan with no assignments still ends `DONE (adopted)`. A rewind deletes the teams it rewinds past (`TeamStore.delete_teams`, with their notices).
```

- [ ] **Step 2: Full suites**

```bash
cd services/agentd-py && .venv/bin/pytest --color=no --timeout=120 > /tmp/py.txt 2>&1; echo exit=$?; tail -5 /tmp/py.txt
npm run build && npm run typecheck && npm run test
cd apps/vscode-extension/webview-ui && perl -e 'alarm 600; exec @ARGV' npx vitest run
```

Expected: green except the known pre-existing `test_command_only_step_runs_command_and_verifies`. Lint/type: no new ruff or mypy findings in the changed files (compare against the base).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): team review — closing proposals, routing, fixes"
```

- [ ] **Step 4: Live smoke (dev host `vscode-p4`, CDP 9335, NIM, `CRUCIBLE_TEAMS_ENABLED=1`)**

After a rebuild: **Developer: Reload Webviews** (webview) or **Reload Window** (host/backend — it reaps live teams). Scenarios:

1. **Clean review:** a two-member team with assignments (`budget` ≥ 300, no approval gate). Expect `REVIEWING` after the `● finished` posts, a closing proposal labelled `closing proposal · review cycle 1` with each member's files, both members verifying, `Review of P<n>: agree …`, and `DONE (reviewed)` with the `done` milestone.
2. **Objection and fix:** ask (via the main agent, before the team starts) that `tests` object if `round_price(2.675)` is not `2.68`, with the implementation using plain `round`. Expect an objection on the closing proposal citing `shop/pricing.py`, `Fixing: api → #n`, `api` woken with the framed objection, a second closing proposal asking `api` and `tests` only.
3. **Edits section:** open `api`'s tab — its implementation chapter lists `shop/pricing.py +n −m applied`; its profile shows the assignment.
4. **Rewind:** rewind the thread to before the team was created — the team's card and board are gone (`GET …/teams` no longer lists it).

Record findings in memory `project_subagents_v2_spec.md`.
