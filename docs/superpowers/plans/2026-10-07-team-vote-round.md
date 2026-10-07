# Team Vote Round Implementation Plan

> **Superseded** by `docs/superpowers/specs/2026-10-07-team-lead-proposer-design.md` (the vote round was reverted in 450b881; only round-bounded visibility, 424445b, shipped).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a deliberation round ends with several unanimously agreed proposals, run one vote round between them (no self-votes, vote-only, ties go to the main agent), and stop members from seeing or answering posts made during the current round.

**Architecture:** Pure rules stay pure: `teams/adoption.py` gains the qualifying list and `tally_votes`; `teams/state_machine.py` gains `vote_between`, `RoundEvaluated(tied, voters)` and `VoteEvaluated`. The coordinator persists two new team columns (`round_cutoff_seq`, `vote_between`) so `TeamService` (reads, header, status, validation) and the controller can see the round's cutoff and the vote. Members vote with a `team_vote` tool or a single `vote` field on their report.

**Tech Stack:** Python 3.13 (pydantic, sqlite3, pytest-asyncio), TypeScript (zod, React, vitest).

**Spec:** `docs/superpowers/specs/2026-10-07-team-vote-round-design.md`

## Global Constraints

- All Python commands run from `services/agentd-py` with `.venv/bin/pytest` / `.venv/bin/ruff` / `.venv/bin/mypy`. Never pass `-q` to pytest (pyproject already sets it); never pipe pytest into `tail` when you need the exit code.
- Tests run with teams/sub-agents forced OFF by `tests/conftest.py`; unit tests here construct `TeamService`/`TeamCoordinator` directly and do not need the flags.
- The vote round does not count toward `max_rounds` (`max_rounds += 1` when it starts).
- A vote is one value per member; the latest vote in the vote round counts.
- No self-votes: a member votes only among tied proposals authored by others.
- Vote-only: in a vote round `team_propose`, `team_agree`, `team_object`, `team_withdraw` (and report `stances`/`proposal`) are refused with: `This round is a vote between <ids>; only votes count. Use team_vote, or the vote field of your report.`
- Same-round stance refusal text: `<Pn> was posted this round; you can respond to it next round`.
- Vote-round activations are capped at 15 iterations.
- The main agent's prompt and schema stay byte-identical (`tests/goldens/controller_prompt_main.json` unchanged); only `tests/goldens/controller_prompt_teams.json` is regenerated.
- Milestones carry ids and counts only — never member-written text (spec v2 §8.8).
- Commit messages: `type(scope): description`, ending with the session's attribution lines.

## Review Focus

