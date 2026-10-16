# Codex-style chat and theming: design

Date: 2026-10-13
Status: revision 6. Approved after five Codex review rounds; revision 6 folds in the Phase 1 dry-run findings.

## Goal

Give the Crucible chat a calm, Codex-style look, and make the webviews work in every VS Code theme. The visual reference is two screenshots of the Codex desktop app (the "Codex" mode of ChatGPT.app) supplied by the user.

Success means:

- The main chat reads like Codex: neutral greys, one accent, unboxed answers in a centered column, plain activity lines and a bare composer.
- Chat, Settings, Setup and Memory look right under dark, light and high-contrast VS Code themes. Today a light-theme user gets a dark violet panel.
- Users pick an appearance and an accent in Settings, and the change applies instantly.
- Users pick an approval mode from the composer. Command approval moves out of Settings into that mode, and the backend is the single source of truth for it.
- Every existing chat feature keeps working: gates, plan mode, teams, sub-agents, usage, rewind and live state.

## Decisions taken in brainstorming

| Question | Decision |
|---|---|
| Why revamp | New visual identity, plus theme support |
| Colour source | "Match VS Code" by default, plus our own Crucible Dark and Crucible Light |
| Accent | Configurable: Blue, Violet or Ember. **Blue** is the default. |
| Effort control | A slider with notches, inside the model menu |
| Edit and command approval | One approval-mode dropdown: Ask for approval, Accept edits, Full access. Auto is reserved for later. |
| Approval-mode scope | **One setting for the whole backend**, like today's review preference and Plan Mode |
| Full access | In the dropdown, with a warning the first time it's picked |
| Plan Mode | Stays its own toggle, separate from the approval mode |
| A command already waiting when the mode changes | Never approved automatically. It still needs an explicit decision. |
| Standalone app | Not in this spec. The token layer stays host-agnostic. |

## Current state

### Audit

The `redesign-existing-projects` audit covered the **main chat subset**, which is exactly these files (excluding `*.test.*`):

- `index.css`;
- `ThreadView`, `MessageRow`, `InputArea`, `WorkBar`, `ContextMeter`, `UsageChip`, `ModelMenu`, `EffortMenu`, `LiveSlot` and `EmptyState` (`.tsx`);
- every `.tsx` under `components/messages/**`, `components/activity/` and `components/shared/`.

That is 49 files and 6,978 lines. Including the plain `.ts` helpers in those folders gives 52 files. All of `webview-ui/src` is 258 files. The counts below are for the 49-file subset.

- **Colour already flows through tokens.** The palette is defined once in `index.css` (`@theme` plus `:root` tints). Inline styles that use `var(--…)` already follow a theme. Three things don't: about 60 hardcoded hex or `rgba()` values, 27 gradients and 13 glows.
- **Only the font reads VS Code.** No colour comes from `--vscode-*` variables, and the webview doesn't react to the `vscode-light`, `vscode-dark`, `vscode-high-contrast` or `vscode-high-contrast-light` body classes.
- **Seven text sizes, mostly tiny:** 11px (54 uses), 10px (21), 10.5px (16), 9.5px (8), 9px (3), 11.5px and 12px.
- **Seven corner radii, and no max width** on the transcript.
- **Accessibility gaps:**
  - only 6 `focus-visible` rings;
  - file mentions are focusable pseudo-buttons with no keyboard activation (`UserMessage.tsx:47`);
  - rewind appears only on hover (`UserMessage.tsx:68`);
  - the settings overlay is marked modal but has no focus management (`ChatSettingsOverlay.tsx:17`).
- **Some uppercase already exists**, for example `UsageChip.tsx:34`.
- **Structure is already close to Codex.** The activity groups (`ActivityGroup`, `TransientActivity`) and the changes card (`TurnChanges`) already exist.
- **Other panels contain literal colours** that no alias can reach. Examples: Memory's active-tab `#fff` (`MemoryApp.tsx:35`), Setup's white foregrounds, and washes and shadows in shared CSS.

### Backend and host contracts this spec builds on

All of these were verified in code. They supersede the older per-thread description in CLAUDE.md.

**Review preference**
- It's backend-wide. `PUT /v1/chat/review-pref {auto_accept}` (`routes.py:1867`) calls `ChatController.set_review_pref`.
- That sets the one shared `ChatTurnControl` (`self._review_control`, `controller.py:548`), and resolves eligible waiting edit gates in every thread. Protected-path and untrusted-agent gates are skipped (`controller.py:1487`).
- The editor-client method is `setGlobalReviewPref`.
- With the legacy `ChatAgent`, the route returns zero counts **without applying anything** (`routes.py:1871`).

**Plan Mode**
- It's backend-wide too: `PUT /v1/chat/plan-mode {plan_mode}`.

**Host re-sync after reconnect**
- After every (re)connect, `controller.ts::pushPreferences` (`controller.ts:1791`, called from the hydration hook at `extension.ts:660`) PUTs the review preference and Plan Mode, and swallows failures.
- Because that review PUT resolves waiting edits, **a reconnect can currently accept a pending edit without the user asking.** This spec fixes that (see "Synchronising" under the approval mode).

**Per-message writes**
- An ordinary `POST …/message` sends `step_review`.
- The controller writes `self._review_control.auto_accept_edits = not step_review` for an ordinary message (`controller.py:765`) and for a message queued during a notice turn (`controller.py:898`).
- A clarification resume passes the stored `step_review` into `_run_loop` (`controller.py:4031`) as history metadata. It does **not** write the shared control; the loop reads `_review_control` (`controller.py:1119`).
- `PUT /v1/chat/review-pref` is the third writer of the edit half.

