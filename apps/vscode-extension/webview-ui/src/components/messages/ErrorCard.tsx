import { useState } from "react";
import { Icon } from "../Icon";
import { vscode } from "../../vscodeApi";
import { BtnPrimary, BtnGhost, BtnDanger } from "../shared/buttons";
import type { LiveErrorView } from "../../types";

interface Props extends LiveErrorView {
  onDismiss: () => void;
}

/**
 * ErrorCard — task failure / abort card.
 *
 * Matches the hi-fi mockup .err-card: red bg, red border.
 * - FAILED → "Execution failed"
 * - ABORTED → "Task aborted"
 * Collapsible detail / trace section. One-shot actions: Resume, Re-plan, Dismiss.
 */
export function ErrorCard({ taskId, status, detail, narrative, onDismiss }: Props) {
  const [detailOpen, setDetailOpen] = useState(false);
  const [resolved, setResolved] = useState<string | null>(null);

  const title = status === "FAILED" ? "Execution failed" : "Task aborted";

  function handleResume() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Resuming…");
    vscode.postMessage({ type: "resumeTask", taskId, stage: "execute" });
  }

  function handleReplan() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Re-planning…");
    vscode.postMessage({ type: "resumeTask", taskId, stage: "plan" });
  }

  function handleDismiss() {
    // Dismiss is local-only — posts nothing.
    onDismiss();
  }

  return (
    <div
      className="rounded-[10px] border overflow-hidden shadow-[inset_0_1px_0_var(--hairline),0_10px_24px_-14px_rgba(0,0,0,.55)]"
      style={{
        background: "var(--red-bg)",
        borderColor: "var(--red-brd)",
      }}
    >
      {/* ── Header (non-clickable) ── */}
      <div className="flex items-center gap-2 px-3 py-[9px] select-none">
        <span className="flex-shrink-0" style={{ color: "var(--color-red)" }}>
          <Icon name="warn" size={13} />
        </span>
        <span className="text-xs font-semibold" style={{ color: "var(--color-red)" }}>
          {title}
        </span>
        {/* taskId subtitle */}
        <span className="font-mono text-[10px] text-text-3 flex-1 min-w-0 truncate">{taskId}</span>
      </div>

      {/* ── Narrative (LLM-authored: what was attempted / where it stopped) ── */}
      {narrative && (
        <div className="px-3 pb-2" style={{ borderTop: "1px solid var(--red-brd)", paddingTop: "0.5rem" }}>
          <p className="text-text font-medium text-[11px] mb-1">{narrative.headline}</p>
          {narrative.points.length > 0 && (
            <ul className="list-disc pl-4">
              {narrative.points.map((p, i) => (
                <li key={i} className="text-text-2 text-[11px]">{p}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* ── Collapsible detail / trace ── */}
      {detail && (
        <div style={{ borderTop: "1px solid var(--red-brd)" }}>
          {/* Summary toggle row */}
          <button
            type="button"
            onClick={() => setDetailOpen((o) => !o)}
            className="w-full flex items-center gap-[7px] px-3 py-2 text-[11px] text-text-2 bg-transparent border-0 cursor-pointer text-left"
          >
            <span
              className="text-text-4 transition-transform"
              style={{
                transform: detailOpen ? "rotate(90deg)" : "rotate(0deg)",
                transition: "transform .15s",
              }}
            >
              <Icon name="chev-r" size={11} />
            </span>
            Error detail
          </button>

          {/* Trace box */}
          {detailOpen && (
            <div
              className="mx-3 mb-2.5 px-[10px] py-2 rounded-[7px] font-mono text-[10px] overflow-y-auto anim-rise"
              style={{
                background: "rgba(0,0,0,.35)",
                border: "1px solid var(--red-brd)",
                color: "var(--color-red)",
                lineHeight: 1.7,
                maxHeight: "5rem",
              }}
            >
              {detail}
            </div>
          )}
        </div>
      )}

      {/* ── Actions row ── */}
      <div
        className="flex gap-1.5 px-2.5 py-2"
        style={{ borderTop: "1px solid var(--red-brd)" }}
      >
        {resolved === null ? (
          <>
            <BtnPrimary flex icon="retry" onClick={handleResume}>
              Resume
            </BtnPrimary>
            <BtnGhost onClick={handleReplan}>
              Re-plan
            </BtnGhost>
            <BtnDanger onClick={handleDismiss}>
              Dismiss
            </BtnDanger>
          </>
        ) : (
          <div className="flex items-center gap-1.5">
            <span style={{ color: "var(--color-green)" }}>
              <Icon name="retry" size={12} />
            </span>
            <span className="text-[11px] text-text-2">{resolved}</span>
          </div>
        )}
      </div>
    </div>
  );
}
