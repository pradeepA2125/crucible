# Team vote round + round-bounded visibility — design

> **Superseded** by `docs/superpowers/specs/2026-10-07-team-lead-proposer-design.md` (the vote round was reverted in 450b881; only round-bounded visibility, 424445b, shipped).

Date: 2026-10-07 · Branch: `feat/chatgpt-plan` · Extends spec v2 §8.3 (`2026-10-02-subagents-v2-design.md`)

## 1. Problem

**Ties are decided by post order.** Adoption (`teams/adoption.py::evaluate_round`) adopts the
lowest-`seq` proposal when several qualify. In 3 of the last 6 live team runs a round ended with 2–3
unanimously agreed proposals, and twice a member had said in a note which one it preferred — not the
one adopted:

| Run | Unanimous | Adopted | Stated preference |
|---|---|---|---|
| Snake (run 4, team-71eb8339dbc7) | P2, P4, P5 | P2 | reviewer: "P5 should be the adopted proposal" |
| Tetris (team-85248ddcdd8d) | P4, P5, P7 | P4 | reviewer on P7: "Preferred concrete plan" |
| team-deecc2a55985 | P2, P3 | P2 | — |

Members `agree` with anything acceptable, so agreement carries no preference.

**Same-round posts leak.** §8.3 says posts made in round N reach members at round N+1. Only the
activation's starting inbox honors that (`render_delta_posts(until_seq=cutoff)`). These do not:

- `TeamService.status_text` — the `team_status` tail re-injected every iteration — lists every open
  proposal (asking for a stance on each) and unread counts with no cutoff;
