# Serpentine Tool Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `flex-wrap` grid of chat tool pills with a serpentine track — one continuous line threading the pills, turning at the row edge, where reading order follows the road and the road ends on a dot.

**Architecture:** A new `ToolTrack` component owns row layout and rail drawing. Because `flex-wrap` cannot alternate direction per wrapped line, the browser can no longer do the wrapping: pill widths are measured once per distinct `(tool, state)` pair into a module-level cache, greedy-packed into rows for the current container width, and rendered into explicit row elements with `flex-direction` set per row. All geometry lives in a pure, DOM-free module (`tool-track-layout.ts`) because jsdom reports zero for every layout metric — that module is where the real tests live.

**Tech Stack:** React 18, TypeScript (strict), Tailwind CSS v4, Vitest + jsdom + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-08-07-serpentine-tool-track-design.md`
**Approved wireframe:** `https://claude.ai/code/artifact/ca7c76ea-3377-4649-bfa2-dba4e5c7fda4`

## Global Constraints

- Scope is `apps/vscode-extension/webview-ui` only. No backend, no `editor-client` contract, no Zod schema, no `types.ts` change.
- `webview-ui` is **not** an npm workspace. Run its commands with `cd apps/vscode-extension/webview-ui` (the repo-level `npm run test` invokes it via `npm --prefix`).
- No `any`. No untyped returns. Strict typing throughout.
- The rendered pill button must stay visually identical to today: same markup, icon map, status colours, hover, running shimmer, chevron rotation.
- Colours come from existing CSS custom properties only — `var(--color-border-strong)`, `var(--color-panel)`, `var(--color-surface)`, `var(--accent-brd)`. Do not introduce new tokens or hex literals.
- Geometry constants are fixed by the spec: `SIDE_PAD` 28, `COL_GAP` 14, `ROW_GAP` 24, `TURN_RADIUS` 12, `TURN_INSET` 9, `RAIL_OVERSHOOT` 8, `CAP_RADIUS` 3.5.
- Comments explain **why**, not what. English only.
- Commit after every task. Never `git push`.

## File Structure

| File | Responsibility |
|---|---|
| `src/components/shared/tool-track-layout.ts` (create) | Pure geometry: `packRows`, `buildTrackPath`, the constants. Zero DOM, zero React. |
| `src/components/shared/tool-icon.ts` (create) | `toolIcon(tool)` — moved out of `ToolPill` so both the pill and the panel can use it without importing each other. |
| `src/components/shared/ToolDetailPanel.tsx` (create) | The expanded input/output panel, extracted verbatim from `ToolPill`. |
| `src/components/shared/ToolTrack.tsx` (create) | Measurement, packing, row rendering, panel mounting, rail drawing. The only new stateful component. |
| `src/components/shared/ToolPill.tsx` (modify) | Loses its `expanded` state and its panel JSX; becomes a controlled button. |
| `src/components/messages/AgentRow.tsx` (modify) | Swaps its `flex flex-wrap` wrapper for `<ToolTrack>`. |
| `src/components/messages/DiffCard.tsx` (modify) | Same swap. |
| `src/test/setup.ts` (modify) | Stubs `ResizeObserver`, which jsdom does not implement. |
| `src/test/tool-track-layout.test.ts` (create) | The substantive tests — pure geometry, no DOM. |
| `src/test/ToolTrack.test.tsx` (create) | Structural tests with injected widths and a stubbed `clientWidth`. |
| `src/test/components.test.tsx` (modify) | Existing `ToolPill` tests updated for the controlled props. |

---

### Task 1: Pure row packing

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/shared/tool-track-layout.ts`
- Test: `apps/vscode-extension/webview-ui/src/test/tool-track-layout.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `packRows(widths: number[], avail: number, gap: number): number[][]` and the exported geometry constants `SIDE_PAD`, `COL_GAP`, `ROW_GAP`, `TURN_RADIUS`, `TURN_INSET`, `RAIL_OVERSHOOT`, `CAP_RADIUS` (all `number`). Task 4 uses `packRows`, `SIDE_PAD`, `COL_GAP`, `ROW_GAP`. Task 6 uses the rest.

- [ ] **Step 1: Write the failing test**

Create `apps/vscode-extension/webview-ui/src/test/tool-track-layout.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { packRows } from "../components/shared/tool-track-layout";

describe("packRows", () => {
  it("returns no rows for no pills", () => {
    expect(packRows([], 300, 14)).toEqual([]);
  });

  it("keeps pills that fit on one row", () => {
    expect(packRows([40, 40, 40], 300, 14)).toEqual([[0, 1, 2]]);
  });

  it("counts the gap between pills but not before the first", () => {
    // 40 + 14 + 40 = 94 — fits in 94 exactly, does not fit in 93.
    expect(packRows([40, 40], 94, 14)).toEqual([[0, 1]]);
    expect(packRows([40, 40], 93, 14)).toEqual([[0], [1]]);
  });

  it("gives an over-wide pill its own row rather than dropping it", () => {
    expect(packRows([200], 100, 14)).toEqual([[0]]);
  });

  it("does not merge neighbours around an over-wide pill", () => {
    expect(packRows([40, 200, 40], 100, 14)).toEqual([[0], [1], [2]]);
  });

  it("breaks into as many rows as the width demands", () => {
    // Each pill 40 wide, gap 14 => 2 per row at width 100 (40+14+40=94).
    expect(packRows([40, 40, 40, 40, 40], 100, 14)).toEqual([[0, 1], [2, 3], [4]]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/tool-track-layout.test.ts`
Expected: FAIL — cannot resolve `../components/shared/tool-track-layout`.

- [ ] **Step 3: Write minimal implementation**

Create `apps/vscode-extension/webview-ui/src/components/shared/tool-track-layout.ts`:

```ts
/**
 * Pure geometry for the serpentine tool track.
 *
 * Deliberately free of React and the DOM: jsdom reports zero for every layout
 * metric, so this module is the only place the track's behaviour can actually
 * be asserted. The component measures; this module decides.
 */

/** Horizontal lane reserved on each side for the U-turns, as row padding. */
export const SIDE_PAD = 28;
/** Gap between pills within a row. */
export const COL_GAP = 14;
/** Vertical gap between rows. */
export const ROW_GAP = 24;
/** Corner radius of a U-turn. */
export const TURN_RADIUS = 12;
/** Distance from the container edge to the U-turn's vertical run. */
export const TURN_INSET = 9;
/** How far the rail runs past the first and last pill of a row. */
export const RAIL_OVERSHOOT = 8;
/** Radius of the start ring and end dot. */
export const CAP_RADIUS = 3.5;

/**
 * Greedy-pack pill widths into rows that fit `avail`.
 *
 * Returns indices into `widths`, grouped by row, in call order. A pill wider
 * than `avail` gets a row to itself and overflows within it — the same thing
 * flex-wrap does today, rather than being dropped or shrunk.
 */
export function packRows(widths: number[], avail: number, gap: number): number[][] {
  const rows: number[][] = [];
  let current: number[] = [];
  let used = 0;

  for (let i = 0; i < widths.length; i++) {
    const cost = current.length === 0 ? widths[i] : gap + widths[i];
    if (current.length > 0 && used + cost > avail) {
      rows.push(current);
      current = [];
      used = 0;
    }
    used += current.length === 0 ? widths[i] : gap + widths[i];
    current.push(i);
  }

  if (current.length > 0) rows.push(current);
  return rows;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/tool-track-layout.test.ts`
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/shared/tool-track-layout.ts \
        apps/vscode-extension/webview-ui/src/test/tool-track-layout.test.ts
git commit -m "feat(chat): pure row packing for the tool track"
```

---

### Task 2: Pure rail path

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/shared/tool-track-layout.ts`
- Test: `apps/vscode-extension/webview-ui/src/test/tool-track-layout.test.ts`

**Interfaces:**
- Consumes: the constants from Task 1.
- Produces:
  ```ts
  export interface RowGeometry { y: number; startX: number; endX: number; dirRight: boolean }
  export interface TrackCap { cx: number; cy: number }
  export interface TrackPath { d: string; startCap: TrackCap | null; endCap: TrackCap | null }
  export function buildTrackPath(rows: RowGeometry[], width: number): TrackPath
  ```
  Task 6 calls `buildTrackPath` and writes the result onto SVG elements.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/webview-ui/src/test/tool-track-layout.test.ts`:

```ts
import { buildTrackPath, type RowGeometry } from "../components/shared/tool-track-layout";

