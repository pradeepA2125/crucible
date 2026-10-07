# Team Lead Proposer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A team has one lead, chosen by the main agent in `create_team`, who alone proposes; the lead's new proposal replaces the open one, so agreed proposals can never tie.

**Architecture:** `TeamRecord.lead` (new nullable column) is set by `create_team`; `TeamService.prepare_propose` enforces lead-only proposing and closes every open proposal when the lead proposes; the state machine pauses the team when the lead leaves the quorum during deliberation; the brief, round headers and prompts tell members who leads; the UI shows a lead badge. A team row without a lead keeps the old any-member behavior.

**Tech Stack:** Python 3.13 (pydantic, sqlite3, pytest-asyncio), TypeScript (zod, React, vitest).

**Spec:** `docs/superpowers/specs/2026-10-07-team-lead-proposer-design.md`

## Global Constraints

- Python commands run from `services/agentd-py` with `.venv/bin/pytest` / `.venv/bin/ruff`; never pass `-q`.
- Refusal text for a non-lead proposing: `Only <lead> (the team's lead) proposes. Suggest your change with a note on your stance, an objection with evidence, or a post.`
- `create_team` without a valid lead: `lead must be one of the members: <labels comma-separated>`.
- Pause reason when the lead is lost: `lead lost`.
- Main prompt golden (`tests/goldens/controller_prompt_main.json`) must not change; the teams golden is regenerated with `ensure_ascii=True, indent=2` (the file's existing encoding).
- Commit messages: `type(scope): description` + the session attribution lines.

## Review Focus

1. **A team with no lead (legacy rows, tests building `TeamRecord` directly).** Expected: anyone may propose, `supersedes` still works, the stance-first check still applies. → Task 2 test `test_a_team_without_a_lead_keeps_the_old_rules`.
2. **The lead stopped by the user (■) mid-deliberation, then `resume_team`.** Expected: PAUSED / `lead lost`, then the lead is back in the quorum and restarted. → Task 3 test `test_resume_after_lead_lost_restarts_the_lead`.
3. **The lead lost while REVIEWING or IMPLEMENTING.** Expected: no pause; handled as today. → Task 3 test `test_lead_lost_outside_deliberation_does_not_pause`.
4. **A `post` kickoff with empty mentions.** Expected: everyone starts round 1 (unchanged); the lead is not singled out. → Task 1 test `test_post_kickoff_mentions_gain_the_lead`.
5. **The lead's report carrying a `proposal` with `supersedes`.** Expected: accepted; the field is ignored because the lead's proposal closes every open one anyway. → Task 2 test `test_lead_report_proposal_closes_the_open_one`.

---

### Task 1: The lead on the team record and in `create_team`

**Files:**
- Modify: `services/agentd-py/agentd/teams/models.py`, `agentd/teams/store.py`, `agentd/teams/tools.py` (`CreateTeamRequest`, `parse_create_team`, `create_team` tool schema), `agentd/teams/service.py` (`summary`), `agentd/chat/controller.py` (`_create_team`, `/live` teams dict)
- Test: `services/agentd-py/tests/test_team_lead.py` (create); `tests/test_team_tools.py` (`_args` helper gains `lead`)

**Interfaces:**
- Produces: `TeamRecord.lead: str | None`; `CreateTeamRequest.lead: str | None = None` (last field); `summary()["lead"]`; `/live` team dict `"lead"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_team_lead.py`:

```python
"""One lead proposes; the others review (spec 2026-10-07 lead proposer)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.tools import parse_create_team
from agentd.teams.validation import TeamInputError


def _args(**over):  # type: ignore[no-untyped-def]
    args = {"name": "auth", "goal": "Add login", "lead": "alice",
            "members": [{"label": "alice", "agent": "general-purpose"},
                        {"label": "bob", "agent": "explore"},
                        {"label": "carol", "agent": "explore"}],
            "kickoff": {"kind": "post", "text": "Plan it", "mentions": ["bob"]}}
    args.update(over)
    return args


def test_create_team_requires_a_known_lead() -> None:
    assert parse_create_team(_args(), dict(BUILTIN_AGENTS)).lead == "alice"
    for bad in (None, "dave"):
        args = _args(lead=bad)
        with pytest.raises(TeamInputError,
                           match="lead must be one of the members: alice, bob, carol"):
            parse_create_team(args, dict(BUILTIN_AGENTS))


def test_post_kickoff_mentions_gain_the_lead() -> None:
    assert parse_create_team(_args(), dict(BUILTIN_AGENTS)).kickoff_mentions == ["bob", "alice"]
    everyone = _args(kickoff={"kind": "post", "text": "Plan it", "mentions": []})
    assert parse_create_team(everyone, dict(BUILTIN_AGENTS)).kickoff_mentions == []


def _team(tmp_path: Path, lead: str | None = "alice"):  # type: ignore[no-untyped-def]
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=3, budget=100, created_turn_id="u",
                      created_at=datetime.now(UTC), lead=lead)
    teams.create_team(team)
    for label in ("alice", "bob", "carol"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    service = TeamService(teams, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    return service, team.team_id, teams


def test_the_lead_round_trips_and_shows_in_the_summary(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    assert teams.get_team(tid).lead == "alice"  # type: ignore[union-attr]
    assert service.summary(tid)["lead"] == "alice"
```

In `tests/test_team_tools.py`, in `_args`, add `"lead": "alice",` to the `args` dict.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_team_lead.py` → FAIL (`TeamRecord` has no `lead`).

- [ ] **Step 3: Implement**

`agentd/teams/models.py`, `TeamRecord` after `round_cutoff_seq`:

```python
    lead: str | None = None               # the member who alone proposes (None: anyone may)
```

`agentd/teams/store.py`, `_migrate`, after the `round_cutoff_seq` block:

```python
        if "lead" not in existing:
            self._conn.execute("ALTER TABLE teams ADD COLUMN lead TEXT")
```

`agentd/teams/tools.py`: `CreateTeamRequest` gains a last field `lead: str | None = None`. In `parse_create_team`, after `roster = [m.label for m in members]`:

```python
    lead = str(args.get("lead") or "").strip().lstrip("@").casefold()
    if lead not in roster:
        raise TeamInputError(f"lead must be one of the members: {', '.join(roster)}")
```

after the `mentions = …` statement:

```python
    if mentions and lead not in mentions:
        mentions = [*mentions, lead]   # round 1 always starts the lead
```

and pass `lead=lead` to `CreateTeamRequest(...)`. In `MainTeamToolSource.definitions`, add `"lead": _STR` to `create_team`'s properties, `"lead"` to its `required`, and replace the description with:

```python
                "Start a team of agents that deliberate on a shared board and then implement "
                "together. Each member's role is its agent definition. lead is the member who "
                "writes and revises the plan — the only one who proposes; pick the member whose "
                "role owns the overall design. kickoff \"proposal\" opens with your own plan for "
                "them to check; \"post\" asks the lead to propose. Returns at once; the team runs "
                "in the background."),
