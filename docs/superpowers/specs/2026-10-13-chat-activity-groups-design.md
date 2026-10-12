# Chat activity groups: transient progress, collapsed work, turn changes

Date: 2026-10-13. Scope: the main chat transcript (webview `ThreadView`). Agent and team
member views reuse the same pieces in a follow-up.

## Problem

A Crucible turn renders every step as it happened: a "Thinking (N steps)" block, a
serpentine track of tool pills (often 10–20 per step, 40+ per stretch), and a full
"Changes ready / Applied" card per edit. A 20-minute turn becomes several screens of
pills; the few lines that explain what is going on are inside collapsed panes. Claude
Code and Codex show the same work as prose with one-line, expandable summaries
("Ran 3 commands, read 2 files ›", "Edited InputArea.tsx +28 −11 ›") and a changes
card at the end.

Narration exists but is unused: the `progress` action (a non-terminal note, rendered as a
prose line) was used 0 times in 216 steps (gpt-5.6-luna) and once in 63 (gpt-6.1-sol).
Each note is its own step and costs a full request, and the prompt only offers it as an
option.

## Goals

1. While the agent works, one transient line says what is happening now.
2. Finished work collapses: everything between two pieces of prose (tools and edits
   together) is one expandable line.
3. A turn that changed files ends with a changes card (files, +/−).
4. The agent narrates every few steps through `progress`, with no schema change, and the
   notes read as first-class prose rather than muted status text.
5. A reloaded thread renders exactly like the live one.

Non-goals: a new action or field in the response schema; changing what is persisted for
tools; agent/team views (follow-up); restyling gate cards in the live slot.

## Design

### 1. Grouping (pure, `webview-ui/src/transcript/groupTranscript.ts`)

`groupTranscript(messages: ChatMsg[]): TranscriptItem[]` turns the persisted message list
into render items:

- `{ kind: "message", msg }` — rendered by `MessageRow` as today.
- `{ kind: "activity", key, steps: ActivityStep[] }` — a stretch of work.
- `{ kind: "changes", key, files: FileChange[] }` — the end-of-turn changes card.

An **activity member** is one of:
- an agent `text` message whose content is empty and which carries `tool_events`
  (a pills-only message);
- a `diff_card` message;
- an edit-outcome breadcrumb: `metadata.edit_outcome === true` (new, see §5), or for
  threads written before this change, a breadcrumb whose text starts with
  "✓ Edit accepted:", "✗ Edit rejected:" or "✗ Not applied:".

Consecutive members form one activity group. Anything else is a **boundary** and stays a
`message` item: user messages, `progress` notes, agent text with content (answers),
plan/task/gate cards, notices, agent rosters, team cards, other breadcrumbs.

`ActivityStep` preserves order: `{ type: "tools", events }`, `{ type: "edit", entries,
resolved }` or `{ type: "outcome", text }`.

**Turn changes.** A turn is the span from one user message to the next. If its activity
contains at least one `diff_card`, a `changes` item is inserted after the turn's last
message. `FileChange = { path, additions, deletions, applied }`: per path, the sum of
+/− over the turn's applied edits; a path whose only edits were discarded or not applied
is listed with `applied: false` and excluded from the totals.

The function is pure and unit-tested; `ThreadView` memoizes it on `state.messages`.

### 2. Activity line (`components/activity/ActivityGroup.tsx`)

Collapsed (default): one line, text-3 color, chevron ›, e.g.
**"Read 6 files, edited `InputArea.tsx` +28 −11, ran 2 commands ›"**.

Summary text (`summarizeActivity(steps)`, pure, unit-tested):
- Counts per tool in first-seen order: `read_file` → "read N files" (distinct paths),
  `search_code` / `search_semantic` → "searched code N times", `list_directory` →
  "listed N folders", `run_command` / session tools → "ran N commands", `web_search` →
  "searched the web N times", `web_fetch` → "fetched N pages", `query_graph` → "queried
  the code graph N times", `dispatch_agents` → "started N agents", `write_todos` →
  "updated the todo list"; anything else → "used <tool> N times". Singular forms for 1.
- Edits: one file → "edited `name` +a −d"; several → "edited N files +a −d".
  Discarded or not-applied edits add "(not applied)".
