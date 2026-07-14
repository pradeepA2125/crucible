import { useState } from "react";
import { Icon } from "../../Icon";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnPrimary, BtnGhost } from "../../shared/buttons";
import { DiffPanes } from "../../shared/DiffPanes";
import { FileRow } from "../../shared/FileRow";
import type { DiffEntry } from "../../../types";

interface Props {
  /** Carries the threadId (controller gates have no task — LiveSlot passes activeTaskId ?? threadId). */
  taskId: string;
  payload: Record<string, unknown>;
}

/** Parse diff_entries from the payload — tolerates missing or malformed entries. */
function parseDiffEntries(payload: Record<string, unknown>): DiffEntry[] {
  if (!Array.isArray(payload.diff_entries)) return [];
  return (payload.diff_entries as Array<Record<string, unknown>>).map((e) => ({
    path: String(e.path ?? ""),
    additions: typeof e.additions === "number" ? e.additions : 0,
    deletions: typeof e.deletions === "number" ? e.deletions : 0,
    temp_path: e.temp_path ? String(e.temp_path) : undefined,
    unified_diff: e.unified_diff ? String(e.unified_diff) : undefined,
  }));
}

/**
 * EditGate — per-edit change review card for the chat controller's EDIT phase.
 *
 * Mirrors StepGate: shows the diff for the files an `edit` action touched, then
 * lets the user accept (instant-promote to real) or reject (the agent revises).
 * Decisions post on the held-open chat message stream via the controller. Reject
 * opens an optional reason box first — the backend already feeds `reason` back to
 * the model on the next attempt, and a specific reason (what's wrong, what to keep)
 * measurably steers the retry; a blind reject tends to reproduce the same defect.
 */
export function EditGate({ taskId, payload }: Props) {
  const entries = parseDiffEntries(payload);

  const [resolved, setResolved] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  function handleAccept() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Accepted");
    vscode.postMessage({ type: "editDecision", threadId: taskId, decision: "accept", reason: "" });
  }

  function confirmReject() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Rejected");
    vscode.postMessage({
      type: "editDecision", threadId: taskId, decision: "reject", reason: reason.trim(),
    });
  }

  return (
    <CardShell
      icon="diff"
      title="Review edit"
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      {/* ── File rows ── */}
      {entries.length > 0 && (
        <div className="border-t border-border py-1">
          {entries.map((entry, idx) => (
            <FileRow key={`${entry.path}-${idx}`} entry={entry} />
          ))}
        </div>
      )}

      {/* ── Tabbed diff panes (when entries carry unified_diff) ── */}
      <DiffPanes entries={entries} />

      {/* ── Actions row ── */}
      {resolved === null && rejecting ? (
        <div className="flex flex-col gap-1.5 px-2.5 py-2 border-t border-border">
          <textarea
            className="w-full rounded border border-border bg-surface-2 px-2 py-1 text-[12px] text-text-1 outline-none resize-none"
            rows={3}
            autoFocus
            placeholder="What's wrong, and what should stay? (optional — helps the agent fix it correctly on the next attempt)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex gap-1.5">
            <BtnGhost className="flex-1" onClick={() => setRejecting(false)}>
              Back
            </BtnGhost>
            <BtnPrimary onClick={confirmReject}>
              Reject
            </BtnPrimary>
          </div>
        </div>
      ) : (
        <div className="flex gap-1.5 px-2.5 py-2 border-t border-border">
          {resolved === null ? (
            <>
              <BtnPrimary flex onClick={handleAccept}>
                Accept
              </BtnPrimary>
              <BtnGhost onClick={() => setRejecting(true)}>
                Reject
              </BtnGhost>
            </>
          ) : (
            <div className="flex items-center gap-1.5">
              <span style={{ color: resolved === "Accepted" ? "var(--color-green)" : "var(--color-red)" }}>
                <Icon name={resolved === "Accepted" ? "check" : "x"} size={12} />
              </span>
              <span className="text-[11px] text-text-2">{resolved}</span>
            </div>
          )}
        </div>
      )}
    </CardShell>
  );
}
