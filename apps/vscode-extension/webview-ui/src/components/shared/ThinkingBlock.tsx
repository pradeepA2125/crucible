import { useState, useEffect, useLayoutEffect, useRef } from "react";
import { Icon } from "../Icon";
import { isPinnedToBottom } from "./scroll-pinning";

interface Props {
  entries: string[];
  activeChunk?: string;
  streaming?: boolean;
}

/**
 * Collapsible thinking block.
 * Matches .think / .think.live / .think-detail in the hi-fi mockup.
 *
 * Streaming state: accent-bg pill with pulse dot, auto-opens.
 * Idle state: surface pill with rotating chev-r icon.
 * Detail panel: scrollable left-border list of numbered entries.
 */
export function ThinkingBlock({ entries, activeChunk, streaming }: Props) {
  const [open, setOpen] = useState(streaming ?? false);

  // Collapse when streaming ends.
  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);

  const bodyRef = useRef<HTMLDivElement | null>(null);
  // Whether the reader is still following the stream. A ref, not state: it is
  // read inside a layout effect and must never itself trigger a render.
  const pinnedRef = useRef(true);
  const wasOpenRef = useRef(false);

  function handleScroll() {
    const el = bodyRef.current;
    if (!el) return;
    pinnedRef.current = isPinnedToBottom({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
    });
  }

  // Follow the stream. Only while pinned — someone who scrolled up to re-read an
  // earlier step would otherwise be dragged back on every token. Re-opening the
  // pane counts as asking to follow again.
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!open || !el) {
      wasOpenRef.current = open;
      return;
    }
    if (!wasOpenRef.current) pinnedRef.current = true;
    wasOpenRef.current = true;
    if (pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [open, entries.length, activeChunk]);

  const hasContent = entries.length > 0 || !!activeChunk || !!streaming;
  if (!hasContent) return null;

  const entryCount = entries.length;

  return (
    <div className="flex flex-col items-start gap-1.5">
      {/* Header pill */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={[
          "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] cursor-pointer",
          "border transition-colors duration-150",
          streaming
            ? "border-[var(--accent-brd)] text-accent-ink"
            : "border-border text-text-3 hover:border-border-strong hover:text-text-2",
        ].join(" ")}
        style={streaming ? { background: "var(--accent-bg)" } : { background: "var(--color-surface)" }}
      >
        {streaming ? (
          /* Live: pulse dot */
          <span
            className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse"
            style={{ boxShadow: "0 0 8px var(--accent-glow)" }}
          />
        ) : (
          /* Idle: rotating chevron */
          <span
            className={[
              "text-text-4 transition-transform duration-150",
              open ? "rotate-90" : "",
            ].join(" ")}
          >
            <Icon name="chev-r" size={10} />
          </span>
        )}

        {streaming ? (
          <span>Thinking…</span>
        ) : (
          <span>Thinking ({entryCount} {entryCount === 1 ? "step" : "steps"})</span>
        )}
      </button>

      {/* Detail panel */}
      {open && (
        <div
          ref={bodyRef}
          data-thinking-body=""
          onScroll={handleScroll}
          className="anim-rise border-l-2 border-border-strong pl-3 ml-1.5 max-h-40 overflow-y-auto text-[11px] text-text-3 leading-relaxed"
        >
          {entries.map((entry, i) => (
            <div key={i} className="mb-0.5">
              <b className="text-text-2 font-medium">{i + 1}.</b> {entry}
            </div>
          ))}
          {activeChunk && (
            <div className="opacity-60">
              <b className="text-text-2 font-medium">{entries.length + 1}.</b> {activeChunk}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