**Command policy**
- It's fixed at process start: `ChatController._shell_policy` comes from `CRUCIBLE_SHELL_POLICY`. It's read in three places:
  - the main command gate, live (`controller.py:1614`);
  - the child command gate, live (`controller.py:2005`);
  - when a child's prompt is rendered, as a snapshot frozen into its `RenderContext` for the life of that child loop (`controller.py:3588`, `controller_loop.py:847`).
- Under `ask`, a remembered workspace rule auto-approves a command.
- A `dontAsk` child is denied anything not covered by `allow_all` or a remembered rule.
- Child edit behaviour also depends on the child's own definition: an `acceptEdits` or `dontAsk` child auto-accepts edits, and a `plan` child can't edit at all (`controller.py:3608`, `subagents/permissions.py:41`).
- MCP tools have their own gate. PTY `write_stdin` is ungated.

**Effort descriptor**
- Shape: `{options: [{value, label, description, state}], source, default, reason}`, where `state` is `supported | unsupported | unknown` (`reasoning_effort.py:162`).
- `EffortMenu` also offers Default (null) as a separate choice (`EffortMenu.tsx:210`). When the descriptor is missing, it falls back to the five-level ladder `off, low, medium, high, max` (`EffortMenu.tsx:9`) and derives each rung's state from the separate `support.supported` / `support.unsupported` metadata (`EffortMenu.tsx:124`). It also shows a pending state, provider errors and clamp notes.
- The editor-client Zod schema for `control.options[].state` currently allows only `supported` and `unknown` (`task-contracts.ts:669`); unsupported rungs travel in the separate map.

**Config**
- `HttpBackendClient.getConfig()` (`http-backend-client.ts:935`) maps each field explicitly before parsing with `BackendConfigSchema`. A new backend field needs a mapper change as well as a schema change.

**Queueing**
- A second message during a running turn is rejected with 409, except in a notice turn, where it's queued (`inputAvailability.ts:95–107`).

**Changes card and Stop**
- `TurnChanges` receives only file summaries. It has no Undo.
- The chat Stop cancels the turn and has no revert.

**Activity items**
- They carry no timestamps (`transcript/groupTranscript.ts`).

**Settings**
- Settings sections are registered on the host side (`src/settings-sections.ts`) and in the webview (`settings/sections/meta.ts`). The host and webview message/state protocols are mirrored too.
- Settings render in the Settings panel and in `ChatSettingsOverlay`.
- The initial `SettingsState` is built from backend calls (`settings-data.ts:163`), and `SettingsApp` renders no sections until it arrives (`SettingsApp.tsx:65`).

**Graph**
- The Graph panel paints its own HUD colours from `graph/palette.ts`.

## Design

### 1. Theme foundation

**Semantic tokens.** `index.css` defines one vocabulary, and migrated components read only these:

| Group | Tokens |
|---|---|
| Surfaces, each with a paired foreground | `--surface-0` / `--on-surface-0` (page), `--surface-1` / `--on-surface-1` (inset, inline code), `--surface-2` / `--on-surface-2` (cards, widgets), `--surface-3` / `--on-surface-3` (popovers, menus) |
| Text | `--text-1`, `--text-2`, `--text-3` (muted). In the `dark` and `light` palettes each must reach ≥ 4.5:1 on every surface. In `vscode` and `hc` mode contrast is the theme author's, which we can't guarantee; we only use paired roles. |
| Input | `--input-bg`, `--input-fg`, `--input-placeholder`, `--input-border` |
| Buttons | `--button-bg`, `--button-fg`, `--button-hover-bg`, `--button-secondary-bg`, `--button-secondary-fg` |
| Lines | `--border`, `--border-strong`, `--focus-ring` |
| Accent | `--accent` (links and icons on surfaces), `--accent-fill` (user bubble, send button), `--on-accent` (text on the fill), `--accent-bg` (tint), `--accent-border` |
| Status | `--success`, `--danger`, `--warning`, each with a `-bg` pair |
| Diff | `--diff-add`, `--diff-del` |
| Type scale | `--fs-caption`, `--fs-meta`, `--fs-body`, `--fs-prose`. They're derived from `--vscode-font-size` (the workbench UI font size, not the editor font size), keeping today's base. At the default 13px they come out to 11, 12, 13 and 15px after the 14 October readability refinement. |
| Radius scale | `--r-control` 6px, `--r-card` 10px, `--r-composer` 16px, and `999px` for round buttons and avatars |
| Motion | The existing `--dur-*` and `--ease-*`. `prefers-reduced-motion` turns off shimmer and transitions. |

The existing Tailwind `@theme` names (`--color-surface`, `--color-text-2` and so on) stay as aliases of the new tokens until every user has migrated.

**Readable text on every surface.** In `vscode` mode, surfaces 1–3 are tinted from the page background toward the foreground (`color-mix(in srgb, var(--surface-0) N%, var(--text-1))`), and each `--on-surface-*` is `--text-1`. General text is therefore readable on every surface by construction. (Revision 6: this replaces per-surface contextual foreground classes. Forty components put colour on surfaces 2/3, many of them chips and hover fills rather than popovers, and an inherited legacy alias such as `--color-text` cannot be re-pointed per container without scoping every one of them.)

**Theme resolution.** `webview-ui/src/theme.ts` sets one effective state on `<html>`: `data-theme="vscode|dark|light|hc"` and `data-accent="blue|violet|ember"`.

- It resolves to `hc` whenever the body carries `vscode-high-contrast` or `vscode-high-contrast-light`, whatever the appearance and accent settings say. Otherwise it uses the configured appearance.
- It re-runs:
  - on load;
  - on a `themeChanged` message from the host;
  - when a `MutationObserver` sees the body `class` attribute change. VS Code updates those classes live, including the high-contrast light class.