- the `team_read` tool returns everything after `since_seq`;
- `ChatController._drain_member` calls `render_delta_posts` without `until_seq` (only reached when a
  team marker is queued, which deliberation's held posts should prevent — pinned by a test here);
- `prepare_agree` / `prepare_object` accept a stance on a proposal posted in the current round.

Tetris evidence: ui posted P5 at seq 5 (round 1); gameplay posted `agree P5` at seq 6, also round 1.
Same-round agreement is a large part of why several proposals end up unanimous at once.

## 2. Decisions (agreed with the user)

1. Several qualifying proposals → the next round is a **vote** between exactly those.
2. A vote tie (or no votes) → the **main agent decides**, as on a deadlock.
3. **No self-votes**: a member votes among tied proposals authored by others.
4. The vote round is **vote-only**.
5. The vote round does **not** count toward `max_rounds`.
6. A vote is a **single** value per member (no "several preferred" by construction).

## 3. Round-bounded visibility

- `teams` gains `round_cutoff_seq INTEGER` (ALTER migration, default NULL). The coordinator writes
  it when it executes `StartRound` — the same value as its in-memory `_cutoff`.
- While `phase == "DELIBERATING"` (vote rounds included), reads bound posts by others to
  `seq <= round_cutoff_seq`; a member's own posts are always visible:
  - `status_text`: open proposals, stances, unread counts;
  - `team_read`;
  - `_drain_member` passes `until_seq`.
- In every other phase (IMPLEMENTING, REVIEWING, …) delivery stays live and unbounded, as now.
- `_stance_phase` refuses, while DELIBERATING, a stance on a proposal whose `round` equals
  `team.round`: `"P5 was posted this round; you can respond to it next round."` Covers the tools and
  the report's `stances` (both go through `prepare_agree`/`prepare_object`).

## 4. The vote round

### 4.1 Evaluation

`evaluate_round` returns `Evaluation.qualifying: tuple[str, ...]` (every proposal that is open,
eligible and unanimous, seq order) alongside `adopted`. `adopted` is set only when exactly one
qualifies; with two or more, `adopted` is None and each qualifying proposal's `reason` is
`"tied — vote next round"`. The coordinator passes `qualifying` on `RoundEvaluated(tied=…)`.

### 4.2 State machine

`TeamState` gains `vote_between: tuple[str, ...] = ()`, persisted as `teams.vote_between_json`
(TEXT, default `'[]'`) so the service, tools and board can read it.

`RoundEvaluated` in DELIBERATING:

| Condition | Result |
|---|---|
| `vote_between` empty, `adopted` set | adopt (today) |
| `vote_between` empty, `len(tied) >= 2` | `round += 1`, `max_rounds += 1`, `vote_between = tied`; voters = quorum members with ≥ 1 candidate they did not author; if none → tie path; else `StartRound(round, voters)` with `EnterPhase("DELIBERATING", round, "vote")` |
| `vote_between` empty, nothing qualifies | next round / deadlock (today) |

A vote round's end is evaluated by a new pure `tally_votes(posts, vote_between, voters)` in
`teams/adoption.py` (latest `vote` post per voter counts; self-votes cannot exist — refused at
post time) and fed back as `VoteEvaluated(winner: str | None, counts: dict[str, int])`:

- unique top count → `vote_between = ()`, adopt `winner` by `"team"` (approval gate / implementation
  as for any adoption);
- tie or zero votes → `vote_between = ()`, `phase = DEADLOCKED`, `EnterPhase("DEADLOCKED", round,
  "vote tie")`, `Milestone("vote_tie", {candidates, counts, votes: [{label, proposal_id, note}]})`.

Existing DEADLOCKED exits apply unchanged: `adopt_proposal`, `post_board` (one more normal round,
`max_rounds += 1`), `disband_team`. Pause/resume preserve `vote_between` (it is persisted; `_resume`
re-runs the owing voters of the vote round).

### 4.3 Vote-only

In a vote round (`vote_between` non-empty) the service refuses `team_propose`, `team_agree`,
`team_object`, `team_withdraw` (and the report's `stances`/`proposal` fields):
`"This round is a vote between P4, P5 and P7; only votes count."` `team_post`/`team_message`
still work. The candidates therefore cannot be closed or superseded mid-vote. A vote-round
activation is capped at **15** iterations (beside today's 40 deliberation / 25 review caps).

## 5. Member interface

- **Tool** `team_vote(proposal_id, note?)` (added to `MEMBER_TOOL_NAMES`). Re-voting replaces the
  vote (latest counts).
- **Report field** `vote: {proposal_id, note?}` — one object, declared only in the team-member report
  schema (flat and tight, beside `stances`). The main agent's and every non-team child's schema stay
  byte-identical.
- Both post a `vote` post (`POST_KINDS` gains `"vote"`; `ref_id` = candidate, `payload.note`).
- **Validation** (`prepare_vote`, used by the tool and `report_check`): refused outside a vote
  round; refused for a proposal not in `vote_between`; refused for the voter's own proposal —
  `"you can't vote for your own proposal; pick among P4, P7"`.
- **Missing vote**: a voter that reports without having voted this round is redirected once (the
  missing-stance redirect, same rules: not malformed, not spent on the forced-final iteration). On the
  forced-final iteration an invalid vote is dropped and recorded in the coordinator trace; the member
  counts as not voting.
- **Header** (`TeamService._header`, vote round):
  `Round 3 (vote) of 4: P4, P5 and P7 each have everyone's agreement, so the team picks one. Vote
  for the proposal the team should build — not your own — with team_vote or the vote field of your
  report, and a note saying why. Only votes count this round.` followed by one compact card per
  candidate: id, author, the approach's first ~300 chars, assignments with files.
- **Status line**: `vote: P4, P5, P7 — your vote: none` (or the id).
- **Prompt**: one sentence + a short worked example of the `vote` field in `_TEAM_BLOCK`; regenerate
  `tests/goldens/controller_prompt_teams.json`; `controller_prompt_main.json` unchanged.

## 6. Main agent

`milestone_text` gains `vote_tie`, compact (ids and counts only, like every milestone):
`Team X's vote between P4 and P7 tied` / `P4: 1 vote (reviewer)` / `P7: 1 vote (ui)` /
`Next: adopt_proposal to choose one, post_board to run one more round, or disband_team.` The vote
notes stay on the board. It is a wake notice (`source_kind "team"`) like `deadlock`.

## 7. Board, live state, UI

- No new activity kind: the `vote` post itself is the board's record of a vote.
- `round_ended` payload carries `vote: {between, counts, winner | null}` for a vote round.
- `/live` teams: `round_progress` gains `vote: [ids] | null` (already inside `lastLiveSignature`,
  since the whole `teams` array is).
- Webview: `vote` post kind in `teamJourney.ts`/`Journey.tsx` ("ui votes for P7 — note"); the
  round strip reads `Round 3 · vote`; the round verdict shows `P7 adopted by vote (2–1)` or
  `vote tied → main agent`; `TeamStrip` shows `voting: 2 of 3 in`.

## 8. Testing

- Pure: `evaluate_round` qualifying list + reasons; `tally_votes` (unique winner, tie, zero votes);
  state machine (2+ qualify → vote round with `max_rounds + 1` and the right voters; win → adopt /
  approval gate; tie → DEADLOCKED + `vote_tie`; no eligible voter → tie path; pause/resume mid-vote).
- Service: `prepare_vote` refusals; vote-only refusals; cutoff-bounded `status_text`/`team_read`;
  same-round stance refused; `_drain_member` bounded.
- Loop: report `vote` field — invalid refused, missing redirected once, forced-final drop.
- Goldens: teams regenerated, main unchanged.
- Webview: vote post rendering, round strip, verdict.
- Live: the Tetris prompt on the ChatGPT plan; check (a) no same-round stances in the board, (b) a
  tie becomes a vote, (c) the trace's evaluation names the vote result.

## 9. Out of scope

Ranked/approval voting; letting members object during a vote; carrying votes across rounds.
