import { useState } from "react";
import { Icon } from "../Icon";
import { ThinkingBlock } from "../shared/ThinkingBlock";
import { ToolPill } from "../shared/ToolPill";
import { vscode } from "../../vscodeApi";
import { BtnPrimary, BtnDanger } from "../shared/buttons";
import { FileRow } from "../shared/FileRow";
import type { DiffEntry, ToolEventView } from "../../types";

interface Props {
  taskId: string;
  diffEntries: DiffEntry[];
  resolved?: "applied" | "discarded" | null;
  thinkingLog?: string[];
  toolEvents?: ToolEventView[];
}

/**
 * DiffCard — inline change result card with file-by-file rows and accept/reject actions.
 * Matches .card / .diff-card / .dstats / .fdot in the hi-fi mockup.
 */
export function DiffCard({ taskId, diffEntries, resolved, thinkingLog, toolEvents }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [localResolved, setLocalResolved] = useState<"applied" | "discarded" | null>(null);

  // Effective resolution: local optimistic state takes priority.
  const effectiveResolved = localResolved ?? resolved ?? null;

  // Aggregate stats.
  const totalAdditions = diffEntries.reduce((s, e) => s + (e.additions ?? 0), 0);
  const totalDeletions = diffEntries.reduce((s, e) => s + (e.deletions ?? 0), 0);
  const fileCount = diffEntries.length;

  function handleAccept() {
    if (effectiveResolved !== null) return; // one-shot guard (UX Rule 2)
    setLocalResolved("applied");
    vscode.postMessage({ type: "applyInlineChange", taskId });
  }

  function handleReject() {
    if (effectiveResolved !== null) return; // one-shot guard (UX Rule 2)
    setLocalResolved("discarded");
    vscode.postMessage({ type: "discardInlineChange", taskId });
  }

  // Border tint by resolution state.
  const borderColor =
    effectiveResolved === "applied"
      ? "var(--green-brd)"
      : effectiveResolved === "discarded"
      ? "var(--red-brd)"
      : expanded
      ? "var(--accent-brd)"
      : "var(--color-border)";

  return (
    <div
      className={[
        "rounded-[10px] overflow-hidden",
        "bg-surface",
        "shadow-[inset_0_1px_0_var(--hairline),0_10px_24px_-14px_rgba(0,0,0,.55)]",
      ].join(" ")}
      style={{ border: `1px solid ${borderColor}`, transition: "border-color 0.2s" }}
    >
      {/* ── Header ── */}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-[9px] cursor-pointer select-none bg-transparent border-0 text-left"
      >
        {/* Icon */}
        <span className="text-accent flex-shrink-0">
          <Icon name="diff" size={13} />
        </span>

        {/* Title */}
        <span className="text-xs font-semibold text-text">Changes ready</span>

        {/* Aggregate +/- stats */}
        <span className="font-mono text-[10px] font-semibold flex items-center gap-1 flex-1 min-w-0">
          <span className="text-green">+{totalAdditions}</span>
          <span className="text-red">&minus;{totalDeletions}</span>
        </span>

        {/* File count badge */}
        <span
          className="text-[9.5px] font-semibold px-[7px] py-[1.5px] rounded-full flex-shrink-0"
          style={{
            color: "var(--color-accent-ink)",
            background: "var(--accent-bg)",
            border: "1px solid var(--accent-brd)",
          }}
        >
          {fileCount} {fileCount === 1 ? "file" : "files"}
        </span>

        {/* Chevron */}
        <span
          className="text-text-4 flex-shrink-0 transition-transform duration-[180ms]"
          style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}
        >
          <Icon name="chev-d" size={12} />
        </span>
      </button>

      {/* ── Optional ThinkingBlock ── */}
      {thinkingLog && thinkingLog.length > 0 && (
        <div className="px-3 pb-1">
          <ThinkingBlock entries={thinkingLog} />
        </div>
      )}

      {/* ── Persisted tool pills (explore + execution trace of this change) ── */}
      {toolEvents && toolEvents.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {toolEvents.map((event) => (
            <ToolPill key={event.id} event={event} />
          ))}
        </div>
      )}

      {/* ── Body (expanded file rows) ── */}
      {expanded && (
        <div className="anim-rise border-t border-border py-1">
          {diffEntries.map((entry, idx) => (
            <FileRow key={`${entry.path}-${idx}`} entry={entry} />
          ))}
        </div>
      )}

      {/* ── Actions row ── */}
      <div className="flex gap-1.5 px-2.5 py-2 border-t border-border">
        {effectiveResolved === null && (
          <>
            <BtnPrimary flex icon="check" onClick={handleAccept}>
              Accept all
            </BtnPrimary>
            <BtnDanger onClick={handleReject}>
              Reject
            </BtnDanger>
          </>
        )}

        {effectiveResolved === "applied" && (
          <div className="flex items-center gap-1.5">
            <span style={{ color: "var(--color-green)" }}>
              <Icon name="check" size={12} />
            </span>
            <span
              className="text-[11px] font-[550]"
              style={{ color: "var(--color-green)" }}
            >
              Applied
            </span>
          </div>
        )}

        {effectiveResolved === "discarded" && (
          <div className="flex items-center gap-1.5">
            <span style={{ color: "var(--color-red)" }}>
              <Icon name="x" size={12} />
            </span>
            <span
              className="text-[11px] font-[550]"
              style={{ color: "var(--color-red)" }}
            >
              Discarded
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
