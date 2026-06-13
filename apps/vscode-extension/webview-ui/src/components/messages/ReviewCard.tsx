import { useState, useRef } from "react";
import { Icon } from "../Icon";
import { vscode } from "../../vscodeApi";
import { CardShell } from "../shared/CardShell";
import { BtnPrimary, BtnGhost, BtnDanger } from "../shared/buttons";
import { FileRow } from "../shared/FileRow";
import type { LiveReviewView, DiffEntry } from "../../types";

// ── ReviewCard ────────────────────────────────────────────────────────────────
//
// Semantics (Tier B): changes are applied per step (partial promote). "Finish"
// keeps them and completes the task (→ SUCCEEDED). "Discard all changes" calls
// rejectTask which now performs a TRUE revert — the workspace is rolled back to
// its pre-task state (modified files restored, task-created files deleted).

/**
 * ReviewCard — task-complete summary card with finish / close actions.
 *
 * Shows applied files, step completion progress, run deviations, and
 * a two-stage close flow (reveals reason input) before posting rejectTask.
 */
export function ReviewCard({
  taskId,
  modifiedFiles,
  stepsCompleted,
  stepsTotal,
  deviations,
  narrative,
}: LiveReviewView) {
  type ActionMode = "idle" | "closing" | "resolved";

  const [mode, setMode] = useState<ActionMode>("idle");
  const [resolvedLabel, setResolvedLabel] = useState<string>("");
  const [closeReason, setCloseReason] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Build minimal DiffEntry-ish objects from modifiedFiles (no stat info available here)
  const fileEntries: DiffEntry[] = modifiedFiles.map((p) => ({
    path: p,
    additions: 0,
    deletions: 0,
  }));

  function handleFinish() {
    if (mode !== "idle") return; // one-shot guard
    setMode("resolved");
    setResolvedLabel("Finishing…");
    vscode.postMessage({ type: "acceptTask", taskId });
  }

  function handleCloseWithoutFinishing() {
    if (mode !== "idle") return;
    setMode("closing");
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  function handleCancelClose() {
    setMode("idle");
    setCloseReason("");
  }

  function handleConfirmClose() {
    if (mode !== "closing") return; // one-shot guard
    const reason = closeReason.trim() || "closed from chat";
    setMode("resolved");
    setResolvedLabel("Discarded");
    vscode.postMessage({ type: "rejectTask", taskId, reason });
  }

  const showSteps =
    stepsCompleted !== null &&
    stepsCompleted !== undefined &&
    stepsTotal !== null &&
    stepsTotal !== undefined;

  return (
    <CardShell
      icon="check"
      iconColor="var(--color-green)"
      title="Task complete — changes applied"
      subtitle={taskId}
    >
      {/* ── Narrative (LLM-authored: what the task did) ── */}
      {narrative && (
        <div className="border-t border-border px-3 pt-2 pb-1.5">
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

      {/* ── File rows ── */}
      {fileEntries.length > 0 && (
        <div className="border-t border-border py-1">
          {fileEntries.map((entry, idx) => (
            <FileRow key={`${entry.path}-${idx}`} entry={entry} />
          ))}
        </div>
      )}

      {/* ── Steps line ── */}
      {showSteps && (
        <p className="px-3 py-1.5 text-text-2 text-[11px]">
          {stepsCompleted} of {stepsTotal} steps completed
        </p>
      )}

      {/* ── Deviations ── */}
      {deviations.length > 0 && (
        <div className="border-t border-border px-3 pt-2 pb-1.5">
          <p
            className="text-text-4 uppercase tracking-wide mb-1.5"
            style={{ fontSize: 9 }}
          >
            During the run
          </p>
          {deviations.map((d, i) => (
            <div key={i} className="flex items-center gap-1.5 py-0.5">
              <span style={{ color: "var(--color-accent)" }}>
                <Icon name="retry" size={11} />
              </span>
              <span className="text-text-2 text-[11px]">{d}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── Actions ── */}
      <div className="border-t border-border">
        {/* idle: Finish + Close without finishing */}
        {mode === "idle" && (
          <div className="flex gap-1.5 px-2.5 py-2">
            <BtnPrimary flex icon="check" onClick={handleFinish}>
              Finish
            </BtnPrimary>
            <BtnGhost onClick={handleCloseWithoutFinishing}>
              Discard all changes
            </BtnGhost>
          </div>
        )}

        {/* closing: reason input row */}
        {mode === "closing" && (
          <div className="flex flex-col gap-1.5 px-2.5 py-2 anim-rise">
            <p className="text-text-4 text-[10px]">rolls the workspace back to its pre-task state</p>
            <div className="flex gap-1.5">
              <input
                ref={inputRef}
                type="text"
                value={closeReason}
                onChange={(e) => setCloseReason(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleConfirmClose();
                  if (e.key === "Escape") handleCancelClose();
                }}
                placeholder="Reason (optional)"
                className="flex-1 min-w-0 bg-surface-2 border border-border-strong rounded-md px-2.5 py-[6px] text-[11px] text-text outline-none placeholder:text-text-4"
              />
              <BtnDanger onClick={handleConfirmClose}>
                Discard
              </BtnDanger>
              <BtnGhost onClick={handleCancelClose}>
                Cancel
              </BtnGhost>
            </div>
          </div>
        )}

        {/* resolved */}
        {mode === "resolved" && (
          <div className="flex items-center gap-1.5 px-2.5 py-2 anim-rise">
            <span style={{ color: "var(--color-green)" }}>
              <Icon name="check" size={12} />
            </span>
            <span className="text-[11px] text-text-2">{resolvedLabel}</span>
          </div>
        )}
      </div>
    </CardShell>
  );
}
