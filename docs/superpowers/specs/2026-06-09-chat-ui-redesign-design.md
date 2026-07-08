# Chat UI Redesign — Design Spec

**Date:** 2026-06-09  
**Status:** Approved for implementation planning  
**Scope:** Replace `apps/vscode-extension/media/chat.js` + `chat-panel.ts` HTML template with a React + Tailwind webview

---

## Background

The current chat UI is a ~700-line vanilla JS file (`media/chat.js`) that builds the DOM via `innerHTML` strings and inline `style=` attributes. Compared to Cline (React + Tailwind + HeroUI) and Continue (React + Tailwind + styled-components), the gap is fundamental — no component abstraction, no design system, no icons, inconsistent visual hierarchy. This spec defines a full one-shot replacement.

---

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Framework | React 18 + TypeScript + Tailwind v4 + Vite | Same stack as Cline; VS Code CSS vars map directly to Tailwind tokens |
| Panel scope | Unified single panel | Retire the separate Review panel; Accept/Reject appears inline in chat |
| History navigation | Full-view flip (History ↔ Thread) | Narrow sidebar can't accommodate a drawer; clean separation of contexts |
| Visual style | Modern / Linear-inspired | Dark base, purple accent, tool-call pills, soft card borders |
| Migration | One-shot replacement | No shared code with old JS; parallel maintenance adds no value |
| Safety net | Commit current `main` branch by logical group before any work begins | Hard prerequisite — unblocks rollback |

---

## Visual Language

### Colour tokens

All colours are mapped through Tailwind's `theme.css` to VS Code CSS variables so the UI adapts to any VS Code theme. Purple accent is a fixed brand colour layered on top.

```css
--color-base:        #141414                     /* panel background */
--color-surface:     #1a1a1a                     /* card backgrounds */
--color-surface-alt: #1f1f1f                     /* input, pill backgrounds */
--color-border:      #2a2a2a                     /* default borders */
--color-border-subtle: var(--vscode-panel-border)
--color-text:        #e0e0e0                     /* primary text */
--color-text-muted:  #888888                     /* secondary text */
--color-text-dim:    #444444                     /* timestamps, labels */
--color-accent:      #9d6ff0                     /* purple — brand */
--color-accent-bg:   #2a1a3a                     /* purple-tinted backgrounds */
--color-accent-border: #3a2a4a                   /* purple-tinted borders */
--color-success:     #4ade80                     /* diff additions */
--color-error:       #f87171                     /* error states */
--color-error-bg:    #1a1010
--color-error-border: #4a2a2a
--color-code:        #9cdcfe                     /* inline code, file paths */
```

### Typography
- UI text: `var(--vscode-font-family)` system stack
- Code / file paths / tool output: `var(--vscode-editor-font-family)` monospace
- Font sizes scale from `var(--vscode-font-size)` — no hardcoded `px` sizes in components

### Spacing
4px base unit. All padding/gap values are multiples of 4px.

---

## Architecture

### What changes

```
apps/vscode-extension/
  media/
    chat.js              ← DELETED (replaced by React build output)
    marked.umd.js        ← DELETED
  webview-ui/            ← NEW — React app
    src/
      components/
      index.css
      main.tsx
      App.tsx
    tailwind.config.ts
    vite.config.ts
    package.json
  src/
    chat-panel.ts        ← MINIMAL CHANGE: point HTML src at webview-ui build output
```

### How it integrates

`chat-panel.ts` currently inlines a full HTML string. After the migration it loads the compiled `webview-ui/dist/index.html` (Vite output).

**Extension → webview init messages** (`vscode.postMessage` → `window.addEventListener('message', ...)`) stay identical: the extension sends the backend base URL, workspace path, and initial thread ID the same way it does today. No changes to these message types.

**Backend communication** is direct from the React app — same pattern as `chat.js` today: `fetch()` calls and `EventSource` SSE connections go straight to the Python backend using the injected base URL. Nothing is proxied through `chat-panel.ts`.

No changes to the Python backend, no changes to `task-contracts.ts`.

### Build

The `webview-ui` package is built with Vite. The extension's build step runs `vite build` inside `webview-ui/` before packaging. During development, the extension dev host loads the compiled output; hot-reload works by running `vite build --watch` alongside the extension host.

---

## Views

### View 1 — History

Shown on first open and whenever the user navigates back from a thread.

**Layout (top → bottom):**
1. Header bar — "Crucible" title left, `+ New Chat` button right (purple-outlined)
2. Search input — full width, `⌕` icon, placeholder "Search chats…"
3. Scrollable list — items grouped by day label (Today / Yesterday / Last week / older)

**History item row:**
- Full thread title (no truncation via ellipsis is fine — wraps to 2 lines max)
- Timestamp + message count below (e.g. "2 min ago · 3 messages")
- `›` chevron right-aligned, purple on active/hover