1. **A member who left the quorum mid-vote.** The tally counts only voters' latest votes; a voter who left still counts if it voted. Expected: no crash, the round ends when the remaining quorum has reported. → Task 6 test `test_vote_round_ends_when_a_voter_is_lost`.
2. **Pause during a vote round, then resume.** Expected: `vote_between` survives (persisted) and resume re-runs only voters who still owe their report, then tallies. → Task 6 test `test_pause_mid_vote_resumes_the_vote`.
3. **A re-vote** (a member calls `team_vote` twice for different candidates). Expected: only the latest counts. → Task 2 test `test_latest_vote_counts`.
4. **A report repeating the vote already cast with `team_vote`.** Expected: not posted twice, no refusal. → Task 5 test `test_report_vote_repeat_is_not_posted_twice`.
5. **Every tied proposal written by one member** (e.g. builder's P4 and P5): builder can't vote, the other members can; if no member can vote at all, straight to the tie path. → Task 3 test `test_no_voters_goes_straight_to_the_tie`, Task 6 test `test_voters_exclude_authors_of_every_candidate`.

---

### Task 0: Spec touch-ups

**Files:**
- Modify: `docs/superpowers/specs/2026-10-07-team-vote-round-design.md` (§6, §7)

- [ ] **Step 1: Edit §6** — replace the milestone example with one that has no notes:

```markdown
`milestone_text` gains `vote_tie`, compact (ids and counts only, like every milestone):
`Team X's vote between P4 and P7 tied` / `P4: 1 vote (reviewer)` / `P7: 1 vote (ui)` /
`Next: adopt_proposal to choose one, post_board to run one more round, or disband_team.` The vote
notes stay on the board. It is a wake notice (`source_kind "team"`) like `deadlock`.
```

- [ ] **Step 2: Edit §7** — replace the first and third bullets:

```markdown
- No new activity kind: the `vote` post itself is the board's record of a vote.
- `/live` teams: `round_progress` gains `vote: [ids] | null` (already inside `lastLiveSignature`,
  since the whole `teams` array is).
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-10-07-team-vote-round-design.md
git commit -m "docs(teams): vote spec — no notes in milestones, vote rides round_progress"
```

---

### Task 1: Round-bounded visibility

**Files:**
- Modify: `services/agentd-py/agentd/teams/models.py` (TeamRecord)
- Modify: `services/agentd-py/agentd/teams/store.py` (`_migrate`)
- Modify: `services/agentd-py/agentd/teams/service.py` (`_stance_phase`, `read`, `render_delta_posts`, `status_text`, new helpers)
- Modify: `services/agentd-py/agentd/teams/coordinator.py` (`__init__`, `_start_round`)
- Modify: `services/agentd-py/tests/test_team_service.py::test_agree_object_withdraw_rules`
- Modify: `services/agentd-py/tests/test_team_tools.py::test_member_propose_and_stances`
- Create: `services/agentd-py/tests/test_team_round_visibility.py`

**Interfaces:**
- Produces: `TeamRecord.round_cutoff_seq: int | None`, `TeamRecord.vote_between: str` (comma-separated ids), `TeamRecord.vote_ids -> list[str]` (property); `TeamService._round_cutoff(team) -> int | None`; `TeamService._within(posts, cutoff, viewer) -> list[TeamPost]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_team_round_visibility.py`:

```python
"""Members see the board as it stood when their round started (spec 2026-10-07 §3)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.validation import TeamInputError
from tests.test_team_coordinator import _setup as coordinator_setup


def _setup(tmp_path: Path):  # type: ignore[no-untyped-def]
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=3, budget=100, created_turn_id="u", created_at=datetime.now(UTC))
    teams.create_team(team)
    for label in ("alice", "bob"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    teams.append_post(team.team_id, author="main", kind="proposal", text="kickoff", round=0,
                      payload={"assignments": [], "shared_files": [], "supersedes": []})
    teams.update_team(team.team_id, round_cutoff_seq=1)   # round 1 started after the kickoff
    service = TeamService(teams, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    service.agree(team.team_id, "alice", "P1")            # seq 2
    proposal = service.propose(team.team_id, "alice", "Plan", [])   # seq 3, round 1
    return service, team.team_id, teams, proposal


def test_new_columns_round_trip(tmp_path: Path) -> None:
    _, tid, teams, _ = _setup(tmp_path)
    teams.update_team(tid, vote_between="P4,P7")
    team = teams.get_team(tid)
    assert team is not None and team.round_cutoff_seq == 1 and team.vote_ids == ["P4", "P7"]
    teams.update_team(tid, vote_between="")
    assert teams.get_team(tid).vote_ids == []  # type: ignore[union-attr]


def test_a_stance_on_a_proposal_from_this_round_is_refused(tmp_path: Path) -> None:
    service, tid, teams, proposal = _setup(tmp_path)
    with pytest.raises(TeamInputError, match="P3 was posted this round; you can respond to it "
                                             "next round"):
        service.agree(tid, "bob", proposal.proposal_id)
    teams.update_team(tid, round=2, round_cutoff_seq=3)
    assert service.agree(tid, "bob", proposal.proposal_id).ref_id == "P3"


def test_read_and_status_stop_at_the_round_cutoff(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    service.post(tid, "alice", "look @bob")                           # seq 4
    assert [p.seq for p in service.read(tid, "bob")] == [1]
    assert [p.seq for p in service.read(tid, "alice")] == [1, 2, 3, 4]   # own posts always
    bob = service.status_text(tid, "bob")
    assert "P3" not in bob and "0 mentions" in bob
    assert "- P3 by alice — your stance: yours" in service.status_text(tid, "alice")
    teams.update_team(tid, phase="IMPLEMENTING")                      # live delivery again
    assert [p.seq for p in service.read(tid, "bob")] == [1, 2, 3, 4]


def test_render_delta_defaults_to_the_persisted_cutoff(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    _, top, handed = service.render_delta_posts(tid, "bob")
    assert top == 1 and [p.seq for p in handed] == [1]


@pytest.mark.asyncio
async def test_the_coordinator_persists_the_cutoff(tmp_path: Path) -> None:
    store, _svc, _host, coord, _ = coordinator_setup(tmp_path)
    coord.kickoff("proposal", [])
    assert store.get_team("team-1").round_cutoff_seq == 1  # type: ignore[union-attr]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_team_round_visibility.py`
Expected: FAIL — `update_team` raises `ValueError: unknown team fields: ['round_cutoff_seq']`.

- [ ] **Step 3: Add the columns**

`agentd/teams/models.py`, in `TeamRecord` after `end_reason`:

```python
    round_cutoff_seq: int | None = None   # the board's top seq when the current round started
    vote_between: str = ""                # a vote round's candidates, comma-separated ("P4,P7")

    @property
    def vote_ids(self) -> list[str]:
        return [p for p in self.vote_between.split(",") if p]
```

`agentd/teams/store.py`, in `_migrate` after the `cached_tokens` block:

```python
        if "round_cutoff_seq" not in existing:
            self._conn.execute("ALTER TABLE teams ADD COLUMN round_cutoff_seq INTEGER")
        if "vote_between" not in existing:
            self._conn.execute(
                "ALTER TABLE teams ADD COLUMN vote_between TEXT NOT NULL DEFAULT ''")
```

- [ ] **Step 4: Bound reads by the cutoff**

`agentd/teams/service.py` — add to the helpers section (after `_open_proposal`):

```python
    def _round_cutoff(self, team: TeamRecord) -> int | None:
        """While a round runs, members see the board as it stood when it started (spec v2
        E5). Elsewhere delivery is live."""
        return team.round_cutoff_seq if team.phase == "DELIBERATING" else None

    @staticmethod
    def _within(posts: list[TeamPost], cutoff: int | None, viewer: str) -> list[TeamPost]:
        """Posts up to the cutoff, plus the viewer's own (always theirs to see)."""
        if cutoff is None:
            return posts
        return [p for p in posts if p.seq <= cutoff or p.author == viewer]
```

Replace `_stance_phase`'s DELIBERATING branch:

```python
        if team.phase == "DELIBERATING":
            # Adoption ignores a proposal from the round that just ended (§8.3); a stance on
            # one is refused so the board and the rule agree (spec 2026-10-07 §3).
            if (proposal.round or 0) >= team.round:
                raise TeamInputError(
                    f"{proposal.proposal_id} was posted this round; you can respond to it "
                    "next round")
            return
```

Replace `read`:

```python
    def read(self, team_id: str, viewer: str, since_seq: int = 0) -> list[TeamPost]:
        posts = self._store.posts(team_id, since_seq=since_seq, viewer=viewer)
        team = self._store.get_team(team_id)
        return self._within(posts, self._round_cutoff(team), viewer) if team else posts
```

In `render_delta_posts`, replace the two `until_seq` lines:

```python
        limit = until_seq if until_seq is not None else self._round_cutoff(team)
        if limit is not None:
            visible = [p for p in visible if p.seq <= limit]
```

In `status_text`, add right after `stances = self.stances(team_id)`:

```python
        cutoff = self._round_cutoff(team)
```

and replace the `open_ps = …` and `unread = …` lines:

```python
        open_ps = self._within(self.open_proposals(team_id), cutoff, label)
```

```python
        unread = self._within(
            self._store.posts(team_id, since_seq=member.delivered_seq, viewer=label),
            cutoff, label)
```

- [ ] **Step 5: Persist the cutoff from the coordinator**

`agentd/teams/coordinator.py`, in `__init__` replace `self._cutoff = 0 …` with:

```python
        self._cutoff = team.round_cutoff_seq or 0    # highest post seq at the round's start
```

In `_start_round`, right after the `self._cutoff = max(...)` line:

```python
        self._store.update_team(self._team_id, round_cutoff_seq=self._cutoff)
```

- [ ] **Step 6: Update the two tests that relied on same-round stances**

`tests/test_team_service.py::test_agree_object_withdraw_rules` — after the `p = service.propose(...)` statement insert:

```python
    teams.update_team(tid, round=2)   # stances answer proposals from an earlier round
```

`tests/test_team_tools.py::test_member_propose_and_stances` — after `assert out["proposal_id"] == "P1"` insert:

```python
    bob = TeamToolSource(svc, tid, "bob", ActivationCounters())
    same_round = await bob.execute("team_agree", {"proposal_id": "P1"})
    assert same_round.is_error and "posted this round" in same_round.output
    svc._store.update_team(tid, round=2)
```

and delete the now-duplicate `bob = TeamToolSource(...)` line below it.

- [ ] **Step 7: Run the team tests**

Run: `.venv/bin/pytest tests/test_team_round_visibility.py tests/test_team_service.py tests/test_team_tools.py tests/test_team_report_fields.py tests/test_team_coordinator.py tests/test_team_store.py`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add agentd/teams/models.py agentd/teams/store.py agentd/teams/service.py agentd/teams/coordinator.py tests/test_team_round_visibility.py tests/test_team_service.py tests/test_team_tools.py
git commit -m "fix(teams): members see only the board as their round started"
```

---

### Task 2: Qualifying proposals and the vote tally

**Files:**
- Modify: `services/agentd-py/agentd/teams/adoption.py`
- Modify: `services/agentd-py/tests/test_team_adoption.py`

**Interfaces:**
- Produces: `Evaluation.qualifying: tuple[str, ...]` (also `as_payload()["qualifying"]`); `VoteTally(round, between, counts, votes, winner)` with `as_payload() -> {"between", "counts", "votes": [{"label","proposal_id","note"}], "winner"}`; `tally_votes(posts: list[TeamPost], between: tuple[str, ...], voters: list[str] | tuple[str, ...], vote_round: int) -> VoteTally`; `evaluation_text` understands a `"vote"` payload.

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_adoption.py`, change the import to:

```python
from agentd.teams.adoption import evaluate_round, evaluation_text, tally_votes
```

Replace `test_lowest_seq_wins_and_closed_proposals_are_skipped` with:

```python
def test_several_qualifying_go_to_a_vote_and_closed_are_skipped() -> None:
    posts = [_post(1, "main", "proposal", round_=0, closed="superseded"),
             _post(2, "alice", "proposal", round_=0), _post(3, "alice", "proposal", round_=0),
             _post(4, "bob", "agree", ref="P2"), _post(5, "bob", "agree", ref="P3")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted is None and ev.qualifying == ("P2", "P3")
    assert [p.id for p in ev.proposals] == ["P2", "P3"]
    assert {p.reason for p in ev.proposals} == {"tied — vote next round"}
    assert ev.as_payload()["qualifying"] == ["P2", "P3"]


def test_one_qualifying_is_adopted() -> None:
    posts = [_post(2, "alice", "proposal", round_=0), _post(3, "alice", "proposal", round_=0),
             _post(4, "bob", "agree", ref="P2"), _post(5, "bob", "object", ref="P3")]
    ev = evaluate_round(posts, ["alice", "bob"], 1)
    assert ev.adopted == "P2" and ev.qualifying == ("P2",)


def _vote(seq, author, ref, round_=2, note="why"):  # type: ignore[no-untyped-def]
    post = _post(seq, author, "vote", ref=ref, round_=round_)
    return post.model_copy(update={"payload": {"note": note}})


def test_unique_top_count_wins() -> None:
    posts = [_vote(10, "alice", "P4"), _vote(11, "bob", "P7"), _vote(12, "carol", "P7")]
    tally = tally_votes(posts, ("P4", "P7"), ["alice", "bob", "carol"], 2)
    assert tally.winner == "P7" and tally.counts == {"P4": 1, "P7": 2}
    assert tally.as_payload()["votes"][0] == {"label": "alice", "proposal_id": "P4", "note": "why"}


def test_latest_vote_counts() -> None:
    posts = [_vote(10, "alice", "P4"), _vote(11, "alice", "P7"), _vote(12, "bob", "P7")]
    tally = tally_votes(posts, ("P4", "P7"), ["alice", "bob"], 2)
    assert tally.counts == {"P4": 0, "P7": 2} and tally.winner == "P7"


def test_a_tie_or_no_votes_has_no_winner() -> None:
    tied = tally_votes([_vote(10, "alice", "P4"), _vote(11, "bob", "P7")],
                       ("P4", "P7"), ["alice", "bob"], 2)
    assert tied.winner is None
    assert tally_votes([], ("P4", "P7"), ["alice", "bob"], 2).winner is None


def test_votes_from_another_round_or_non_voter_are_ignored() -> None:
    posts = [_vote(10, "alice", "P4", round_=1), _vote(11, "dave", "P4"), _vote(12, "bob", "P7")]
    tally = tally_votes(posts, ("P4", "P7"), ["alice", "bob"], 2)
    assert tally.counts == {"P4": 0, "P7": 1} and tally.winner == "P7"


def test_evaluation_text_for_a_vote() -> None:
    won = {"vote": {"counts": {"P4": 1, "P7": 2}, "winner": "P7"}}
    assert evaluation_text(won) == "vote: P4 1, P7 2 → P7 adopted"
    tied = {"vote": {"counts": {"P4": 1, "P7": 1}, "winner": None}}
    assert evaluation_text(tied) == "vote: P4 1, P7 1 → tied, the main agent decides"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_team_adoption.py`
Expected: FAIL — `ImportError: cannot import name 'tally_votes'`.

- [ ] **Step 3: Implement**

`agentd/teams/adoption.py` — module docstring becomes:

```python
"""Adoption at round end (spec v2 §8.3) and the vote round's tally (spec 2026-10-07 §4) —
pure, so both are testable without a team."""
```

Replace `Evaluation`:

```python
@dataclass(frozen=True)
class Evaluation:
    round: int
    proposals: tuple[ProposalEvaluation, ...]
    adopted: str | None
    qualifying: tuple[str, ...] = ()   # every unanimous, eligible, open proposal

    def as_payload(self) -> dict[str, object]:
        return {"round": self.round, "adopted": self.adopted,
                "qualifying": list(self.qualifying),
                "proposals": [p.as_payload() for p in self.proposals]}

    def summary(self) -> str:
        return evaluation_text(self.as_payload())
```

Replace `evaluation_text`:

```python
def evaluation_text(payload: dict[str, object]) -> str:
    """One line for a stored round_ended payload — team_status and the trace read it back."""
    vote = payload.get("vote")
    if isinstance(vote, dict):
        raw_counts = vote.get("counts")
        counts = raw_counts if isinstance(raw_counts, dict) else {}
        tally = ", ".join(f"{pid} {n}" for pid, n in counts.items())
        winner = vote.get("winner")
        return (f"vote: {tally} → {winner} adopted" if winner
                else f"vote: {tally} → tied, the main agent decides")
    raw = payload.get("proposals")
    proposals = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    if not proposals:
        return "no open proposals"
    return "; ".join(f"{p['id']} adopted" if p.get("adopted") else
                     f"{p['id']} not adopted: {p.get('reason', '')}" for p in proposals)
```

Replace `evaluate_round`:

```python
def evaluate_round(posts: list[TeamPost], quorum: list[str], ended_round: int) -> Evaluation:
    """A proposal qualifies when it is open, was posted before the ended round started,
    and every quorum member's latest stance on it is agree. Exactly one qualifying proposal
    is adopted; two or more go to a vote (spec 2026-10-07 §4)."""
    ordered = sorted(posts, key=lambda p: p.seq)
    latest: dict[str, dict[str, str]] = {}
    for post in ordered:
        if post.kind in ("agree", "object") and post.ref_id:
            latest.setdefault(post.ref_id, {})[post.author] = post.kind
    rows: list[tuple[TeamPost, dict[str, str], bool, tuple[str, str] | None]] = []
    for post in ordered:
        if post.kind != "proposal" or post.closed is not None:
            continue
        pid = post.proposal_id
        stances = {label: "agree" if label == post.author
                   else latest.get(pid, {}).get(label, "none") for label in quorum}
        eligible = (post.round or 0) < ended_round
        blocking = next(((lb, st) for lb, st in stances.items() if st != "agree"), None)
        rows.append((post, stances, eligible, blocking))
    qualifying = tuple(post.proposal_id for post, _s, eligible, blocking in rows
                       if eligible and blocking is None)
    adopted = qualifying[0] if len(qualifying) == 1 else None
    out: list[ProposalEvaluation] = []
    for post, stances, eligible, blocking in rows:
        pid = post.proposal_id
        if pid == adopted:
            reason = "adopted"
        elif pid in qualifying:
            reason = "tied — vote next round"
        elif not eligible:
            reason = "posted this round"
        else:
            assert blocking is not None
            reason = (f"{blocking[0]} objects" if blocking[1] == "object"
                      else f"{blocking[0]} has no stance")
        out.append(ProposalEvaluation(id=pid, round=post.round or 0, stances=stances,
                                      eligible=eligible, adopted=pid == adopted, reason=reason))
    return Evaluation(round=ended_round, proposals=tuple(out), adopted=adopted,
                      qualifying=qualifying)
```

Append:

```python
@dataclass(frozen=True)
class VoteTally:
    round: int
    between: tuple[str, ...]
    counts: dict[str, int]
    votes: tuple[tuple[str, str, str], ...]   # (voter, proposal id, note), by voter
    winner: str | None

    def as_payload(self) -> dict[str, object]:
        return {"between": list(self.between), "counts": dict(self.counts),
                "votes": [{"label": label, "proposal_id": pid, "note": note}
                          for label, pid, note in self.votes],
                "winner": self.winner}


def tally_votes(posts: list[TeamPost], between: tuple[str, ...],
                voters: list[str] | tuple[str, ...], vote_round: int) -> VoteTally:
    """Each voter's latest vote in the vote round counts. A unique top count wins; a tie or
    no votes at all is the main agent's call (spec 2026-10-07 §2)."""
    latest: dict[str, TeamPost] = {}
    for post in sorted(posts, key=lambda p: p.seq):
        if (post.kind == "vote" and post.round == vote_round and post.author in voters
                and post.ref_id in between):
            latest[post.author] = post
    counts = {pid: 0 for pid in between}
    for post in latest.values():
        counts[str(post.ref_id)] += 1
    top = max(counts.values(), default=0)
    leaders = [pid for pid, n in counts.items() if n == top]
    winner = leaders[0] if top > 0 and len(leaders) == 1 else None
    votes = tuple((label, str(p.ref_id), str(p.payload.get("note", "")))
                  for label, p in sorted(latest.items()))
    return VoteTally(vote_round, tuple(between), counts, votes, winner)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_team_adoption.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentd/teams/adoption.py tests/test_team_adoption.py
git commit -m "feat(teams): several agreed proposals qualify for a vote; tally the vote"
```

---

### Task 3: State machine — the vote round

**Files:**
- Modify: `services/agentd-py/agentd/teams/state_machine.py`
- Modify: `services/agentd-py/tests/test_team_state_machine.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TeamState.vote_between: tuple[str, ...]`; `RoundEvaluated(adopted, assignees=(), tied=(), voters=())`; new event `VoteEvaluated(winner: str | None, assignees: tuple[str, ...] = ())`; `Milestone("vote_tie", {"round": int})` (the coordinator adds the rest).

- [ ] **Step 1: Write the failing tests**

In `tests/test_team_state_machine.py` add `VoteEvaluated,` to the import list (alphabetical, after `Stuck,`/`TeamState,` — keep the list sorted: it goes after `TeamState,`), then append:

```python
def test_several_qualifying_start_a_vote_round_outside_the_limit() -> None:
    state = _round1(_state("alice", "bob", "carol", max_rounds=3))
    state, actions = apply(state, RoundEvaluated(None, tied=("P4", "P7"),
                                                 voters=("alice", "bob")))
    assert state.round == 2 and state.max_rounds == 4
    assert state.vote_between == ("P4", "P7")
    assert actions == [StartRound(2, ("alice", "bob"))]


def test_a_vote_at_the_round_limit_still_runs() -> None:
    state = _round1(_state(max_rounds=1))
    state, actions = apply(state, RoundEvaluated(None, tied=("P2", "P3"), voters=("alice",)))
    assert state.phase == "DELIBERATING" and actions == [StartRound(2, ("alice",))]


def test_no_voters_goes_straight_to_the_tie() -> None:
    state = _round1(_state())
    state, actions = apply(state, RoundEvaluated(None, tied=("P2", "P3"), voters=()))
    assert state.phase == "DEADLOCKED" and state.vote_between == () and state.round == 1
    assert actions == [EnterPhase("DEADLOCKED", 1, "vote tie"),
                       Milestone("vote_tie", {"round": 1})]


def _voting() -> TeamState:
    state = _round1(_state())
    state, _ = apply(state, RoundEvaluated(None, tied=("P2", "P3"), voters=("alice", "bob")))
    return state


def test_a_vote_winner_is_adopted() -> None:
    state, actions = apply(_voting(), VoteEvaluated("P3", ("alice",)))
    assert state.vote_between == ()
    assert actions[0] == Adopt("P3", "team")
    assert state.phase == "IMPLEMENTING"


def test_a_vote_tie_goes_to_the_main_agent() -> None:
    state, actions = apply(_voting(), VoteEvaluated(None))
    assert state.phase == "DEADLOCKED" and state.vote_between == ()
    assert actions == [EnterPhase("DEADLOCKED", 2, "vote tie"),
                       Milestone("vote_tie", {"round": 2})]


def test_vote_evaluated_outside_a_vote_is_ignored() -> None:
    state = _round1(_state())
    assert apply(state, VoteEvaluated("P2")) == (state, [])


def test_a_vote_round_ends_like_any_round() -> None:
    state = _voting()
    state, _ = apply(state, MemberReported("alice", "completed"))
    state, actions = apply(state, MemberReported("bob", "completed"))
    assert actions == [EvaluateRound(2)]


def test_revive_from_a_failed_vote_starts_a_normal_round() -> None:
    state = _voting()
    state.phase = "FAILED"
    state, _ = apply(state, Revive("DELIBERATING"))
    assert state.vote_between == ()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_team_state_machine.py`
Expected: FAIL — `ImportError: cannot import name 'VoteEvaluated'`.

- [ ] **Step 3: Implement**

`agentd/teams/state_machine.py`:

In `TeamState`, after `max_review_cycles`:

```python
    vote_between: tuple[str, ...] = ()  # a vote round's candidates (spec 2026-10-07 §4)
```

Replace `RoundEvaluated`:

```python
@dataclass(frozen=True)
class RoundEvaluated:
    adopted: str | None
    assignees: tuple[str, ...] = ()
    tied: tuple[str, ...] = ()       # two or more qualifying proposals → a vote round
    voters: tuple[str, ...] = ()     # quorum members with a candidate they did not write
```

After `RoundEvaluated` add:

```python
@dataclass(frozen=True)
class VoteEvaluated:
    """A vote round ended: the unique top-voted candidate, or None for a tie / no votes."""
    winner: str | None
    assignees: tuple[str, ...] = ()
```

Add `| VoteEvaluated` to the `Event` union (after `ReviewEvaluated`).

After `_pause` add:

```python
def _vote_tie(state: TeamState) -> list[Action]:
    """A tied vote (or nobody able to vote) is the main agent's call (spec 2026-10-07 §2)."""
    state.vote_between = ()
    state.phase = "DEADLOCKED"
    return [EnterPhase("DEADLOCKED", state.round, "vote tie"),
            Milestone("vote_tie", {"round": state.round})]
```

In `_revive`, in the final deliberation path, before `state.round += 1`:

```python
    state.vote_between = ()
```

Replace the `RoundEvaluated` branch of `apply`:

```python
    if isinstance(event, RoundEvaluated):
        if s.phase != "DELIBERATING":
            return s, []
        if event.adopted is not None:
            return s, _adopt(s, event.adopted, "team", event.assignees)
        if len(event.tied) >= 2:
            if not event.voters:
                return s, _vote_tie(s)
            # The vote round never counts toward the round limit (spec 2026-10-07 §2).
            s.round += 1
            s.max_rounds += 1
            s.vote_between = event.tied
            return s, _start_round(s, list(event.voters))
        if s.round >= s.max_rounds:
            s.phase = "DEADLOCKED"
            return s, [EnterPhase("DEADLOCKED", s.round, "round limit"),
                       Milestone("deadlock", {"round": s.round})]
        s.round += 1
        return s, _start_round(s, s.quorum())
    if isinstance(event, VoteEvaluated):
        if s.phase != "DELIBERATING" or not s.vote_between:
            return s, []
        s.vote_between = ()
        if event.winner is not None:
            return s, _adopt(s, event.winner, "team", event.assignees)
        return s, _vote_tie(s)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_team_state_machine.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentd/teams/state_machine.py tests/test_team_state_machine.py
git commit -m "feat(teams): a vote round between tied proposals; ties go to the main agent"
```

---

### Task 4: Votes on the board — service and `team_vote`

**Files:**
- Modify: `services/agentd-py/agentd/teams/models.py` (`POST_KINDS`)
- Modify: `services/agentd-py/agentd/teams/service.py`
- Modify: `services/agentd-py/agentd/teams/tools.py`
- Create: `services/agentd-py/tests/test_team_votes.py`

**Interfaces:**
- Consumes: `TeamRecord.vote_ids` (Task 1).
- Produces: `TeamService.prepare_vote(team_id, author, proposal_id, note=None) -> PreparedPost` (kind `"vote"`); `TeamService.vote(team_id, author, proposal_id, note=None) -> TeamPost`; `TeamService.vote_candidates(team_id, label) -> list[str]`; `TeamService.current_vote(team_id, label) -> str | None`; module function `ids_text(ids: list[str]) -> str` in `service.py`; member tool `team_vote` in `MEMBER_TOOL_NAMES`.

- [ ] **Step 1: Write the failing tests**

`tests/test_team_votes.py`:

```python
"""Votes on the board (spec 2026-10-07 §4.3, §5)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService, ids_text
from agentd.teams.tools import MEMBER_TOOL_NAMES, TeamToolSource
from agentd.teams.validation import TeamInputError

_EMPTY = {"assignments": [], "shared_files": [], "supersedes": []}


def _voting(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Round 2 is a vote between alice's P1 and P2 and bob's P3."""
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=4, budget=100, created_turn_id="u", created_at=datetime.now(UTC),
                      round=2)
    teams.create_team(team)
    for label in ("alice", "bob", "carol"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    for author in ("alice", "alice", "bob"):
        teams.append_post(team.team_id, author=author, kind="proposal", text=f"plan by {author}",
                          round=1, payload={**_EMPTY, "assignments": [
                              {"member": "carol", "part": "ui", "files": ["ui.js"]}]})
    teams.update_team(team.team_id, vote_between="P1,P2,P3", round_cutoff_seq=3)
    service = TeamService(teams, tmp_path, lambda _a: AgentInfo("gp", "", "running"))
    return service, team.team_id, teams


def test_ids_text() -> None:
    assert ids_text(["P4"]) == "P4"
    assert ids_text(["P4", "P7"]) == "P4 and P7"
    assert ids_text(["P4", "P5", "P7"]) == "P4, P5 and P7"


def test_a_vote_is_posted_with_its_note(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    post = service.vote(tid, "carol", "P3", note="cleaner split")
    assert (post.kind, post.ref_id, post.round, post.text) == ("vote", "P3", 2, "cleaner split")
    assert service.current_vote(tid, "carol") == "P3"
    assert service.current_vote(tid, "bob") is None


def test_vote_refusals(tmp_path: Path) -> None:
    service, tid, teams = _voting(tmp_path)
    with pytest.raises(TeamInputError, match="can't vote for your own proposal; pick one of P3"):
        service.vote(tid, "alice", "P1")
    with pytest.raises(TeamInputError, match="P9 is not in this vote; pick one of P1 and P2"):
        service.vote(tid, "bob", "P9")
    teams.update_team(tid, vote_between="")
    with pytest.raises(TeamInputError, match="only for a vote round"):
        service.vote(tid, "carol", "P1")


def test_vote_candidates_exclude_own_proposals(tmp_path: Path) -> None:
    service, tid, teams = _voting(tmp_path)
    assert service.vote_candidates(tid, "alice") == ["P3"]
    assert service.vote_candidates(tid, "carol") == ["P1", "P2", "P3"]
    teams.update_team(tid, vote_between="")
    assert service.vote_candidates(tid, "carol") == []


def test_a_vote_round_is_vote_only(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    only = "This round is a vote between P1, P2 and P3; only votes count"
    with pytest.raises(TeamInputError, match=only):
        service.agree(tid, "carol", "P1")
    with pytest.raises(TeamInputError, match=only):
        service.object_(tid, "carol", "P1", "no", {"quote_seq": 1})
    with pytest.raises(TeamInputError, match=only):
        service.propose(tid, "carol", "new", [])
    with pytest.raises(TeamInputError, match=only):
        service.withdraw(tid, "alice", "P1")
    service.post(tid, "carol", "posting still works")
    assert service.expected_stances(tid, "carol") == []


def test_header_and_status_describe_the_vote(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    text, _top, _handed = service.render_delta_posts(tid, "carol")
    assert "Round 2 (vote) of 4: P1, P2 and P3 each have everyone's agreement" in text
    assert "candidate P3" in text and "carol → ui (ui.js)" in text
    status = service.status_text(tid, "carol")
    assert "vote: P1, P2, P3 — your vote: none" in status
    service.vote(tid, "carol", "P2")
    assert "your vote: P2" in service.status_text(tid, "carol")


@pytest.mark.asyncio
async def test_team_vote_tool(tmp_path: Path) -> None:
    service, tid, _ = _voting(tmp_path)
    assert "team_vote" in MEMBER_TOOL_NAMES
    carol = TeamToolSource(service, tid, "carol", ActivationCounters())
    assert {d.name for d in carol.definitions()} == MEMBER_TOOL_NAMES
    out = await carol.execute("team_vote", {"proposal_id": "P1", "note": "simplest"})
    assert not out.is_error and json.loads(out.output) == {"seq": 4, "voted": "P1"}
    bad = await carol.execute("team_vote", {"proposal_id": "P9"})
    assert bad.is_error and "not in this vote" in bad.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_team_votes.py`
Expected: FAIL — `ImportError: cannot import name 'ids_text'`.

- [ ] **Step 3: Implement the service**

`agentd/teams/models.py`:

```python
POST_KINDS = frozenset({"post", "proposal", "agree", "object", "withdraw", "vote", "system"})
```

and update the `TeamPost.kind` comment to `# post | proposal | agree | object | withdraw | vote | system`.

`agentd/teams/service.py` — after `_STANCE_KINDS = ...`:

```python
def ids_text(ids: list[str]) -> str:
    """'P4', 'P4 and P7', 'P4, P5 and P7'."""
    if len(ids) <= 1:
        return "".join(ids)
    return f"{', '.join(ids[:-1])} and {ids[-1]}"
```

In the helpers section add:

```python
    def _refuse_during_vote(self, team: TeamRecord) -> None:
        """A vote round is vote-only: the candidates cannot change mid-vote (spec
        2026-10-07 §4.3)."""
        if team.phase == "DELIBERATING" and team.vote_ids:
            raise TeamInputError(
                f"This round is a vote between {ids_text(team.vote_ids)}; only votes count. "
                "Use team_vote, or the vote field of your report.")

    def _author_of(self, team_id: str, proposal_id: str) -> str:
        post = self._store.get_post(team_id, parse_proposal_id(proposal_id))
        return post.author if post is not None else ""
```

Call `self._refuse_during_vote(team)`:
- in `prepare_propose`, right after `self._require_phase(team, ("DELIBERATING",), "team_propose")`;
- in `prepare_agree` and `prepare_object`, right after `team = self._team(team_id)`;
- in `withdraw`, right after `self._require_phase(team, ("DELIBERATING",), "team_withdraw")`.

In `expected_stances`, after the `if team is None or team.phase != "DELIBERATING": return []` line:

```python
        if team.vote_ids:
            return []        # a vote round asks for a vote, not stances
```

After `object_` add:

```python
    def prepare_vote(self, team_id: str, author: str, proposal_id: object,
                     note: object = None) -> PreparedPost:
        team = self._team(team_id)
        ids = team.vote_ids
        if team.phase != "DELIBERATING" or not ids:
            raise TeamInputError("team_vote is only for a vote round; state stances with "
                                 "team_agree or team_object")
        pid = f"P{parse_proposal_id(proposal_id)}"
        others = [p for p in ids if self._author_of(team_id, p) != author]
        if pid not in ids:
            raise TeamInputError(f"{pid} is not in this vote; pick one of {ids_text(others)}")
        if pid not in others:
            raise TeamInputError(
                f"you can't vote for your own proposal; pick one of {ids_text(others)}")
        payload = {"note": check_text(note, "note")} if note not in (None, "") else {}
        return PreparedPost(kind="vote", text=str(payload.get("note", "")), ref_id=pid,
                            payload=payload)

    def vote(self, team_id: str, author: str, proposal_id: object,
             note: object = None) -> TeamPost:
        return self.commit(team_id, author, self.prepare_vote(team_id, author, proposal_id, note))

    def vote_candidates(self, team_id: str, label: str) -> list[str]:
        """The vote round's candidates `label` may vote for (not its own); [] outside one."""
        team = self._store.get_team(team_id)
        if team is None or team.phase != "DELIBERATING" or not team.vote_ids:
            return []
        return [p for p in team.vote_ids if self._author_of(team_id, p) != label]

    def current_vote(self, team_id: str, label: str) -> str | None:
        """`label`'s latest vote in the current round."""
        team = self._store.get_team(team_id)
        if team is None:
            return None
        votes = [p for p in self._store.posts(team_id)
                 if p.kind == "vote" and p.author == label and p.round == team.round]
        return votes[-1].ref_id if votes else None
```

In `_render_post`, before the `elif post.recipient is not None:` branch:

```python
        elif post.kind == "vote":
            kind = f"votes for {post.ref_id}"
            body = post.text or "(no note)"
```

In `_header`, insert as the first `elif` after `if team.phase != "DELIBERATING":`:

```python
        elif team.vote_ids:
            lead = (f"Round {n} (vote) of {total}: {ids_text(team.vote_ids)} each have "
                    "everyone's agreement, so the team picks one. Vote for the proposal the "
                    "team should build — not your own — with team_vote or the vote field of "
                    "your report, and a note saying why. Only votes count this round.")
```

and right after `lines = [lead]`:

```python
        if team.phase == "DELIBERATING" and team.vote_ids:
            lines.extend(self._vote_card(team.team_id, pid) for pid in team.vote_ids)
```

Add the card helper next to `_render_post`:

```python
    def _vote_card(self, team_id: str, proposal_id: str) -> str:
        """One candidate, compact: members compare them side by side without team_read."""
        post = self._store.get_post(team_id, parse_proposal_id(proposal_id))
        if post is None:
            return f"candidate {proposal_id}: (missing)"
        text = post.text if len(post.text) <= 300 else post.text[:300] + "…"
        parts = "; ".join(
            f"{a.get('member')} → {a.get('part')} "
            f"({', '.join(a.get('files') or []) or 'no files'})"
            for a in post.payload.get("assignments", []) if isinstance(a, dict))
        body = text + (f"\nassignments: {parts}" if parts else "")
        return frame(self._who(team_id, post.author), f"candidate {proposal_id}", body,
                     seq=post.seq)
```

In `status_text`, right after the loop that appends the `- {p.proposal_id} by …` lines:

```python
        if team.phase == "DELIBERATING" and team.vote_ids:
            lines.append(f"vote: {', '.join(team.vote_ids)} — your vote: "
                         f"{self.current_vote(team_id, label) or 'none'}")
```

- [ ] **Step 4: Add the tool**

`agentd/teams/tools.py`:

```python
MEMBER_TOOL_NAMES = frozenset({
    "team_post", "team_message", "team_propose", "team_agree", "team_object", "team_withdraw",
    "team_vote", "team_read"})
```

In `TeamToolSource.definitions`, after the `team_withdraw` tool:

```python
            tool("team_vote", "In a vote round, vote for the tied proposal the team should "
                 "build (not your own). Voting again replaces your vote. The note says why.",
                 {"proposal_id": _STR, "note": _STR}, ["proposal_id"]),
```

In `execute`, after the `team_withdraw` branch:

```python
            if tool == "team_vote":
                post = self._svc.vote(tid, me, args.get("proposal_id"), args.get("note"))
                return _ok({"seq": post.seq, "voted": post.ref_id})
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_team_votes.py tests/test_team_service.py tests/test_team_tools.py tests/test_team_report_fields.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agentd/teams/models.py agentd/teams/service.py agentd/teams/tools.py tests/test_team_votes.py
git commit -m "feat(teams): team_vote, vote-only rounds, and the vote header"
```

---

### Task 5: The report's `vote` field and prompt teaching

**Files:**
- Modify: `services/agentd-py/agentd/teams/report_fields.py`
- Modify: `services/agentd-py/agentd/chat/controller_prompts.py` (`_REPORT_TEAM_FIELDS`, `_TEAM_BLOCK`)
- Modify: `services/agentd-py/tests/test_team_report_fields.py`
- Modify: `services/agentd-py/tests/test_team_prompts.py`
- Modify: `services/agentd-py/tests/goldens/controller_prompt_teams.json` (regenerated)

**Interfaces:**
- Consumes: `prepare_vote`, `current_vote`, `vote_candidates` (Task 4).
- Produces: report field `vote: {proposal_id, note?}` in the team-member schema only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_report_fields.py`:

```python
def _vote_round(store, tid) -> None:  # type: ignore[no-untyped-def]
    """bob's P2 and P3 tie with main's P1; round 2 is the vote."""
    for _ in range(2):
        store.append_post(tid, author="bob", kind="proposal", text="bob plan", round=1,
                          payload={"assignments": [], "shared_files": [], "supersedes": []})
    store.update_team(tid, vote_between="P1,P2,P3")


def test_report_vote_is_posted(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    assert check(_report(vote={"proposal_id": "P2", "note": "smaller"}), False).message is None
    votes = [p for p in store.posts(tid) if p.kind == "vote"]
    assert [(p.author, p.ref_id, p.text) for p in votes] == [("alice", "P2", "smaller")]


def test_missing_vote_redirects_once(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    first = check(_report(), False)
    assert first.message is not None and '"vote"' in first.message
    assert "P1, P2, P3" in first.message and not first.malformed
    assert check(_report(), False).message is None


def test_a_member_with_no_candidate_is_not_asked_to_vote(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    for _ in range(2):
        store.append_post(tid, author="bob", kind="proposal", text="bob plan", round=1,
                          payload={"assignments": [], "shared_files": [], "supersedes": []})
    store.update_team(tid, vote_between="P2,P3")
    assert ReportFields(svc, tid, "bob", ActivationCounters())(_report(), False).message is None


def test_invalid_vote_refused_then_dropped_on_final(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    dropped: list[list[str]] = []
    check = ReportFields(svc, tid, "bob", ActivationCounters(), on_dropped=dropped.append)
    refused = check(_report(vote={"proposal_id": "P2"}), False)
    assert refused.message is not None and "own proposal" in refused.message
    assert check(_report(vote={"proposal_id": "P2"}), True).message is None
    assert dropped and not [p for p in store.posts(tid) if p.kind == "vote"]


def test_stances_in_a_vote_round_are_refused(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    verdict = ReportFields(svc, tid, "alice", ActivationCounters())(
        _report(stances=[{"proposal_id": "P1", "stance": "agree"}]), False)
    assert verdict.message is not None and "only votes count" in verdict.message


def test_report_vote_repeat_is_not_posted_twice(tmp_path) -> None:
    svc, store, tid = _svc(tmp_path)
    _vote_round(store, tid)
    svc.vote(tid, "alice", "P3")
    check = ReportFields(svc, tid, "alice", ActivationCounters())
    assert check(_report(vote={"proposal_id": "P3"}), False).message is None
    assert len([p for p in store.posts(tid) if p.kind == "vote"]) == 1
```

Append to `tests/test_team_prompts.py`:

```python
def test_team_report_schema_has_a_single_vote() -> None:
    flat = controller_response_schema(phase="AGENT", team_member=True)
    vote = flat["properties"]["vote"]
    assert vote["type"] == "object" and vote["required"] == ["proposal_id"]
    tight = controller_response_schema(phase="AGENT", tight=True, team_member=True)
    report = next(b for b in tight["oneOf"] if b["properties"]["type"]["const"] == "report")
    assert "vote" in report["properties"] and "vote" not in report["required"]
    assert "vote" not in controller_response_schema(phase="AGENT")["properties"]
```

(`controller_response_schema` is already imported at the top of `test_team_prompts.py`; if it is not, add `from agentd.chat.controller_prompts import controller_response_schema`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_team_report_fields.py tests/test_team_prompts.py`
Expected: FAIL — the vote is not posted / `KeyError: 'vote'`.

- [ ] **Step 3: Add the schema field**

`agentd/chat/controller_prompts.py`, in `_REPORT_TEAM_FIELDS` after `"proposal": …`:

```python
    # One object, not a list: a member cannot name two (spec 2026-10-07 §5).
    "vote": {"type": "object", "properties": {"proposal_id": _STR, "note": _STR},
             "required": ["proposal_id"]},
```

- [ ] **Step 4: Validate and post the vote in `ReportFields.__call__`**

`agentd/teams/report_fields.py` — add a constant under `_SHAPE`:

```python
_VOTE_SHAPE = '"vote": {"proposal_id": "P4", "note": "why the team should build it"}'
```

In `__call__`, after the `proposal` block and before `if errors and not final:`:

```python
        voted = False
        vote = resp.get("vote")
        if vote is not None:
            try:
                if not isinstance(vote, dict):
                    raise TeamInputError("must be {proposal_id, note?}")
                pid = f"P{parse_proposal_id(vote.get('proposal_id'))}"
                if self._svc.current_vote(self._team_id, self._label) != pid:
                    prepared.append(self._svc.prepare_vote(
                        self._team_id, self._label, pid, vote.get("note")))
                voted = True
            except TeamInputError as exc:
                errors.append(f"vote: {exc}")
```

After the missing-stance redirect block (before `for p in prepared:`):

```python
        candidates = self._svc.vote_candidates(self._team_id, self._label)
        owes_vote = (bool(candidates) and not voted
                     and self._svc.current_vote(self._team_id, self._label) is None)
        if owes_vote and not final and not self._redirected:
            self._redirected = True
            return ReportVerdict(message=(
                f"This round is a vote and your report has none. Add {_VOTE_SHAPE} — one of "
                f"{', '.join(candidates)} — then report again."))
```

- [ ] **Step 5: Teach the prompt**

`agentd/chat/controller_prompts.py`, in `_TEAM_BLOCK` insert this bullet right after the bullet that ends `— the same as the separate tool calls, in one action.`:

```text
- When several proposals all have everyone's agreement, the next round is a vote between them.
  Vote for the one the team should build — not your own — with team_vote or the report's
  "vote" field ({proposal_id, note}). Only votes count in that round.
```

and insert this example right before the line `Example — implementing, when you need a change in a file another member owns:`:

```text
Example — a vote round: pick one that is not yours, and say why:
{"type":"report","thought":"P4 and P7 both work; P7 splits the files cleanly","summary":"Voted P7.","status":"completed","vote":{"proposal_id":"P7","note":"P7 gives each member disjoint files; P4 has two members editing ui.js."}}
```

- [ ] **Step 6: Regenerate the teams golden and check the main golden is unchanged**

Run:

```bash
.venv/bin/python -c "import json; from tests.test_prompt_goldens_teams import _live, GOLDEN; GOLDEN.write_text(json.dumps(_live(), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')"
git diff --stat tests/goldens/
```

Expected: only `tests/goldens/controller_prompt_teams.json` changed, and its diff touches only `system/teams_member`.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_team_report_fields.py tests/test_team_prompts.py tests/test_prompt_goldens.py tests/test_prompt_goldens_teams.py tests/test_prompt_goldens_subagents.py tests/test_prompt_leak_lint.py tests/test_openai_strict_schema.py`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add agentd/teams/report_fields.py agentd/chat/controller_prompts.py tests/test_team_report_fields.py tests/test_team_prompts.py tests/goldens/controller_prompt_teams.json
git commit -m "feat(teams): members vote through a single report field"
```

---

### Task 6: Coordinator, milestone and controller wiring

**Files:**
- Modify: `services/agentd-py/agentd/teams/coordinator.py`
- Modify: `services/agentd-py/agentd/teams/milestones.py`
- Modify: `services/agentd-py/agentd/chat/controller.py` (iteration cap; `_round_progress`)
- Modify: `services/agentd-py/tests/test_team_coordinator.py`
- Modify: `services/agentd-py/tests/test_team_milestones.py`

**Interfaces:**
- Consumes: `tally_votes`, `Evaluation.qualifying` (Task 2); `RoundEvaluated(tied, voters)`, `VoteEvaluated` (Task 3); `TeamRecord.vote_ids` (Task 1); `TeamService.vote` (Task 4).
- Produces: `round_ended` payload for a vote round `{"round", "adopted", "proposals": [], "vote": VoteTally.as_payload(), "new_posts"}`; milestone kind `vote_tie`; `/live` `round_progress.vote: list[str] | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_coordinator.py`:

```python
def _tie(store, svc, coord, labels=("alice", "bob", "carol")):  # type: ignore[no-untyped-def]
    """Round 1: alice proposes P2, bob P3 (posted round 1 → eligible at round 2's end)."""
    coord.kickoff("post", [])
    svc.propose("team-1", "alice", "plan A", [])
    svc.propose("team-1", "bob", "plan B", [])
    for label in labels:
        coord.on_report(label, "completed", None)
    # Round 2: everyone agrees with both.
    for label in labels:
        for pid in ("P2", "P3"):
            if not (label == "alice" and pid == "P2") and not (label == "bob" and pid == "P3"):
                svc.agree("team-1", label, pid)
    for label in labels:
        coord.on_report(label, "completed", None)


@pytest.mark.asyncio
async def test_a_tie_starts_a_vote_round(tmp_path) -> None:
    store, svc, host, coord, trace = _setup(tmp_path, labels=("alice", "bob", "carol"))
    store.close_proposal("team-1", 1, "withdrawn")
    _tie(store, svc, coord)
    team = store.get_team("team-1")
    assert team.round == 3 and team.max_rounds == 4 and team.vote_ids == ["P2", "P3"]
    assert host.started[-3:] == ["alice", "bob", "carol"]
    assert '"qualifying": ["P2", "P3"]' in trace.read_text()


@pytest.mark.asyncio
async def test_the_vote_winner_is_adopted(tmp_path) -> None:
    store, svc, host, coord, trace = _setup(tmp_path, labels=("alice", "bob", "carol"))
    store.close_proposal("team-1", 1, "withdrawn")
    _tie(store, svc, coord)
    svc.vote("team-1", "alice", "P3")
    svc.vote("team-1", "bob", "P2")
    svc.vote("team-1", "carol", "P3", note="simpler")
    for label in ("alice", "bob", "carol"):
        coord.on_report(label, "completed", None)
    team = store.get_team("team-1")
    assert team.adopted_proposal_id == "P3" and team.vote_ids == []
    ended = [a for a in store.activity("team-1") if a.kind == "round_ended"][-1]
    assert ended.payload["vote"]["counts"] == {"P2": 1, "P3": 2}
    assert ended.payload["vote"]["winner"] == "P3"


@pytest.mark.asyncio
async def test_a_tied_vote_wakes_the_main_agent(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, labels=("alice", "bob", "carol"))
    store.close_proposal("team-1", 1, "withdrawn")
    _tie(store, svc, coord)
    svc.vote("team-1", "alice", "P3")
    svc.vote("team-1", "bob", "P2")
    for label in ("alice", "bob", "carol"):
        coord.on_report(label, "completed", None)
    assert store.get_team("team-1").phase == "DEADLOCKED"
    assert host.milestones[-1] == "vote_tie"
    assert "P2: 1 vote (bob)" in host.bodies[-1] and "P3: 1 vote (alice)" in host.bodies[-1]
    coord.main_adopt("P2")
    assert store.get_team("team-1").adopted_proposal_id == "P2"


@pytest.mark.asyncio
async def test_voters_exclude_authors_of_every_candidate(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, labels=("alice", "bob"))
    store.close_proposal("team-1", 1, "withdrawn")
    coord.kickoff("post", [])
    svc.propose("team-1", "alice", "plan A", [])
    svc.propose("team-1", "alice", "plan A2", [])
    for label in ("alice", "bob"):
        coord.on_report(label, "completed", None)
    for pid in ("P2", "P3"):
        svc.agree("team-1", "bob", pid)
    for label in ("alice", "bob"):
        coord.on_report(label, "completed", None)
    assert store.get_team("team-1").vote_ids == ["P2", "P3"]
    assert host.started[-1:] == ["bob"]                 # alice wrote both candidates


@pytest.mark.asyncio
async def test_vote_round_ends_when_a_voter_is_lost(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, labels=("alice", "bob", "carol"))
    store.close_proposal("team-1", 1, "withdrawn")
    _tie(store, svc, coord)
    svc.vote("team-1", "carol", "P2")
    coord.on_report("carol", "completed", None)
    coord.on_report("alice", "failed", None)            # leaves the quorum
    coord.on_report("bob", "completed", None)
    assert store.get_team("team-1").adopted_proposal_id == "P2"


@pytest.mark.asyncio
async def test_pause_mid_vote_resumes_the_vote(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, labels=("alice", "bob", "carol"))
    store.close_proposal("team-1", 1, "withdrawn")
    _tie(store, svc, coord)
    svc.vote("team-1", "alice", "P3")
    coord.on_report("alice", "completed", None)
    host.requests = 1000
    coord.check_budget()
    assert store.get_team("team-1").phase == "PAUSED"
    assert store.get_team("team-1").vote_ids == ["P2", "P3"]
    host.started.clear()
    coord.resume(100)
    assert sorted(host.started) == ["bob", "carol"]
    svc.vote("team-1", "bob", "P3")
    coord.on_report("bob", "completed", None)
    coord.on_report("carol", "completed", None)
    assert store.get_team("team-1").adopted_proposal_id == "P3"


@pytest.mark.asyncio
async def test_votes_are_not_held(tmp_path) -> None:
    store, svc, host, coord, _ = _setup(tmp_path, labels=("alice", "bob", "carol"))
    store.close_proposal("team-1", 1, "withdrawn")
    _tie(store, svc, coord)
    post = svc.vote("team-1", "carol", "P2")
    coord.on_post(post)
    assert not [a for a in store.activity("team-1") if a.kind == "held"
                and a.cause_seq == post.seq]
```

These tests post through `svc` directly, so `coord.on_post` is not called for them (the existing tests work the same way); `test_votes_are_not_held` calls it explicitly.

Append to `tests/test_team_milestones.py` (reuse that file's team fixture; if it builds `TeamRecord` inline, build one the same way and name it `team`):

```python
def test_vote_tie_text() -> None:
    team = TeamRecord(team_id="t", thread_id="th", name="auth", goal="g", max_rounds=4,
                      budget=100, created_turn_id="u", created_at=datetime.now(UTC),
                      phase="DEADLOCKED", round=3)
    headline, body = milestone_text(team, "vote_tie", {
        "round": 3, "between": ["P4", "P7"], "counts": {"P4": 1, "P7": 1},
        "votes": [{"label": "ui", "proposal_id": "P7", "note": "never shown"},
                  {"label": "reviewer", "proposal_id": "P4", "note": "never shown"}]})
    assert headline == "Team 'auth''s vote between P4 and P7 tied"
    assert "P4: 1 vote (reviewer)" in body and "P7: 1 vote (ui)" in body
    assert "never shown" not in body
    assert "adopt_proposal to choose one" in body
    _, nobody = milestone_text(team, "vote_tie", {"round": 3, "between": ["P2", "P3"],
                                                  "counts": {}, "votes": []})
    assert "No member could vote" in nobody
```

(Add `from datetime import UTC, datetime` and `from agentd.teams.models import TeamRecord` to that file's imports if missing.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_team_coordinator.py tests/test_team_milestones.py`
Expected: FAIL — the round after the tie adopts nothing / `vote_tie` is unknown.

- [ ] **Step 3: Wire the coordinator**

`agentd/teams/coordinator.py`:

Import: `from agentd.teams.adoption import evaluate_round, tally_votes`.

In `__init__`, after `self._state.max_review_cycles = team_review_cycles()`:

```python
        self._state.vote_between = tuple(team.vote_ids)
```

Replace `_apply`:

```python
    def _apply(self, event: sm.Event) -> None:
        before = self._state.stuck_count
        before_vote = self._state.vote_between
        self._state, actions = sm.apply(self._state, event)
        self._trace.write("event", name=type(event).__name__, event=repr(event),
                          actions=[repr(a) for a in actions])
        if self._state.stuck_count != before:
            self._store.update_team(self._team_id, stuck_count=self._state.stuck_count)
        if self._state.vote_between != before_vote:
            # Persisted before StartRound runs: the voters' header and checks read it.
            self._store.update_team(self._team_id,
                                    vote_between=",".join(self._state.vote_between))
        for action in actions:
            self._execute(action)
```

Replace `_evaluate`:

```python
    def _evaluate(self, ended_round: int) -> None:
        posts = self._store.posts(self._team_id)
        new_posts = sum(1 for p in posts if p.round == ended_round and p.kind != "system")
        if self._state.vote_between:
            self._evaluate_vote(posts, ended_round, new_posts)
            return
        evaluation = evaluate_round(posts, self._state.quorum(), ended_round)
        payload = {**evaluation.as_payload(), "new_posts": new_posts}
        self._svc.record(self._team_id, "team", "round_ended", payload=payload)
        self._trace.write("evaluation", **evaluation.as_payload())
        # The deadlock / vote_tie milestones read this payload (spec §8.8).
        self._evaluation = payload
        adopted = evaluation.adopted
        tied = evaluation.qualifying if adopted is None else ()
        self._apply(sm.RoundEvaluated(adopted, self._assignees(adopted) if adopted else (),
                                      tied=tied, voters=self._voters(tied)))

    def _evaluate_vote(self, posts: list[TeamPost], ended_round: int, new_posts: int) -> None:
        tally = tally_votes(posts, self._state.vote_between, self._state.round_members,
                            ended_round)
        payload = {"round": ended_round, "adopted": tally.winner, "proposals": [],
                   "vote": tally.as_payload(), "new_posts": new_posts}
        self._svc.record(self._team_id, "team", "round_ended", payload=payload)
        self._trace.write("vote", round=ended_round, **tally.as_payload())
        self._evaluation = payload
        winner = tally.winner
        self._apply(sm.VoteEvaluated(winner, self._assignees(winner) if winner else ()))

    def _voters(self, tied: tuple[str, ...]) -> tuple[str, ...]:
        """Quorum members with a candidate they did not write (no self-votes)."""
        if not tied:
            return ()
        authors = []
        for pid in tied:
            post = self._store.get_post(self._team_id, int(pid.lstrip("Pp")))
            authors.append(post.author if post is not None else "")
        return tuple(lb for lb in self._state.quorum() if any(a != lb for a in authors))
```

In `_milestone`, after the `if action.kind == "deadlock":` branch add:

```python
        elif action.kind == "vote_tie":
            vote = self._evaluation.get("vote")
            if isinstance(vote, dict):
                data.update(between=vote.get("between", []), counts=vote.get("counts", {}),
                            votes=vote.get("votes", []))
            else:   # nobody could vote: the tie came straight from the deliberation round
                data.update(between=self._evaluation.get("qualifying", []), counts={},
                            votes=[])
```

In `_hold`, change the first check to:

```python
        if post.kind in ("agree", "object", "withdraw", "vote"):
            return
```

- [ ] **Step 4: Milestone text**

`agentd/teams/milestones.py`, before `elif kind == "member_lost":`:

```python
    elif kind == "vote_tie":
        raw_between = data.get("between")
        between = [str(p) for p in raw_between] if isinstance(raw_between, list) else []
        raw_votes = data.get("votes")
        votes = ([v for v in raw_votes if isinstance(v, dict)]
                 if isinstance(raw_votes, list) else [])
        joined = (", ".join(between[:-1]) + f" and {between[-1]}" if len(between) > 1
                  else "".join(between))
        headline = f"Team {name}'s vote between {joined} tied"
        details = []
        for pid in between:
            backers = [str(v.get("label")) for v in votes if v.get("proposal_id") == pid]
            n = len(backers)
            details.append(f"{pid}: {n} vote{'' if n == 1 else 's'}"
                           + (f" ({', '.join(backers)})" if backers else ""))
        if not votes:
            details.append("No member could vote (each candidate was its voter's own), "
                           "or nobody voted.")
        details.append("The notes behind each vote are on the board. Next: adopt_proposal to "
                       "choose one, post_board to run one more round, or disband_team.")
```

- [ ] **Step 5: Controller — iteration cap and live progress**

`agentd/chat/controller.py`, in the activation-start block, replace the `cap = …` line:

```python
            voting = deliberating and bool(team_row.vote_ids)
            cap = 15 if voting else 40 if deliberating else 25 if reviewing else ctx.max_iters
```

and update the comment above it to mention vote rounds (`… deliberation activations are capped at 40 iterations (15 in a vote round) and review activations at 25 (§3.11)`).

In `_round_progress`, change the return to:

```python
    return {"round": team.round, "members": labels,
            "reported": [lb for lb in labels if last.get(lb) == "wrapped_up"],
            "vote": team.vote_ids or None}
```

- [ ] **Step 6: Run the team and controller tests**

Run: `.venv/bin/pytest tests/test_team_*.py tests/test_controller_delegated_hint.py tests/test_subagent*.py`
Expected: PASS. If an existing test asserts the exact `round_progress` dict, add `"vote": None` to its expected value.

- [ ] **Step 7: Commit**

```bash
git add agentd/teams/coordinator.py agentd/teams/milestones.py agentd/chat/controller.py tests/test_team_coordinator.py tests/test_team_milestones.py
git commit -m "feat(teams): the coordinator runs and tallies the vote round"
```

---

### Task 7: Frontend — vote posts, verdicts, progress

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`roundProgress`)
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (`toTeamLive`)
- Modify: `apps/editor-client/test/team-activity-contracts.test.ts`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (`TeamRoundProgressView`)
- Modify: `apps/vscode-extension/webview-ui/src/teams.ts` (`roundProgressText`)
- Modify: `apps/vscode-extension/webview-ui/src/teamJourney.ts` (stance kinds, verdict fields)
- Modify: `apps/vscode-extension/webview-ui/src/components/teams/Journey.tsx` (`StanceReply`, `SaidPost`)
- Modify: `apps/vscode-extension/webview-ui/src/components/teams/RoundItems.tsx` (`Verdict`)
- Modify: `apps/vscode-extension/webview-ui/src/test/teams.test.ts`, `test/teamJourney.test.ts`, `test/roundItems.test.tsx`

**Interfaces:**
- Consumes: `/live` `round_progress.vote`; `round_ended` payload `vote` and `qualifying` (Task 6, Task 2); post kind `vote`.
- Produces: `TeamRoundProgressView.vote?: string[] | null`; verdict item fields `vote: { counts: Record<string, number>; winner: string | null } | null` and `tied: string[]`.

- [ ] **Step 1: Write the failing tests**

`apps/editor-client/test/team-activity-contracts.test.ts` — in `maps /live round_progress, null when absent`, change the expectation to:

```ts
    expect(withProgress.teams![0].roundProgress).toEqual(
      { round: 2, members: ["alice", "bob"], reported: ["alice"], vote: null });
```

and add after it:

```ts
    const voting = await live([{ ...base, round_progress: {
      round: 3, members: ["bob"], reported: [], vote: ["P2", "P3"] } }]).getThreadLiveState("t");
    expect(voting.teams![0].roundProgress?.vote).toEqual(["P2", "P3"]);
```

`apps/vscode-extension/webview-ui/src/test/teams.test.ts` — in `round progress on the card`, add:

```ts
    expect(roundProgressText({ round: 3, members: ["alice", "bob"], reported: [], vote: ["P2", "P3"] },
                             team, agents, now)?.text)
      .toBe("Round 3 · vote between P2 and P3 · 0 of 2 reported · waiting on bob (42s)");
```

`apps/vscode-extension/webview-ui/src/test/teamJourney.test.ts` — append inside `describe("buildJourney", …)`:

```ts
  it("a vote is a reply, and a re-vote replaces the earlier one", () => {
    const votes = [
      post(5, 5, { author: "impl", kind: "vote", refId: "P2", round: 3 }),
      post(6, 6, { author: "impl", kind: "vote", refId: "P3", round: 3 }),
    ];
    const items = buildJourney([KICKOFF, ...votes], [PHASE], ROSTER);
    const stances = items.filter((i) => i.kind === "stance");
    expect(stances.map((i) => i.kind === "stance" && i.replaces)).toEqual([null, 5]);
  });

  it("a vote round's verdict carries the tally; a tied round lists the tie", () => {
    const tied = ev(2, 10, "team", "round_ended", { activation: null, payload: {
      round: 2, adopted: null, qualifying: ["P2", "P3"], proposals: [], new_posts: 0 } });
    const voted = ev(3, 20, "team", "round_ended", { activation: null, payload: {
      round: 3, adopted: "P3", proposals: [], new_posts: 2,
      vote: { between: ["P2", "P3"], counts: { P2: 1, P3: 2 }, votes: [], winner: "P3" } } });
    const verdicts = buildJourney([KICKOFF], [PHASE, tied, voted], ROSTER)
      .filter((i) => i.kind === "verdict");
    expect(verdicts.map((v) => v.kind === "verdict" && v.tied)).toEqual([["P2", "P3"], []]);
    expect(verdicts.map((v) => v.kind === "verdict" && v.vote)).toEqual(
      [null, { counts: { P2: 1, P3: 2 }, winner: "P3" }]);
  });
```

`apps/vscode-extension/webview-ui/src/test/roundItems.test.tsx` — append inside `describe("round items", …)`:

```tsx
  it("verdicts: a tie goes to a vote, then the vote's result", () => {
    const { rerender } = wrap(<Verdict roster={["alice", "bob"]} item={{
      kind: "verdict", key: "a4", at: "x", round: 2, adopted: null, nextRound: 3, newPosts: 0,
      tied: ["P2", "P3"], vote: null,
      proposals: [{ id: "P2", stances: { alice: "agree", bob: "agree" }, adopted: false },
                  { id: "P3", stances: { alice: "agree", bob: "agree" }, adopted: false }] }} />);
    expect(screen.getByTestId("verdict-2")).toHaveTextContent("P2 and P3 tied — round 3 is a vote");
    rerender(<Verdict roster={["alice", "bob"]} item={{
      kind: "verdict", key: "a5", at: "x", round: 3, adopted: "P3", nextRound: null, newPosts: 0,
      tied: [], vote: { counts: { P2: 1, P3: 2 }, winner: "P3" }, proposals: [] }} />);
    expect(screen.getByTestId("verdict-3")).toHaveTextContent("Round 3 ended · vote: P2 1 · P3 2 — P3 adopted");
    rerender(<Verdict roster={["alice", "bob"]} item={{
      kind: "verdict", key: "a6", at: "x", round: 3, adopted: null, nextRound: null, newPosts: 0,
      tied: [], vote: { counts: { P2: 1, P3: 1 }, winner: null }, proposals: [] }} />);
    expect(screen.getByTestId("verdict-3")).toHaveTextContent("vote: P2 1 · P3 1 — tied, the main agent decides");
  });
```

Also add `tied: [], vote: null,` to the two existing `Verdict` items in `verdicts: not adopted with the next round, then adopted` (the item type gains required fields).

- [ ] **Step 2: Run them to verify they fail**

Run (from repo root):

```bash
npm run -w @crucible/editor-client test -- team-activity-contracts
cd apps/vscode-extension/webview-ui && npx vitest run src/test/teams.test.ts src/test/teamJourney.test.ts src/test/roundItems.test.tsx; cd -
```

Expected: FAIL (missing `vote` / `tied` fields and texts).

- [ ] **Step 3: editor-client**

`apps/editor-client/src/contracts/task-contracts.ts`, `roundProgress`:

```ts
  roundProgress: z.object({
    round: z.number(), members: z.array(z.string()), reported: z.array(z.string()),
    // A vote round's candidates (spec 2026-10-07 §7), null otherwise.
    vote: z.array(z.string()).nullable().default(null),
  }).nullable().default(null),
```

`apps/editor-client/src/client/http-backend-client.ts`, in `toTeamLive`:

```ts
      roundProgress: rp ? { round: rp["round"], members: rp["members"] ?? [],
                            reported: rp["reported"] ?? [], vote: rp["vote"] ?? null } : null,
```

Run `npm run -w @crucible/editor-client build` (the extension types off `dist/`).

- [ ] **Step 4: Webview types and progress text**

`apps/vscode-extension/webview-ui/src/types.ts`:

```ts
export interface TeamRoundProgressView {
  round: number;
  members: string[];
  reported: string[];
  vote?: string[] | null;
}
```

`apps/vscode-extension/webview-ui/src/teams.ts`, in `roundProgressText` replace the `let text = …` line:

```ts
  const vote = progress.vote?.length
    ? ` · vote between ${progress.vote.length > 1
        ? `${progress.vote.slice(0, -1).join(", ")} and ${progress.vote[progress.vote.length - 1]}`
        : progress.vote[0]}`
    : "";
  let text = `Round ${progress.round}${vote} · ${done} of ${progress.members.length} reported`;
```

- [ ] **Step 5: Journey — vote posts and verdict fields**

`apps/vscode-extension/webview-ui/src/teamJourney.ts`:

```ts
const STANCE_KINDS = new Set(["agree", "object", "withdraw", "vote"]);
```

In the `JourneyItem` union, the verdict variant becomes:

```ts
  | { kind: "verdict"; key: string; at: string; round: number; proposals: VerdictProposal[];
      adopted: string | null; nextRound: number | null; newPosts: number;
      tied: string[]; vote: { counts: Record<string, number>; winner: string | null } | null }
```

In `verdictItem`, before `return`:

```ts
  const rawVote = e.payload.vote as { counts?: Record<string, number>; winner?: string | null } | undefined;
  const vote = rawVote ? { counts: rawVote.counts ?? {}, winner: rawVote.winner ?? null } : null;
  const tied = Array.isArray(e.payload.qualifying) && e.payload.qualifying.length > 1
    ? (e.payload.qualifying as string[]) : [];
```

and add `tied, vote,` to the returned object.

In `buildJourney`'s `postItem`, replace the `earlier` computation so a re-vote replaces the member's earlier vote in that round (a different `refId`):

```ts
      const earlier = posts.filter((q) => q.seq < p.seq && q.author === p.author && (
        p.kind === "vote"
          ? q.kind === "vote" && q.round === p.round
          : q.refId === p.refId && STANCE_KINDS.has(q.kind) && q.kind !== "vote"));
```

`apps/vscode-extension/webview-ui/src/components/teams/Journey.tsx`, in `StanceReply` replace the `tone` and `verb` lines:

```tsx
  const tone = post.kind === "agree" ? "var(--color-green)" : post.kind === "object" ? "var(--color-red)"
    : post.kind === "vote" ? "var(--color-accent-ink)" : "var(--color-text-3)";
  const verb = post.kind === "agree" ? "agrees with" : post.kind === "object" ? "objects to"
    : post.kind === "vote" ? "votes for" : "withdraws";
```

and the mark span:

```tsx
          <span style={{ color: tone }}>{post.kind === "agree" ? "✓ " : post.kind === "object" ? "✗ " : post.kind === "vote" ? "★ " : ""}</span>
```

In `SaidPost`, extend the stance condition:

```tsx
  if (post.kind === "agree" || post.kind === "object" || post.kind === "withdraw" || post.kind === "vote") {
```

- [ ] **Step 6: Verdict rendering**

`apps/vscode-extension/webview-ui/src/components/teams/RoundItems.tsx`, in `Verdict`, after the `<strong>Round {item.round} ended</strong>` line insert:

```tsx
      {item.vote && (
        <span>
          {` · vote: ${Object.entries(item.vote.counts).map(([id, n]) => `${id} ${n}`).join(" · ")} — `}
          {item.vote.winner
            ? <strong style={{ color: "var(--color-green)" }}>{item.vote.winner} adopted</strong>
            : "tied, the main agent decides"}
        </span>
      )}
```

change `{item.proposals.length === 0 && …}` to `{item.proposals.length === 0 && !item.vote && …}`, and after the `item.proposals.map(…)` block insert:

```tsx
      {item.tied.length > 1 && item.nextRound !== null && (
        <span className="text-text-3">
          {` · ${item.tied.slice(0, -1).join(", ")} and ${item.tied[item.tied.length - 1]} tied — round ${item.nextRound} is a vote`}
        </span>
      )}
```

Then make the existing "round N next" line skip ties: change `{!yes && item.nextRound !== null && (` to `{!yes && item.nextRound !== null && item.tied.length < 2 && (`, and change `{!yes && item.nextRound === null && (` to `{!yes && item.nextRound === null && !item.vote && (`.

- [ ] **Step 7: Run the frontend checks**

```bash
npm run -w @crucible/editor-client test
npm run -w @crucible/editor-client build
npm run -w crucible-vscode-extension typecheck
cd apps/vscode-extension/webview-ui && npx vitest run && npx tsc --noEmit; cd -
npm run -w crucible-vscode-extension test
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/editor-client apps/vscode-extension/webview-ui/src
git commit -m "feat(webview): show votes, vote verdicts and the vote round's progress"
```

---

### Task 8: Full verification, docs, live run

**Files:**
- Modify: `CLAUDE.md` (Agent teams bullet)

- [ ] **Step 1: Full Python suite, lint, types**

```bash
cd services/agentd-py
.venv/bin/pytest --color=no > /tmp/claude-vote-full.txt 2>&1; echo exit=$?; tail -5 /tmp/claude-vote-full.txt
.venv/bin/ruff check agentd/teams agentd/chat/controller.py agentd/chat/controller_prompts.py tests/test_team_*.py
.venv/bin/mypy agentd/teams
```

Expected: the only failure, if any, is the pre-existing `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies` ("total budget exceeded"; fails on the base commit too). ruff and mypy report nothing in the touched files that wasn't there before (compare with `git stash` if unsure).

- [ ] **Step 2: Document it**

In `CLAUDE.md`, in the "Agent teams — foundations" paragraph, replace `` `teams/adoption.py::evaluate_round` adopts at round end (open, posted before the round, every quorum member's latest stance `agree`; lowest seq wins) `` with:

```markdown
`teams/adoption.py::evaluate_round` adopts at round end when exactly one proposal qualifies (open, posted before the round, every quorum member's latest stance `agree`); **two or more → a vote round** (spec `docs/superpowers/specs/2026-10-07-team-vote-round-design.md`): `TeamState.vote_between` (persisted `teams.vote_between`), voters = quorum members with a candidate they did not write, vote-only (`team_vote` or the report's single `vote` field; propose/agree/object/withdraw refused), capped at 15 iterations, not counted toward `max_rounds`; `tally_votes` → unique top count adopted, tie / no votes → `DEADLOCKED` + `vote_tie` milestone (main agent uses `adopt_proposal`). Reads during a round (`status_text`, `team_read`, the drain) stop at the persisted `teams.round_cutoff_seq`, and a stance on a proposal from the current round is refused
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: the team vote round"
```

- [ ] **Step 4: Live run**

Restart the dev host's managed backend (**Developer: Reload Window**, then **Developer: Reload Webviews** after the rebuild), open a fresh thread in `workspaces/chatgpt-smoke` on the ChatGPT plan, and send: `design a tetris game` with a team (the prompt used in the Tetris run). Then check, in `.crucible/state/artifacts/chat/<thread>/<turn>/teams/<team>/coordinator.jsonl` and the board:

1. No `agree`/`object` post references a proposal whose `round` equals the post's own `round` (`sqlite3 .crucible/state/chat.sqlite3 "select s.seq, s.ref_id from team_posts s join team_posts p on p.team_id=s.team_id and ('P'||p.seq)=s.ref_id where s.kind in ('agree','object') and s.round=p.round and s.team_id='<team>'"` returns nothing).
2. If a round ended with several qualifying proposals: the trace has an `evaluation` with `qualifying` ≥ 2, then a `vote` record, and the board shows `★ votes for` replies and the vote verdict.
3. Report the requests/input/cached usage beside run 4's (43 requests, 657k input, 80–83% cached).

Report the result to the user; a tie may not occur in one run — say so if it didn't.