```

`agentd/chat/controller.py`, `_create_team`: add `lead=req.lead,` to the `TeamRecord(...)` call. In the `/live` teams dict (`"team_id": team.team_id, "name": team.name, "phase": team.phase,`) add `"lead": team.lead,`.

`agentd/teams/service.py`, `summary`: add `"lead": team.lead,` after `"adopted_proposal_id": …`.

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_team_lead.py tests/test_team_tools.py tests/test_team_controller.py tests/test_team_store.py tests/test_team_routes.py` → PASS.

- [ ] **Step 5: Commit** — `feat(teams): create_team names a lead`.

---

### Task 2: Only the lead proposes; its proposal replaces the open one

**Files:**
- Modify: `agentd/teams/service.py` (`prepare_propose`), `agentd/teams/tools.py` (`team_propose` tool), `agentd/teams/report_fields.py` (proposal call), `agentd/chat/controller_prompts.py` (`_REPORT_TEAM_FIELDS.proposal`)
- Test: `tests/test_team_lead.py`

**Interfaces:**
- Consumes: `TeamRecord.lead` (Task 1).
- Produces: `prepare_propose(...)` unchanged signature; lead-only rule when `team.lead` is set.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_team_lead.py`:

```python
from agentd.teams.report_fields import ReportFields  # noqa: E402
from agentd.teams.service import ActivationCounters  # noqa: E402


def _kickoff(teams, tid) -> None:  # type: ignore[no-untyped-def]
    teams.append_post(tid, author="main", kind="proposal", text="main plan", round=0,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})