**Behaviour:**
- Clicking a row transitions to Thread view (no animation needed for v1; slide-in is v2)
- Active item (last viewed) gets surface-alt background + purple border
- Search filters title text client-side in real time

---

### View 2 — Thread

**Layout (top → bottom):**
1. Header bar — `‹` back chevron (purple) + truncated thread title + `+ New` right
2. Scrollable message list
3. Input area — fixed to bottom

**Back navigation:** `‹` returns to History view. Thread scroll position is preserved on re-open within the same session.

---

## Message Types (Thread View)

Messages render in order. Each type is a separate React component dispatched from `MessageRow`.

### UserMessage
Right-aligned bubble. Background `surface-alt`, border `border`, rounded `10px 10px 2px 10px`. Max-width 85%. Plain text, no markdown.

### AgentRow
Left-aligned. Composed of up to four sub-elements in order:

**1. AI chip** — `18×18px` chip, `accent-bg` fill, `accent-border`, label "AI". Fixed to left, all sub-elements indent to its right.

**2. ThinkingBlock** — collapsible `<details>`-style block.
- Collapsed (default after completion): "▶ Thinking (N steps)" in `text-dim`
- Active/streaming: pulsing dot + "Thinking…" in purple
- Expanded: numbered list of thought entries, scrollable, max-height 200px

**3. ToolPills** — horizontal wrapping row of pills.

Each pill has two states:

*Collapsed:*
- Background `surface-alt`, border `border`, text `text-muted`
- Label: `tool_name ✓` (after completion) or `tool_name` (streaming — see below)
- Streaming active pill: purple border + `accent-bg` + CSS spinner (no ✓)

*Expanded (click to toggle):*
- Border `accent-border`, background `accent-bg`
- Label: `tool_name ✓ ▴`
- Below the pill row, an inline panel appears:
  - **Header**: tool name (purple, bold) + "click to collapse ▴" right-aligned
  - **Input section**: monospace key-value pairs (query, path, pattern, etc.)
  - **Output section**: scrollable, max-height 120px, monospace — match lines / file content / command output truncated at 50 lines with "… N more lines" indicator
- Multiple pills can be expanded simultaneously; each shows its own inline panel directly below the pill row, stacked vertically

**4. Breadcrumb text** — plain `font-size-sm text-muted` lines. Examples:
- `✓ Task task-xxx queued`
- `✓ Plan approved — starting execution`
- `✓ Step N accepted: <step title>`
- `✓ Scope extension approved: <file>`

Copy button (⎘) appears top-right of the full AgentRow on hover. Copies the plain-text representation of the turn (breadcrumbs + tool names + any QA answer text).

### QAMessage
AgentRow variant for question-answering turns. No tool pills. Renders the answer as markdown (using a lightweight markdown renderer — `marked` or `react-markdown`). Copy button on hover.

### PlanCard

**Collapsed (default on arrival):**
- Header row: "📋 Plan" bold + step count right + `▾ expand` purple
- Faded step preview: first 2 steps visible, bottom 44px covered by a `linear-gradient(transparent → surface)` overlay
- Action row (always visible): `Implement` (purple-filled) + `Give feedback` (ghost)

**Expanded:**
- Purple border on card, `surface` background header tinted with `accent-bg`
- Numbered step list (circle badges 1/2/3…): step title bold, target file in `color-code`, description in `text-dim`
- Dividers between steps
- Action row unchanged

**After Implement:** both buttons replaced by breadcrumb `✓ Plan approved — starting execution`. Card becomes read-only (header still toggleable).

**After Give feedback:** buttons replaced by a single-line text input + Send button inline.

### GateCard

Four gate variants share a common card shell (surface background, border, border-radius 8px):

| Variant | Header | Body | Actions |
|---|---|---|---|
| **Command** | "Run command?" | Monospace command in code block, scope radio (exact / prefix / binary) | Accept once · Accept & remember · Reject |
| **Scope** | "Scope extension requested" | Step ID + reason + file list | Approve · Approve & remember · Reject |
| **Validation** | "Validation failed — review" | Diagnostic list (level + message) | Accept · Reject |
| **Step review** | "Review step changes" | Inline diff (same as DiffCard expanded) | Accept · Reject |

After resolution, action row replaced with inline breadcrumb (`✓ Approved` / `✗ Rejected`).

### DiffCard

**Collapsed (default):**
- Header: "📁 Changes ready" bold + file count badge (purple pill) + `▾` toggle right
- Action row (always visible): `Accept all` (purple-filled) + `Reject` (ghost)

