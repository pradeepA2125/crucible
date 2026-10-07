# Team lead proposer — design

Date: 2026-10-07 · Branch: `feat/chatgpt-plan` · Changes spec v2 §8.3 (`2026-10-02-subagents-v2-design.md`)
Supersedes: `2026-10-07-team-vote-round-design.md` (the vote round; reverted in 450b881). Keeps its §3,
round-bounded visibility, which shipped in 424445b.

## 1. Problem

Every member can propose, and members `agree` with anything acceptable. In 3 of the last 6 live team runs
a round ended with 2–3 unanimously agreed proposals; adoption took the lowest `seq`, and twice a member had
said in a note it preferred another (Snake run 4: P2/P4/P5, reviewer wanted P5; Tetris: P4/P5/P7, reviewer
preferred P7). Authors also left their own earlier drafts open next to their revisions.

A vote, a narrowing round and a merge round were each considered and rejected: a vote picks without
weighing flaws; merges written in parallel (members cannot see same-round posts) compete with each other.

## 2. Decision

One member — the **lead**, chosen by the main agent in `create_team` — is the only one who proposes. The
others review: agree (with a note for a small change), object with evidence, post, message. There is at
most one open proposal at a time, so ties cannot happen. Deliberation is the lead revising one plan until
everyone agrees, or the round limit (DEADLOCKED → the main agent decides, unchanged).

## 3. Behavior

### 3.1 Creating a team

- `create_team` gains a **required** `lead: <member label>`; it must be a roster label whose agent can
  propose (any agent can — proposing is not editing). Missing/unknown → `TeamInputError`
  `"lead must be one of the members: alice, bob"`.
- `teams` gains `lead TEXT` (ALTER migration, nullable; `TeamRecord.lead: str | None = None`). A team row
  with no lead (none exist after a restart — live teams are reaped) keeps the old any-member behavior.
- A `post` kickoff whose mentions leave out the lead gets the lead added to its mentions, so round 1
  always starts the lead.

### 3.2 Proposing

- `prepare_propose` (tool and report field): an author other than the lead is refused —
  `"Only <lead> (the team's lead) proposes. Suggest your change with a note on your stance, an objection
  with evidence, or a post."`
- The lead's new proposal **closes every other open proposal** as `superseded` (only the lead's previous
  draft and the main agent's kickoff can be open). The `supersedes` argument is no longer needed: it is
  removed from the `team_propose` tool and from the report's `proposal` field; a payload's `supersedes`
  records what was closed, as now.
- The lead's "state your stance on P<n> first" check is dropped — its new proposal replaces those.

### 3.3 Reviewing

Unchanged rules for everyone else: stances on the open proposal from an earlier round, the
missing-stance redirect, same-round stances refused (424445b). Adoption is unchanged (exactly one open
proposal can qualify now). `evaluate_round`'s "lowest seq wins" stays as dead-safe code.

### 3.4 Losing the lead

If the lead leaves the quorum (`failed`, or a user ■) while the team is DELIBERATING, the team **pauses**
with reason `lead lost` (like `quorum lost`), with a `paused` milestone. `resume_team` restores the quorum,
the lead included (existing `_restore_quorum`). In other phases the lead is an ordinary member.

### 3.5 What members read

- Brief (system prompt): the roster line of the lead reads `- alice (lead): …`, and one line follows the
  roster: `alice is the lead: only alice proposes; the others review.` (cache-stable — set at creation).
- Round headers (`_header`):
  - round 1, `post` kickoff — lead: `Round 1 of N: the main agent asked for a plan, and you are the lead.
    Check the code your role covers, then propose (team_propose, or the proposal field of your report).`
    Others: `Round 1 of N: <lead> is drafting the plan. Check the code your role covers and post what the
    plan must account for — <lead> reads it next round.`
  - round 1, `proposal` kickoff: unchanged lead-in, plus for the lead: `As the lead, revise it with
    team_propose if it needs changes; your proposal replaces it.`
  - no open proposal (after approval feedback or a withdrawal): lead → `propose a revised plan`; others
    → `<lead> revises the plan; post what it must account for`.
  - otherwise: unchanged; for the lead, add `Revise with team_propose when an objection or note calls for
    it — your new proposal replaces the current one.`
- `_TEAM_BLOCK` (member prompt): the `team_propose` sentence becomes `The lead (named in your brief) sets
  out the approach with team_propose; a new proposal from the lead replaces the open one. Everyone else
  shapes it with stances, notes and objections.`; the report bullet's `when you were asked to propose`
  becomes `when you are the lead`; the "a proposal that is close: agree with a note" example stays.
- `_TEAMS_MAIN_BLOCK` / `create_team` description: `lead` is the member who writes and revises the plan;
  pick the member whose role owns the overall design.

### 3.6 UI

- `summary`/`/live`/team routes carry `lead`; editor-client `TeamSummary`/`TeamLive` gain
  `lead: string | null`; the team card's roster and the board's member tabs show a `lead` badge.

## 4. Testing

- Validation: `parse_create_team` requires a known `lead`; a `post` kickoff gains the lead in mentions.
- Service: a non-lead's propose (tool and report field) is refused with the exact text; the lead's
  proposal closes every open proposal; the lead needs no stance on the kickoff first; no-lead teams keep
  the old behavior.
- State machine: the lead lost during DELIBERATING → `PAUSED` / `lead lost`; another member lost → as
  today; resume restores the lead.
- Prompts: teams golden regenerated (`teams_main`, `teams_member`); main golden unchanged.
- Webview: the lead badge.
- Live: the Tetris prompt — one open proposal at a time on the board, adoption without a tie.

## 5. Out of scope

Handing the lead role to another member; a lead per phase.
