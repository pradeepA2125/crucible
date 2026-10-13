# Theming Phase 2: Codex-Style Transcript Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the chat transcript read like Codex:
- a centered column;
- agent answers as plain prose with no box and no avatar;
- an accent-filled user bubble;
- tool activity as one plain line per step instead of pills;
- a restyled changes card;
- no gradients or glows.

All of it is themed by Phase 1's tokens.

**Architecture:** Presentation-only changes in `apps/vscode-extension/webview-ui`.
- **Tokens as utilities.** The Phase 1 type and radius tokens become Tailwind utilities (`text-caption`, `rounded-card`, …).
- **One activity line.** A new `ActivityLine` component, driven by a pure `toolLine()` describer, replaces the serpentine `ToolTrack`/`ToolPill` everywhere they are used. The two are then deleted.
- **Lint last.** A one-off codemod moves the remaining literal sizes and radii in the transcript components onto the scale. The Phase 1 lint then covers every transcript file.
- **No backend changes.**

**Tech Stack:** React 18, Tailwind v4, Vite 6, vitest + Testing Library (jsdom).

**Spec:** `docs/superpowers/specs/2026-10-13-codex-style-chat-theming-design.md` (revision 6), §2 Transcript and the Phase 2 rows of Testing. Phase 1's plan, `docs/superpowers/plans/2026-10-13-theming-phase1-foundation.md`, defines the tokens used here.

## Global Constraints

- **Column:** max width `760px`, centered; narrower than that, it fills the panel with `16px` gutters.
- **User bubble:** right-aligned, `--accent-fill` background with `--on-accent` text, `--fs-body`, radius 16px (`--r-composer`), max 80% of the column.
- **Agent answer:** no card, no border, no avatar. `--fs-prose` with line height 1.6 and `text-wrap: pretty`. File references are `--accent` links. Inline code is a neutral monospace chip on `--surface-1`.
- **Activity:**
  - **Folded header:** the existing action summary on a muted `--fs-meta` row with a hairline rule; no elapsed time.
  - **Expanded:** one `--fs-body` line per step (icon, verb, muted target).
  - **Kept:** failed and not-applied outcomes, shared thoughts, and keyboard-reachable tool details.
  - **Live line:** shimmers only its verb, and only when reduced motion is off.
- **Changes card:** one card with radius `--r-card`.
  - Header: "Edited N files" and `+N −N`.
  - Rows: the path muted, the basename in `--text-1`, the per-file `+/−`, and the existing diff expanders.
  - "Show N more files" once there are more than three files.
  - No Undo or View-changes button.
- **Gates and cards:** components and behaviour unchanged; only tokens and type scale change.
- **Removed:** gradients, glows, coloured shadows and shimmer borders. One functional fade-out mask stays (PlanCard's collapse fade).
- **Brand assets are exempt:** `components/shared/ChatGPTBrand.tsx` and `ClaudeBrand.tsx` (CLAUDE.md: the approved "Continue with ChatGPT" button must not be restyled).
- **The type scale is `--fs-caption` 11, `--fs-meta` 12, `--fs-body` 13, `--fs-prose` 14** (px at the default 13px UI font); radii are `--r-control` 6, `--r-card` 10, `--r-composer` 16.
- **Commits:** one per task on the dry-run branch, and never pushed. The user approved this workflow for Phase 1. Confirm once at the start of execution that the same applies to Phase 2.

## Review Focus

1. **A very long unbroken token in an answer** (a URL or deep path) at side-panel width: it wraps, and the transcript never scrolls sideways. Pinned in Task 4 (the wrap classes survive the restyle).
2. **A tool event with missing or odd arguments** (no `path`, `args` null, an unknown MCP tool): the activity line still renders a verb, with no crash and no "undefined". Pinned in Task 3's `toolLine` tests.
3. **A turn with dozens of tool calls:** the expanded list stays inside its capped scroll box instead of pushing the whole transcript down. Pinned in Task 3 (the max-height assertion is kept).
4. **An `@mention` inside the accent-filled user bubble:** it stays readable (on-accent colour, underlined) and opens with the keyboard. Pinned in Task 5.
5. **Exactly three vs four changed files:** three shows no toggle, four shows "Show 1 more file". Pinned in Task 6.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `webview-ui/src/index.css` | modify | Type and radius utilities in `@theme`; the `.shimmer-text` live-verb style |
| `webview-ui/src/styles/themes.css` | modify | `--diff-add-bg` / `--diff-del-bg` |
| `webview-ui/src/components/ThreadView.tsx` | modify | Centered column; avatar-free transcript and tail |
| `webview-ui/src/transcript/summarizeActivity.ts` | modify | `toolLine()`, the verb/target describer; `describeTool` built on it |
| `webview-ui/src/components/activity/ActivityLine.tsx` | create | One tool step: icon, verb, muted target, expandable detail |
| `webview-ui/src/components/activity/{ActivityGroup,TransientActivity,TurnChanges}.tsx` | modify | Lines instead of pills; restyled changes card |
| `webview-ui/src/components/messages/{AgentRow,QAMessage,DiffCard,UserMessage}.tsx` | modify | No avatar; lines instead of pills; the accent bubble |
| `webview-ui/src/components/shared/MarkdownContent.tsx` | modify | Unboxed prose |
| `webview-ui/src/components/shared/{Avatar,ToolTrack,ToolPill}.tsx`, `tool-track-layout.ts` | delete | Replaced |
| gates, cards, shared primitives | modify | Gradients and glows removed; sizes and radii moved onto the scale |
| `webview-ui/src/test/themeLint.test.ts` | modify | Phase 2 scope; decoration check |

All paths below are relative to `apps/vscode-extension/` unless written in full. Run webview commands from `apps/vscode-extension/webview-ui`.

---

### Task 1: Scale utilities and diff tokens

**Files:**
- Modify: `webview-ui/src/index.css` (the `@theme` block; add `.shimmer-text`)
- Modify: `webview-ui/src/styles/themes.css` (the shared `:root` block, and the `hc` accent block)
- Test: `webview-ui/src/test/typeScale.test.ts`

**Interfaces:**
- Produces:
  - Tailwind utilities `text-caption`, `text-meta`, `text-body`, `text-prose`, `rounded-control`, `rounded-card`, `rounded-composer`.
  - CSS tokens `--diff-add-bg` and `--diff-del-bg`.
  - The CSS class `.shimmer-text` (an animated verb; static under reduced motion).

- [ ] **Step 1: Write the failing test**

Create `webview-ui/src/test/typeScale.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const INDEX = readFileSync(join(__dirname, "..", "index.css"), "utf8");
const THEMES = readFileSync(join(__dirname, "..", "styles", "themes.css"), "utf8");

describe("Phase 2 scale utilities (spec §1 type and radius scales)", () => {
  it.each([
    ["--text-caption", "var(--fs-caption)"],
    ["--text-meta", "var(--fs-meta)"],
    ["--text-body", "var(--fs-body)"],
    ["--text-prose", "var(--fs-prose)"],
    ["--radius-control", "var(--r-control)"],
    ["--radius-card", "var(--r-card)"],
    ["--radius-composer", "var(--r-composer)"],
  ])("@theme maps %s to %s", (name, value) => {
    const theme = INDEX.slice(INDEX.indexOf("@theme {"), INDEX.indexOf("}", INDEX.indexOf("@theme {")));
    expect(theme).toContain(`${name}: ${value};`);
  });

  it("defines diff row tints and switches them off in high contrast", () => {
    expect(THEMES).toMatch(/--diff-add-bg: color-mix\(in srgb, var\(--diff-add\) \d+%, transparent\);/);
    expect(THEMES).toMatch(/--diff-del-bg: color-mix\(in srgb, var\(--diff-del\) \d+%, transparent\);/);
    const hc = THEMES.slice(THEMES.indexOf(':root[data-theme="hc"][data-accent] {'));
    expect(hc).toContain("--diff-add-bg: transparent;");
    expect(hc).toContain("--diff-del-bg: transparent;");
  });

  it("animates the live verb only when motion is allowed", () => {
    expect(INDEX).toMatch(/\.shimmer-text \{[^}]*animation: shimmer/);
    expect(INDEX).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\.shimmer-text \{ animation: none;/);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/test/typeScale.test.ts`
Expected: FAIL. The utilities, tokens and class are missing.

- [ ] **Step 3: Implement**

In `webview-ui/src/index.css`, inside the existing `@theme { … }` block, add after `--color-code: var(--accent);`:

```css
  /* Type and radius scales (spec §1) as Tailwind utilities:
     text-caption/meta/body/prose, rounded-control/card/composer. */
  --text-caption: var(--fs-caption);
  --text-meta: var(--fs-meta);
  --text-body: var(--fs-body);
  --text-prose: var(--fs-prose);
  --radius-control: var(--r-control);
  --radius-card: var(--r-card);
  --radius-composer: var(--r-composer);
```

After the `.workbar-line { … }` rule, add:

```css
/* The live activity line's verb (spec §2): a text shimmer on the verb only. */
.shimmer-text {
  background: linear-gradient(90deg, var(--text-2) 40%, var(--text-1) 50%, var(--text-2) 60%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: shimmer 2.2s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .shimmer-text { animation: none; color: var(--text-2); background: none; }
}
```

In `webview-ui/src/styles/themes.css`, add to the end of the first shared `:root { … }` block (after `--warning-border: …;`):

```css
  --diff-add-bg: color-mix(in srgb, var(--diff-add) 10%, transparent);
  --diff-del-bg: color-mix(in srgb, var(--diff-del) 9%, transparent);
```

and inside `:root[data-theme="hc"][data-accent] { … }`, before its closing brace:

```css
  --diff-add-bg: transparent;
  --diff-del-bg: transparent;
```

- [ ] **Step 4: Run the tests and the build**

Run: `npx vitest run src/test/typeScale.test.ts src/test/themeContrast.test.ts src/test/themeLint.test.ts && npx vite build`
Expected: PASS, and the build succeeds. `grep -o "font-size:var(--text-caption)" dist/assets/*.css` returns nothing yet (no utility is used), which is fine.

- [ ] **Step 5: Commit**

```bash
git add webview-ui/src/index.css webview-ui/src/styles/themes.css webview-ui/src/test/typeScale.test.ts
git commit -m "feat(webview): type and radius scale utilities, diff tints, live-verb shimmer"
```

---

### Task 2: Centered column; no agent avatars

**Files:**
- Modify: `webview-ui/src/components/ThreadView.tsx` (`TranscriptItems` and the message list container)
- Modify: `webview-ui/src/components/messages/QAMessage.tsx`, `webview-ui/src/components/messages/AgentRow.tsx`, `webview-ui/src/components/MessageRow.tsx`
- Delete: `webview-ui/src/components/shared/Avatar.tsx`
- Modify tests: `webview-ui/src/components/messages/QAMessage.avatar.test.tsx` (rename to `QAMessage.test.tsx`), `webview-ui/src/components/messages/AgentRow.progress.test.tsx`, `webview-ui/src/test/thread-activity.test.tsx`, `webview-ui/src/components/ThreadView.test.tsx`

**Interfaces:**
- Produces:
  - `TranscriptItems({ …, renderTail?: () => ReactNode })`. The `continuesAgentRun` argument is gone.
  - `QAMessage({ content, thinkingLog })` and `AgentRow({ … })` without `showAvatar`.
  - `MessageRow` without `showAvatar`.
  - The transcript column is the element with `data-testid="transcript-column"`.

- [ ] **Step 1: Write the failing tests**

Rename `components/messages/QAMessage.avatar.test.tsx` to `components/messages/QAMessage.test.tsx` and replace its body:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QAMessage } from "./QAMessage";

