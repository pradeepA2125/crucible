# Team activity UI — the journey view

Status: approved design, 2026-10-05. Builds on sub-agents v2 (`2026-10-02-subagents-v2-design.md`, rev 11) §7–§9.
Visual reference (approved): `.superpowers/brainstorm/teams-activity-ui/content/team-activity-ui.html`
(published as an artifact; its "mid round 2" / "implementing" switch shows both moments).

## 1. Problem

A team's members wake, take up input, post, message, state stances, report and go idle — and today the user
sees almost none of it as a story:

- The board is one flat list of posts with equal weight; a stance is a full post detached from its proposal.
- Nothing records **why** a member woke or **what it was handed**. A report reaches the board only as a bare
  `"<label> finished (completed)"` system line; the report text lives on the agent row.
- Those `finished` lines are system **posts**, so they are rendered into the other members' input deltas —
  noise for the agents (a Phase 4 defect).
- A member's tab is a flat transcript under a "TASK FROM PARENT" header.
- Post bodies render as raw text (markdown asterisks, no code styling).

Goal: a user watching a team can tell, at a glance and without reading logs, for each member: when it was
woken and by what, when it took up work and with what input, what it posted, messaged or decided, when it
went idle, and what it reported — on the board (the team's story), on each member's tab (that agent's
story), and on the transcript card.

## 2. Principles

1. **The board reads as a journey.** A spine with chapters; the user can scroll it as "main proposed P1 →
   both members woke → review dug in and objected → round 2 → adopted → impl implemented".
2. **Three visual weights.** Primary: posts and proposals (cards). Secondary: stances (slim replies linked to
   their proposal). Tertiary: lifecycle beats (small, muted, on the spine). Nothing else competes.
3. **Fold a cause into what caused it.** A wake caused by a visible post is a footer on that post, not its own
   line. A beat stands alone only when its cause is not visible (hidden DM, leftover input, a cap, a restart).
4. **One beat per group event.** A round start is one strip for all members, never N "woke" lines.
5. **Facts in the backend, wording in the UI.** The backend records what happened; the webview decides how it
   reads. Phase 5's coordinator writes into the same record.
6. **Identity is stable.** Each member has one avatar (initial + hue) and name everywhere: board, tabs, card,
   window header.

## 3. Phasing

| Part | Lands |
|---|---|
| Activity record (§4), channel + backfill (§5), `/live` additions (§5.3) | **now** (phase 4.5) |
| Board journey: spine, Kickoff + phase chapters, post cards with markdown, stance replies + tally, cause footers, wrap-up rows, standalone beats, gap dividers, "now" strip, filters (§6) | **now** |
| Member tab: profile header, activation chapters (§7) | **now** |
| Transcript card: member state rows, latest line, counts (§8) | **now** |
| Removing the `finished` system posts; the wake-cap system post becomes an activity event | **now** |
| Round strips, round verdicts, "held for round N" footers, adoption line, assignment rows, Edits section, round-keyed chapters (§9) | **with Phase 5** (the coordinator) |

Phase 4's interim activation policy (immediate wakes) stays until Phase 5 replaces it; everything built now
renders it truthfully because it renders recorded facts.

## 4. The activity record

### 4.1 Storage

A new table on the chat database connection, next to the team tables (`agentd/teams/store.py`):

```
team_activity(
  team_id     TEXT NOT NULL,
  aseq        INTEGER NOT NULL,      -- per-team, monotonic, from 1; independent of post seq
  at          TEXT NOT NULL,         -- ISO-8601 UTC
  label       TEXT NOT NULL,         -- member label, or "main" / "team" for team-level events
  kind        TEXT NOT NULL,         -- §4.2
  activation  INTEGER,               -- the member's activation number the event belongs to, when it has one
  cause_seq   INTEGER,               -- the board post that caused it, when one did
  payload     TEXT NOT NULL,         -- JSON, kind-specific
  PRIMARY KEY (team_id, aseq)
)
```

`aseq` is separate from post `seq` on purpose: post seq is the delivery cursor (`delivered_seq`) and the
proposal id (`P<seq>`); activity must never shift either. Activity is **never** rendered into a member's
input (`render_delta`, `team_read`, `status_text` ignore it).

### 4.2 Event kinds (now)

| kind | label | written when | payload |
|---|---|---|---|
| `phase` | `team` | the team is created (`KICKOFF`→`DELIBERATING`), and on every phase change (`DISBANDED`, `FAILED`; Phase 5 adds the rest) | `{phase, round, reason?}` |
| `woke` | member | an idle member is activated | `{cause, by?, post_seq?}` — `cause` ∈ `kickoff · mention · team_mention · message · main_post · leftover` |
| `notified` | member | a post concerns a member that is already running (a marker goes to its inbox) | `{cause, by, post_seq}` |
| `took_up` | member | an activation's input is built | `{posts: [seq…], from: [label…]}` (empty list = nothing new) |
| `picked_up` | member | a mid-work drain rendered new posts | `{posts: [seq…], from: [label…]}` |
| `wrapped_up` | member | an activation ends (report, failure, stop) | `{status, report, duration_ms, tools, posts, messages, stances, files_changed: [path…]}` |
| `capped` | member | a wake was refused by the wake cap | `{wakes, cap}` |

Notes:

- `cause` is derived at the wake site from the triggering post: recipient set → `message`; author `main` →
  `main_post`; `team` in mentions → `team_mention`; the member's label in mentions → `mention`; the team's
  first activation → `kickoff`; a re-activation from leftover inbox items → `leftover` (no `post_seq`).
- `woke` and `notified` carry `cause_seq` = the post's seq, so the UI can fold them under that post.
- `took_up` lists exactly the posts the rendered delta covered (the same set `delivered_seq` advanced over).
  `render_delta` therefore returns the covered seqs in addition to the text and top seq.
- `wrapped_up.report` is the member's full report (capped at 20 000 chars with a marker). `tools`, `posts`,
  `messages`, `stances` count only this activation. `duration_ms` is the activation's wall time.
- `wrapped_up` is written for every member activation end, including `stopped` (disband, ■) and `failed`.
  The restart reap writes `wrapped_up {status: "failed", report: "", reason: "backend restarted"}` for each
  member that was live, then a `phase` event `FAILED`.
- `capped` replaces today's wake-cap **system post**; the `"<label> finished (…)"` system posts are removed
  (their information is in `wrapped_up`). `"The team was disbanded."` stays a system post (board news).

### 4.3 Writers (now)

All writes go through one `TeamService.record(team_id, label, kind, *, activation, cause_seq, payload)` that
appends the row and calls an `on_activity(team, event)` callback (the controller broadcasts it, §5.2).
Sites in `ChatController`:

- `_create_team` → `phase` (DELIBERATING, round 1).
- `_wake_member` → `woke` (idle path, before enqueue) or `notified` (running path) or `capped`.
- `_on_leftover` (team member) → `woke {cause: "leftover"}`.
- `_activate` (member branch) → `took_up`, with `activation` = the activation number it computes.
- `_drain_member` → `picked_up` when it rendered posts.
- `_route_report` → `_team_member_reported` → `wrapped_up` (replaces the system post). Written even when the
  team has ended (a disband's stopped activations still close their chapter).
- `disband_team` → `phase` (DISBANDED).
- `reap_subagents` → `wrapped_up` failed per live member + `phase` (FAILED).

The divider message `_activate` already writes into a member's transcript for activation > 1 gains
`metadata.activation = <n>`, and activation 1 gets one too, so the member tab can cut chapters (§7).

## 5. Transport

### 5.1 Routes

`GET /chat/threads/{id}/teams/{team_id}` gains `activity: [event…]` (all, oldest first) and `last_aseq`.
No new route.

### 5.2 Team channel

New event on `chat:{thread}:team:{team}`: `team_activity {aseq, payload: {event}}`. `team_phase` stays (the
header updates from it); `team_post` unchanged. `TeamViewManager` keeps two cursors — `lastSeq` (posts) and
`lastAseq` (activity) — backfills both via `getTeam`, and skips any live event at or below its cursor.

### 5.3 `/live` teams

Each live team gains slow-changing fields that change on events, never on model calls:

- `members[].last`: `{kind, at, cause_seq?, by?, status?}` — the member's latest activity event, for the
  card's state phrase.
- `latest`: `{kind: "post" | "activity", label, text (≤ 140 chars), at}` — the most recent post or
  `wrapped_up`/`phase` event, for the card's "latest" line.
- `counts`: `{posts, proposals: [{id, agree, object, pending}]}`.

They stay in `lastLiveSignature` (the dedup invariant). The live "working · read_file …" phrase comes from
the existing `/live` `agents` rows (`now`), not from teams.

## 6. Board (team window, Board tab)

A pure builder (`webview-ui/src/teams/journey.ts`) turns `posts + activity + roster` into an ordered list of
journey items; components only render items. All folding rules live in the builder and are unit-tested.

- **Spine and chapters.** A vertical line on the left with chapter nodes. Chapters come from `phase` events:
  `Kickoff` (round-0 posts), `Round N · deliberating`, `Implementing`, `Review`, `Done`/`Disbanded`/`Failed`.
  Done chapters get a green node; the current one violet. Chapter header shows its time span.
- **Posts** (primary): card with author avatar + name, `P<n>` badge for proposals or `#n` for posts, kind,
  time; body rendered with `MarkdownContent`, folded to ~6 lines with "Show more"; `@mentions` highlighted.
  Proposals add an **assigns** row (when assignments exist) and a **stance tally** (one chip per member:
  ✓ / ✗ / pending; a changed stance shows `(was ✗)`); clicking a chip jumps to that stance.
- **Stances** (secondary): agree/object posts render as slim replies (`↳ review ✗ objects to P1 #4`) with the
  note or reason and expandable evidence; a later stance from the same member shows `replaces #4`.
  Clicking the proposal id jumps to the proposal.
- **Cause footers.** `woke`/`notified` events whose `cause_seq` is a visible post become that post's footer:
  `⚡ woke review · impl`, `↪ queued for impl · it was working — picks this up at its next step`.
- **Standalone beats** (tertiary): `took_up` (`▶ impl took up #5 · 1 new post`), `picked_up`
  (`↪ impl picked up #9 while working`), `capped`, and a `woke` whose cause is not visible (leftover; a DM
  while the Messages filter is off — `⚡ review woke — direct message from impl #9`).
- **Wrap-up rows:** one per `wrapped_up`: avatar, name, `wrapped up`, status chip, `duration · tools · posts`,
  and `report ▸` expanding the full report as markdown.
- **Gap dividers:** `· N min later ·` between items more than 60 s apart.
- **"Now" strip** pinned at the bottom: one line per running member (`● impl is working — read_file
  shop/cart.py · 32s`, from `/live` agents) and one dimmed line per idle member.
- **Filters:** Posts · Activity · Messages (Messages off by default). Cause footers stay with their post when
  Activity is off. Times are relative with the exact time in a tooltip.
- **Identity:** avatar = first letter (two letters on a collision) on a hue from a fixed member palette
  (sky, fuchsia, teal, orange, lime, rose — distinct from the semantic green/red/amber), assigned by roster
  order; `main` is violet with ✦; `system` is grey.

## 7. Member tab

- **Profile header** (replaces "TASK FROM PARENT"): large avatar with a state ring (pulsing while working,
  grey when idle), name, definition chip, state pill (working · idle · waiting on a teammate · failed ·
  stopped), the definition's description, stats (chapters · wakes used `n/15` · tools · posts · messages ·
  active time), stance chips per open proposal, and (Phase 5) the assignment.
- **Chapters**, one per activation, on a small spine, the latest open and earlier ones collapsed to their
  header and the first line of their report. Header: `Chapter n · <why>` — `kickoff — main's proposal P1`,
  `woken by review's post #5`, `woken by impl's message #9`, `leftover input` — then start time, duration and
  status chip (or `● working · 32s`).
- **Inside a chapter**, in order: **Handed** (post chips from `took_up`; click jumps to the board), **Work**
  (the activation's tool pills folded to `Explored · N tools ▸`), mid-work `picked_up` beats, **Said** (its
  posts, messages and stances in this activation, rendered as on the board), and the **report card**
  (`wrapped_up`). A running chapter ends with the live "now" line instead.
- Chapters are cut from the transcript at the divider messages (`metadata.activation`) and joined to activity
  events by `activation` number; posts are assigned to a chapter by their time falling inside it.

## 8. Transcript card

- Header: avatar ✦ + team name + **Open board**; a mini phase stepper.
- One row per member: avatar with state ring, name, definition chip, and a state phrase from `members[].last`
  or the `/live` agents row: `⚡ woken by review #5 · 12s`, `● working · read_file shop/cart.py · 32s`,
  `✓ reported · 2m ago`, `💤 idle`, `✗ failed — backend restarted`; right side `n ch · n tools`.
- A **latest** line (`review agreed with P1 · 1m ago`) and **counts** (`9 posts · 1 proposal · P1 2 ✓ ·
  budget 60 requests`).
- A paused team shows its reason; an ended team shows its final phase and no live elements.

## 9. With Phase 5 (recorded here so the coordinator emits what the UI needs)

New activity kinds, written by the coordinator:

| kind | payload | renders as |
|---|---|---|
| `round_started` | `{round, members: [{label, handed: [seq…]}]}` | one strip: `Round 2 started — both members woke`, a row per member with handed post chips and live status, a progress bar `1 of 2 reported · waiting on impl (42s)` |
| `round_ended` | `{round, proposals: [{id, stances: {label: agree/object/none}}], adopted?}` | a verdict line: `P1: impl ✓ review ✗ — not adopted · round 2 next, with 3 new posts` or `… — adopted` |
| `held` | `{post_seq, for: [label…], until_round}` | the post footer `→ for impl · held for round 2 — nothing reaches a member mid-round` |
| `requeued` | `{retry, of, after_ms}` | a member beat `↻ impl restarted after a provider error (retry 1 of 2)` |
| `deadline` | `{}` | `⏱ impl hit the round's time limit — reporting what it has` |
| `phase` | adds `IMPLEMENTING`, `REVIEWING`, `AWAITING_APPROVAL`, `DEADLOCKED`, `PAUSED`, `DONE` with `reason` | chapter nodes; `Adopted P1` system line with assignment rows |

During deliberation, members get no `woke` events (round strips replace them); during implementation the
§4 kinds apply unchanged. Member-tab chapters are keyed `Round n` in deliberation and by wake in
implementation; an implementation chapter adds an **Edits** section (files, `+/-`, applied / editing). The
card adds the round's progress bar.

## 10. Error handling and edge cases

- Activity writes are best-effort: a failure logs a warning and never fails an activation or a post.
- An event referencing a post the viewer cannot see (a DM with Messages off) folds into a standalone beat.
- An event for an unknown label (a removed member) renders with a grey avatar and its label.
- A backfill that races a live event is deduplicated by `aseq`; out-of-order live events are inserted by
  `aseq`.
- Old teams (created before this change) have no activity: the board renders posts only, members get a
  single chapter from the transcript, and the card falls back to agent row statuses.

## 11. Testing

- **Backend:** store round-trip and per-team `aseq`; each writer site emits the right kind, `cause`,
  `cause_seq`, `activation` (wake by mention / DM / main post / team / leftover; notified while running;
  took_up covers exactly the delta's seqs; picked_up; wrapped_up for completed, stopped on disband, failed on
  reap; capped); activity never appears in `render_delta` / `team_read`; `finished` system posts gone; route
  returns `activity` + `last_aseq`; `/live` `members[].last`, `latest`, `counts`; GET allowlist unchanged.
- **editor-client:** `TeamActivity` schema, `team_activity` stream event, `getTeam` mapping.
- **Extension host:** `TeamViewManager` dedups activity by `aseq` independently of posts; `/live` fields in
  the signature.
- **Webview:** the journey builder (pure): chapter cuts, cause folding (visible post → footer; hidden DM →
  standalone beat), stance replies and tally with changed stances, gap dividers, filter effects; member
  chapter builder (divider cuts, activity join, posts by time); component tests for post card, reply,
  wrap-up row, profile header, chapter, card rows.
- **Live smoke** on the dev host: a team run where both members wake, one mentions the other, one reports;
  check footers, beats, wrap-ups, chapters, card phrases, reload with no duplicates, disband and restart
  rendering.

## 12. Out of scope

- Editing or deleting posts; replying from the board (the user reaches a team through the main agent).
- Per-member token usage (Phase 5 budgets).
- Notifications outside the webview.