describe("buildTrackPath", () => {
  it("draws nothing for no rows", () => {
    const path = buildTrackPath([], 300);
    expect(path.d).toBe("");
    expect(path.startCap).toBeNull();
    expect(path.endCap).toBeNull();
  });

  it("draws a bare rail with both caps for a single row", () => {
    const rows: RowGeometry[] = [{ y: 10, startX: 28, endX: 200, dirRight: true }];
    const path = buildTrackPath(rows, 300);
    // Overshoots 8px before the first pill and 8px past the last.
    expect(path.d).toBe("M 20 10 L 208 10");
    expect(path.startCap).toEqual({ cx: 20, cy: 10 });
    expect(path.endCap).toEqual({ cx: 208, cy: 10 });
  });

  it("turns on the right after a left-to-right row", () => {
    const rows: RowGeometry[] = [
      { y: 10, startX: 28, endX: 272, dirRight: true },
      { y: 44, startX: 272, endX: 28, dirRight: false },
    ];
    const path = buildTrackPath(rows, 300);
    // Turn axis is width - TURN_INSET = 291.
    expect(path.d).toBe(
      "M 20 10 L 279 10 Q 291 10 291 22 L 291 32 Q 291 44 279 44 L 20 44"
    );
    // The road ends at the last row's last pill, not at an edge.
    expect(path.endCap).toEqual({ cx: 20, cy: 44 });
  });

  it("turns on the left after a right-to-left row", () => {
    const rows: RowGeometry[] = [
      { y: 10, startX: 28, endX: 272, dirRight: true },
      { y: 44, startX: 272, endX: 28, dirRight: false },
      { y: 78, startX: 28, endX: 150, dirRight: true },
    ];
    const path = buildTrackPath(rows, 300);
    // Second turn runs down the left axis at TURN_INSET = 9.
    expect(path.d).toContain("Q 9 44 9 56");
    expect(path.d).toContain("Q 9 78 21 78");
    expect(path.d.endsWith("L 158 78")).toBe(true);
    expect(path.endCap).toEqual({ cx: 158, cy: 78 });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/tool-track-layout.test.ts`
Expected: FAIL — `buildTrackPath` is not exported.

- [ ] **Step 3: Write minimal implementation**

Append to `apps/vscode-extension/webview-ui/src/components/shared/tool-track-layout.ts`:

```ts
/** One rendered row, measured. `startX`/`endX` are the road's entry and exit. */
export interface RowGeometry {
  y: number;
  /** x of the row's earliest call, on the side the road enters. */
  startX: number;
  /** x of the row's latest call, on the side the road leaves. */
  endX: number;
  dirRight: boolean;
}

export interface TrackCap {
  cx: number;
  cy: number;
}

export interface TrackPath {
  d: string;
  startCap: TrackCap | null;
  endCap: TrackCap | null;
}

/**
 * Build the serpentine's SVG path.
 *
 * Even rows run left-to-right and turn on the right; odd rows run right-to-left
 * and turn on the left, so the call after a row's last pill is the next row's
 * first pill directly beneath it. The final row never runs on to an edge — it
 * stops at its last pill, where the caller draws the end dot.
 */
export function buildTrackPath(rows: RowGeometry[], width: number): TrackPath {
  if (rows.length === 0) return { d: "", startCap: null, endCap: null };

  const axisRight = width - TURN_INSET;
  const axisLeft = TURN_INSET;
  const lead = (row: RowGeometry) => (row.dirRight ? -RAIL_OVERSHOOT : RAIL_OVERSHOOT);
  const tail = (row: RowGeometry) => (row.dirRight ? RAIL_OVERSHOOT : -RAIL_OVERSHOOT);

  let d = `M ${rows[0].startX + lead(rows[0])} ${rows[0].y}`;

  rows.forEach((row, index) => {
    if (index === rows.length - 1) {
      d += ` L ${row.endX + tail(row)} ${row.y}`;
      return;
    }
    const axis = row.dirRight ? axisRight : axisLeft;
    // Approach direction: a rightward row meets its turn from the left.
    const approach = row.dirRight ? -1 : 1;
    const nextY = rows[index + 1].y;
    d +=
      ` L ${axis + approach * TURN_RADIUS} ${row.y}` +
      ` Q ${axis} ${row.y} ${axis} ${row.y + TURN_RADIUS}` +
      ` L ${axis} ${nextY - TURN_RADIUS}` +
      ` Q ${axis} ${nextY} ${axis + approach * TURN_RADIUS} ${nextY}`;
  });

  const first = rows[0];
  const last = rows[rows.length - 1];
  return {
    d,
    startCap: { cx: first.startX + lead(first), cy: first.y },
    endCap: { cx: last.endX + tail(last), cy: last.y },
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/tool-track-layout.test.ts`
Expected: PASS — 10 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/shared/tool-track-layout.ts \
        apps/vscode-extension/webview-ui/src/test/tool-track-layout.test.ts
git commit -m "feat(chat): pure rail path builder for the tool track"
```

---

### Task 3: Extract the detail panel, make the pill controlled

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/shared/tool-icon.ts`
- Create: `apps/vscode-extension/webview-ui/src/components/shared/ToolDetailPanel.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/shared/ToolPill.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/test/components.test.tsx:23-80`

**Interfaces:**
- Consumes: `ToolEventView` from `../../types`, `Icon`/`IconName` from `../Icon`.
- Produces:
  - `toolIcon(tool: string): IconName`
  - `ToolDetailPanel({ event }: { event: ToolEventView })`
  - `ToolPill({ event, expanded, onToggle }: { event: ToolEventView; expanded: boolean; onToggle: () => void })` — renders a bare `<button>`, no wrapper, no panel. The button carries `data-event-id={event.id}`: `ToolTrack` needs to find a specific pill's box to aim the panel caret, and a stable identity attribute on the element itself beats a wrapper element that exists only to hold one. Task 4 renders it; Task 5 renders `ToolDetailPanel`.

**Note:** `AgentRow.tsx` and `DiffCard.tsx` still render `<ToolPill event={…}/>` and will not typecheck after this task. That is expected and fixed in Task 7. Run tests, not `typecheck`, at this task's gate.

- [ ] **Step 1: Write the failing test**

Replace the whole `describe("ToolPill", …)` block at `src/test/components.test.tsx:25-80` with:

```tsx
describe("ToolPill", () => {
  it("running: shows the tool name and does not call onToggle when clicked", () => {
    const onToggle = vi.fn();
    const event = makeEvent({ done: false });
    render(<ToolPill event={event} expanded={false} onToggle={onToggle} />);

    expect(screen.getByText("read_file")).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("done: clicking calls onToggle", () => {
    const onToggle = vi.fn();
    const event = makeEvent({ done: true, output: "line 1\nline 2" });
    render(<ToolPill event={event} expanded={false} onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("done: reflects expanded state on aria-expanded", () => {
    const event = makeEvent({ done: true });
    const { rerender } = render(
      <ToolPill event={event} expanded={false} onToggle={() => {}} />
    );
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");

    rerender(<ToolPill event={event} expanded={true} onToggle={() => {}} />);
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
  });

  it("never renders the detail panel itself", () => {
    const event = makeEvent({ done: true, output: "line 1" });
    render(<ToolPill event={event} expanded={true} onToggle={() => {}} />);
    expect(screen.queryByText("INPUT")).toBeNull();
    expect(screen.queryByText("OUTPUT")).toBeNull();
  });
});

describe("ToolDetailPanel", () => {
  it("renders the tool name, input args and output", () => {
    const event = makeEvent({
      done: true,
      isError: false,
      args: { path: "src/foo.ts" },
      output: "line 1\nline 2",
    });
    render(<ToolDetailPanel event={event} />);

    expect(screen.getByText("INPUT")).toBeTruthy();
    expect(screen.getByText("OUTPUT")).toBeTruthy();
    expect(screen.getByText("path:")).toBeTruthy();
    expect(screen.getByText(/line 1/)).toBeTruthy();
    // Line count badge comes from the output.
    expect(screen.getByText("2 lines")).toBeTruthy();
  });

  it("renders error output", () => {
    const event = makeEvent({
      done: true,
      isError: true,
      args: { cmd: "npm test" },
      output: "FAIL src/foo.ts",
    });
    render(<ToolDetailPanel event={event} />);
    expect(screen.getByText(/FAIL src\/foo\.ts/)).toBeTruthy();
  });
});
```

Then update the imports at the top of the file (`src/test/components.test.tsx:1-8`):

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ToolPill } from "../components/shared/ToolPill";
import { ToolDetailPanel } from "../components/shared/ToolDetailPanel";
import { ThinkingBlock } from "../components/shared/ThinkingBlock";
import { AgentRow } from "../components/messages/AgentRow";
import { UserMessage } from "../components/messages/UserMessage";
import { MessageRow } from "../components/MessageRow";
import type { ToolEventView } from "../types";
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/components.test.tsx`
Expected: FAIL — cannot resolve `../components/shared/ToolDetailPanel`.

- [ ] **Step 3a: Create the shared icon map**

Create `apps/vscode-extension/webview-ui/src/components/shared/tool-icon.ts` (moved verbatim out of `ToolPill.tsx:10-21` so the pill and the panel can share it without importing each other):

```ts
import type { IconName } from "../Icon";

/** Map tool names to icons. */
export function toolIcon(tool: string): IconName {
  const map: Record<string, IconName> = {
    search_code: "search",
    read_file: "file",
    run_command: "term",
    query_graph: "diff",
    list_directory: "list",
    search_semantic: "search",
  };
  return map[tool] ?? "bolt";
}
```

- [ ] **Step 3b: Create the detail panel**

Create `apps/vscode-extension/webview-ui/src/components/shared/ToolDetailPanel.tsx` — this is `ToolPill.tsx:107-173` lifted out unchanged, with `icon`/`isError`/`outputLineCount` recomputed locally and the `expanded && event.done` guard dropped (the parent decides when to mount it):

```tsx
import type { ToolEventView } from "../../types";
import { Icon } from "../Icon";
import { toolIcon } from "./tool-icon";

interface Props {
  event: ToolEventView;
}

/**
 * Expanded input/output panel for one tool call.
 *
 * Mounted by ToolTrack below the pill's row rather than inside the pill's own
 * column: with rows packed at a fixed width, a ~230px panel under a single chip
 * would blow out its column.
 */
export function ToolDetailPanel({ event }: Props) {
  const icon = toolIcon(event.tool);
  const isError = event.isError === true;
  const outputLineCount = event.output ? event.output.split("\n").length : 0;

  return (
    <div
      className="anim-rise rounded-lg border overflow-hidden"
      style={{
        borderColor: "var(--accent-brd)",
        background: "var(--color-surface)",
        boxShadow: "inset 0 1px 0 var(--hairline), 0 8px 20px -10px rgba(0,0,0,.5)",
      }}
    >
      <div
        className="flex items-center gap-1.5 px-[11px] py-[7px] border-b border-border"
        style={{ background: "linear-gradient(180deg, var(--accent-bg), transparent)" }}
      >
        <Icon name={icon} size={11} className="text-accent" />
        <span className="mono text-[11px] font-semibold text-accent-ink">{event.tool}</span>

        {event.thought && (
          <span className="text-[11px] italic text-text-3 ml-1">{event.thought}</span>
        )}

        <span className="ml-auto flex items-center gap-2 text-[9.5px] text-text-4">
          {outputLineCount > 0 && (
            <span
              className="text-[9px] font-semibold text-green px-1.5 py-px rounded-full border"
              style={{ background: "var(--green-bg)", borderColor: "var(--green-brd)" }}
            >
              {outputLineCount} lines
            </span>
          )}
          <span>collapse</span>
        </span>
      </div>

      <div className="px-[11px] py-2 border-b border-border">
        <div className="text-[9px] font-semibold uppercase tracking-widest text-text-4 mb-1.5">
          INPUT
        </div>
        <div className="mono text-[10.5px] leading-[1.8]">
          {Object.entries(event.args).map(([k, v]) => (
            <div key={k}>
              <span className="text-text-3">{k}:</span>{" "}
              <span className="text-accent-ink">
                {typeof v === "string" ? v : JSON.stringify(v)}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="px-[11px] py-2">
        <div className="text-[9px] font-semibold uppercase tracking-widest text-text-4 mb-1.5">
          OUTPUT
        </div>
        <pre
          className={[
            "mono text-[10.5px] leading-[1.75] max-h-24 overflow-y-auto whitespace-pre-wrap break-all",
            isError ? "text-red" : "text-text-2",
          ].join(" ")}
        >
          {event.output ?? ""}
        </pre>
      </div>
    </div>
  );
}
```

- [ ] **Step 3c: Make the pill controlled**

Replace the entire contents of `apps/vscode-extension/webview-ui/src/components/shared/ToolPill.tsx`:

```tsx
import type { ToolEventView } from "../../types";
import { Icon } from "../Icon";
import { toolIcon } from "./tool-icon";

interface Props {
  event: ToolEventView;
  expanded: boolean;
  onToggle: () => void;
}

/**
 * Single tool pill — a node on the serpentine track.
 *
 * Running: shimmer-bg gradient with spinner, not clickable.
 * Done ok: surface bg with green check.
 * Done error: red x with red border.
 *
 * Expansion state is owned by ToolTrack, which mounts the detail panel below
 * the row rather than inside this component.
 */
export function ToolPill({ event, expanded, onToggle }: Props) {
  const running = !event.done;
  const isError = event.isError === true;

  function handleClick() {
    if (!event.done) return;
    onToggle();
  }

  const pillBase =
    "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full mono text-[11px] border transition-colors duration-150 flex-none";

  let pillStyle: React.CSSProperties = {};
  let pillClass = pillBase;

  if (running) {
    pillClass += " shimmer-bg border-[var(--accent-brd)] text-accent-ink cursor-default";
  } else if (expanded) {
    pillClass += " border-[var(--accent-brd)] text-accent-ink";
    pillStyle = { background: "var(--accent-bg)" };
  } else if (isError) {
    pillClass += " bg-surface border-[var(--red-brd)] text-text-3 cursor-pointer";
  } else {
    pillClass += " bg-surface border-border text-text-3 cursor-pointer hover:border-border-strong hover:text-text-2";
  }

  const icon = toolIcon(event.tool);

  return (
    <button
      type="button"
      data-event-id={event.id}
      className={pillClass}
      style={pillStyle}
      onClick={handleClick}
      aria-expanded={event.done ? expanded : undefined}
    >
      <Icon name={icon} size={10} />
      <span>{event.tool}</span>

      {running && (
        <span
          className="w-[9px] h-[9px] rounded-full border-2 border-t-accent animate-spin"
          style={{ borderColor: "var(--accent-brd)", borderTopColor: "var(--color-accent)" }}
        />
      )}

      {!running && !isError && <Icon name="check" size={9} className="text-green" />}
      {!running && isError && <Icon name="x" size={9} className="text-red" />}

      {event.done && (
        <Icon
          name="chev-d"
          size={9}
          className={[
            "transition-transform duration-150",
            expanded ? "rotate-180" : "",
          ].join(" ")}
        />
      )}
    </button>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/components.test.tsx`
Expected: PASS. `ToolPill` has 4 tests, `ToolDetailPanel` has 2.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/shared/tool-icon.ts \
        apps/vscode-extension/webview-ui/src/components/shared/ToolDetailPanel.tsx \
        apps/vscode-extension/webview-ui/src/components/shared/ToolPill.tsx \
        apps/vscode-extension/webview-ui/src/test/components.test.tsx
git commit -m "refactor(chat): extract ToolDetailPanel, make ToolPill controlled"
```

---

### Task 4: ToolTrack renders packed rows

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/shared/ToolTrack.tsx`
- Create: `apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/test/setup.ts`

**Interfaces:**
- Consumes: `packRows`, `SIDE_PAD`, `COL_GAP` (Task 1); `ToolPill` (Task 3).
- Produces: `ToolTrack({ events, measureWidths }: { events: ToolEventView[]; measureWidths?: (events: ToolEventView[]) => number[] })`. `measureWidths` is the test seam — when omitted the component measures real pills in the DOM. Task 5 adds panel mounting; Task 6 adds rails; Task 7 renders it at the two call sites.

**Note:** jsdom has no layout engine, so `clientWidth` is 0 and `getBoundingClientRect().width` is 0. Tests stub `clientWidth` on the prototype and inject `measureWidths`.

- [ ] **Step 1a: Stub ResizeObserver**

Replace `apps/vscode-extension/webview-ui/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom";
import { vi } from "vitest";

vi.stubGlobal("acquireVsCodeApi", () => ({ postMessage: vi.fn() }));

// jsdom does not implement ResizeObserver. ToolTrack observes its container to
// re-pack rows; tests drive width through a stubbed clientWidth instead.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
```

- [ ] **Step 1b: Write the failing test**

Create `apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, it, expect, beforeAll } from "vitest";
import { ToolTrack } from "../components/shared/ToolTrack";
import type { ToolEventView } from "../types";

// jsdom has no layout engine. Pin the container width so packing is deterministic.
const CONTAINER_WIDTH = 300;

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => CONTAINER_WIDTH,
  });
});

function makeEvents(count: number): ToolEventView[] {
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1,
    tool: "read_file",
    args: { path: `src/f${i}.ts` },
    source: "execution" as const,
    done: true,
    isError: false,
    output: "ok",
  }));
}

/** Every pill 80 wide => with SIDE_PAD 28 both sides and COL_GAP 14,
 *  avail = 300 - 56 = 244 => 80 + 14 + 80 + 14 + 80 = 268 > 244, so 2 per row. */
const fixedWidths = (events: ToolEventView[]) => events.map(() => 80);

describe("ToolTrack", () => {
  it("renders nothing for no events", () => {
    const { container } = render(<ToolTrack events={[]} measureWidths={fixedWidths} />);
    expect(container.querySelectorAll("[data-row]").length).toBe(0);
  });

  it("packs pills into rows that fit the container", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rows = container.querySelectorAll("[data-row]");
    expect(rows.length).toBe(3);
    expect(rows[0].querySelectorAll("button").length).toBe(2);
    expect(rows[1].querySelectorAll("button").length).toBe(2);
    expect(rows[2].querySelectorAll("button").length).toBe(1);
  });

  it("alternates row direction so reading order follows the road", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rows = container.querySelectorAll("[data-row]");
    expect(rows[0].getAttribute("data-dir")).toBe("r");
    expect(rows[1].getAttribute("data-dir")).toBe("l");
    expect(rows[2].getAttribute("data-dir")).toBe("r");
    // Odd rows must actually reverse visually, not just be labelled.
    expect(rows[1].className).toContain("flex-row-reverse");
    expect(rows[0].className).not.toContain("flex-row-reverse");
  });

  it("justifies full rows but never the last one", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rows = container.querySelectorAll("[data-row]");
    expect(rows[0].getAttribute("data-fill")).toBe("true");
    expect(rows[1].getAttribute("data-fill")).toBe("true");
    expect(rows[2].getAttribute("data-fill")).toBe("false");
  });

  it("renders every event in call order", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    // DOM order is call order regardless of visual direction, so a screen
    // reader announces 1..n in sequence even though row 2 renders reversed.
    const ids = [...container.querySelectorAll("[data-row] button[data-event-id]")].map((el) =>
      el.getAttribute("data-event-id")
    );
    expect(ids).toEqual(["1", "2", "3", "4", "5"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/ToolTrack.test.tsx`
Expected: FAIL — cannot resolve `../components/shared/ToolTrack`.

- [ ] **Step 3: Write minimal implementation**

Create `apps/vscode-extension/webview-ui/src/components/shared/ToolTrack.tsx`:

```tsx
import { useLayoutEffect, useRef, useState } from "react";
import type { ToolEventView } from "../../types";
import { ToolPill } from "./ToolPill";
import { COL_GAP, SIDE_PAD, packRows } from "./tool-track-layout";

/**
 * Pill width is a pure function of (tool, state): the content is an icon, the
 * tool name, a status glyph, and — when not running — a chevron. A turn with
 * 28 calls has only a handful of distinct pairs, so measure each once and keep
 * it for the session. Re-packing on resize then touches no DOM at all.
 */
const widthCache = new Map<string, number>();

type PillState = "run" | "err" | "ok";

function pillState(event: ToolEventView): PillState {
  if (!event.done) return "run";
  return event.isError === true ? "err" : "ok";
}

function pillKey(event: ToolEventView): string {
  return `${event.tool}|${pillState(event)}`;
}

interface Props {
  events: ToolEventView[];
  /** Test seam: supply widths directly instead of measuring the DOM. */
  measureWidths?: (events: ToolEventView[]) => number[];
}

/**
 * Serpentine track of tool pills.
 *
 * flex-wrap cannot alternate direction per wrapped line, so the browser can no
 * longer do the wrapping: pills are measured, greedy-packed for the current
 * width, and rendered into explicit rows whose direction alternates. Even rows
 * run left-to-right, odd rows right-to-left, so the call after a row's last
 * pill sits directly beneath it.
 */
export function ToolTrack({ events, measureWidths }: Props) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const probeRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  // The value is never read — only the re-render it forces matters. Naming the
  // setter alone keeps noUnusedLocals happy and says so.
  const [, bumpMeasuredWidths] = useState(0);

  // Track the container width. useLayoutEffect so the first real measurement
  // lands before paint and the user never sees a mis-packed frame.
  useLayoutEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Plain per-render computation rather than useMemo: these are O(events) over a
  // few dozen items, and memoising them would need a fake dependency just to
  // invalidate after a measure pass.
  const keys = events.map(pillKey);
  const missing = [...new Set(keys)].filter((k) => !widthCache.has(k));
  const needsProbe = measureWidths === undefined && missing.length > 0;

  const widths = measureWidths
    ? measureWidths(events)
    : missing.length > 0
      ? null
      : keys.map((k) => widthCache.get(k) ?? 0);

  const groups =
    widths && width > 0
      ? packRows(widths, Math.max(120, width - SIDE_PAD * 2), COL_GAP)
      : [];

  useLayoutEffect(() => {
    if (!needsProbe || !probeRef.current) return;
    for (const child of Array.from(probeRef.current.children)) {
      const key = (child as HTMLElement).dataset.key;
      if (key) widthCache.set(key, Math.ceil(child.getBoundingClientRect().width));
    }
    // The cache is module-level, so React cannot see that it changed. Force the
    // re-render that reads the fresh widths; it clears `missing`, so this effect
    // no-ops on its next run.
    bumpMeasuredWidths((v) => v + 1);
  }, [needsProbe, missing.join("|")]);

  if (events.length === 0) return null;

  return (
    <div ref={trackRef} className="relative">
      <div className="flex flex-col gap-6">
        {groups.map((indices, rowIndex) => {
          const dirRight = rowIndex % 2 === 0;
          // The last row keeps its natural packing so the road can end on a
          // dot instead of being stretched to an edge it has no content for.
          const fill = rowIndex < groups.length - 1;
          return (
            <div
              key={rowIndex}
              data-row=""
              data-dir={dirRight ? "r" : "l"}
              data-fill={String(fill)}
              className={[
                "flex items-start gap-[14px] px-7",
                dirRight ? "" : "flex-row-reverse",
                fill ? "justify-between" : "",
              ].join(" ")}
            >
              {indices.map((i) => (
                <ToolPill
                  key={events[i].id}
                  event={events[i]}
                  expanded={false}
                  onToggle={() => {}}
                />
              ))}
            </div>
          );
        })}
      </div>

      {needsProbe && (
        <div
          ref={probeRef}
          aria-hidden="true"
          className="absolute top-0 left-0 invisible pointer-events-none whitespace-nowrap"
        >
          {missing.map((key) => {
            const event = events[keys.indexOf(key)];
            return (
              <span key={key} data-key={key} className="inline-block">
                <ToolPill event={event} expanded={false} onToggle={() => {}} />
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/ToolTrack.test.tsx`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/shared/ToolTrack.tsx \
        apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx \
        apps/vscode-extension/webview-ui/src/test/setup.ts
git commit -m "feat(chat): ToolTrack packs pills into alternating rows"
```

---

### Task 5: Expansion mounts the panel below its row

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/shared/ToolTrack.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx`

**Interfaces:**
- Consumes: `ToolDetailPanel` (Task 3).
- Produces: no new exports. The expanded panel renders as a sibling immediately after its row, marked `data-rowpanel`, with a caret marked `data-caret`.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx`, inside the existing `describe("ToolTrack", …)` block. Add `fireEvent` to the `@testing-library/react` import at the top of the file:

```tsx
  it("mounts the detail panel immediately after the pill's own row", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    // Third pill lives on row 2 (rows hold 2, 2, 1).
    const pills = container.querySelectorAll("button[aria-expanded]");
    fireEvent.click(pills[2]);

    const panel = container.querySelector("[data-rowpanel]");
    expect(panel).not.toBeNull();

    const rows = [...container.querySelectorAll("[data-row]")];
    expect(panel!.previousElementSibling).toBe(rows[1]);
    expect(panel!.querySelector("[data-caret]")).not.toBeNull();
  });

  it("moves the panel rather than opening a second one", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const pills = container.querySelectorAll("button[aria-expanded]");

    fireEvent.click(pills[0]);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(1);

    fireEvent.click(pills[4]);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(1);
    const rows = [...container.querySelectorAll("[data-row]")];
    expect(container.querySelector("[data-rowpanel]")!.previousElementSibling).toBe(rows[2]);
  });

  it("collapses when the open pill is clicked again", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const pill = container.querySelectorAll("button[aria-expanded]")[0];

    fireEvent.click(pill);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(1);

    fireEvent.click(pill);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(0);
  });

  it("does not open a panel for a running call", () => {
    const events = makeEvents(2);
    events[1] = { ...events[1], done: false };
    const { container } = render(
      <ToolTrack events={events} measureWidths={fixedWidths} />
    );
    // The running pill carries no aria-expanded, so target it by position.
    const buttons = container.querySelectorAll("[data-row] button");
    fireEvent.click(buttons[1]);
    expect(container.querySelectorAll("[data-rowpanel]").length).toBe(0);
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/ToolTrack.test.tsx`
Expected: FAIL — no element matches `[data-rowpanel]`.

- [ ] **Step 3: Write minimal implementation**

In `ToolTrack.tsx`, add the import:

```tsx
import { ToolDetailPanel } from "./ToolDetailPanel";
```

Add expansion state next to the other hooks, plus a stable identity for the
layout — `groups` is a fresh array every render, so an effect depending on it
directly would re-run on every render:

```tsx
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const layoutKey = groups.map((g) => g.join(",")).join("|");
```

Replace the `groups.map(...)` body so each row can be followed by its panel. The row `<div>` and the panel are returned together from a fragment:

```tsx
        {groups.map((indices, rowIndex) => {
          const dirRight = rowIndex % 2 === 0;
          // The last row keeps its natural packing so the road can end on a
          // dot instead of being stretched to an edge it has no content for.
          const fill = rowIndex < groups.length - 1;
          const openIndex = indices.find((i) => events[i].id === expandedId);

          return (
            <Fragment key={rowIndex}>
              <div
                data-row=""
                data-dir={dirRight ? "r" : "l"}
                data-fill={String(fill)}
                className={[
                  "flex items-start gap-[14px] px-7",
                  dirRight ? "" : "flex-row-reverse",
                  fill ? "justify-between" : "",
                ].join(" ")}
              >
                {indices.map((i) => (
                  <ToolPill
                    key={events[i].id}
                    event={events[i]}
                    expanded={events[i].id === expandedId}
                    onToggle={() =>
                      setExpandedId((current) =>
                        current === events[i].id ? null : events[i].id,
                      )
                    }
                  />
                ))}
              </div>

              {openIndex !== undefined && (
                <div data-rowpanel="" className="relative mx-7">
                  <span
                    data-caret=""
                    className="absolute -top-[5px] w-[9px] h-[9px] rotate-45"
                    style={{
                      left: 14,
                      background: "var(--color-surface)",
                      borderLeft: "1px solid var(--accent-brd)",
                      borderTop: "1px solid var(--accent-brd)",
                    }}
                  />
                  <ToolDetailPanel event={events[openIndex]} />
                </div>
              )}
            </Fragment>
          );
        })}
```

Add `Fragment` to the React import:

```tsx
import { Fragment, useLayoutEffect, useRef, useState } from "react";
```

Finally, point the caret at the pill it belongs to. Add a ref for the rows container and a layout effect after the width effect:

```tsx
  const rowsRef = useRef<HTMLDivElement | null>(null);

  // Aim the caret at the expanded pill. Read after commit, when the row and the
  // panel are both laid out; jsdom reports zeroes, so the clamp keeps it valid.
  useLayoutEffect(() => {
    const host = rowsRef.current;
    if (!host || expandedId === null) return;
    const panel = host.querySelector<HTMLElement>("[data-rowpanel]");
    const pill = host.querySelector<HTMLElement>(`button[data-event-id="${expandedId}"]`);
    const caret = panel?.querySelector<HTMLElement>("[data-caret]");
    if (!panel || !pill || !caret) return;
    const centre = pill.offsetLeft + pill.offsetWidth / 2 - panel.offsetLeft;
    caret.style.left = `${Math.max(14, Math.min(panel.offsetWidth - 14, centre))}px`;
  }, [expandedId, layoutKey]);
```

and attach it to the rows container:

```tsx
      <div ref={rowsRef} className="flex flex-col gap-6">
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/ToolTrack.test.tsx`
Expected: PASS — 9 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/shared/ToolTrack.tsx \
        apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx
git commit -m "feat(chat): expand a tool pill into a panel below its row"
```

---

### Task 6: Draw the rails

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/shared/ToolTrack.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx`

**Interfaces:**
- Consumes: `buildTrackPath`, `RowGeometry`, `CAP_RADIUS` (Task 2).
- Produces: no new exports. The SVG carries `data-rails`; its `<path>` carries `data-rail-path`, the caps `data-cap="start"` / `data-cap="end"`.

**Note:** jsdom returns 0 for `offsetTop`/`offsetLeft`/`offsetWidth`, so the drawn path is degenerate in tests. Assert that the elements exist and that the effect runs without throwing; the real geometry assertions live in `tool-track-layout.test.ts`, which is exactly why that module is pure.

- [ ] **Step 1: Write the failing test**

Append inside the existing `describe("ToolTrack", …)` block in `src/test/ToolTrack.test.tsx`:

```tsx
  it("renders a decorative rails layer with both caps", () => {
    const { container } = render(
      <ToolTrack events={makeEvents(5)} measureWidths={fixedWidths} />
    );
    const rails = container.querySelector("[data-rails]");
    expect(rails).not.toBeNull();
    expect(rails!.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("[data-rail-path]")).not.toBeNull();
    expect(container.querySelector('[data-cap="start"]')).not.toBeNull();
    expect(container.querySelector('[data-cap="end"]')).not.toBeNull();
  });

  it("draws no rails when there are no rows", () => {
    const { container } = render(<ToolTrack events={[]} measureWidths={fixedWidths} />);
    expect(container.querySelector("[data-rails]")).toBeNull();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/ToolTrack.test.tsx`
Expected: FAIL — no element matches `[data-rails]`.

- [ ] **Step 3: Write minimal implementation**

In `ToolTrack.tsx`, extend the layout import:

```tsx
import {
  CAP_RADIUS,
  COL_GAP,
  SIDE_PAD,
  buildTrackPath,
  packRows,
  type RowGeometry,
} from "./tool-track-layout";
```

Add refs for the SVG parts, next to the others:

```tsx
  const svgRef = useRef<SVGSVGElement | null>(null);
  const pathRef = useRef<SVGPathElement | null>(null);
  const startCapRef = useRef<SVGCircleElement | null>(null);
  const endCapRef = useRef<SVGCircleElement | null>(null);
```

Add the drawing effect after the caret effect. It depends on `expandedId` too, because an open panel shifts every row below it:

```tsx
  // Measure the committed rows and write the road straight onto the SVG. Going
  // through refs rather than state keeps this to one render per layout change.
  useLayoutEffect(() => {
    const host = rowsRef.current;
    const track = trackRef.current;
    const path = pathRef.current;
    if (!host || !track || !path) return;

    const rowEls = Array.from(host.querySelectorAll<HTMLElement>("[data-row]"));
    const geometry: RowGeometry[] = rowEls.map((row, index) => {
      const pills = Array.from(row.querySelectorAll<HTMLElement>("button"));
      const first = pills[0];
      const last = pills[pills.length - 1];
      const dirRight = index % 2 === 0;
      // `first` is the earliest call in the row; row-reverse puts it on the right.
      return {
        y: row.offsetTop + row.offsetHeight / 2,
        startX: dirRight ? first.offsetLeft : first.offsetLeft + first.offsetWidth,
        endX: dirRight ? last.offsetLeft + last.offsetWidth : last.offsetLeft,
        dirRight,
      };
    });

    const result = buildTrackPath(geometry, track.clientWidth);
    path.setAttribute("d", result.d);
    svgRef.current?.setAttribute("viewBox", `0 0 ${track.clientWidth} ${host.offsetHeight}`);

    if (result.startCap && startCapRef.current) {
      startCapRef.current.setAttribute("cx", String(result.startCap.cx));
      startCapRef.current.setAttribute("cy", String(result.startCap.cy));
    }
    if (result.endCap && endCapRef.current) {
      endCapRef.current.setAttribute("cx", String(result.endCap.cx));
      endCapRef.current.setAttribute("cy", String(result.endCap.cy));
    }
  }, [layoutKey, expandedId, width]);
```

Render the SVG as the first child of the track wrapper, before the rows:

```tsx
    <div ref={trackRef} className="relative">
      {groups.length > 0 && (
        <svg
          ref={svgRef}
          data-rails=""
          aria-hidden="true"
          className="absolute inset-0 w-full h-full pointer-events-none overflow-visible"
        >
          <path
            ref={pathRef}
            data-rail-path=""
            fill="none"
            stroke="var(--color-border-strong)"
            strokeWidth={1}
            strokeLinecap="round"
          />
          <circle
            ref={startCapRef}
            data-cap="start"
            r={CAP_RADIUS}
            fill="var(--color-panel)"
            stroke="var(--color-border-strong)"
            strokeWidth={1}
          />
          <circle ref={endCapRef} data-cap="end" r={CAP_RADIUS} fill="var(--color-border-strong)" />
        </svg>
      )}

      <div ref={rowsRef} className="flex flex-col gap-6">
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/ToolTrack.test.tsx`
Expected: PASS — 11 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/shared/ToolTrack.tsx \
        apps/vscode-extension/webview-ui/src/test/ToolTrack.test.tsx
git commit -m "feat(chat): draw the serpentine rails behind the tool pills"
```

---

### Task 7: Wire the track into both render sites

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx:72-78`
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx:115-121`
- Modify: `apps/vscode-extension/webview-ui/src/test/components.test.tsx`

**Interfaces:**
- Consumes: `ToolTrack` (Tasks 4–6).
- Produces: nothing further. This is the last task.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/webview-ui/src/test/components.test.tsx`, inside the existing `describe("AgentRow", …)` block:

```tsx
  it("renders tool events on a track rather than a flat wrap", () => {
    const events: ToolEventView[] = [
      makeEvent({ id: 1, done: true, isError: false, output: "ok" }),
      makeEvent({ id: 2, tool: "search_code", done: true, isError: false, output: "ok" }),
    ];
    const { container } = render(<AgentRow content="" toolEvents={events} />);
    expect(container.querySelectorAll("[data-row]").length).toBeGreaterThan(0);
  });

  it("renders no track when the turn made no tool calls", () => {
    const { container } = render(<AgentRow content="done" />);
    expect(container.querySelector("[data-row]")).toBeNull();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/components.test.tsx`
Expected: FAIL — no element matches `[data-row]`; `AgentRow` still renders a plain wrap.

- [ ] **Step 3a: Wire AgentRow**

In `apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx`, replace the import at line 6:

```tsx
import { ToolTrack } from "../shared/ToolTrack";
```

and replace the pills block at lines 72-78:

```tsx
        {/* Tool pills, threaded onto a serpentine track */}
        {pills.length > 0 && <ToolTrack events={pills} />}
```

- [ ] **Step 3b: Wire DiffCard**

In `apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx`, replace the import at line 5:

```tsx
import { ToolTrack } from "../shared/ToolTrack";
```

and replace the pills block at lines 115-121:

```tsx
      {/* ── Persisted tool pills (explore + execution trace of this change) ── */}
      {toolEvents && toolEvents.length > 0 && (
        <div className="px-3 pb-2">
          <ToolTrack events={toolEvents} />
        </div>
      )}
```

- [ ] **Step 4: Run the full verification sweep**

Run each and confirm the stated result before moving on:

```bash
cd apps/vscode-extension/webview-ui && npx vitest run
```
Expected: PASS, all suites. Note the total count.

```bash
cd apps/vscode-extension/webview-ui && npm run typecheck
```
Expected: no output, exit 0. This is the first point at which the call sites and the controlled `ToolPill` are consistent.

```bash
cd "$(git rev-parse --show-toplevel)" && npm run test
```
Expected: PASS across editor-client, the extension, and webview-ui.

```bash
cd "$(git rev-parse --show-toplevel)" && npm run typecheck
```
Expected: exit 0.

```bash
cd apps/vscode-extension/webview-ui && npm run build
```
Expected: Vite build succeeds. The webview is a separate bundle — a green test run does not prove it compiles.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/AgentRow.tsx \
        apps/vscode-extension/webview-ui/src/components/messages/DiffCard.tsx \
        apps/vscode-extension/webview-ui/src/test/components.test.tsx
git commit -m "feat(chat): render tool pills on a serpentine track"
```

---

## Live verification

Unit tests cannot see any of this — jsdom has no layout engine, so every rail in
the test suite is a degenerate zero-length path. The visual result must be
confirmed in a real dev host before this is called done.

```bash
cd "$(git rev-parse --show-toplevel)"
npm run build
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/crucible-stress"
```

Reload the window (`Cmd+Shift+P` → Developer: Reload Window) after any rebuild —
the webview bundle is cached, and a stale bundle will pin the old layout.

Drive a turn that makes many tool calls and check, against the approved wireframe:

1. The road runs unbroken from the start ring to the end dot.
2. Row 2's rightmost pill is the call immediately after row 1's rightmost pill.
3. The last row ends on a dot at its last pill, never stretched to an edge.
4. U-turns alternate: right after row 1, left after row 2.
5. Resizing the panel re-packs the rows and redraws the road with no flash.
6. A single-call turn shows a ring, a short rail, and a dot — no turn.

**The one thing the wireframe does not prove:** an open panel sits *between* two
rows, so the U-turn bridging them stretches to span it — a long vertical bracket
at the panel's edge. Expand a pill on a middle row and judge it. If it reads
badly, the fallback named in the spec is to dim that spanning turn while a panel
is open. Do not add the dimming pre-emptively.