**Expanded:**
- File tabs row — one tab per changed file. Active tab has purple underline.
- Diff content area: scrollable, max-height 200px, monospace, `color-dim` for context lines, `#0d2010` background + `#4ade80` text for `+` lines, `#200808` background + `#f87171` text for `-` lines
- Hunk headers (`@@ … @@`) in `text-dim`
- Action row unchanged

**After Accept/Reject:** action row replaced with breadcrumb. Card border colour changes to success/error tint.

### ErrorCard

Triggered when backend reports execution failure.

- Red-tinted card (`error-bg` fill, `error-border`)
- Header: ⚠️ + "Execution failed" in `color-error`
- Collapsible error detail: step name + exception class + last error message (monospace, scrollable, max-height 80px)
- Actions: `↻ Resume from step N` (purple) · `Re-plan` (ghost) · `Discard` (red ghost)

---

## Special States

### Streaming / in-progress
- Active tool pill: purple border + CSS `spin` animation spinner replacing ✓
- ThinkingBlock: pulsing dot + "Thinking…" label
- Blinking cursor character at end of streaming text
- Input textarea: `disabled`, placeholder changes to "Agent is working…"

### Empty state (new thread, no messages)
- Centred layout: `✦` icon in `accent-bg` rounded square, "What are we building?" heading, subtitle "Describe a change, ask a question, or explore the codebase."
- Three suggestion chip buttons (pre-fill input on click). Chips are static strings defined in the component — not dynamic.
- Input area active and focused

### Input area
- Single `<textarea>` with auto-resize (grows to max 5 lines, then scrolls)
- `⌘↵` label right-aligned inside the input border
- Send on `Enter` (not `Shift+Enter`); `Shift+Enter` inserts newline
- Disabled with dimmed style while agent is streaming

---

## What the Review Panel Becomes

The existing "Crucible Review" webview panel (in `review-panel.ts`) is retired. Its only non-redundant feature — viewing Plan JSON and Patch JSON — moves to a future debug mode (v2 roadmap). The Accept/Reject functionality is fully covered by the inline DiffCard.

`review-panel.ts` is deleted. The command registrations in `extension.ts` that open it are removed.

---

## Wireframe Reference

**Visual source of truth: `docs/superpowers/design/chat-ui-hifi.html`** — interactive hi-fi mockup (open in any browser) that supersedes the wireframes below for all visual detail: refined token ramp (layered surfaces, violet accent ramp, hairline highlights), SVG iconography replacing emoji, elevation/shadow system, motion (shimmer streaming pills, work-status bar, pulse/spinner/caret), plan-step timeline connectors, diff line numbers + stats, history status chips, and hover/press states. The wireframes remain the record of the *decisions*; the hi-fi file is the record of the *look*.

All wireframes live at `.superpowers/brainstorm/36442-1781025172/content/` (open in any browser). Files marked ✓ are the approved/final versions; earlier iterations are kept for context.

| File | Status | What it shows |
|---|---|---|
| `full-wireframe-v2.html` | ✓ Final | Complete thread view — AI chip, thinking block, tool pills (collapsed + expanded with scrollable output), diff card with file tabs, plan card, gate cards, breadcrumbs, input area |
| `full-wireframe.html` | Iteration | Earlier full wireframe before tool-pill expansion and diff card enhancements |
| `missing-states.html` | ✓ Final | Four special states: streaming pills with spinner, error/resume card, empty state with suggestion chips, copy button on agent rows |
| `plan-card-v2.html` | ✓ Final | Plan card with faded step preview in collapsed state (gradient overlay) and full expanded state |
| `plan-card.html` | Iteration | Earlier plan card — collapsed/expanded without the faded preview |
| `history-nav-v2.html` | ✓ Final | Four history navigation patterns including the right-drawer and full-view flip (option D, approved) |
| `history-nav.html` | Iteration | Earlier two-pattern comparison of history nav |
| `visual-style.html` | ✓ Final | Three visual style options A/B/C — option C (Modern/Linear with purple accent) was selected |
| `scope.html` | ✓ Final | Two-panel vs unified single-panel comparison — option B (unified) selected |
| `approach.html` | ✓ Final | Three build-approach options — option B (React + Tailwind direct, no Figma) selected |

---

## Future Roadmap (out of scope for this implementation)

- Token / cost tracking per turn (footer line in AgentRow)
- `@mentions` in the input for attaching file/URL context
- Regenerate button on agent turns
- Model / provider selector in header
- Slide-in animation for History ↔ Thread transition
- Debug mode: expandable Plan JSON / Patch JSON raw view
- Workspace indicator in header (for multi-workspace setups)

---

## Non-Goals

- No changes to `chat-panel.ts` message protocol
- No changes to the Python backend or `task-contracts.ts`
- No mobile/responsive design (VS Code panel widths only)
- No new agent capabilities — UI parity with current functionality first
