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
