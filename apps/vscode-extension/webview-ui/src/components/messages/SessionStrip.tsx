import { useEffect, useState } from "react";
import { Icon } from "../Icon";
import { CardShell } from "../shared/CardShell";
import type { LiveSessionItem, SessionTranscriptView } from "../../types";

interface Props {
  items: LiveSessionItem[];
  /** sessionId → transcript (undefined = loading, null = fetch failed). */
  transcripts: Record<string, SessionTranscriptView | null>;
  /** Posts fetchSessionTranscript; the strip re-invokes it every 2s while a row stays expanded. */
  onExpand: (sessionId: string) => void;
}

const esc = (chars: string) =>
  chars.replace(/[\x00-\x1f]/g, (c) =>
    c === "\n" ? "\\n" : `\\x${c.charCodeAt(0).toString(16).padStart(2, "0")}`);

// Age is computed HERE, not shipped in /live rows — a ticking age_sec in the
// payload would churn the host's lastLiveSignature every second (the /live
// dedup invariant). Display-only: computed at render; never stored in state.
const ageSec = (startedAt: number) =>
  Math.max(0, Math.floor(Date.now() / 1000 - startedAt));

const TRANSCRIPT_POLL_MS = 2000;

/**
 * SessionStrip — read-only "● running: npm run dev" rows for the thread's live
 * PTY exec sessions, pinned in the LiveSlot. Clicking a row expands a monospace
 * scrollback (server-side independent view — never consumes the model's unread
 * output). No kill button in v1 (spec: the user asks the model to kill).
 */
export function SessionStrip({ items, transcripts, onExpand }: Props) {
  const [open, setOpen] = useState<string | null>(null);

  // Re-poll the transcript while a row stays expanded so the scrollback follows
  // the live process; stops on collapse/unmount (interval cleared).
  useEffect(() => {
    if (open === null) return;
    const timer = setInterval(() => onExpand(open), TRANSCRIPT_POLL_MS);
    return () => clearInterval(timer);
  }, [open, onExpand]);

  if (items.length === 0) return null;

  return (
    <CardShell icon="term" title="Sessions">
      <ul className="flex flex-col border-t border-border py-0.5">
        {items.map((s) => {
          const transcript = transcripts[s.id];
          const isOpen = open === s.id;
          return (
            <li key={s.id} className="flex flex-col">
              <button
                type="button"
                className="flex items-center gap-2 px-3 py-[5px] text-left hover:bg-surface-2"
                onClick={() => {
                  const next = isOpen ? null : s.id;
                  setOpen(next);
                  if (next) onExpand(s.id);
                }}
              >
                <span
                  className="block h-[8px] w-[8px] flex-shrink-0 rounded-full"
                  style={{
                    background: s.status === "running" ? "var(--color-green)" : "var(--color-text-4)",
                    ...(s.status === "running"
                      ? { boxShadow: "0 0 0 3px var(--accent-bg-2)" }
                      : {}),
                  }}
                />
                <code className="min-w-0 flex-1 truncate text-[11px] text-text-2">{s.command}</code>
                <span className="flex-shrink-0 text-[10px] tabular-nums text-text-3">
                  {s.status}
                  {s.exit_code !== null ? ` (exit ${s.exit_code})` : ""} · {ageSec(s.started_at)}s
                </span>
                <span className="flex-shrink-0 text-text-4">
                  <Icon name={isOpen ? "chev-d" : "chev-r"} size={11} />
                </span>
              </button>
              {isOpen && (
                <div className="mx-3 mb-2 rounded border border-border bg-surface-2 text-[10px]">
                  {transcript === undefined && (
                    <div className="px-2 py-1.5 text-text-3">Loading…</div>
                  )}
                  {transcript === null && (
                    <div className="px-2 py-1.5 text-text-3">Transcript unavailable.</div>
                  )}
                  {transcript != null && (
                    <>
                      <pre className="max-h-60 overflow-y-auto overflow-x-auto whitespace-pre-wrap break-all px-2 py-1.5 font-mono leading-[1.45] text-text-2">
                        {transcript.output_tail || "(no output yet)"}
                      </pre>
                      {transcript.stdin_history.length > 0 && (
                        <div className="border-t border-border px-2 py-1.5 text-text-3">
                          <div className="mb-0.5">stdin sent:</div>
                          <div className="flex flex-wrap gap-1">
                            {transcript.stdin_history.map((e, i) => (
                              <code key={i} className="rounded bg-surface-3 px-1 font-mono">
                                {esc(e.chars)}
                              </code>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </CardShell>
  );
}
