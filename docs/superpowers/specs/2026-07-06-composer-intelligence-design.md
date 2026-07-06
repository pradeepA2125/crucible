# Composer intelligence — `@`-file mentions + unified `/`-autocomplete — design

**Date:** 2026-07-06
**Status:** approved by user, ready for planning
**Parent:** copilot-parity roadmap, Phase 4 scope C ("`@`-file mentions" · "unified `/`-autocomplete
dropdown for prompts+skills")

## Context

Two of the four remaining P4-C items live in the same surface (`InputArea.tsx`, the chat composer)
and share the same interaction shape: type a trigger character, see a live dropdown, pick an item,
it gets inserted into the draft. Neither exists as a real dropdown today:

- **`/`-commands**: `slash.ts`'s `parseSlashCommand` + `InputArea.tsx` already resolve `/name args`
  on Enter into either a prompt-file expansion (`expandPrompt` round-trip, fills the draft for
  review) or a forced-skill send (`forced_skills`), per existing prompt-file-wins-on-collision
  rule. The prompt/skill catalogs are **already lazily fetched** on the first `/` keystroke
  (`listPrompts`/`listSkills`). There is **no visual list** — the user must already know the name.
- **`@`-file mentions**: nothing exists. No trigger detection, no file search, no contract field.

This spec covers both, sharing one small trigger-detection layer, and keeps them logically separate
in every other respect (different data sources, different insertion/resolution behavior).

## Shared foundation

A `useComposerTrigger` hook (or equivalent) watches the textarea's value + cursor position for an
**unterminated** trigger token: `@` or `/` immediately followed by non-whitespace, with no
whitespace between the trigger char and the cursor. It reports `{kind: "file" | "slash", query,
range}` where `range` is the token's start/end offset (used to replace the token on selection).

A single `TriggerDropdown` presentational component renders above the composer (positioned near
the cursor line, capped height, scrollable) and takes a list of `{id, label, sublabel?, badge?}`
rows + `onSelect(id)`. Keyboard handling (↑/↓ to move, Enter/Tab to select, Esc to dismiss) lives
once in the hook/dropdown pairing, reused by both kinds. Each kind supplies its own item list and
its own post-selection insertion/resolution logic — the dropdown itself has no opinion about what
"selecting a prompt" vs. "selecting a file" means.

This is purely additive: existing direct-typing flows (`/name args` + Enter without ever opening
the dropdown; free text with no trigger char) are unaffected.

## Part 1 — Unified `/`-autocomplete

**Data:** reuse the existing lazily-fetched prompt names (`listPrompts()`) and skill catalog
(`listSkills()`) — no new backend calls, no new contract fields. As the user types after `/`, filter
both lists client-side by substring/fuzzy match on name.

**Rendering:** each row shows the name, badged `Prompt` or `Skill`. Skill rows show their
frontmatter `description` as a sublabel (already available from the catalog). Prompt rows show no
sublabel — prompts have no description concept today, and adding one would mean parsing prompt file
content just for this UI; out of scope (open question resolved: blank, not first-line).

**Collision rule (unchanged):** if a name exists as both a prompt and a skill, only the prompt row
is shown — mirrors the existing `resolveSkillCommand` precedence (prompt wins).

**Selection behavior:** inserts `/name ` (trailing space) into the draft at the trigger token's
range; does **not** send. The user continues typing args exactly as today. Enter afterward follows
the existing resolution path unchanged (prompt → `expandPrompt` round-trip fills the draft for
review; skill-only match → sends tagged `forced_skills=[name]`).

**No contract change, no backend change.** This part is a frontend-only UI layer over existing data.

## Part 2 — `@`-file mentions

### File source (new host wiring)

`extension.ts` (has `vscode` API access — unlike `controller.ts`, which stays vscode-free) exposes a
`searchWorkspaceFiles()` handler backed by `vscode.workspace.findFiles`, with standard excludes
(`node_modules`, `.git`, `dist`, `target`, `__pycache__`, `.venv`, and the other conventional
ignore-dirs already used elsewhere in this repo, e.g. the indexer's `IGNORED_DIRS`), capped at
~5000 results for pathological repos. Message-passing mirrors the existing `listPrompts`/
`promptList` pattern: webview posts `{type: "listWorkspaceFiles"}`, host replies
`{type: "workspaceFileList", paths: string[]}`.

Fetched **once**, lazily, on the first `@` keystroke in a session (same lazy-fetch-once pattern
already established for the skills catalog) — cached in webview state, filtered client-side per
keystroke as the query narrows. No per-character round trip.

### Insertion

Selecting a file inserts `@relative/path ` (trailing space) into the draft at the trigger token's
range — plain text, not a rich "pill" (the composer is a plain textarea, consistent with how
`/name` insertion works). The webview additionally tracks which exact `(path, insertion-range)`
pairs came from real dropdown selections, in a small ordered list local to the current draft — this
is what distinguishes a "real mention" from incidental `@` characters a user might type (e.g. an
email address or a handle) when resolving mentions at send time.

### Send-time resolution

On send, only the tracked-mention paths (not a blind regex scan of the message text) are resolved:
`controller.ts` reads each file's content, capped at a fixed constant (~20,000 chars, matching the
existing skills-body-cap order of magnitude — a fixed constant, not a new env var, since this is a
UI-side convenience cap rather than a backend policy knob). A file that fails to read (deleted,
permissions) is skipped with an inline marker (`(file not found or unreadable)`) rather than
blocking send.

### Contract change

- **editor-client:** `sendChatMessage(client, threadId, content, {stepReview?, forcedSkills?,
  mentionedFiles?})` gains `mentionedFiles: {path: string; content: string}[]`. New Zod field,
  optional, defaults to empty.
- **Backend route:** `POST /v1/chat/threads/{id}/message` body gains `mentioned_files: list[{path,
  content}] = []`. Routed into `handle_message(..., mentioned_files=...)`.
- **Injection point:** folded into **this turn's user content**, as a trailing plain-text block —
  not a system-prompt addition, not the dynamic payload tail used for `recalled_memories`/
  `active_skills`. Mentions are turn-scoped by nature (the user attached these files to *this*
  message), so the natural shape is the same as a human pasting file content inline:

  ```
  <original user message text>

  ---
  Referenced files:
  ### path/to/foo.py
  ```
  <content, capped>
  ```
  ```

  This needs no new persistent per-turn state and no change to the ReAct loop's dynamic-tail
  machinery.

### Rendering (clickable mentions)

In the transcript, only the paths recorded as actual mentions for that message (not an unrestricted
`@`-regex over the display text) render as clickable spans. Click → `{type: "openFile", path}` →
host calls `vscode.window.showTextDocument`. `ChatMessage.metadata` gains `mentioned_files:
string[]` (**paths only**, never content) so this rendering survives reload without duplicating file
content into chat storage — the content itself is forwarded to the LLM payload for that turn only
and otherwise only visible in the per-turn debug artifact (`controller-turn-NN.json`), same as any
other prompt content.

## Out of scope

- Rich "pill" rendering of mentions inside the live-editing textarea (plain text insertion only).
- Mentioning directories or multi-file globs (single file per mention).
- Persisting mentioned file content in chat storage (paths only, per above).
- Any ranking/fuzzy-scoring sophistication for the file search beyond simple substring filtering —
  can be revisited if the flat list proves hard to navigate on large repos.

## Testing

- **Unit (webview):** trigger-detection hook (opens/closes at the right token boundaries, cursor
  math), `/`-dropdown filtering + collision precedence, `@`-dropdown filtering, insertion-range
  replacement for both kinds, tracked-mention bookkeeping (only real selections count, not stray
  `@`/`/` text).
- **Unit (extension host):** `searchWorkspaceFiles` excludes + cap; `openFile` message handling;
  mention-content read + cap + missing-file marker.
- **Unit (editor-client):** `sendChatMessage` with `mentionedFiles` serializes to the expected
  snake_case body.
- **Unit (py):** route accepts `mentioned_files`, `ChatController` folds it into the turn content in
  the expected shape; empty list is a no-op (byte-identical to today when no mentions present).
- **Live smoke:** in the dev host, type `@` and `/` in the composer, confirm dropdowns appear and
  filter live, select entries of both kinds, send a message with a file mention and confirm the
  model's response reflects file content it was never told to `read_file` on its own; click the
  rendered mention in the transcript and confirm it opens the file.

## Effort

Medium — new host message types, one contract field, one backend route field + controller-side
folding, plus the shared trigger-detection UI layer. Comfortably under the ~500-line single-shot
gate if built as one focused pass; the two parts (`/`-dropdown UI vs. `@`-mention wiring) are
naturally splittable into separate commits/PRs if it runs long.