def test_only_the_lead_proposes(tmp_path: Path) -> None:
    service, tid, _ = _team(tmp_path)
    with pytest.raises(TeamInputError, match=(
            r"Only alice \(the team's lead\) proposes\. Suggest your change with a note on "
            r"your stance, an objection with evidence, or a post\.")):
        service.propose(tid, "bob", "my plan", [])


def test_the_lead_replaces_every_open_proposal_without_a_stance_first(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    _kickoff(teams, tid)                                    # P1, no stance from alice
    p2 = service.propose(tid, "alice", "draft", [])
    assert teams.get_post(tid, 1).closed == "superseded"   # type: ignore[union-attr]
    p3 = service.propose(tid, "alice", "revised", [])
    assert teams.get_post(tid, p2.seq).closed == "superseded"  # type: ignore[union-attr]
    assert [p.proposal_id for p in service.open_proposals(tid)] == [p3.proposal_id]
    assert p3.payload["supersedes"] == [p2.proposal_id]


def test_lead_report_proposal_closes_the_open_one(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    _kickoff(teams, tid)
    check = ReportFields(service, tid, "alice", ActivationCounters())
    verdict = check({"type": "report", "thought": "t", "summary": "s", "status": "completed",
                     "proposal": {"text": "revised", "assignments": [], "supersedes": ["P9"]}},
                    False)
    assert verdict.message is None
    assert [p.author for p in service.open_proposals(tid)] == ["alice"]


def test_a_non_lead_report_proposal_is_refused(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    check = ReportFields(service, tid, "bob", ActivationCounters())
    verdict = check({"type": "report", "thought": "t", "summary": "s", "status": "completed",
                     "proposal": {"text": "mine", "assignments": []}}, False)
    assert verdict.message is not None and "Only alice" in verdict.message


def test_a_team_without_a_lead_keeps_the_old_rules(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path, lead=None)
    _kickoff(teams, tid)
    with pytest.raises(TeamInputError, match="State your stance on P1 first"):
        service.propose(tid, "bob", "mine", [])
    service.agree(tid, "bob", "P1")
    service.propose(tid, "bob", "mine", [])
    assert len(service.open_proposals(tid)) == 2           # nothing closed implicitly
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_team_lead.py` → the new tests FAIL.

- [ ] **Step 3: Implement**

`agentd/teams/service.py`, `prepare_propose` — after `self._require_phase(team, ("DELIBERATING",), "team_propose")`:

```python
        lead = team.lead
        if lead is not None and author != lead:
            raise TeamInputError(
                f"Only {lead} (the team's lead) proposes. Suggest your change with a note on "
                "your stance, an objection with evidence, or a post.")
```

wrap the existing `owed` computation and its `raise` in `if lead is None:` (the lead's proposal replaces those proposals, so no stance is owed first), and replace the `closes` block with:

```python
        closes: list[TeamPost] = []
        if lead is not None:
            # One open plan at a time: the lead's proposal replaces its previous draft and
            # the main agent's kickoff (spec 2026-10-07 lead proposer §3.2).
            closes = self.open_proposals(team_id)
        elif supersedes is not None:
            if not isinstance(supersedes, list):
                raise TeamInputError("supersedes must be a list of proposal ids")
            closes = [self._open_proposal(team_id, raw) for raw in supersedes]
```

`agentd/teams/tools.py` — `team_propose` tool: description `"Propose how the team does the work (the lead only): the approach, and who does which part with which files. shared_files may be edited by any assignee. A new proposal replaces the open one."`; drop `"supersedes": _STRS` from its properties; in `execute`, call `self._svc.propose(tid, me, args.get("text"), args.get("assignments"), args.get("shared_files"), None, n)`.

`agentd/teams/report_fields.py` — in the `proposal` branch, pass `None` instead of `proposal.get("supersedes")`, and change the shape message to `"must be {text, assignments, shared_files?}"`.

`agentd/chat/controller_prompts.py` — in `_REPORT_TEAM_FIELDS["proposal"]["properties"]` delete the `"supersedes"` entry.

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_team_*.py tests/test_openai_strict_schema.py` → PASS except the teams golden (`tests/test_prompt_goldens_teams.py` is regenerated in Task 4; it is not in this run's glob). If an existing test passes `supersedes` through `team_propose`, it relies on a team without a lead and still works.

- [ ] **Step 5: Commit** — `feat(teams): only the lead proposes, and its proposal replaces the open one`.

---

### Task 3: Losing the lead pauses deliberation

**Files:**
- Modify: `agentd/teams/state_machine.py` (`TeamState.lead`, `_round_report`), `agentd/teams/coordinator.py` (`__init__`)
- Test: `tests/test_team_state_machine.py`, `tests/test_team_coordinator.py`

**Interfaces:**
- Produces: `TeamState.lead: str | None = None`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_team_state_machine.py`:

```python
def test_lead_lost_while_deliberating_pauses() -> None:
    state = _round1(_state("alice", "bob", "carol"))
    state.lead = "alice"
    state, actions = apply(state, MemberReported("alice", "failed"))
    assert state.phase == "PAUSED"
    assert EnterPhase("PAUSED", 1, "lead lost") in actions


def test_another_member_lost_does_not_pause() -> None:
    state = _round1(_state("alice", "bob", "carol"))
    state.lead = "alice"
    state, _ = apply(state, MemberReported("bob", "failed"))
    assert state.phase == "DELIBERATING"


def test_lead_lost_outside_deliberation_does_not_pause() -> None:
    state = _state("alice", "bob", "carol", phase="REVIEWING")
    state.lead = "alice"
    state.round_members = ["alice", "bob", "carol"]
    state, _ = apply(state, MemberReported("alice", "failed"))
    assert state.phase == "REVIEWING"
```

Append to `tests/test_team_coordinator.py`:

```python
@pytest.mark.asyncio
async def test_resume_after_lead_lost_restarts_the_lead(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, labels=("alice", "bob", "carol"))
    store.update_team("team-1", lead="alice")
    coord = TeamCoordinator("team-1", store, svc, host,
                            CoordinatorTrace(tmp_path / "c2.jsonl"), round_timeout_s=900.0,
                            grace_s=0.05)
    coord.kickoff("proposal", [])
    coord.on_report("alice", "stopped", "user")
    assert store.get_team("team-1").phase == "PAUSED"
    assert store.get_team("team-1").paused_reason == "lead lost"
    host.started.clear()
    coord.resume(0)
    assert "alice" in host.started
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_team_state_machine.py tests/test_team_coordinator.py` → the new tests FAIL.

- [ ] **Step 3: Implement**

`agentd/teams/state_machine.py`, `TeamState` after `max_review_cycles`:

```python
    lead: str | None = None          # the member who alone proposes (spec 2026-10-07)
```

In `_round_report`, inside `if lost:`, right after the `actions += [SetQuorum…, Milestone("member_lost", …)]` statement:

```python
        deliberating = (state.paused_from if paused else state.phase) == "DELIBERATING"
        if event.label == state.lead and deliberating and not paused:
            # Nobody else may propose, so the team cannot go on without its lead.
            return [*actions, *_pause(state, "lead lost")]
```

`agentd/teams/coordinator.py`, `__init__`, after `self._state.max_review_cycles = team_review_cycles()`:

```python
        self._state.lead = team.lead
```

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_team_state_machine.py tests/test_team_coordinator.py` → PASS.

- [ ] **Step 5: Commit** — `feat(teams): losing the lead pauses deliberation`.

---

### Task 4: What members and the main agent read

**Files:**
- Modify: `agentd/teams/service.py` (`brief`, `_header`, `render_delta_posts`), `agentd/chat/controller_prompts.py` (`_TEAM_BLOCK`, `_TEAMS_MAIN_BLOCK`)
- Modify: `tests/goldens/controller_prompt_teams.json` (regenerated)
- Test: `tests/test_team_lead.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_team_lead.py`:

```python
def test_the_brief_names_the_lead(tmp_path: Path) -> None:
    service, tid, _ = _team(tmp_path)
    brief = service.brief(tid, "bob")
    assert "- alice (lead): gp" in brief
    assert "alice is the lead: only alice proposes; the others review." in brief


def test_round_one_headers_for_a_post_kickoff(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    teams.append_post(tid, author="main", kind="post", text="Plan it", round=0)
    lead_text, _, _ = service.render_delta_posts(tid, "alice")
    assert "the main agent asked for a plan, and you are the lead" in lead_text
    other, _, _ = service.render_delta_posts(tid, "bob")
    assert "alice is drafting the plan" in other


def test_later_round_reminds_the_lead_it_revises(tmp_path: Path) -> None:
    service, tid, teams = _team(tmp_path)
    service.propose(tid, "alice", "draft", [])
    teams.update_team(tid, round=2)
    lead_text, _, _ = service.render_delta_posts(tid, "alice")
    assert "your new proposal replaces the current one" in lead_text
    other, _, _ = service.render_delta_posts(tid, "bob")
    assert "your new proposal replaces" not in other
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_team_lead.py` → the three new tests FAIL.

- [ ] **Step 3: Implement**

`agentd/teams/service.py`, `brief`: replace the roster loop and return with:

```python
        for m in self._store.members(team_id):
            info = self._agent_info(m.agent_id)
            you = " (you)" if m.label == label else ""
            lead = " (lead)" if m.label == team.lead else ""
            lines.append(f"- {m.label}{lead}{you}: {info.name} — {info.description}")
        if team.lead is not None:
            lines.append(f"{team.lead} is the lead: only {team.lead} proposes; the others "
                         "review.")
        return "\n".join(lines)
```

`_header` gains a `label: str` parameter (`def _header(self, team: TeamRecord, first: bool, label: str) -> str:`); `render_delta_posts` calls `self._header(team, first=member.delivered_seq == 0, label=label)`. Inside, define `lead = team.lead` and `is_lead = lead == label`, then replace the round-1 and no-open-proposal leads:

```python
        elif n == 1 and kickoff is not None and kickoff.kind == "proposal":
            lead_in = (f"Round 1 of {total}: the main agent proposed {kickoff.proposal_id}. "
                       "Check the claims relevant to your role, then state your stance.")
            lead = (lead_in + " As the lead, revise it with team_propose if it needs changes; "
                    "your proposal replaces it." if is_lead else lead_in)
        elif n == 1 and team_lead is not None and not is_lead:
            lead = (f"Round 1 of {total}: {team_lead} is drafting the plan. Check the code your "
                    f"role covers and post what the plan must account for — {team_lead} reads "
                    "it next round.")
        elif n == 1:
            lead = (f"Round 1 of {total}: the main agent asked for a plan"
                    + (", and you are the lead" if is_lead else "")
                    + ". Check the code your role covers, then propose (team_propose, or the "
                    "proposal field of your report).")
```

(Rename the local so it does not shadow: use `team_lead = team.lead`, `is_lead = team_lead == label`, and keep the header string variable named `lead` as today.) In the no-open-proposal branch, when `team_lead is not None and not is_lead`, use `f"Round {n} of {total}. No proposal is open{why}. {team_lead} revises the plan; post what it must account for."`; otherwise keep today's text. In the final `else` branch, append for the lead: `" Revise with team_propose when an objection or note calls for it — your new proposal replaces the current one."`.

`agentd/chat/controller_prompts.py`, `_TEAM_BLOCK`: replace `team_propose sets out an approach with\n  assignments (member, part, files).` with `The lead (named in your brief) sets out the approach with\n  team_propose — assignments (member, part, files); a new proposal from the lead replaces the open\n  one. Everyone else shapes it with stances, notes and objections.` and replace `when you were asked to propose, your proposal` with `when you are the lead, your proposal`. In `_TEAMS_MAIN_BLOCK`, replace `- kickoff "proposal" opens with your own plan for the members to check; kickoff "post" asks the\n  mentioned members to propose.` with `- lead names the member who writes and revises the plan (the only one who proposes). kickoff\n  "proposal" opens with your own plan for the members to check; kickoff "post" asks the lead to\n  propose.`

- [ ] **Step 4: Regenerate the teams golden**

```bash
.venv/bin/python -c "import json; from tests.test_prompt_goldens_teams import _live, GOLDEN; GOLDEN.write_text(json.dumps(_live(), indent=2, ensure_ascii=True) + '\n', encoding='utf-8')"
git diff --stat tests/goldens/
```

Expected: only `controller_prompt_teams.json` changes.

- [ ] **Step 5: Run** — `.venv/bin/pytest tests/test_team_*.py tests/test_prompt_goldens.py tests/test_prompt_goldens_teams.py tests/test_prompt_goldens_subagents.py tests/test_prompt_leak_lint.py tests/test_openai_strict_schema.py tests/test_controller_delegated_hint.py` → PASS. If an existing header test asserts the old round-1 text for a team without a lead, it still passes (the no-lead path keeps today's words).

- [ ] **Step 6: Commit** — `feat(teams): members and the main agent know who leads`.

---

### Task 5: Lead badge in the UI

**Files:**
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (`toTeamCore`), `apps/editor-client/src/contracts/task-contracts.ts` (`TeamSummarySchema`, `TeamLiveSchema`)
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (`TeamSummaryView`, `TeamLiveView`), `components/teams/TeamCard.tsx`, `components/teams/TeamWindow.tsx`
- Test: `apps/editor-client/test/team-activity-contracts.test.ts`, `apps/vscode-extension/webview-ui/src/test/teamCard.test.tsx`, `src/test/teamWindow.test.tsx`

- [ ] **Step 1: Write the failing tests**

editor-client — in `team-activity-contracts.test.ts`, inside `maps /live round_progress, null when absent`, add `lead: "alice"` to `base` and assert `expect(withProgress.teams![0].lead).toBe("alice");`, and `expect(without.teams![0].lead).toBe("alice");`.

webview — `teamCard.test.tsx`, new test:

```tsx
  it("marks the lead", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />,
         { teams: { "team-1": { ...TEAM, lead: "impl" } } });
    expect(screen.getByTestId("member-row-impl")).toHaveTextContent("lead");
    expect(screen.getByTestId("member-row-review")).not.toHaveTextContent("lead");
  });
```

`teamWindow.test.tsx`: in an existing test that renders the window, set `lead` on its `TEAM` (or a copy) to its first member and assert `screen.getByLabelText(/Open <that label>'s tab/)` has text content `lead`.

- [ ] **Step 2: Run** — editor-client `npm run -w @crucible/editor-client test -- team-activity-contracts`, webview `npx vitest run src/test/teamCard.test.tsx src/test/teamWindow.test.tsx` → FAIL.

- [ ] **Step 3: Implement**

`toTeamCore` adds `lead: t["lead"] ?? null,`. `TeamSummarySchema` and `TeamLiveSchema` add `lead: z.string().nullable().default(null),`. Webview `TeamSummaryView` and `TeamLiveView` add `lead?: string | null;`.

`TeamCard.tsx` — after the label `<span>` in the member row:

```tsx
                {team.lead === m.label && <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px] font-semibold" style={{ color: "var(--color-amber)", borderColor: "var(--color-amber)" }}>lead</span>}
```

`TeamWindow.tsx` — inside the member tab's `<small>`, prefix: `{team.lead === m.label && "lead · "}`.

- [ ] **Step 4: Run all frontend checks**

```bash
npm run -w @crucible/editor-client test && npm run -w @crucible/editor-client build
npm run -w crucible-vscode-extension typecheck
cd apps/vscode-extension/webview-ui && npx vitest run && npx tsc --noEmit; cd -
npm run -w crucible-vscode-extension test
```

- [ ] **Step 5: Commit** — `feat(webview): show the team's lead`.

---

### Task 6: Verify, document, live run

- [ ] **Step 1:** Full suite: `.venv/bin/pytest --color=no > <file> 2>&1; echo exit=$?`; the only allowed failure is the pre-existing `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies`. `ruff check agentd/teams agentd/chat/controller.py agentd/chat/controller_prompts.py tests/test_team_*.py` clean.
- [ ] **Step 2:** `CLAUDE.md`, Agent teams paragraph: after `adopt_proposal`/`resume_team` tool list sentence add `**One lead** (spec 2026-10-07 lead proposer): create_team's required lead is the only member who proposes; the lead's proposal closes every open proposal, so agreed proposals never tie; the lead lost while DELIBERATING pauses the team (lead lost). Reads during a round stop at teams.round_cutoff_seq, and a stance on a proposal from the current round is refused.` Commit `docs: the team lead`.
- [ ] **Step 3: Live run** — rebuild (`npm run build`), Reload Window + Reload Webviews in the dev host, fresh thread in `workspaces/chatgpt-smoke` on the ChatGPT plan, prompt `design a tetris game with a team`. Check: `create_team` args name a lead; the board has at most one open proposal at a time; no stance on a same-round proposal (SQL in the vote plan's Task 8); adoption happens without a tie; usage vs run 4 (43 requests, 657k input, 80–83% cached).