- Failed calls append " · N failed".
- More than four phrases: the first three, then "and N more".

Expanded: the steps in order inside a box capped at 320 px with its own scroll:
- `tools` steps render the existing `ToolTrack` (pills keep their own detail panels);
- `edit` steps render a compact row ("Edited `path` +a −d", "Not applied" when
  discarded) that expands to the existing `DiffPanes`;
- `outcome` steps render as a small text line.

Expansion state is local and survives re-renders (keyed by the group's first message id).

### 3. Transient line (live turn only)

The streaming bubble (`state.streaming`) currently renders a `ThinkingBlock` and a
`ToolTrack`. It becomes:
- the turn's finished activity (already in `state.messages`) renders as groups above;
- one **transient line** with a pulsing dot and elapsed time, showing the newest
  unfinished tool event ("Reading `controller.py`", "Running `pytest -q`",
  "Searching the web for …", "Editing `x.ts`") or, when no tool is running, the latest
  thinking text truncated to one line; when nothing is known yet, "Working…";
- the line has a › to expand the in-flight step's pills (same capped box).

When the step's pills are persisted (the pills message arrives) they join the group and
the transient line moves on. Streamed answer text keeps rendering as today.

### 4. Changes card (`components/activity/TurnChanges.tsx`)

Rendered for `changes` items: header "Edited N files +X −Y ›", rows per file
(name clickable → `openFile`; +/− right-aligned; "not applied" in text-3), each row
expandable to its diff (`DiffPanes`, from the newest applied entry's `unified_diff`).
Collapsed by default to header + rows; diffs on demand.

### 5. Backend: edit-outcome flag

`ChatController._edit_record_cb` and the child equivalent pass
`metadata={"breadcrumb": True, "edit_outcome": True}` for the three outcome
breadcrumbs (`_write_breadcrumb` gains an optional `extra_metadata`). The live
`chat_breadcrumb` event carries `edit_outcome: true` so the live list matches the
reloaded one. Text is unchanged.

### 6. Prompt: narrate with `progress`

`controller_prompts.py` keeps `progress` exactly as is (schema, tool use, cost) and
changes its guidance for the main agent from an option to an expectation:

> Keep the user oriented. Every several steps — after a finding, when you change
> direction, or before a long stretch of work — post a `progress` note: one or two
> sentences for the user, saying what you found and what you will do next. Do not
> repeat your previous note, and do not post one instead of real work.

The same wording, adapted, goes to child agents' `progress` guidance (their notes stay
in their own transcripts). Prompt goldens are regenerated.

### 7. Progress notes render as narration

Today `ProgressLine` (`AgentRow.tsx`) renders a note at 11 px in muted italic text-3 with
a clock icon — deliberately skippable. Narration is the most important text in the new
layout, so a note renders like answer prose: the agent row's body size and color
(`text-xs`, `text-text`), markdown via `MarkdownContent`, no icon, no italics, normal
paragraph spacing. Consecutive agent items (a note followed by an activity line) share
one avatar, so the column reads as one continuous narrative. The live `chat_progress`
echo uses the same component, so a note looks the same live and after reload.

## Error handling and edge cases

- Unknown message shapes stay `message` items (never dropped).
- A pills-only message with zero `tool_events` is ignored (renders nothing today).
- Gated edits awaiting a decision are not messages (live slot); their outcome breadcrumb
  and inert diff_card join the group once decided.
- Very long groups: the capped box scrolls; nothing is truncated.
- Rewind: grouping is derived, so truncated threads re-group naturally.

## Testing

- `groupTranscript`: boundaries, consecutive merging, legacy breadcrumb prefixes, turn
  changes (sums, discarded files, multiple turns, no-edit turns).
- `summarizeActivity`: counts, distinct reads, singulars, failures, "and N more".
- Components: collapsed by default, expand/collapse, capped scroll, edit row → diff,
  changes card rows and totals, transient line content per state; a progress note
  renders as markdown prose at body size (no icon, no italics).
- `ThreadView`: a persisted thread and the same thread built live produce the same items.
- Backend: outcome breadcrumbs carry `edit_outcome`; live event carries it.
- Prompt goldens updated; a live turn on gpt-6.1-sol reviewed for narration frequency
  (target: a note every ~5–10 steps, none repeated).
