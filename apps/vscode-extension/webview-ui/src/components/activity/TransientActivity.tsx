import { useEffect, useState } from "react";
import type { StreamingBubble } from "../../types";
import { describeTool } from "../../transcript/summarizeActivity";
import { ToolTrack } from "../shared/ToolTrack";

function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function TransientActivity({ bubble, startedAt, fallback, waiting = false }: {
  bubble: StreamingBubble; startedAt: number; fallback?: string | null;
  /** A main-agent gate is pending: nothing is running, the user is the one being waited on. */
  waiting?: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  // A tool batch emits all its members as pending at once and they finish in any order:
  // name the oldest unfinished one and count the rest. Finished work already shows in the
  // activity group above, so this line and its box only carry what is running now.
  const unfinished = waiting ? [] : bubble.toolEvents.filter((e) => !e.done);
  const thinking = bubble.activeThinkingChunk.trim().split("\n").pop()
    || bubble.thinkingEntries[bubble.thinkingEntries.length - 1] || "";
  const text = waiting ? "Waiting for your approval" : unfinished.length
    ? describeTool(unfinished[0]) + (unfinished.length > 1 ? ` +${unfinished.length - 1} more` : "")
    : thinking || fallback || "Working…";
  return (
    <div data-testid="transient-activity" className="grid min-w-0 gap-1.5">
      <div className="flex min-w-0 items-center gap-2 text-[12px] text-text-2">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full" aria-hidden="true"
          style={{ background: "var(--color-accent)", animation: "pulse 1.5s ease-in-out infinite" }} />
        <span data-testid="transient-text" className="min-w-0 truncate">{text}</span>
        {!waiting && <span className="shrink-0 text-text-3">{elapsed(now - startedAt)}</span>}
        {unfinished.length > 0 && (
          <button type="button" aria-label="Show current step" onClick={() => setOpen(!open)}
            className="shrink-0 text-text-3 hover:text-text">{open ? "▾" : "›"}</button>
        )}
      </div>
      {open && unfinished.length > 0 && (
        <div data-testid="transient-detail"
          className="grid min-w-0 grid-cols-[minmax(0,1fr)] overflow-y-auto rounded-[8px] p-2"
          style={{ maxHeight: "320px", border: "1px solid var(--color-border)", background: "var(--color-surface)" }}>
          <ToolTrack events={unfinished} />
        </div>
      )}
    </div>
  );
}