// The removed shared Avatar was the only element with h-5 + rounded-md.
const OLD_AVATAR = ".h-5.rounded-md";

describe("QAMessage (spec §2: no card, no border, no avatar)", () => {
  it("renders no avatar and no avatar spacer", () => {
    const { container } = render(<QAMessage content="Final answer." />);
    expect(container.querySelector("[data-testid='avatar-spacer']")).toBeNull();
    expect(container.querySelector(OLD_AVATAR)).toBeNull();
  });
});
```

In `components/messages/AgentRow.progress.test.tsx`, keep the `AVATAR_SELECTOR` constant, delete the two avatar tests, and add:

```tsx
  it("renders no avatar or spacer", () => {
    const { container } = render(<AgentRow content="first" progress />);
    expect(container.querySelector("[data-testid='avatar-spacer']")).toBeNull();
    expect(container.querySelector(AVATAR_SELECTOR)).toBeNull();
  });
```

In `test/thread-activity.test.tsx`, make three changes:

- In `"renders activity lines, narration and the changes card in order"`, replace the `avatar-spacer` assertion with `expect(screen.queryAllByTestId("avatar-spacer")).toHaveLength(0);`.
- Delete the test `"shows the avatar on narration that follows a full-width card"` (avatars no longer exist).
- Replace `"tells renderTail whether the last item continues an agent run"` with:

```tsx
  it("renders the tail after the transcript", () => {
    render(<TranscriptItems messages={[user]} {...common} renderTail={() => <div data-testid="tail" />} />);
    expect(screen.getByTestId("tail")).toBeInTheDocument();
  });
