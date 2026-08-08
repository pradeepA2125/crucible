import { Fragment, useLayoutEffect, useRef, useState } from "react";
import type { ToolEventView } from "../../types";
import { ToolDetailPanel } from "./ToolDetailPanel";
import { ToolPill } from "./ToolPill";
import {
  CAP_RADIUS,
  ROW_GAP,
  buildTrackPath,
  metricsForWidth,
  packRows,
  type RowGeometry,
} from "./tool-track-layout";

/**
 * Pill width is a pure function of (tool, state): the content is an icon, the
 * tool name, a status glyph, and — when not running — a chevron. A turn with
 * 28 calls has only a handful of distinct pairs, so measure each once and keep
 * it for the session. Re-packing on resize then touches no DOM at all.
 */
const widthCache = new Map<string, number>();

/**
 * Test-only escape hatch. `widthCache` is module-level (deliberately — it
 * outlives any one ToolTrack mount for the session), which means it also
 * outlives any one test. Without a reset, a test that renders without
 * `measureWidths` would silently reuse widths a previous test recorded.
 */
export function resetToolTrackWidthCacheForTests(): void {
  widthCache.clear();
}

type PillState = "run" | "err" | "ok";

function pillState(event: ToolEventView): PillState {
  if (!event.done) return "run";
  return event.isError === true ? "err" : "ok";
}

function pillKey(event: ToolEventView): string {
  return `${event.tool}|${pillState(event)}`;
}

/**
 * The panel lives as a sibling of the whole row rather than inside the pill's
 * own subtree (see ToolDetailPanel), which drops the DOM adjacency a
 * screen reader relies on to connect a disclosure button to what it reveals.
 * A shared id, referenced by both the panel wrapper and the triggering pill's
 * `aria-controls`, restores that link explicitly.
 */
