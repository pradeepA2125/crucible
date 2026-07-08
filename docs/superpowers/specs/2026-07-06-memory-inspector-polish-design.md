# Memory inspector polish — design

**Date:** 2026-07-06
**Status:** approved by user, ready for planning
**Parent:** copilot-parity roadmap, Phase 4 scope C ("design-system consistency pass ... incl. memory inspector palette")

## Context

The memory harness's inspector (`webview-ui/src/memory/{MemoryApp,BrowserTab,RecallTraceTab}.tsx`, a
separate Vite entry at `memory.html`) predates the design-system pass done for chat + settings
(`86ddeed style(webview): reusable design-system primitives + semantic color tints`). It still
hardcodes its own slate hex palette (`#0b1220`, `#1e293b`, `#2563eb`, ...) instead of the shared
`--color-*` custom properties and `.surface-card`/`.menu-item` primitives now used everywhere else
in the webview. There is also no way to open the memory inspector from the chat window today — only
via the VS Code command palette (`crucible.openMemoryPanel`) or the status bar.

This is two small, independent, contained pieces of work.

## Scope

### 1. Palette migration

`memory/main.tsx` already imports the shared `../index.css` — the tokens are available, the
components just never adopted them. Replace hardcoded hex across `MemoryApp.tsx`, `BrowserTab.tsx`,
`RecallTraceTab.tsx` with:

- Backgrounds/surfaces → `var(--color-bg)`, `var(--color-surface)`
- Text → `var(--color-text)`, `var(--color-text-2)`, `var(--color-text-3)`
- Borders → `var(--color-border)`, `var(--color-border-strong)`
- Interactive/accent → `var(--color-accent)`, `.menu-item` for list rows and tab buttons
- Card-like containers (detail panel, filter bar) → `.surface-card` where the shape fits

The four memory-**kind** accent colors (`semantic`/`procedural`/`episodic`/the trace-signal colors)
are domain-meaningful, not theme colors (they distinguish memory kinds from each other, not light
vs. dark surfaces) — keep them as a small local constant, but pull the actual values toward the
existing semantic-tint palette in `settings/sections/meta.ts` so they read as part of the same
system rather than an unrelated hardcoded set.

No behavior change. No new props, no new tests beyond updating any existing test that asserts a
specific hex string (if any — check `BrowserTab.test.tsx`/`RecallTraceTab.test.tsx`/`MemoryApp.test.tsx`).

### 2. Chat-window shortcut

Add a third icon button to `ThreadView`'s header row, alongside the existing ☰ (settings drawer
toggle): a "memory/brain" icon, `title="Memory Inspector"`, same visual treatment (hover states,
size) as the existing header buttons.

- Click → `vscode.postMessage({type: "openMemoryPanel"})`.
- `chat-panel.ts`'s `onDidReceiveMessage` gains a branch: `m.type === "openMemoryPanel"` →
  `vscode.commands.executeCommand("crucible.openMemoryPanel")`.
- No new capability-flag plumbing into the webview: the command already exists
  (`extension.ts:392`) and already degrades gracefully — if `CRUCIBLE_MEMORY_ENABLED` is off it
  shows an info message ("The memory inspector is disabled...") instead of opening a panel. The
  button is always visible; the existing command handles the disabled case.
- Opens as today: a separate VS Code panel (`MemoryPanel`, its own webview/tab) — **not** inline
  in the chat window. An inline floating-overlay version (mirroring how Settings now opens as a
  floating card on top of chat) is explicitly **deferred** to a future pass; this spec only adds
  discoverability via a shortcut button.

## Out of scope (explicitly deferred)

- Merging the memory inspector into the Settings pane/bundle as a section (considered and rejected
  by the user — keep it a separate panel/command).
- An inline floating-overlay presentation of the memory inspector inside the chat webview (deferred
  to a future spec, once desired).
- Any change to the memory inspector's data/functionality (recall trace, browse, supersede chain) —
  visual only.

## Testing

- Visual/manual check in the dev host: open the memory inspector via the new header button with
  memory enabled (panel opens) and disabled (info message, no panel).
- Any existing memory-panel snapshot/hex-color assertions updated to match the new token-based
  styling.
- Existing `ThreadView`/`chat-panel` test suites gain a case for the new header button → message →
  command-execution wiring (mirroring the existing ☰ button test pattern).

## Effort

Small — CSS/JSX-only, no contract changes, no backend changes. Estimated well under a day.
