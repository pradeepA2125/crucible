# Serpentine tool track — design

**Date:** 2026-08-07
**Status:** approved (wireframes reviewed and accepted)
**Scope:** `apps/vscode-extension/webview-ui` only. No backend, no contract, no editor-client change.

## Problem

Tool pills render as a flat `flex flex-wrap gap-1.5` of every `ToolEventView` in a turn
(`AgentRow.tsx:73`, `DiffCard.tsx:116`). A modest turn produces a handful of chips and reads
fine. A real exploration turn produces 28+, overwhelmingly `read_file` and `search_code`
repeated, and the block collapses into one undifferentiated texture — a wall of near-identical
chips with no entry point, no exit point, and nothing encoding that they are a *sequence*.

The information is all there; the structure is not.

## Design

Thread a single continuous line — a "road" — through the pills, turning at the row edge.
The pill itself does not change. Only the container and the connector are new.

Two rules govern the road, and they are the whole design:

1. **Reading order follows the road.** Even rows run left-to-right; odd rows run
   right-to-left. The call after row 1's rightmost pill is row 2's rightmost pill, sitting
   directly beneath it. The eye never jumps back across the panel, and the visual order is
   always the true call order.

2. **The road ends where the calls end.** The final row's rail stops at its last pill and
   terminates in a filled dot. A hollow ring marks call 1. Only full rows justify to the
   edges; a short final row is never stretched to meet an edge it has no content for.

Full rows use `justify-content: space-between` so both edges align and every U-turn is a clean
vertical. (A "ragged" alternative — tight packing with a short rail stub running into each
turn — was prototyped and rejected.)

### Visual reference

The approved wireframe is the source of truth for geometry and states:
`https://claude.ai/code/artifact/ca7c76ea-3377-4649-bfa2-dba4e5c7fda4`

### Two geometry tiers

Amended 2026-08-08, after the final whole-branch review measured the narrow case.

The wireframe was approved against a wide, near-full-width panel. At a docked ~300px
sidebar the same constants fall apart: `SIDE_PAD` claims 56px of the width and `DiffCard`
another 24px, leaving ~220px — less than two finished `read_file` pills plus a gap
(127 + 14 + 127 = 268). The track degrades to one pill per row, making a 28-call turn
*taller* than the wall it replaced, and an interior row holding a single pill leaves a long
empty rail running out to its turn.

So the geometry has two tiers, chosen from the measured track width:

| | wide | narrow |
|---|---|---|
| `SIDE_PAD` | 28 | 8 |
| `COL_GAP` | 14 | 8 |
| `TURN_INSET` | 9 | 4 |

Wide panels keep exactly the approved wireframe. Narrow panels trade the roomy turn lane
for density, and fit two to three pills per row again.

`TURN_INSET` has to shrink with `SIDE_PAD`, not independently: the turn axis sits at
`width - TURN_INSET` and the row's content edge at `width - SIDE_PAD`, so leaving the inset
at 9 while the pad drops to 8 would put the vertical run *inside* the content, cutting
through the last pill instead of turning beyond it.

Rejected alternatives: falling back to the flat wrap below the threshold (ships and
maintains two layouts, and leaves the narrow case with the exact wall this feature exists to
fix); tightening the constants globally (denser than the approved wireframe at every width);
and accepting one pill per row.

## What alternating direction forces

`flex-wrap` cannot alternate direction per wrapped line. The browser therefore can no longer
do the wrapping, and two things follow.

### 1. Rows are chunked in JS

Pills are measured, greedy-packed into rows for the current container width, and rendered into
explicit row elements whose `flex-direction` is set per row.

Measurement is cheap because **pill width is a pure function of `(tool, state)`** — the pill's
content is an icon, the tool name, a status glyph, and (when not running) a chevron. A turn
with 28 calls has at most a handful of distinct `(tool, state)` pairs. Widths are measured once
per pair in an off-screen probe container and held in a module-level cache for the session.
Re-packing on resize reuses the cache and touches no DOM.

### 2. The detail panel moves below the row

Today the expanded panel lives inside the pill's own flex column (`ToolPill.tsx:67`), which
only works because `flex-wrap` tolerates a tall item. With fixed-width rows, a ~230px panel
under one chip would blow out its column.

The panel now spans the full row width, sits directly beneath its row, and points at its pill
with a caret. This also reads better at this density than a narrow box wedged under one chip.
One panel open at a time per track.

**Consequence to verify during implementation:** an open panel sits *between* two rows, so the
U-turn bridging those rows stretches to span it — a long vertical bracket at the panel's edge.
This is expected and arguably communicates that the panel belongs between those rows. If it
reads badly in the running app, the fallback is to dim the spanning turn while a panel is open.
Do not add that dimming pre-emptively.

## Components

### New: `components/shared/ToolTrack.tsx`

Owns layout and rails for a list of tool events. Replaces the `flex flex-wrap` div at both
render sites.

```
ToolTrack({ events }: { events: ToolEventView[] })
```

Structure:

```
<div class="track">              position:relative
  <svg class="rails" aria-hidden="true"/>   absolutely positioned, pointer-events:none
  <div class="rows">             flex column, row-gap 24px
    <div class="row" data-dir="r" data-fill="true">  …pills…
    <div class="rowpanel">       (only when a pill in the row above is expanded)
    <div class="row" data-dir="l" …>
  </div>
  <div class="measure"/>         off-screen probe host, only during a measure pass
</div>
```

### New: `components/shared/tool-track-layout.ts`

Pure, DOM-free, unit-testable. This is the seam that makes the feature testable at all, since
jsdom reports zero for every layout metric.

