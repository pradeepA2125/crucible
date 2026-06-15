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
 * Decisions post on the held-open chat message stream via the controller. v1
 * rejects with an empty reason.
 */
export function EditGate({ taskId, payload }: Props) {
  const entries = parseDiffEntries(payload);

  const [resolved, setResolved] = useState<string | null>(null);

  function handleAccept() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Accepted");
    vscode.postMessage({ type: "editDecision", threadId: taskId, decision: "accept", reason: "" });
  }

  function handleReject() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Rejected");
    vscode.postMessage({ type: "editDecision", threadId: taskId, decision: "reject", reason: "" });
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
      <div className="flex gap-1.5 px-2.5 py-2 border-t border-border">
        {resolved === null ? (
          <>
            <BtnPrimary flex onClick={handleAccept}>
              Accept
            </BtnPrimary>
            <BtnGhost onClick={handleReject}>
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
    </CardShell>
  );
}