function toolPanelId(eventId: number): string {
  return `tool-panel-${eventId}`;
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
  // A plain ref wouldn't re-fire the measurement effect: `events` starts empty
  // for both current call sites, the component returns null for empty events
  // (below), so the div — and the ref — never exist on first mount. If that
  // same instance later gets a non-empty `events` array, a ref-object effect
  // keyed on `[]` never reruns, and the track is stuck at width 0 forever. A
  // callback ref stored in state makes attachment itself a state change, so
  // the effect (keyed on the node) reruns exactly when there's a node to read.
  const [trackNode, setTrackNode] = useState<HTMLDivElement | null>(null);
  const probeRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  // The value is never read — only the re-render it forces matters. Naming the
  // setter alone keeps noUnusedLocals happy and says so.
  const [, bumpMeasuredWidths] = useState(0);

  // Track the container width. useLayoutEffect so the first real measurement
  // lands before paint and the user never sees a mis-packed frame.
  useLayoutEffect(() => {
    if (!trackNode) return;
    setWidth(trackNode.clientWidth);
    const observer = new ResizeObserver(() => setWidth(trackNode.clientWidth));
    observer.observe(trackNode);
    return () => observer.disconnect();
  }, [trackNode]);

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

  // Derived once per render from the measured width: at and above
  // NARROW_TRACK_WIDTH this is exactly the approved wireframe's constants; a
  // docked ~300px sidebar drops below the threshold and gets the denser
  // narrow tier instead. Every value below that reads from this rather than
  // the flat SIDE_PAD/COL_GAP/TURN_INSET constants — see metricsForWidth.
  const metrics = metricsForWidth(width);

  const groups =
    widths && width > 0
      ? packRows(widths, Math.max(120, width - metrics.sidePad * 2), metrics.colGap)
      : [];

  const [expandedId, setExpandedId] = useState<number | null>(null);
  // groups is a fresh array every render; deriving a string key lets the
  // caret- and rail-positioning effects below depend on row membership AND
  // pill identity, without re-running on every unrelated render. Identity
  // matters as much as membership: a pill's width changes when it finishes
  // (ToolPill: running has no chevron, done adds a check plus a chevron plus
  // another gap), so a call finishing in place — same index, same row, wider
  // pill — must still re-aim the rails even though no index crossed a row
  // boundary. Keying on indices alone would miss exactly that case and leave
  // the geometry pointing at the pre-completion layout.
  const layoutKey = groups.map((g) => g.map((i) => keys[i]).join(",")).join("|");

  useLayoutEffect(() => {
    if (!needsProbe || !probeRef.current) return;
    let recordedAny = false;
    for (const child of Array.from(probeRef.current.children)) {
      const key = (child as HTMLElement).dataset.key;
      if (!key) continue;
      const measured = Math.ceil(child.getBoundingClientRect().width);
      // A hidden webview (backgrounded VS Code tab) lays out every child at 0.
      // Caching that would pin the width forever — since the cache is only
      // ever filled, never re-measured, once a key gets a 0 it stays 0 for
      // the rest of the session and the track crushes into one row. Leave a
      // 0-measured key out of the cache so it stays in `missing` and gets
      // probed again on the next opportunity (see the `width` dependency
      // below — the ResizeObserver firing when the panel becomes visible
      // again is exactly that opportunity).
      if (measured > 0) {
        widthCache.set(key, measured);
        recordedAny = true;
      }
    }
    // The cache is module-level, so React cannot see that it changed. Force
    // the re-render that reads the fresh widths — but ONLY if something was
    // actually recorded. If every child measured 0 (hidden panel), bumping
    // unconditionally would re-render with the exact same missing keys, which
    // reruns this same effect, which measures 0 again, forever. Skipping the
    // bump breaks that loop: nothing here changed, so nothing here re-renders,
    // and the component sits quietly at `groups = []` until something OUTSIDE
    // this effect changes `width` (the container regaining a real size),
    // which is a real re-render for an unrelated reason and gives this effect
    // (re-run via the `width` dep) another honest attempt.
    if (recordedAny) bumpMeasuredWidths((v) => v + 1);
  }, [needsProbe, missing.join("|"), width]);

  const rowsRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const pathRef = useRef<SVGPathElement | null>(null);
  const startCapRef = useRef<SVGCircleElement | null>(null);
  const endCapRef = useRef<SVGCircleElement | null>(null);

  // Aim the caret at the expanded pill. Read after commit, when the row and the
  // panel are both laid out; jsdom reports zeroes, so the clamp keeps it valid.
  // `width` must be a dependency alongside `layoutKey`: every non-last row is
  // justify-between, so a resize that doesn't cross a packing threshold moves
  // each pill's offsetLeft without changing which indices land in which row —
  // layoutKey alone would miss that and leave the caret pointing at empty space.
  useLayoutEffect(() => {
    const host = rowsRef.current;
    if (!host || expandedId === null) return;
    const panel = host.querySelector<HTMLElement>("[data-rowpanel]");
    const pill = host.querySelector<HTMLElement>(`button[data-event-id="${expandedId}"]`);
    const caret = panel?.querySelector<HTMLElement>("[data-caret]");
    if (!panel || !pill || !caret) return;
    const centre = pill.offsetLeft + pill.offsetWidth / 2 - panel.offsetLeft;
    caret.style.left = `${Math.max(14, Math.min(panel.offsetWidth - 14, centre))}px`;
  }, [expandedId, layoutKey, width]);

  // Measure the committed rows and write the road straight onto the SVG. Going
  // through refs rather than state keeps this to one render per layout change.
  useLayoutEffect(() => {
    const host = rowsRef.current;
    const path = pathRef.current;
    if (!host || !path || width === 0) return;

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

    // `width` is the container's own clientWidth, so the rails are drawn
    // against exactly the width packRows packed the rows against. Re-reading
    // the node here could disagree with it mid-resize. `metrics.turnInset`
    // is the same tier that produced `sidePad`/`colGap` for these rows —
    // passing a mismatched inset would put the turn inside the content lane.
    const result = buildTrackPath(geometry, width, metrics.turnInset);
    path.setAttribute("d", result.d);
    svgRef.current?.setAttribute("viewBox", `0 0 ${width} ${host.offsetHeight}`);

    if (result.startCap && startCapRef.current) {
      startCapRef.current.setAttribute("cx", String(result.startCap.cx));
      startCapRef.current.setAttribute("cy", String(result.startCap.cy));
    }
    if (result.endCap && endCapRef.current) {
      endCapRef.current.setAttribute("cx", String(result.endCap.cx));
      endCapRef.current.setAttribute("cy", String(result.endCap.cy));
    }
  }, [layoutKey, expandedId, width]);

  if (events.length === 0) return null;

  return (
    <div ref={setTrackNode} className="relative">
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

      {/* `relative` (not a background/z-index trick — see ToolTrack design notes)
          puts this in the same auto-z-index stacking context as the rails
          <svg> above, so DOM order decides paint order and the opaque pills
          paint over the road instead of the road painting over their faces. */}
      <div ref={rowsRef} className="relative flex flex-col" style={{ rowGap: ROW_GAP }}>
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
                  "flex items-start",
                  dirRight ? "" : "flex-row-reverse",
                  fill ? "justify-between" : "",
                ].join(" ")}
                // `metrics` drives the packing math above (`packRows(...,
                // width - metrics.sidePad * 2, metrics.colGap)`); restating
                // either value as a bare Tailwind class (px-7, gap-[14px])
                // would let the two silently desync the moment the tier
                // changes. The rail SVG is also built directly on these row
                // boundaries, so the rendered padding/gap must stay provably
                // identical to the packed geometry.
                style={{
                  columnGap: metrics.colGap,
                  paddingLeft: metrics.sidePad,
                  paddingRight: metrics.sidePad,
                }}
              >
                {indices.map((i) => (
                  <ToolPill
                    key={events[i].id}
                    event={events[i]}
                    panelId={toolPanelId(events[i].id)}
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
                <div
                  id={toolPanelId(events[openIndex].id)}
                  data-rowpanel=""
                  className="relative"
                  style={{ marginLeft: metrics.sidePad, marginRight: metrics.sidePad }}
                >
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
                <ToolPill
                  event={event}
                  panelId={toolPanelId(event.id)}
                  expanded={false}
                  onToggle={() => {}}
                />
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