**Theme sets.** Each one is a CSS block keyed on `data-theme` and `data-accent`.

- **`vscode` (match the editor).** Every token is `var(--vscode-…, var(--vscode-fallback…, <Crucible Dark value of the same token>))`, so a theme missing a variable falls back to our dark palette rather than to nothing. (`hc` mode is the exception: its fallbacks are black/white high-contrast values, so a high-contrast user never falls back to low contrast.) Foreground and background are always paired:

  | Our token | VS Code variable chain |
  |---|---|
  | `--surface-0` / `--on-surface-0` | `sideBar-background` → `editor-background` / `sideBar-foreground` → `foreground` |
  | `--surface-1..3` / `--on-surface-1..3` | tinted from the page: 96%, 94% and 90% `--surface-0` mixed toward `--text-1` / `--text-1` |
  | `--input-*` | `input-background`, `input-foreground`, `input-placeholderForeground`, `input-border` → `widget-border` |
  | `--button-*` | `button-background`, `button-foreground`, `button-hoverBackground`, `button-secondaryBackground`, `button-secondaryForeground` |
  | `--text-1` | `foreground` |
  | `--text-2` and `--text-3` | Both `descriptionForeground`. No opacity reduction, so contrast is whatever the theme author chose. |
  | `--border` | `panel-border` → `widget-border` |
  | `--focus-ring` | `focusBorder` |
  | `--diff-add` / `--diff-del` | `gitDecoration-addedResourceForeground` / `gitDecoration-deletedResourceForeground` |
  | scrollbars | `scrollbarSlider-*` |

  The accent comes from our accent setting.
- **`dark`:**
  - surfaces: page `#181818`, inset `#232323`, card `#202020`, input `#262626`, popover `#2a2a2a`;
  - borders: `#2e2e2e`, strong `#3a3a3a`;
  - text `#e8e8e8` / `#a3a3a3` / `#949494` (muted is 4.73:1 on the popover, which is the worst case).
- **`light`:**
  - surfaces: page `#ffffff`, inset `#f0f0f0`, card `#f6f6f6`, input `#ffffff`, popover `#ffffff`;
  - borders: `#e3e3e3`, strong `#d0d0d0`;
  - text `#1a1a1a` / `#5c5c5c` / `#6b6b6b` (muted is 4.68:1 on the inset, which is the worst case).
- **`hc`:**
  - every token maps to its `--vscode-*` variable;
  - borders use `contrastBorder`, focus and selection use `contrastActiveBorder` and `focusBorder`;
  - accent tints, washes, shadows and opacity effects are off, including the legacy translucent borders (`--green-brd`, `--red-brd`, `--hairline`), which become `contrastBorder`;
  - the accent and fills come from `button-background` and `button-foreground`, not from our accent setting.
- **Accents.** Link (`--accent`) and fill (`--accent-fill`) are separate values. The minimum contrast below is over all surfaces in that theme. `--on-accent` is white everywhere.

  | Accent | Dark link | Dark fill (white on it) | Light link | Light fill (white on it) |
  |---|---|---|---|---|
  | Blue (default) | `#6aa2f5` (5.54) | `#2f6fd6` (4.81) | `#2563eb` (4.54) | `#2563eb` (5.17) |
  | Violet | `#a898f0` (5.75) | `#6d5bd0` (5.18) | `#5b4bc4` (5.67) | `#5b4bc4` (6.46) |
  | Ember | `#f0995a` (6.43) | `#a9531a` (5.33) | `#a9531a` (4.68) | `#a9531a` (5.33) |

  Ember is a burnt orange, darker and redder than Claude's clay.

**Colour-only cleanup of the other panels.** Settings, Setup and Memory, plus shared CSS primitives (`.surface-card`, `.scrim`, `.accent-wash`, `.menu-item`), have their literal colours replaced by paired tokens: for example, `#fff` on an accent background becomes `--on-accent`, and washes and shadows become tokens that `hc` turns off. Layout doesn't change. That is what makes their high-contrast look right.

**Settings storage.**

- Two VS Code settings, contributed in `apps/vscode-extension/package.json` at application scope, with no backend involved:
  - `crucible.appearance`: `"vscode" | "dark" | "light"`, default `"vscode"`;
  - `crucible.accent`: `"blue" | "violet" | "ember"`, default `"blue"`.
- **Every** Crucible webview panel (chat, a restored chat, settings, setup and memory) writes the resolved attributes into its HTML template when it's created.
- `workspace.onDidChangeConfiguration` posts `{type: "themeChanged", appearance, accent}` to every open panel.
- Graph is excluded.

**Appearance section in Settings.**

- **Registration:** add it to both registries (`src/settings-sections.ts`, `settings/sections/meta.ts`), to both message/state mirrors, and to both mount points (the Settings panel and `ChatSettingsOverlay`). It holds two swatch pickers with a live preview, and writes the settings through `settings/setAppearance`.
- **It works offline.** The host posts a separate `settings/localState {appearance, accent}` that needs no backend. `SettingsApp` renders the navigation and the Appearance section from that local state straight away. Backend-dependent sections show their own loading or error state instead of hiding the whole panel. Today a backend failure blanks Settings.

### 2. Transcript