```ts
export function packRows(widths: number[], avail: number, gap: number): number[][]
export function buildTrackPath(rows: RowGeometry[], bounds: TrackBounds): string
```

`RowGeometry` is `{ y, startX, endX, dirRight }` — measured by the component, consumed by the
path builder. `buildTrackPath` returns the SVG `d` string and knows nothing about React or the
DOM.

### Changed: `components/shared/ToolPill.tsx`

The rendered button is visually identical to today — same markup, icon map, status colours,
hover, running shimmer, chevron rotation. Two things about the file change: the expanded-panel
JSX is **extracted** into a new
`ToolDetailPanel` component that `ToolTrack` mounts below the row, and `ToolPill` loses its
internal `expanded` state in favour of controlled props:

```ts
ToolPill({ event, expanded, onToggle }: { event; expanded: boolean; onToggle: () => void })
```

Existing component tests that render `<ToolPill event={…}/>` standalone must pass the new
props.

### Changed: `AgentRow.tsx`, `DiffCard.tsx`

Each swaps its `flex flex-wrap gap-1.5` wrapper for `<ToolTrack events={…}/>`. No other change
at either site. `DiffCard` keeps its `px-3 pb-2` padding on the surrounding element.

## Algorithm

1. **Measure.** For each `(tool, state)` pair not in the cache, render a probe pill into the
   off-screen `.measure` host, read `getBoundingClientRect().width`, cache it, clear the host.
2. **Pack.** `packRows(widths, containerWidth - 2*SIDE_PAD, COL_GAP)` — greedy: append while
   the running width plus gap fits, else start a new row.
3. **Render.** One `.row` per group, `data-dir` alternating `r`/`l` (odd rows get
   `flex-direction: row-reverse`), `data-fill="true"` on every row except the last.
4. **Draw.** In `useLayoutEffect`, read each row's `offsetTop`/`offsetHeight` and its first and
   last pill's `offsetLeft`/`offsetWidth`, build `RowGeometry[]`, call `buildTrackPath`, and
   write the result straight to the `<path>` element via ref — no extra React render.
5. **Resize.** A `ResizeObserver` on the track updates the width; steps 2–4 re-run. Step 1 is
   skipped entirely (cache hit).

### Geometry constants

| Constant | Value | Meaning |
|---|---|---|
| `SIDE_PAD` | 28px | horizontal lane the U-turns occupy, as `.row` padding |
| `COL_GAP` | 14px | gap between pills in a row |
| `ROW_GAP` | 24px | vertical gap between rows |
| `RADIUS` | 12px | U-turn corner radius |
| turn axis | `9px` / `W - 9px` | x of the vertical run, just outside the content lane |
| cap radius | 3.5px | start ring and end dot |

Rails stroke `var(--color-border-strong)` at 1px. Caps: start is `fill: var(--color-panel)`
with a `border-strong` stroke (hollow ring); end is solid `border-strong`.

## Edge cases

| Case | Behaviour |
|---|---|
| One pill | Ring, ~16px of rail, dot. No turn. |
| One row | Rail from first to last pill with both caps. No turn. |
| Last row is full | Still terminates on a dot at its last pill — `data-fill` is false for the last row regardless, so it is never stretched. |
| Running pill | Not clickable (unchanged). Still a node on the road. |
| Panel open mid-track | Rows below shift; the bridging U-turn spans the panel. See "Consequence to verify". |
| Container narrower than one pill | `avail` clamps to a 120px floor; a too-wide pill occupies its own row and overflows within it, matching today's behaviour. |
| Empty `events` | `ToolTrack` renders nothing — both call sites already guard on `length > 0`, and the component guards too. |
| `prefers-reduced-motion` | Panel rise animation is suppressed; the shimmer already respects the existing global rule. |

## Accessibility

- The rails `<svg>` is `aria-hidden="true"` — it is pure decoration over content that already
  reads correctly.
- DOM order is always call order. Screen readers therefore announce calls 1..n in sequence
  regardless of the visual direction of each row.
- `row-reverse` means keyboard Tab order on odd rows moves right-to-left *visually* while
  remaining correct *logically*. This is accepted: the whole design premise is that the road,
  not the raw left-to-right sweep, is the reading order.
- Focus-visible outlines on pills are unchanged.

## Testing

**Unit (`tool-track-layout.test.ts`)** — the substantive tests, no DOM:
- `packRows` fills a row to capacity and breaks correctly; gap is counted between items only;
  a single over-wide item gets its own row; empty input yields no rows.
- `buildTrackPath` emits: a bare rail with both caps for one row; alternating turn sides across
  three rows; a turn axis on the right after an even row and on the left after an odd row; no
  trailing run past the last pill.

**Component (`components.test.tsx`)** — structure only, since jsdom has no layout. Inject the
measurement function so packing is deterministic:
- renders one `.row` per packed group with alternating `data-dir`
- `data-fill` is false on the last row and true on the others
- clicking a pill mounts exactly one `ToolDetailPanel`, immediately after that pill's row
- clicking a second pill moves the panel rather than opening two
- a `run`-state pill does not respond to click
- existing `ToolPill` tests updated for the controlled `expanded`/`onToggle` props

## Out of scope

Deliberately not part of this change:

- Collapsing repeated calls into a single node with a count
- Showing the call's argument (path / query) on the pill or the road
- Grouping or summarising a burst behind a "28 calls" affordance
- Dimming older rows, or any progressive de-emphasis
- Applying the track to any surface other than the two existing pill render sites