```

Append to `components/ThreadView.test.tsx`. It already mocks `vscodeApi` and `SettingsApp`, and defines `base: AppState`.

```tsx
describe("transcript column (spec §2)", () => {
  it("keeps the transcript in a centered column of at most 760px", () => {
    render(<ThreadView state={base} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    const column = screen.getByTestId("transcript-column");
    expect(column.className).toContain("max-w-[760px]");
    expect(column.className).toContain("mx-auto");
    expect(column.className).toContain("w-full");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/messages src/components/ThreadView.test.tsx src/test/thread-activity.test.tsx`
Expected: FAIL. The avatar elements still render, and no `transcript-column` exists.

- [ ] **Step 3: Implement**

`components/messages/QAMessage.tsx`:
- Remove the `Avatar` import, the `showAvatar` prop and its doc comment.
- Replace the avatar/spacer block with nothing, so the outer `div` becomes `<div className="group relative flex flex-col items-stretch">`. Keep its children: the thinking block and the markdown wrapper, then the copy button.

`components/messages/AgentRow.tsx`:
- Remove the `Avatar` import, the `showAvatar` prop and its doc comment.
- Remove the avatar/spacer block; the outer `div` becomes `<div className="group relative flex flex-col items-stretch">`.

`components/MessageRow.tsx`: remove the `showAvatar` prop from `Props` and every `showAvatar={showAvatar}` it passes to `AgentRow`/`QAMessage`.

`components/ThreadView.tsx`:
- **Imports:** remove `import { Avatar } from "./shared/Avatar";`, and drop `type TranscriptItem` from the `groupTranscript` import (only the deleted `carriesAvatar` used it, and `tsc` fails on the unused import).
- **In `TranscriptItems`:**
  - remove `carriesAvatar` and the `prev`/`showAvatar` computation;
  - change `renderTail?: (continuesAgentRun: boolean) => ReactNode;` to `renderTail?: () => ReactNode;`;
  - render activity and changes items without the avatar column:

```tsx
        if (item.kind === "activity" || item.kind === "changes") {
          return (
            <div key={item.key} className="min-w-0">
              {item.kind === "activity" ? <ActivityGroup steps={item.steps} /> : <TurnChanges files={item.files} />}
            </div>
          );
        }
        return (
          <MessageRow key={item.msg.id ?? `m-${item.index}`} msg={item.msg} planVersion={planVersions.get(item.index)}
            turnActive={turnActive} queued={item.msg.id != null && queuedIds.includes(item.msg.id)}
            onRewindRequest={onRewindRequest} />
        );
```

and end with `{renderTail?.()}`. Delete `const lastItem = …` if nothing else uses it.

- **The tail:** change `renderTail={(continuesAgentRun) => (` to `renderTail={() => (`. Render the transient line without the avatar wrapper:

```tsx
                  {showTransient && (
                    <TransientActivity bubble={transientBubble} startedAt={streamStartedAt}
                      fallback={state.thinkingStatus} waiting={waitingOnGate} />
                  )}
                  {!state.retryStatus && state.streaming?.text && (
                    <AgentRow content={state.streaming.text} streaming hideThinkingBlock />
                  )}
```

- **The message list:** keep the scroller as the outer element, and wrap its contents in the column:

```tsx
      <div className="flex-1 overflow-y-auto py-3 [&>*]:flex-shrink-0">
        <div data-testid="transcript-column" className="mx-auto flex w-full max-w-[760px] flex-col gap-3 px-4 [&>*]:flex-shrink-0">
          {/* existing isEmpty ? <EmptyState/> : <TranscriptItems …/> and the scroll anchor, unchanged */}
        </div>
      </div>
```

The existing `[&>*]:flex-shrink-0` comment explains why children must not shrink. Keep it on the inner column, where the cards now live.

Delete `components/shared/Avatar.tsx`. Then confirm nothing imports it: `grep -rn "shared/Avatar" src` must return nothing. The team components import their own `teams/Avatar`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run && npx tsc --noEmit`
Expected: PASS. If a test outside the three files above still asserts an avatar or spacer, change it to assert their absence, the same way.

- [ ] **Step 5: Commit**

```bash
git add -A webview-ui/src
git commit -m "feat(webview): transcript in a centered 760px column; agent rows without avatars"
```

---

### Task 3: Activity lines replace the pill track

**Files:**
- Modify: `webview-ui/src/transcript/summarizeActivity.ts` (add `toolLine`; rebuild `describeTool` on it)
- Create: `webview-ui/src/components/activity/ActivityLine.tsx`
- Modify: `webview-ui/src/components/activity/ActivityGroup.tsx`, `TransientActivity.tsx`, `webview-ui/src/components/messages/AgentRow.tsx`, `webview-ui/src/components/messages/DiffCard.tsx`, `webview-ui/src/components/shared/ToolDetailPanel.tsx` (doc comment only)
- Delete: `webview-ui/src/components/shared/ToolTrack.tsx`, `ToolPill.tsx`, `tool-track-layout.ts`, `webview-ui/src/test/ToolTrack.test.tsx`, `webview-ui/src/test/tool-track-layout.test.ts`
- Test: `webview-ui/src/transcript/summarizeActivity.test.ts` (create, or extend if present), `webview-ui/src/components/activity/ActivityLine.test.tsx`, the updated `ActivityGroup.test.tsx`, `TransientActivity.test.tsx`, `test/DiffCard.test.tsx`, `test/agentTranscript.test.tsx`

**Interfaces:**
- Consumes: `toolIcon(tool)` (`components/shared/tool-icon.ts`); `ToolDetailPanel({ event })`; the `.shimmer-text` class (Task 1).
- Produces:
  - `toolLine(event: ToolEventView): { verb: string; target: string }`. The verb is present tense while `!event.done` ("Reading") and past tense once done ("Read").
  - `describeTool(event)` keeps its exact present-tense output.
  - `ActivityLine({ event }: { event: ToolEventView })`.

- [ ] **Step 1: Write the failing tests**

Create `webview-ui/src/transcript/summarizeActivity.test.ts`. If the file already exists, append the `describe` blocks.

```ts
import { describe, expect, it } from "vitest";
import { describeTool, toolLine } from "./summarizeActivity";
import type { ToolEventView } from "../types";

const ev = (tool: string, args: Record<string, unknown> | null, done = true, extra: Partial<ToolEventView> = {}): ToolEventView =>
  ({ id: 1, tool, args: args as ToolEventView["args"], source: "explore", done, ...extra });

describe("toolLine (one activity line per step, spec §2)", () => {
  it.each([
    ["read_file", { path: "src/a/upload_client.py" }, "Read", "upload_client.py"],
    ["list_directory", { path: "src" }, "Listed", "src"],
    ["search_code", { pattern: "retry|backoff" }, "Searched for", "“retry|backoff”"],
    ["run_command", { command: "pytest", args: ["tests/test_upload.py"] }, "Ran", "pytest tests/test_upload.py"],
    ["web_search", { query: "vite chunk names" }, "Searched the web for", "“vite chunk names”"],
    ["web_fetch", { url: "https://example.com" }, "Fetched", "https://example.com"],
    ["query_graph", { node: "src/a.ts" }, "Queried the code graph for", "src/a.ts"],
    ["write_todos", { items: [] }, "Updated the todo list", ""],
  ])("%s done → %s %s", (tool, args, verb, target) => {
    expect(toolLine(ev(tool, args))).toEqual({ verb, target });
  });

  it("uses the present tense while the step runs", () => {
    expect(toolLine(ev("read_file", { path: "a.py" }, false))).toEqual({ verb: "Reading", target: "a.py" });
    expect(toolLine(ev("run_command", { command: "pytest" }, false))).toEqual({ verb: "Running", target: "pytest" });
  });

  it("never crashes or prints undefined on missing or odd arguments (Review Focus 2)", () => {
    for (const args of [null, {}, { path: 42 }, { command: null, args: "nope" }]) {
      for (const tool of ["read_file", "run_command", "search_code", "web_fetch", "mcp__github__create_issue"]) {
        const { verb, target } = toolLine(ev(tool, args as Record<string, unknown> | null));
        expect(verb.length).toBeGreaterThan(0);
        expect(`${verb} ${target}`).not.toContain("undefined");
      }
    }
  });

  it("names an unknown tool instead of guessing", () => {
    expect(toolLine(ev("mcp__github__create_issue", {}))).toEqual({ verb: "Used", target: "mcp__github__create_issue" });
  });
});

describe("describeTool keeps its live wording", () => {
  it.each([
    [ev("read_file", { path: "a/b.py" }, false), "Reading b.py"],
    [ev("search_code", { pattern: "x" }, false), "Searching code for “x”"],
    [ev("run_command", { command: "pytest" }, false), "Running pytest"],
    [ev("list_directory", {}, false), "Listing ."],
  ])("%#", (event, text) => {
    expect(describeTool(event)).toBe(text);
  });
});
```

Create `webview-ui/src/components/activity/ActivityLine.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityLine } from "./ActivityLine";

const done = { id: 7, tool: "run_command", args: { command: "pytest -q" }, source: "explore" as const, done: true, output: "3 passed" };

describe("ActivityLine", () => {
  it("is one line: icon, verb, muted target", () => {
    render(<ActivityLine event={done} />);
    const line = screen.getByRole("button", { name: /Ran pytest -q/ });
    expect(line.className).toContain("text-body");
    expect(screen.getByText("pytest -q").className).toContain("text-text-3");
  });

  it("opens the tool's input and output with the keyboard (native button)", () => {
    render(<ActivityLine event={done} />);
    const line = screen.getByRole("button", { name: /Ran pytest -q/ });
    expect(line.tagName).toBe("BUTTON");
    expect(line).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(line);
    expect(line).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("3 passed")).toBeInTheDocument();
  });

  it("marks a failed step", () => {
    render(<ActivityLine event={{ ...done, isError: true }} />);
    expect(screen.getByRole("button", { name: /Ran pytest -q.*failed/ })).toBeInTheDocument();
  });

  it("shimmers only the verb while running", () => {
    render(<ActivityLine event={{ ...done, done: false }} />);
    expect(screen.getByText("Running").className).toContain("shimmer-text");
    expect(screen.getByText("pytest -q").className).not.toContain("shimmer-text");
  });
});
```

Update `components/activity/ActivityGroup.test.tsx`:
- In `"shows a batch's shared thought once above its pills"`, rename the test to `"shows a batch's shared thought once above its lines"`. The assertion is unchanged.
- Append:

```tsx
  it("lists each tool step as one line, not pills (spec §2)", () => {
    render(<ActivityGroup steps={steps} />);
    fireEvent.click(screen.getByRole("button", { name: /Read a file/ }));
    expect(screen.getByRole("button", { name: /Read a\.py/ })).toBeInTheDocument();
    expect(screen.getByTestId("activity-detail").querySelector("[data-event-id]")).toBeNull();
  });

  it("puts the folded header on a muted meta row over a hairline rule", () => {
    render(<ActivityGroup steps={steps} />);
    const header = screen.getByRole("button", { name: /Read a file/ });
    expect(header.className).toContain("text-meta");
    expect(header.className).toContain("text-text-3");
    expect(header.style.borderBottom).toBe("1px solid var(--border)");
  });
```

Update `test/DiffCard.test.tsx` in `"DiffCard — persisted tool pills"`:
- Rename the describe to `"DiffCard — persisted tool steps"`.
- Replace the three tests with:

```tsx
  it("renders a line per persisted tool event", () => {
    render(<DiffCard taskId="t1" diffEntries={ENTRIES} toolEvents={TOOL_EVENTS} />);
    expect(screen.getByRole("button", { name: /Read a\.py/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Ran pytest -q/ })).toBeTruthy();
  });

  it("expands a step to show its input and output", () => {
    render(<DiffCard taskId="t1" diffEntries={ENTRIES} toolEvents={TOOL_EVENTS} />);
    fireEvent.click(screen.getByRole("button", { name: /Ran pytest -q/ }));
    expect(screen.getByText("3 passed")).toBeTruthy();
  });

  it("renders no step lines when toolEvents is absent", () => {
    render(<DiffCard taskId="t1" diffEntries={ENTRIES} />);
    expect(screen.queryByRole("button", { name: /Read a\.py/ })).toBeNull();
  });
```

In `test/agentTranscript.test.tsx` `"renders live pills and a loading state"`:
- Rename it to `"renders live steps and a loading state"`.
- Replace `expect(screen.getByText("read_file")).toBeInTheDocument();` with `expect(screen.getByRole("button", { name: /^Read/ })).toBeInTheDocument();`.

Delete `test/ToolTrack.test.tsx` and `test/tool-track-layout.test.ts`. They test the deleted components.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/transcript src/components/activity src/test/DiffCard.test.tsx src/test/agentTranscript.test.tsx`
Expected: FAIL. `toolLine` and `ActivityLine` don't exist yet, and the pills still render.

- [ ] **Step 3: Implement `toolLine` and rebuild `describeTool` on it**

In `transcript/summarizeActivity.ts`, replace the whole `describeTool` function with:

```ts
/** One activity line's words (spec §2): a verb, then a muted target. Present tense while
 * the step runs ("Reading a.py"), past once it is done ("Read a.py"). Missing or odd
 * arguments degrade to an empty target, never "undefined". */
export function toolLine(event: ToolEventView): { verb: string; target: string } {
  const args = (event.args ?? {}) as Record<string, unknown>;
  const str = (value: unknown): string => (typeof value === "string" ? value : "");
  const live = !event.done;
  const pick = (running: string, finished: string) => (live ? running : finished);
  const quoted = (value: string) => (value ? `“${value}”` : "");
  switch (event.tool) {
    case "read_file":
      return { verb: pick("Reading", "Read"), target: basename(str(args.path)) };
    case "list_directory":
      return { verb: pick("Listing", "Listed"), target: str(args.path) || "." };
    case "search_code":
    case "search_semantic": {
      const query = str(args.pattern) || str(args.query);
      return query
        ? { verb: pick("Searching code for", "Searched for"), target: quoted(query) }
        : { verb: pick("Searching code", "Searched code"), target: "" };
    }
    case "run_command":
    case "start_session": {
      const extra = Array.isArray(args.args) ? args.args.filter((a) => typeof a === "string").join(" ") : "";
      return { verb: pick("Running", "Ran"), target: [str(args.command), extra].filter(Boolean).join(" ") };
    }
    case "web_search":
      return { verb: pick("Searching the web for", "Searched the web for"), target: quoted(str(args.query)) };
    case "web_fetch":
      return { verb: pick("Fetching", "Fetched"), target: str(args.url) };
    case "query_graph":
      return { verb: pick("Querying the code graph for", "Queried the code graph for"), target: str(args.node) };
    case "write_stdin":
      return { verb: pick("Sending input to a session", "Sent input to a session"), target: "" };
    case "kill_session":
      return { verb: pick("Stopping a session", "Stopped a session"), target: "" };
    case "list_sessions":
      return { verb: pick("Listing sessions", "Listed sessions"), target: "" };
    case "dispatch_agents":
      return { verb: pick("Starting agents", "Started agents"), target: "" };
    case "write_todos":
      return { verb: pick("Updating the todo list", "Updated the todo list"), target: "" };
    default:
      return { verb: pick("Using", "Used"), target: event.tool };
  }
}

/** The live line's text: the running step's verb and target. */
export function describeTool(event: ToolEventView): string {
  const { verb, target } = toolLine({ ...event, done: false });
  return target ? `${verb} ${target}` : verb;
}
```

`basename` already exists at the top of the file. An empty `read_file` path makes `basename("")` return `""`, which is fine.

- [ ] **Step 4: Create `ActivityLine`**

Create `webview-ui/src/components/activity/ActivityLine.tsx`:

```tsx
import { useState } from "react";
import type { ToolEventView } from "../../types";
import { toolLine } from "../../transcript/summarizeActivity";
import { Icon } from "../Icon";
import { ToolDetailPanel } from "../shared/ToolDetailPanel";
import { toolIcon } from "../shared/tool-icon";

/** One tool step as one line (spec §2): icon, verb, muted target. A native button, so the
 * step's input/output opens from the keyboard as well as the mouse. While the step runs,
 * only the verb shimmers (static under reduced motion — see .shimmer-text). */
export function ActivityLine({ event }: { event: ToolEventView }) {
  const [open, setOpen] = useState(false);
  const { verb, target } = toolLine(event);
  return (
    <div className="grid min-w-0 gap-1">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex min-w-0 items-center gap-2 text-left text-body text-text-2 hover:text-text">
        <span aria-hidden="true" className="shrink-0 text-text-3"><Icon name={toolIcon(event.tool)} size={13} /></span>
        <span className={event.done ? "shrink-0" : "shrink-0 shimmer-text"}>{verb}</span>
        {target && <span className="min-w-0 truncate text-text-3">{target}</span>}
        {event.isError && <span className="shrink-0" style={{ color: "var(--danger)" }}>failed</span>}
      </button>
      {open && <ToolDetailPanel event={event} />}
    </div>
  );
}
```

- [ ] **Step 5: Use it everywhere the track was used**

**`components/activity/ActivityGroup.tsx`:**
- Replace `import { ToolTrack } from "../shared/ToolTrack";` with `import { ActivityLine } from "./ActivityLine";`.
- In `ToolsStep`, replace `<ToolTrack events={run.events} />` with `{run.events.map((event) => <ActivityLine key={event.id} event={event} />)}`.
- Change the thought line's classes to `"text-meta text-text-3"`.
- Replace the header button and the detail box:

```tsx
      <button type="button" aria-expanded={open} aria-label={summary} onClick={() => setOpen(!open)}
        className="flex min-w-0 items-center gap-1.5 pb-1 text-left text-meta text-text-3 hover:text-text-2"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <span data-testid="activity-summary" className="min-w-0 truncate">{summary}</span>
        <span className="shrink-0">{open ? "▾" : "›"}</span>
      </button>
      {open && (
        <div data-testid="activity-detail" className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-1.5 overflow-y-auto"
          style={{ maxHeight: "320px" }}>
          {steps.map((step, i) => step.type === "tools" ? <ToolsStep key={i} events={step.events} />
            : step.type === "edit" ? <EditRow key={i} step={step} />
            : <div key={i} className="text-body text-text-3">{step.text.replace(/^[✓✗]\s*/, "")}</div>)}
        </div>
      )}
```

- In `EditRow`, change the button classes to `"flex min-w-0 items-center gap-2 text-left text-body text-text-2 hover:text-text"`, and use `var(--diff-add)`/`var(--diff-del)` for the `+`/`−` colours.

**`components/activity/TransientActivity.tsx`:**
- Replace `import { ToolTrack } from "../shared/ToolTrack";` with `import { ActivityLine } from "./ActivityLine";`.
- Replace the detail box with:

```tsx
      {open && unfinished.length > 0 && (
        <div data-testid="transient-detail" className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-1.5 overflow-y-auto"
          style={{ maxHeight: "320px" }}>
          {unfinished.map((event) => <ActivityLine key={event.id} event={event} />)}
        </div>
      )}
```

- Change the text row's `text-[12px]` to `text-meta`.
- Make the live verb shimmer: replace `<span data-testid="transient-text" className="min-w-0 truncate">{text}</span>` with

```tsx
        <span data-testid="transient-text" className="min-w-0 truncate">
          {waiting || !unfinished.length ? text : (
            <><span className="shimmer-text">{toolLine(unfinished[0]).verb}</span>
              {text.slice(toolLine(unfinished[0]).verb.length)}</>
          )}
        </span>
```

  and import `toolLine` alongside `describeTool`.

**`components/messages/AgentRow.tsx`:** replace `import { ToolTrack } from "../shared/ToolTrack";` with `import { ActivityLine } from "../activity/ActivityLine";`, and `{pills.length > 0 && <ToolTrack events={pills} />}` with

```tsx
        {pills.length > 0 && (
          <div className="grid min-w-0 gap-1.5">{pills.map((event) => <ActivityLine key={event.id} event={event} />)}</div>
        )}
```

**`components/messages/DiffCard.tsx`:** the same swap, replacing `<ToolTrack events={toolEvents} />` with the identical `grid` of `ActivityLine`s.

**`components/shared/ToolDetailPanel.tsx`:** change its doc comment's second sentence to "Mounted under its ActivityLine."

**Delete** `components/shared/ToolTrack.tsx`, `components/shared/ToolPill.tsx` and `components/shared/tool-track-layout.ts`. Then confirm nothing imports them: `grep -rnE "ToolTrack|ToolPill|tool-track-layout" src` must return nothing.

- [ ] **Step 6: Run the tests, typecheck and build**

Run: `npx vitest run && npx tsc --noEmit && npx vite build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A webview-ui/src
git commit -m "feat(webview): one activity line per tool step; remove the pill track"
```

---

### Task 4: Agent answers as plain prose

**Files:**
- Modify: `webview-ui/src/components/shared/MarkdownContent.tsx`
- Test: `webview-ui/src/test/MarkdownContent.test.tsx`

**Interfaces:**
- Consumes: `text-prose`, `rounded-control` (Task 1).
- Produces: `MarkdownContent({ content })` with no box. `MARKDOWN_WRAP_CLASSES` and `MARKDOWN_TABLE_CLASSES` stay exported.

- [ ] **Step 1: Write the failing test**

Append to `webview-ui/src/test/MarkdownContent.test.tsx`. Keep the existing imports; add `render` from `@testing-library/react` if missing.

```tsx
describe("MarkdownContent as plain prose (spec §2)", () => {
  it("has no box: no border, background or radius", () => {
    const { container } = render(<MarkdownContent content="Plain answer." />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.border).toBe("");
    expect(root.style.background).toBe("");
    expect(root.style.borderRadius).toBe("");
  });

  it("uses the prose size, primary text and a 1.6 line height", () => {
    const { container } = render(<MarkdownContent content="Plain answer." />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("text-prose");
    expect(root.className).toContain("text-text");
    expect(root.className).toContain("leading-[1.6]");
    expect(root.className).toContain("[&_p]:[text-wrap:pretty]");
  });

  it("renders inline code as a neutral chip on surface-1", () => {
    const { container } = render(<MarkdownContent content="Use `retry()` here." />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("[&_:not(pre)>code]:bg-[var(--surface-1)]");
    expect(root.className).not.toContain("[&_code]:text-code");
  });

  it("still wraps a very long unbroken token (Review Focus 1)", () => {
    const { container } = render(<MarkdownContent content={"x".repeat(400)} />);
    expect((container.firstElementChild as HTMLElement).className).toContain("[overflow-wrap:anywhere]");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/test/MarkdownContent.test.tsx`
Expected: FAIL. The box style is present and the classes differ.

- [ ] **Step 3: Implement**

Replace the `MarkdownContent` component (keep the exports above it) with:

```tsx
/**
 * An agent answer as plain prose (spec §2): no card, no border, no avatar. Prose size,
 * primary text, 1.6 line height, `text-wrap: pretty`. Inline code is a neutral chip on
 * surface-1; file references keep their accent links (MessageMarkdown). Shared by
 * QAMessage and AgentRow so an answer looks the same with or without tool steps.
 */
export function MarkdownContent({ content }: Props) {
  return (
    <div
      className={[
        "self-stretch",
        ...MARKDOWN_WRAP_CLASSES,
        "text-prose text-text leading-[1.6]",
        "[&_p]:[text-wrap:pretty]",
        // Inline code: a neutral chip (not code-coloured)
        "[&_:not(pre)>code]:mono [&_:not(pre)>code]:bg-[var(--surface-1)] [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:rounded-control",
        // Pre blocks
        "[&_pre]:mono [&_pre]:bg-[var(--surface-1)] [&_pre]:rounded-control [&_pre]:p-2",
        // Paragraphs
        "[&_p]:mb-2 [&_p:last-child]:mb-0",
        // Lists
        "[&_ul]:list-disc [&_ul]:pl-4 [&_ul]:mb-2",
        "[&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:mb-2",
        // Tables (GFM)
        ...MARKDOWN_TABLE_CLASSES,
      ].join(" ")}
    >
      <MessageMarkdown content={content} />
    </div>
  );
}
```

`MARKDOWN_TABLE_CLASSES` contains `[&_table]:text-[11px]`. Change it to `[&_table]:text-meta` (PlanCard shares it).

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/test/MarkdownContent.test.tsx src/test/markdownWrapping.test.tsx src/components/messages`
Expected: PASS. If `markdownWrapping.test.tsx` asserted the old box, change it to assert the wrap classes only.

- [ ] **Step 5: Commit**

```bash
git add webview-ui/src/components/shared/MarkdownContent.tsx webview-ui/src/test/MarkdownContent.test.tsx webview-ui/src/test/markdownWrapping.test.tsx
git commit -m "feat(webview): agent answers as unboxed prose"
```

---

### Task 5: The accent user bubble

**Files:**
- Modify: `webview-ui/src/components/messages/UserMessage.tsx`
- Test: `webview-ui/src/components/messages/UserMessage.test.tsx`

**Interfaces:**
- Consumes: `--accent-fill`, `--on-accent`, `text-body`, `rounded-composer` (Task 1).
- Produces: the same `UserMessage` props as today.

- [ ] **Step 1: Write the failing tests**

Append to `webview-ui/src/components/messages/UserMessage.test.tsx`. Keep its existing imports; it already mocks `vscodeApi`. Check with `grep -n "vi.mock" src/components/messages/UserMessage.test.tsx` and add `vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));` plus `import { vscode } from "../../vscodeApi";` if missing.

```tsx
describe("UserMessage bubble (spec §2)", () => {
  it("is an accent-filled bubble with on-accent body text, at most 80% wide", () => {
    render(<UserMessage content="Add a retry" />);
    const bubble = screen.getByText("Add a retry").closest("[data-testid='user-bubble']") as HTMLElement;
    expect(bubble.style.background).toBe("var(--accent-fill)");
    expect(bubble.style.color).toBe("var(--on-accent)");
    expect(bubble.className).toContain("text-body");
    expect(bubble.className).toContain("rounded-composer");
    expect((bubble.parentElement as HTMLElement).className).toContain("max-w-[80%]");
  });

  it("renders a mention as a real button, readable on the accent fill, opening with the keyboard (Review Focus 4)", () => {
    render(<UserMessage content="look at @src/a.ts please" mentionedFiles={["src/a.ts"]} />);
    const mention = screen.getByRole("button", { name: "@src/a.ts" });
    expect(mention.tagName).toBe("BUTTON");
    expect(mention.style.color).toBe("var(--on-accent)");
    expect(mention.style.textDecoration).toBe("underline");
    mention.focus();
    fireEvent.click(mention);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFile", path: "src/a.ts" });
  });

  it("shows the rewind action on keyboard focus as well as hover", () => {
    render(<UserMessage content="hi" onRewind={() => {}} />);
    const rewind = screen.getByRole("button", { name: "Rewind to here" });
    expect(rewind.className).toContain("group-hover:opacity-100");
    expect(rewind.className).toContain("group-focus-within:opacity-100");
    expect(rewind.className).toContain("focus-visible:opacity-100");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/messages/UserMessage.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `UserMessage.tsx`, make four changes.

1. In `renderTextSegment`, replace the mention `<span role="button" …>` with a native button:

```tsx
            <button
              key={i}
              type="button"
              onClick={() => vscode.postMessage({ type: "openFile", path: piece.slice(1) })}
              className="inline cursor-pointer border-0 bg-transparent p-0 font-[inherit]"
              style={{ color: "var(--on-accent)", textDecoration: "underline" }}
            >
              {piece}
            </button>
```

2. Change the outer wrapper's `max-w-[86%]` to `max-w-[80%]`.

3. Change the rewind button's classes to `"absolute -left-6 top-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 transition-opacity"`.

4. Replace the bubble `div` (the one with the gradient style) and its inline code with:

```tsx
        <div
          data-testid="user-bubble"
          className="max-w-full rounded-composer px-3 py-2 text-body leading-relaxed whitespace-pre-wrap break-words"
          style={{ background: "var(--accent-fill)", color: "var(--on-accent)" }}
        >
          {codeParts.map((part, i) => {
            if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
              return (
                <code key={i} className="mono rounded-control px-1"
                  style={{ background: "color-mix(in srgb, var(--on-accent) 16%, transparent)" }}>
                  {part.slice(1, -1)}
                </code>
              );
            }
            return renderTextSegment(part, `seg-${i}`);
          })}
          {queued && (
            <div data-testid="queued-tag" className="mt-1 text-caption" style={{ opacity: 0.85 }}>
              Queued — the agent picks this up at its next step
            </div>
          )}
        </div>
```

In the image tiles, change `rounded-[10px]` to `rounded-card` (twice) and `text-[10px]` to `text-caption`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/components/messages src/test/queuedMessage.test.tsx src/test/image-previews.test.tsx`
Expected: PASS. If `queuedMessage.test.tsx` asserted the old tag colour, assert the `queued-tag` text only.

- [ ] **Step 5: Commit**

```bash
git add webview-ui/src/components/messages/UserMessage.tsx webview-ui/src/components/messages/UserMessage.test.tsx webview-ui/src/test/queuedMessage.test.tsx
git commit -m "feat(webview): accent-filled user bubble; mentions are real buttons; rewind on focus"
```

---

### Task 6: The changes card

**Files:**
- Modify: `webview-ui/src/components/activity/TurnChanges.tsx`
- Test: `webview-ui/src/components/activity/TurnChanges.test.tsx`

**Interfaces:**
- Consumes: `FileChange` (`transcript/groupTranscript.ts`); `DiffPanes`; `rounded-card`, `text-body`, `text-meta`, `--diff-add`/`--diff-del`.
- Produces: `TurnChanges({ files })`, unchanged signature. `data-testid="turn-changes"` is kept.

- [ ] **Step 1: Write the failing tests**

Append to `components/activity/TurnChanges.test.tsx`, reusing its imports and mocks:

```tsx
const f = (path: string): FileChange => ({ path, additions: 2, deletions: 1, applied: true });

describe("TurnChanges card (spec §2)", () => {
  it("is one card: radius card, header 'Edited N files' with totals", () => {
    render(<TurnChanges files={[f("a/b.py"), f("c.ts")]} />);
    const card = screen.getByTestId("turn-changes");
    expect(card.className).toContain("rounded-card");
    expect(screen.getByText("Edited 2 files")).toBeInTheDocument();
    expect(screen.getByTestId("turn-changes-totals").textContent).toBe("+4 −2");
  });

  it("shows the basename in primary text and the folder muted", () => {
    render(<TurnChanges files={[f("services/agentd/client.py")]} />);
    expect(screen.getByText("client.py").className).toContain("text-text");
    expect(screen.getByText("services/agentd/").className).toContain("text-text-3");
  });

  it("shows three files with no toggle, and four with 'Show 1 more file' (Review Focus 5)", () => {
    const { rerender } = render(<TurnChanges files={[f("a"), f("b"), f("c")]} />);
    expect(screen.queryByRole("button", { name: /Show \d+ more/ })).toBeNull();
    rerender(<TurnChanges files={[f("a"), f("b"), f("c"), f("d")]} />);
    expect(screen.queryByText("d")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 more file" }));
    expect(screen.getByText("d")).toBeInTheDocument();
  });
});
```

Add `FileChange` to the imports (`import type { FileChange } from "../../transcript/groupTranscript";`), and `fireEvent` if missing.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/activity/TurnChanges.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `Row` and `TurnChanges` in `TurnChanges.tsx` (keep `sameFiles`):

```tsx
const VISIBLE = 3;

function splitPath(path: string): { dir: string; base: string } {
  const slash = path.lastIndexOf("/");
  return slash === -1 ? { dir: "", base: path } : { dir: path.slice(0, slash + 1), base: path.slice(slash + 1) };
}

function Row({ file }: { file: FileChange }) {
  const [open, setOpen] = useState(false);
  const { dir, base } = splitPath(file.path);
  return (
    <div className="grid min-w-0 gap-1">
      <div className="flex min-w-0 items-center gap-2 text-body">
        <button type="button" onClick={() => vscode.postMessage({ type: "openFile", path: file.path })}
          className="min-w-0 truncate text-left hover:underline" title={file.path}>
          {dir && <span className="text-text-3">{dir}</span>}<span className="text-text">{base}</span>
        </button>
        <span className="flex-1" />
        {file.applied ? (
          <span className="shrink-0 font-mono text-meta">
            <span style={{ color: "var(--diff-add)" }}>+{file.additions}</span>{" "}
            <span style={{ color: "var(--diff-del)" }}>−{file.deletions}</span>
          </span>
        ) : <span className="shrink-0 text-meta text-text-3">not applied</span>}
        {file.diff && (
          <button type="button" aria-label={`Show diff for ${file.path}`} aria-expanded={open} onClick={() => setOpen(!open)}
            className="px-1.5 py-0.5 text-text-3 hover:text-text">{open ? "▾" : "›"}</button>
        )}
      </div>
      {open && file.diff && <DiffPanes entries={[{ path: file.path, additions: file.additions, deletions: file.deletions, unified_diff: file.diff }]} />}
    </div>
  );
}

export const TurnChanges = memo(function TurnChanges({ files }: { files: FileChange[] }) {
  const [expanded, setExpanded] = useState(false);
  const applied = files.filter((f) => f.applied);
  const add = applied.reduce((s, f) => s + f.additions, 0);
  const del = applied.reduce((s, f) => s + f.deletions, 0);
  const title = applied.length ? `Edited ${applied.length} file${applied.length === 1 ? "" : "s"}` : "No changes applied";
  const shown = expanded ? files : files.slice(0, VISIBLE);
  const hidden = files.length - shown.length;
  return (
    <div data-testid="turn-changes" className="grid min-w-0 overflow-hidden rounded-card"
      style={{ border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 px-3 py-2" style={{ background: "var(--surface-2)" }}>
        <Icon name="diff" size={14} />
        <span className="text-body text-text">{title}</span>
        {applied.length > 0 && (
          <span data-testid="turn-changes-totals" className="font-mono text-meta">
            <span style={{ color: "var(--diff-add)" }}>+{add}</span>{" "}
            <span style={{ color: "var(--diff-del)" }}>−{del}</span>
          </span>
        )}
      </div>
      <div className="grid gap-1.5 px-3 py-2" style={{ borderTop: "1px solid var(--border)" }}>
        {shown.map((f) => <Row key={f.path} file={f} />)}
        {hidden > 0 && (
          <button type="button" onClick={() => setExpanded(true)} className="justify-self-start text-meta text-text-2 hover:text-text">
            {`Show ${hidden} more file${hidden === 1 ? "" : "s"}`}
          </button>
        )}
      </div>
    </div>
  );
}, (prev, next) => sameFiles(prev.files, next.files));
```

Add `import { Icon } from "../Icon";`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/components/activity src/test/thread-activity.test.tsx`
Expected: PASS. Existing `TurnChanges` tests that matched the old path text, `text-[var(--color-code)]` or `+N` spans may need their selectors updated to the new markup. Keep their intent: the open-file and diff-toggle behaviour are unchanged.

- [ ] **Step 5: Commit**

```bash
git add webview-ui/src/components/activity/TurnChanges.tsx webview-ui/src/components/activity/TurnChanges.test.tsx
git commit -m "feat(webview): restyled changes card with folder/basename rows and 'Show N more'"
```

---

### Task 7: Remove gradients and glows

**Files:**
- Modify: `webview-ui/src/components/shared/{buttons,Switch,ThinkingBlock,ToolDetailPanel,CardShell}.tsx`, `webview-ui/src/components/messages/{TodoCard,PlanCard,DiffCard,ErrorCard}.tsx`, `webview-ui/src/components/messages/gates/*.tsx`
- Modify: `webview-ui/src/test/themeLint.test.ts`

**Interfaces:**
- Consumes: `--accent-fill`, `--accent-fill-hover`, `--on-accent`, `--accent-bg`, `--surface-2`.

- [ ] **Step 1: Write the failing test**

In `webview-ui/src/test/themeLint.test.ts`, add after the existing `describe`:

```ts
/** Transcript components must be flat (spec §2: gradients, glows, coloured shadows and
 * shimmer borders removed). A line ending in `// decoration-ok: <reason>` is allowed. */
export const DECORATION_SCOPE = [
  "components/shared/buttons.tsx", "components/shared/Switch.tsx", "components/shared/ThinkingBlock.tsx",
  "components/shared/ToolDetailPanel.tsx", "components/shared/CardShell.tsx", "components/shared/MarkdownContent.tsx",
  "components/messages/TodoCard.tsx", "components/messages/PlanCard.tsx", "components/messages/DiffCard.tsx",
  "components/messages/ErrorCard.tsx", "components/messages/UserMessage.tsx",
  "components/messages/gates/ClarifyGate.tsx", "components/messages/gates/CommandGate.tsx",
  "components/messages/gates/EditGate.tsx", "components/messages/gates/McpGate.tsx",
  "components/messages/gates/ModeGate.tsx", "components/messages/gates/ScopeGate.tsx",
  "components/messages/gates/StepGate.tsx", "components/messages/gates/TeamPlanGate.tsx",
  "components/messages/gates/ValidationGate.tsx",
];
const DECORATION = /linear-gradient|radial-gradient|--accent-glow|shimmer-bg|0_10px_24px/;

describe("transcript decoration lint", () => {
  for (const file of DECORATION_SCOPE) {
    it(`${file} has no gradients or glows`, () => {
      const offenders = lines(file)
        .map((text, i) => ({ text, line: i + 1 }))
        .filter(({ text }) => DECORATION.test(text) && !text.includes("decoration-ok:"))
        .map(({ line, text }) => `${file}:${line}: ${text.trim()}`);
      expect(offenders).toEqual([]);
    });
  }
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/test/themeLint.test.ts`
Expected: FAIL, listing the gradient, glow and card-shadow lines.

- [ ] **Step 3: Implement**

| File | Change |
|---|---|
| `buttons.tsx` (`BtnPrimary`) | `background: "linear-gradient(…)"` → `background: "var(--accent-fill)"`; delete the `boxShadow` line. Add `hover:bg-[var(--accent-fill-hover)]` to its classes. Update the doc comment to "Flat accent button: --accent-fill, --on-accent text." |
| `Switch.tsx` | Checked background → `"var(--accent-fill)"`; `boxShadow` → `"none"` in both states (or delete the property); update the doc comment (no gradient or glow). |
| `ThinkingBlock.tsx` | Delete the `style={{ boxShadow: "0 0 8px var(--accent-glow)" }}` attribute. |
| `CommandGate.tsx` (~line 263) | Delete the `boxShadow: "0 0 6px var(--accent-glow)",` line. |
| `TodoCard.tsx` (~line 111) | Progress fill `background: "linear-gradient(90deg, …)"` → `background: "var(--accent-fill)"`. |
| `ToolDetailPanel.tsx` | Header `style={{ background: "linear-gradient(180deg, var(--accent-bg), transparent)" }}` → `style={{ background: "var(--surface-2)" }}`. The outer `boxShadow` → delete it, keep the border, and change `borderColor: "var(--accent-brd)"` to `"var(--border)"`. |
| `CardShell.tsx`, `DiffCard.tsx`, `PlanCard.tsx`, `ErrorCard.tsx` | Delete the class `shadow-[inset_0_1px_0_var(--hairline),0_10px_24px_-14px_rgba(0,0,0,.55)]` (flat bordered cards). In `CardShell` the `className` array then has one entry; keep it as a string. |
| every gate in `components/messages/gates/` | `headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"` → `headerTint="var(--accent-bg)"` (a flat tint). |
| `PlanCard.tsx` (~line 152) | Keep the collapse fade, which is functional, not decoration. Append ` // decoration-ok: collapse fade mask` to that line. |

`MarkdownContent.tsx` and `UserMessage.tsx` are already flat after Tasks 4 and 5.

- [ ] **Step 4: Run the tests and build**

Run: `npx vitest run && npx vite build`
Expected: PASS. If `test/components.test.tsx` or `src/test/Switch.test.tsx` asserted the gradient or glow, change them to assert `var(--accent-fill)`.

- [ ] **Step 5: Commit**

```bash
git add -A webview-ui/src
git commit -m "refactor(webview): flat transcript cards and controls — no gradients or glows"
```

---

### Task 8: Move the remaining literals onto the scale; full transcript lint

**Files:**
- Modify: every non-test `.tsx` under `webview-ui/src/components/messages/`, `components/activity/` and `components/shared/` (except `ChatGPTBrand.tsx` and `ClaudeBrand.tsx`), plus `components/ThreadView.tsx` and `components/MessageRow.tsx`
- Modify: `webview-ui/src/test/themeLint.test.ts`

**Interfaces:**
- Consumes: the Task 1 utilities and the `--diff-*-bg` tokens.

- [ ] **Step 1: Extend the lint scope so it fails**

In `webview-ui/src/test/themeLint.test.ts`, add the imports `import { readdirSync, statSync } from "node:fs";`, and append to the file:

```ts
/** Phase 2 (spec Testing → Lint): every transcript component, brand assets excepted. */
function tsxUnder(dir: string): string[] {
  return readdirSync(join(SRC, dir)).flatMap((name) => {
    const rel = `${dir}/${name}`;
    if (statSync(join(SRC, rel)).isDirectory()) return tsxUnder(rel);
    return name.endsWith(".tsx") && !name.includes(".test.") ? [rel] : [];
  });
}
const BRAND_EXEMPT = new Set(["components/shared/ChatGPTBrand.tsx", "components/shared/ClaudeBrand.tsx"]);
export const PHASE2_SCOPE = [
  ...tsxUnder("components/messages"), ...tsxUnder("components/activity"), ...tsxUnder("components/shared"),
  "components/ThreadView.tsx", "components/MessageRow.tsx",
].filter((f) => !BRAND_EXEMPT.has(f));

describe("theme lint — Phase 2 transcript scope", () => {
  for (const file of PHASE2_SCOPE) {
    it(`${file} uses tokens only`, () => {
      const offenders = lines(file)
        .map((text, i) => ({ text, line: i + 1 }))
        .filter(({ text }) => !text.trim().startsWith("//") && !text.trim().startsWith("*"))
        .filter(({ text }) => COLOR_LITERAL.test(text) || TW_SIZE.test(text) || TW_RADIUS.test(text) || INLINE_SIZE.test(text))
        .map(({ line, text }) => `${file}:${line}: ${text.trim()}`);
      expect(offenders).toEqual([]);
    });
  }
});
```

The Phase 1 `LINT_SCOPE` and `COLOR_ONLY` entries stay as they are.

Run: `npx vitest run src/test/themeLint.test.ts`
Expected: FAIL, listing roughly 150 literal sizes, radii and colours across the transcript components.

- [ ] **Step 2: Run the one-off codemod**

From `apps/vscode-extension/webview-ui/src`, run this script once. Don't commit it.

```bash
python3 - <<'EOF'
import re, glob
files = [f for f in glob.glob("components/messages/**/*.tsx", recursive=True)
         + glob.glob("components/activity/*.tsx") + glob.glob("components/shared/*.tsx")
         + ["components/ThreadView.tsx", "components/MessageRow.tsx"]
         if ".test." not in f and "Brand" not in f]
SIZE = {  # px → scale step (the scale's floor is 11px: spec §1, "text too small")
  **{f"text-[{p}px]": "text-caption" for p in ("9", "9.5", "10", "10.5", "11")},
  **{f"text-[{p}px]": "text-meta" for p in ("11.5", "12")},
  "text-[13px]": "text-body", "text-[14px]": "text-prose",
  "text-xs": "text-meta", "text-sm": "text-body", "text-base": "text-prose",
}
RADIUS = {
  **{f"rounded-[{p}px]": "rounded-control" for p in ("3", "4", "5", "6", "7")},
  **{f"rounded-[{p}px]": "rounded-card" for p in ("8", "9", "10", "11", "12")},
  "rounded-sm": "rounded-control", "rounded-md": "rounded-control",
  "rounded-lg": "rounded-card", "rounded-xl": "rounded-card", "rounded-2xl": "rounded-composer",
}
changed = 0
for path in files:
    src = open(path).read()
    out = src
    for old, new in {**SIZE, **RADIUS}.items():
        out = re.sub(r"(?<![\w-])" + re.escape(old) + r"(?![\w\[-])", new, out)
    if out != src:
        open(path, "w").write(out); changed += 1
print("files changed:", changed)
EOF
```

- [ ] **Step 3: Hand-edit what the codemod can't**

Run `npx vitest run src/test/themeLint.test.ts` and fix each remaining offender. These were measured at plan time:

| File:line | Replace | With |
|---|---|---|
| `MessageRow.tsx` (`TaskCreatedRow`) | `fontSize: "11px",` | `fontSize: "var(--fs-caption)",` |
| `MessageRow.tsx` (`TaskCreatedRow`) | `fontSize: "10px"` | `fontSize: "var(--fs-caption)"` |
| `ErrorCard.tsx` (~102) | `background: "rgba(0,0,0,.35)",` | `background: "var(--surface-1)",` |
| `ReviewCard.tsx` (~118) | `style={{ fontSize: 9 }}` | `style={{ fontSize: "var(--fs-caption)" }}` |
| `DiffPanes.tsx` (~40) | `row: "bg-[rgba(74,222,128,.07)] text-[#b6f0c8]"` | `row: "bg-[var(--diff-add-bg)] text-text"` |
| `DiffPanes.tsx` (~41) | `row: "bg-[rgba(248,113,113,.06)] text-[#f3b8b8]"` | `row: "bg-[var(--diff-del-bg)] text-text"` |
| `FileRow.tsx` (~9) | `return { background: "#3b82f6" };` | `return { background: "var(--color-code)" };` |
| `ImageLightbox.tsx` (~31) | `boxShadow: "0 18px 48px rgba(0,0,0,.45)"` | `boxShadow: "var(--shadow-popover)"` |

Any other offender left after the codemod is an unmapped size (for example `text-[8px]` → `text-caption`) or radius (`rounded-[14px]` → `rounded-composer`). Map it to the nearest scale step and record it in the ledger.

- [ ] **Step 4: Run everything**

Run: `npx vitest run && npx tsc --noEmit && npx vite build`
Expected: PASS. Tests that asserted a literal class (for example `text-[11px]`) are updated to the scale class they now carry. Keep each test's intent.

- [ ] **Step 5: Commit**

```bash
git add -A webview-ui/src
git commit -m "refactor(webview): transcript components on the type and radius scale; Phase 2 lint scope"
```

---

### Task 9: Live verification and audit

**Files:** none new; this task verifies.

- [ ] **Step 1: Build and install from the worktree (extension only)**

```bash
bash scripts/dev/install-local.sh --no-backend
```

- [ ] **Step 2: Restart VS Code with CDP**

Back up `~/Library/Application Support/Code/User/settings.json` first. Quit VS Code (`osascript -e 'quit app "Visual Studio Code"'`), reopen it with `open -a "Visual Studio Code" --args --remote-debugging-port=9335 "<repo>"`, and open the Crucible chat on a thread with tool activity, an edit and a mention.

- [ ] **Step 3: Check the transcript**

Capture `Page.captureScreenshot` at side-panel width and at editor-tab width, in Match VS Code (dark editor) and in Crucible Light. Confirm against the Codex screenshots and the spec:
- the column is centered, at most 760px;
- the answer is prose with no box and no avatar;
- the user bubble is accent-filled;
- the folded activity header has a hairline rule, and expanding it gives one line per step;
- the changes card shows the header, rows and "Show N more";
- no gradients or glows remain.

Run `node scripts/dev/cdp-theme-check.mjs --require "Crucible Chat"`; it must still pass. Send one live message and confirm the transient line shimmers only its verb. Then emulate reduced motion over CDP (`Emulation.setEmulatedMedia` with `prefers-reduced-motion: reduce`) and confirm the verb is static.

- [ ] **Step 4: Audit**

Invoke `web-design-guidelines` on:
- `components/activity/{ActivityLine,ActivityGroup,TransientActivity,TurnChanges}.tsx`
- `components/messages/{UserMessage,QAMessage,AgentRow}.tsx`
- `components/shared/MarkdownContent.tsx`
- `components/ThreadView.tsx`

Fix each finding test-first, or record why it's kept in the fix commit message.

- [ ] **Step 5: Restore and commit**

Restore any `settings.json` lines the run added. Commit any audit fixes:

```bash
git add -A webview-ui/src
git commit -m "fix(webview): Phase 2 audit fixes"
```

---

## Self-review notes

- **Spec §2 coverage:**
  - column — Task 2;
  - user bubble, mention buttons and focus-visible actions — Task 5;
  - unboxed answer, prose size and line height, pretty wrap, accent file links (already in `MessageMarkdown`), neutral code chip — Task 4;
  - activity: header row with hairline, one line per step, kept outcomes and thoughts, keyboard tool detail, shimmer-only-verb, reduced motion — Tasks 1 and 3;
  - changes card — Task 6;
  - gates and cards on tokens — Tasks 7 and 8;
  - decoration removal — Task 7;
  - Phase 2 lint scope — Task 8;
  - live checks and audit — Task 9.
- **Spec drift:** spec §2 says "Gates and cards align to the column edge". With avatars gone, every item is a full-width column child, so this holds with no extra work.
- **Deferred to Phase 3:** composer, approval mode, effort slider, WorkBar restyle, settings gear move, and `ChatSettingsOverlay` focus management.
- **Names used consistently:** `toolLine`, `ActivityLine`, `transcript-column`, `user-bubble`, `turn-changes-totals`, `DECORATION_SCOPE`, `PHASE2_SCOPE`, `.shimmer-text`, `--diff-add-bg`/`--diff-del-bg`, `rounded-card`/`rounded-control`/`rounded-composer`, `text-caption`/`text-meta`/`text-body`/`text-prose`.