- **Column.** Content sits in a centered column, max 760px wide. Narrower than that, it fills the panel with 16px gutters.
  - Readability refinement (14 October, following the user's Codex/Crucible screenshots): 24px between transcript items, 16px between prose paragraphs, and explicit heading/list spacing. Keep the centered column and pinned composer.
- **User message.**
  - A right-aligned bubble in `--accent-fill` with `--on-accent` text, at `--fs-body`, radius 16px, at most 80% of the column wide.
  - File mentions are real `<button>`s.
  - Rewind and copy appear on hover and on `:focus-within`.
  - The queued tag stays.
- **Agent answer.**
  - No card, border or avatar.
  - Copy sits in a normal-flow action row below nonempty finished prose, revealed by
    that message's hover/focus (always available on touch). It never overlays headings,
    tool disclosures or streaming text, and copies only the original message Markdown.
  - Expanded tool details have local input/output copy actions. Run-command input copies
    the command itself; other tools copy formatted argument JSON. Output copies the
    recorded output verbatim. Omit empty-content actions and acknowledge clipboard success
    only after the write completes; denied writes show a retryable failure.
  - Prose at `--fs-prose`, line height 1.65, `text-wrap: pretty`.
  - File references are `--accent` links.
  - Inline code inherits the surrounding prose size and line height, with theme accent text, tint and a 1px accent border. Light-mode ink is darkened slightly for contrast. Wrapped phrases clone their decoration per line; ordinary multiword code stays a single highlight.
  - Code stays at `--fs-body`; tables use that readable size with 8px vertical cell padding and horizontal rules instead of a boxed grid.
  - Gates and cards line up with the column edge.
- **Activity.**
  - **Turn work disclosure (14 October):** progress notes and their tool batches share an outer Working / Worked section. Live work opens by default; completed work folds away unless the user chose to inspect it. Expanded progress keeps the normal answer prose, with independent collapsed tool summaries between notes. Final answers, reports, plans, legacy decision cards and the changes summary remain outside the work disclosure. Standalone roster and board cards stay directly accessible. Work disclosure state is scoped to the selected chat, child or member chapter.
  - **Folded header:** the existing action summary ("Read 4 files, ran 2 commands ›") on a muted `--fs-body` row with a small activity icon and disclosure chevron. The 14 October readability refinement removes the full-width hairline rule. No elapsed time.
  - **Expanded:** one `--fs-body` line per step: icon, verb, then the target in muted text. Tool batches and the current-step disclosure use an internally scrollable panel capped at `min(320px, 40vh)`. The labelled region is keyboard focusable and contains scroll gestures. Expanded tools have a subtle rounded theme border, neutral surface and 12px padding; progress prose remains outside. Action verbs retain their width while long targets ellipsize.
  - **Kept:** failed and not-applied outcomes, shared thoughts, and keyboard-reachable tool details. Expanded tool batches have subtle divider lines with 16px space on each side, 12px between shared thought and tools, and 8px between tool lines. Batch thoughts stay within the activity detail and use secondary body text in a subtle tinted inset with an accent edge; actual progress actions remain normal prose between summaries.
  - **Removed:** the monospace tool pills and the connected-track drawing.
  - **Live line:** the transient line shimmers only its verb, and only when reduced motion is off.
  - **Sub-agent follow-up (14 October):** main chat, expanded sub-agent history and team member work share the grouped transcript renderer. Finished calls fold into activity summaries; unfinished calls use the current-step disclosure. Narration and activation boundaries stay visible. Sub-agent reports use plain answer prose, and team chapters keep their separate edits and reports. Long reports in child transcripts, member chapters and board wrap-ups start as excerpts with Show more / Show less. Opening restores full markdown. Short child/member reports need no extra control, and closed member chapters bound their first-line report preview.
  - **Sub-agent overlays (14 October):** floating child windows use the centered 760px maximum, retain their full height, and open above the parent team board. Closing a child returns to the retained member tab; Escape closes only the foreground child and restores its opener's focus.
- **Changes card.** `TurnChanges` is restyled, with no new features:
  - one card with radius `--r-card`;
  - a header showing "Edited N files" and `+N −N`;
  - file rows with a muted path, the basename in `--text-1`, and per-file `+/−`;
  - the existing per-file diff expanders;
  - "Show N more files" once there are more than three.
- **Gates and cards.** They keep their components and behaviour. They move onto the new tokens and type scale.
- **Removed:** gradients, glows, coloured shadows and shimmer borders.

### 3. Composer

**Layout.** One rounded box (`--input-bg` / `--input-fg`, radius `--r-composer`): the textarea on top, then a control row.

| Side | Control | Contents |
|---|---|---|
| Left | **+** menu | Attach images, mention a file (`@`), commands (`/`) |
| Left | **Approval-mode chip** | Icon and the backend-confirmed mode name. Opens the dropdown. |
| Left | **Plan toggle** | An accent-tinted "Plan" chip when on, a quiet icon button when off. Uses the existing `setPlanMode` / `planModeState` round trip. |
| Right | **Model chip** | "Sonnet 5.5 · High". The menu shows a plan-usage line, the model list, then the effort slider. |
| Right | **Context ring** | 16px. Hover, focus, click or tap opens a card (context %, thread totals, team breakdown). Escape or clicking outside closes it. |
| Right | **Send / Stop** | Round `--accent-fill` button. While a turn runs it becomes the existing Stop. |

- **Placeholders and disabled states** follow the existing `inputAvailability.ts` table. Plan Mode adds "Describe what you want planned".
- **The chips and the toggle stay usable while the textarea is disabled.**
- **The settings gear moves** to the thread header.
- **WorkBar** becomes a quiet `--fs-meta` line.

**Effort slider.**

- **Control:** a native `<input type="range">` labelled "Reasoning effort", with `aria-valuetext` set to the selected label.
- **Stops:** Default (null), followed by `control.options` in order, minus any option whose `state` is `unsupported`. An `unknown` option shows an "unverified" note when it's selected.
- **Below the track:** the selected label and its description.
- **How many stops:**
  - Two or more stops: show the slider. A single explicit option still gives two stops, Default and that option.
  - A descriptor present but with no options: hide the row.
  - **No descriptor at all:** keep `EffortMenu`'s five-level fallback ladder, deriving each rung's state through the existing `support.supported` / `support.unsupported` logic. Known unsupported rungs are skipped, known supported rungs show normally, and only rungs in neither list are marked unverified.
  - The editor-client schema for `control.options[].state` is extended to accept `unsupported`, so a descriptor that carries it parses instead of throwing.
- **Model change:** the stops are rebuilt. If the current value is no longer offered, the slider shows the clamp note.
- **Kept from `EffortMenu`:** the pending state, provider errors and the same host messages. Moving back to Default clears the override (`reasoning_effort: null`).

**Approval-mode dropdown.**

| Mode | Main-agent edits | Commands | Backend policy |
|---|---|---|---|
| Ask for approval (default) | Reviewed | Ask | `auto_accept=false`, `shell_policy=ask` |
| Accept edits | Applied automatically | Ask | `auto_accept=true`, `shell_policy=ask` |
| Full access | Applied automatically | Run without a gate | `auto_accept=true`, `shell_policy=allow_all` |
| Auto (reserved) | Applied automatically | Safe ones run, risky ones ask | A later spec |

**What the dropdown says about exceptions.** Its footer, plus each mode's description, states these plainly:

- Protected-path edits and edits from untrusted agents are always reviewed.
- Commands you told Crucible to remember run without asking.
- Sub-agents follow their own definition: a `plan` agent can't edit, and `acceptEdits` and `dontAsk` agents apply edits automatically.
- MCP tools keep their own approval.
- Typing into an already-running terminal session isn't gated.

**Mode table.** The modes live in one table in the webview (id, label, icon, description, policy), and the dropdown renders from it. The **host** validates the id and derives the policy.

**Full-access warning.**

- The first time Full access is picked, a confirm dialog explains that commands will run without asking.
- The dialog takes focus and traps it. Escape cancels. Focus returns to the chip afterwards.
- "Don't show again" is stored in host `globalState`. Cancelling leaves the mode unchanged.

**The backend is authoritative.**

- **State.** The controller holds one approval state: `auto_accept`, `shell_policy`, `version` (an integer), `owned` (bool) and `process_id`. `process_id` is the **existing** per-process id that `ContextUsageTracker` already mints (`context_usage.py:57`) and `/live` already reports (`routes.py:1523`); approval reuses it rather than minting a second one.
  - `version` increments **only when the effective policy changes**, plus once when ownership is first taken.
  - An identical PUT changes nothing and doesn't bump the version.
- **Reporting.** `/live` and `GET /v1/config` both report `approval: {supported, auto_accept, shell_policy, version, owned, process_id}`.
  - In `/live` it's a slow field, added to `lastLiveSignature`; it only changes when the policy does, so it doesn't churn.
  - The editor-client `getConfig()` mapper, `BackendConfigSchema` and the `/live` schema are all extended.
- **Display.** On a backend that reports `supported`, the chip in every panel and every client shows what `/live` reports. It's never driven by local state alone.

**One rule for resolving waiting edits.** A policy write resolves eligible waiting **edit** gates only when **both** hold:

- the request says `resolve_waiting: true` (an explicit user selection);
- `auto_accept` goes from false to true in that write.

Resolution uses the extracted body of `set_review_pref`, so protected-path and untrusted-agent gates are still skipped. A write that leaves `auto_accept` true (for example, Accept edits → Full access) resolves nothing, and neither does any sync write. **Waiting command gates are never resolved by any policy write.**

**Every writer goes through ownership.** Once `owned` is true:

- `PUT /v1/chat/approval-mode` is the only writer.
- `PUT /v1/chat/review-pref` returns **409** "approval mode is managed by the approval-mode API; update the extension". It doesn't write, and it doesn't resolve gates.
- `step_review` on ordinary messages (`controller.py:765`) and on notice-queued messages (`controller.py:898`) is ignored.
- Clarification resume needs no change, because it doesn't write the control.

Before ownership, all of today's writers behave as they do now, so older clients keep working.

**Selecting a mode (explicit user action).**

1. The webview posts `setApprovalMode {mode}`.
2. The host serialises every approval write (selections and syncs) through one queue.
3. The host sends `PUT /v1/chat/approval-mode {auto_accept, shell_policy, resolve_waiting: true, expected_process_id, expected_version}`, the pair it last saw in `/live`.
4. **On success**, the response `{auto_accept, shell_policy, version, owned, process_id, auto_resolved_edits, background}` is the applied state, and the host saves the mode in `globalState`.
5. **On a conflict** (409: the process id or the version doesn't match), the host adopts the backend state and refreshes the chip. If the version differed, it says the mode was changed elsewhere. If the process id differed, the backend restarted; the host runs the connect decision below and **never replays the user's selection** against the new process. The user picks again if they still want it.
6. **On any other failure**, nothing is saved, the chip keeps the `/live` value, and an error is shown. While a request is in flight, the chip shows a pending state.

**Connecting and reconnecting (no user action).** The hydration hook (`extension.ts:660` → `pushPreferences`) reads `/v1/config` first and then decides:

| Backend reports | What the host does |
|---|---|
| `owned: true` (same process, or a process another client has already set up) | **Adopts** the backend state. Writes nothing. A reconnect never overrides a choice made elsewhere. |
| `owned: false` (a freshly started process) | **Initialises** it with the saved mode: `PUT {…, resolve_waiting: false, expected_process_id, expected_version}` using the pair just read. On a 409, another client got there first (or the process restarted again), and the host adopts or re-runs the decision. |
| `supported: false` | Uses the fallback tiers below. |

- `pushPreferences` no longer calls `setGlobalReviewPref` on a supported backend. That removes today's reconnect-accepts-edits bug.
- **Readiness, enforced on both sides.** Until the host has adopted or initialised the approval state of the current `process_id`, the chip shows "Connecting…".
  - **Host side:** every operation that starts or resumes controller work waits behind one readiness barrier (up to 10 seconds, then it fails with "Approval mode not confirmed — retry"). Those operations are: sending a message, a mode decision (`/mode-decision`), a clarify answer (`/clarify-decision`) and a team-plan decision (`/team-plan-decision`).
  - **Backend side:** those four routes accept an optional `expected_process_id`. When it's present, the route returns **409 "approval not confirmed"** — before it consumes a gate or starts any work — if approval isn't `owned` yet or the id doesn't match. This closes the gap where the process restarts between the host's check and the request arriving. Older clients don't send the field and are unaffected.
  - **A distinguishable error.** The admission 409 body is `{"detail": {"code": "approval_not_confirmed", "process_id": <current>}}`, so it can't be mistaken for the existing 409s (turn busy, stale gate). All four editor-client methods preserve the status and `detail.code`: the mode and clarify methods (`http-backend-client.ts:436`) currently throw generic errors that drop them, and message and JSON errors drop `detail`. The host checks for `approval_not_confirmed` **before** its existing benign-conflict handling (for example the team-plan path at `controller.ts:824`, which treats every 409 as a gate miss). On that code it invalidates readiness, re-runs connect recovery, and keeps the unanswered card and the typed input. `TeamPlanDecisionRequest` (`chat/models.py:331`) gains the optional field.
- **Failures are visible**, as "Not applied — retry", never silently caught.
- The host notices a backend restart when `process_id` changes between reads, and runs the decision again.

**Capability tiers.**

| What the backend supports | Chip behaviour |
|---|---|
| `approval.supported = true` | Full dropdown, driven entirely by `/live` |
| A controller backend without the field (older) | See below |
| A legacy `ChatAgent` (`chat_controller_enabled: false` in config) | The chip is disabled: "Approval modes need the chat controller". |

**The older-controller tier:**

- Only Ask for approval and Accept edits are offered, applied with `PUT /chat/review-pref`. Full access is disabled. The command half reads "set in Settings", and no migration notice about commands is shown.
- There's no shared readback, so the chip shows the **last edit preference that backend acknowledged**, labelled "this window's setting".
- On reconnect the host re-applies it **only if** it is review-on (`auto_accept: false`), because that resolves nothing. If it's auto-accept, the host doesn't re-send it (that would resolve waiting edits); the chip shows "unconfirmed" until the user picks a mode again.

**Migration.** This runs once, when `approvalMode` isn't set yet **and** the backend reports `approval.supported`. The old command half is the backend's **effective** `shell_policy` from `/v1/config`, never the local `crucible.policy.shell` setting, because an external backend can differ from it.

| Effective shell | Local review preference | New mode |
|---|---|---|
| `allow_all` | off | Full access (both halves were already allowed) |
| `ask` | off | Accept edits |
| `ask` | on | Ask for approval |
| `allow_all` | on | Ask for approval. A one-time notice is shown, but only **after** the PUT confirms the command downgrade. |

If the backend's state isn't available, migration waits and the chip shows a pending state. Migration never raises a permission. If the backend is already `owned`, migration adopts its state instead of writing.

**Backend change.**

- **`PUT /v1/chat/approval-mode {auto_accept: bool, shell_policy: "ask" | "allow_all", resolve_waiting: bool, expected_process_id: str, expected_version: int}`.**
  - It returns 422 on invalid input, and 409 with the current state when either `expected_process_id` or `expected_version` doesn't match. Both are compared together, before any ownership, policy or gate change.
  - It calls `ChatController.set_approval_mode(...)`, which applies the transition rule, sets `owned`, and bumps `version` only on an effective change (or first ownership). There's no `await` between the version check and the writes.
- **`PUT /v1/chat/review-pref`** returns 409 once `owned` is true, as described above. Before that, it's unchanged.
- **The four turn-starting routes** (`/message`, `/mode-decision`, `/clarify-decision`, `/team-plan-decision`) gain the optional `expected_process_id` admission check described under readiness.
- **Enforcement.** The main and child command gates read `_shell_policy` live. A change applies to the next command anywhere: main, notice and resumed turns, sub-agents and team members.
- **Prompt text.** A child's prompt describes the policy as it stood when that child loop started (`controller.py:3588`, retained at `controller_loop.py:847`). It catches up at the child's next activation. Enforcement does not wait for that.
- **The legacy `ChatAgent`** has no `set_approval_mode`. The route returns 409 "chat controller required", and config reports `supported: false`.
- **editor-client** gains `setApprovalMode`, plus `approval` in the `/live` schema, the `getConfig()` mapper and `BackendConfigSchema`.

## Phases

1. **Theme foundation.**
   - Tokens, scales and theme sets.
   - `theme.ts`, including high contrast and live body-class changes.
   - The two settings, attribute injection and `themeChanged`.
   - The Appearance section, with offline local state, both registries, both mirrors and both mount points.
   - Colour-only cleanup of Settings, Setup, Memory and the shared primitives.
   - Contrast and lint tests.
   - No layout changes.
2. **Transcript.** Column, user bubble (with the keyboard fixes), unboxed answers, the activity list, the changes card restyle, tokenized gates, and decoration removal.
3. **Composer and approval modes.**
   - Composer layout, model chip with the effort slider, context ring, and the gear move.
   - The approval dropdown, the Full-access warning, migration and capability tiers.
   - The backend route, `/live` and config fields, and per-message ownership.
   - The `pushPreferences` fix.
   - `ChatSettingsOverlay` focus management: focus moves to the first focusable control on open, Tab is trapped inside, Escape closes, and focus returns to the opener.

## Testing

- **Contrast test (vitest, pure).** For dark and light and every accent, it computes WCAG ratios for every text token on every surface it's used on, the link on every surface, and `--on-accent` on the fill. It fails below 4.5:1.
- **Lint test (vitest).**
  - **Scope:** only the migrated components, growing each phase. Phase 1 covers the shared CSS primitives and `theme.ts`. Phase 2 adds `components/messages/**`, `components/activity/*`, `components/shared/*`, `ThreadView` and `MessageRow`. Phase 3 adds `InputArea`, `WorkBar` and the composer menus.
  - **Fails on:**
    - raw hex or `rgba()`;
    - Tailwind arbitrary *and* standard size and radius utilities (`text-[Npx]`, `text-xs|sm|base|lg…`, `rounded-[Npx]`, `rounded-sm|md|lg|xl…`), where the token utilities must be used instead;
    - numeric *or* string `fontSize` and `borderRadius` in inline styles;
    - literal `font-size` and `border-radius` values in CSS files in scope, except inside the token definitions themselves.
  - **Allowed:** dynamic inline values such as positions and widths.
- **Theme tests (vitest, jsdom).**
  - The `theme.ts` resolution table, including both high-contrast classes.
  - Re-resolving on a body-class mutation.
  - `themeChanged` handling.
- **Computed-colour checks (real browser, CDP).**
  - The jsdom environment can't resolve CSS variables, so after each phase a CDP script runs `getComputedStyle` on key elements in each panel for each theme and accent, and asserts the expected colours.
  - Key elements: page, card, input, link, user bubble, send button, a pending gate, and a Settings control.
- **Composer tests (vitest).**
  - The mode table and policies.
  - Every migration row, including deferral when the backend's state is unknown, and showing the notice only after confirmation.
  - Selection: pending, success, failure, and a serialised queue.
  - Sync path: reads config first; adopts when `owned`; initialises with `resolve_waiting: false` and `expected_version` when not; adopts on a 409; surfaces failures.
  - Readiness: a send, a mode decision, a clarify answer and a team-plan decision each wait for initialisation and fail after the timeout; a `process_id` change re-runs the decision; a selection that hits a process-id conflict is not replayed.
  - Error routing: an `approval_not_confirmed` 409 from each of the four methods triggers recovery and keeps the card and input; an ordinary busy or stale-gate 409 keeps today's behaviour.
  - Version conflict on selection: adopts the backend state and shows the changed-elsewhere notice.
  - The older-controller tier re-applies only review-on on reconnect, and shows "unconfirmed" otherwise.
  - The three capability tiers, including Full access disabled.
  - The Full-access warning: shown once, cancel, Escape, focus return.
  - Effort slider:
    - Default plus 3 options;
    - Default plus 1 option (two stops);
    - an unsupported option skipped;
    - an `unknown` option showing the unverified note;
    - an empty descriptor (row hidden);
    - a missing descriptor (fallback ladder), including the legacy case where the metadata marks Max unsupported, which must be skipped (the `EffortMenu.test.tsx:23` case);
    - a model change;
    - clearing back to null;
    - `aria-valuetext`;
    - keyboard stepping.
  - The context card opening by focus, click and touch, and closing with Escape.
  - Controls usable while the textarea is disabled.
  - The `/live` `approval` field is in `lastLiveSignature`.
  - The existing InputArea, ThreadView, activity, `TurnChanges` and `inputAvailability` tests, updated to the new markup, with the availability state table unchanged.
- **Backend (pytest).**
  - `PUT /approval-mode`: sets both halves, increments the version, returns the applied state, and returns 422 on bad input and 409 on the legacy agent.
  - Gate resolution:
    - `resolve_waiting: true` together with a false→true change of `auto_accept` resolves eligible edit gates across threads, including background-only agents, and skips protected and untrusted ones;
    - `resolve_waiting: true` with `auto_accept` already true (Accept edits → Full access) resolves nothing;
    - `resolve_waiting: false` resolves nothing;
    - a gate that has already been decided, has timed out or was stopped mid-resolution is left alone, with no double resolution;
    - **command gates are never resolved.**
  - Versioning: an identical PUT doesn't bump the version; first ownership bumps it once; a stale `expected_version` returns 409 with the current state; a **different `expected_process_id` with the same version** returns 409; `process_id` is stable for the life of the process.
  - Admission: each of the four turn-starting routes, given an `expected_process_id`, returns 409 without consuming its gate or starting work when approval isn't owned or the id differs; without the field, behaviour is unchanged.
  - Ownership: after the first PUT, `PUT /chat/review-pref` returns 409 and neither writes nor resolves; before it, the old behaviour holds.
  - After any PUT, `step_review` on a later message, a notice-queued message or a clarification resume doesn't change the policy. This covers the out-of-order PUT-then-stale-message case.
  - Without a PUT, today's per-message behaviour still applies.
  - `allow_all` skips the gate for the next command in main, notice and resumed turns and in sub-agents.
  - The child permission matrix: `default`, `plan`, `acceptEdits` and `dontAsk`, each under `ask` and under `allow_all`.
  - A child that's already running is enforced with the new policy immediately.
  - Remembered rules apply under `ask`.
  - Two threads see the same policy.
  - `/live` and `/v1/config` report the applied `approval`.
  - The existing `test_global_review_pref.py` cases stay green.
- **Integration (route → editor-client → host).**
  - Selecting a mode updates `/live` and every panel's chip.
  - A simulated reconnect with waiting main and background edits resolves nothing.
  - A failed PUT keeps the previous mode.
  - A second client's change shows up through `/live`.
  - **The two-client reconnect sequence:** A picks Full access, then B picks Ask for approval, then A reconnects. A adopts Ask for approval and doesn't restore Full access.
  - An older client reconnecting to an owned backend gets a 409 from review-pref and resolves nothing.
  - A backend restart (new `process_id`) is initialised once from the saved mode, and the first message waits until it's done.
  - A restart between the host's readiness check and a restored mode card's decision: the backend returns 409, the gate is not consumed, and the host re-runs readiness.
- **Visual checks after each phase.**
  - Run `scripts/dev/install-local.sh` and restart the main VS Code with `--remote-debugging-port=9335`.
  - Take CDP screenshots across editor theme (dark, light, HC dark, HC light) × appearance × accent, at side-panel width, at editor-tab width and at 320px (overflow check).
  - Include a pending gate, a running turn, the open composer menus, and Settings with the backend stopped.
  - Run once with reduced motion emulated.
- **Audit.** Run `web-design-guidelines` on the changed files after each phase. Fix each finding or record why it's kept.

## Out of scope

- The standalone app.
- Auto-mode safety rules.
- A custom accent colour.
- Layout restyling of Settings, Setup and Memory. They get the colour-only cleanup.
- Graph theming.
- Undo on the changes card, and chat stop-and-revert.
- Elapsed time in the activity header.
- The landing site.

## Known gaps and open questions

- PTY `write_stdin` is ungated in every mode. This predates this spec. The Auto-mode spec decides whether it joins command approval.
- Whether Auto mode also governs MCP-tool approval is left to that spec.
- The task subsystem keeps its own per-task `shell_policy`. Wire it in if that subsystem is turned back on.
- CLAUDE.md still describes a per-thread `POST …/review-pref`. Update it when this lands.

## Review log

**Revision 2** folded in all 20 findings from Codex review round 1.

**Round 5 verdict: approve with nits.** Its one nit (a distinguishable admission-error code, preserved through all four client methods and handled before the benign-conflict logic) is folded into revision 5.

**Revision 5** folds in round 4 (same session). Round 4 rated every earlier item resolved except original finding 5, and raised 2 new issues plus a nit, all accepted:

- Approval writes are bound to a process lifetime: `expected_process_id` plus `expected_version`, compared together. A selection that hits a restarted process is never replayed. `process_id` reuses the existing `ContextUsageTracker` id.
- One readiness barrier covers every host operation that starts or resumes work (message, mode decision, clarify answer, team-plan decision), backed by a 409 admission check on those four routes.
- The round-3 counts in this log were corrected.

**Revision 4** folds in round 3 (same session). Every claim was verified against the code. Round 3 rated 14 of the original 20 findings resolved and 6 partially resolved, and 7 of the 8 round-2 issues resolved and 1 partially. It raised 6 new issues, and all were accepted:

- Every policy writer is guarded by ownership (`review-pref` returns 409 once owned).
- Reconnect adopts an owned backend; only a fresh `process_id` is initialised, with `expected_version`; a send waits until that's done.
- One rule for resolving waiting edits: an explicit selection *and* a false→true change of `auto_accept`. The version bumps only on effective change.
- The older-controller tier shows the last acknowledged preference, with a safe reconnect rule.
- The effort fallback keeps the existing per-rung support logic, and the options schema gains `unsupported`.
- Inset and popover colours are specified (dark muted grey is now `#949494`), along with the contextual foreground classes, fallback to the Crucible Dark values, and the contrast floor scoped to our own palettes.
- CSS-property lint, stale-gate and stop-race tests, and corrections to the counts and the clarification-resume wording.

**Revision 6 (Phase 1 dry run)** folds in the Phase 1 implementation reviews:

- `vscode` surfaces are tinted from the page instead of per-surface classes;
- button hover fills are darkened (lightening failed 4.5:1);
- high-contrast legacy borders;
- `color-scheme` and `accent-color` per tone;
- a webview `themeReady` handshake so a remounted hidden panel gets the current theme;
- a failed Appearance save is shown offline;
- `BtnPrimary` uses `--on-accent`.

**Revision 3** folds in round 2 (persistent Codex session `01a13660-cf99-7501-a1cb-f141810d7b23`). Every claim was verified against the code. Round 2 rated 8 of the original findings resolved and 12 partially resolved, and raised 8 new issues. All were accepted:

- Synchronising after a reconnect no longer resolves gates (this also fixes the existing `pushPreferences` bug).
- The backend owns the policy, with a version in `/live`; messages stop writing it after the first PUT.
- Capability tiers, with an honest older-backend fallback.
- Migration reads the backend's effective state.
- Child prompt refresh happens at the next activation; enforcement is immediate.
- The effort slider counts Default as a stop, uses the `unknown` state and keeps the fallback ladder.
- The Appearance section works offline.
- Colour-only cleanup of the other panels.
- Paired surface and button foreground tokens with full fallback chains.
- Complete palettes, with separate link and fill colours per accent.
- The mode was renamed to "Ask for approval", with exceptions listed.
- A wider lint, plus real-browser computed-colour checks.
- The exact audit manifest.
- Font-size wording corrected to `--vscode-font-size`.
- The `getConfig()` mapper change.
- Overlay focus rules, and the context card on touch and Escape.

## References

- The user's Codex screenshots (2026-10-13).
- 21st.dev references, to port onto our tokens: "Tool Group" (`@serafimcloud/tool-group`) and "Input Bar" (`@serafimcloud/input-bar`).
- Installed skills: `redesign-existing-projects` and `web-design-guidelines`.
- [VS Code webview theming](https://code.visualstudio.com/api/extension-guides/webview#theming-webview-content) and the [theme colour reference](https://code.visualstudio.com/api/references/theme-color).
