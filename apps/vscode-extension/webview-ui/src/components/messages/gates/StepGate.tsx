import { useState } from "react";
import { Icon } from "../../Icon";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnPrimary, BtnGhost } from "../../shared/buttons";
import { FileRow } from "../../shared/FileRow";
import type { DiffEntry } from "../../../types";

interface Props {
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
  }));
}

/**
 * StepGate — per-step change review card.
 *
 * Shows file rows (via FileRow) for each changed file in the step diff,
 * then lets the user accept or discard the step.
 */
export function StepGate({ taskId, payload }: Props) {
  const stepTitle = String(payload.step_title ?? "");
  const stepId = String(payload.step_id ?? "");
  const entries = parseDiffEntries(payload);

  const [resolved, setResolved] = useState<string | null>(null);

  function handleAccept() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Accepted");
    vscode.postMessage({ type: "stepDecision", taskId, decision: "accept" });
  }

  function handleDiscard() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Discarded");
    vscode.postMessage({ type: "stepDecision", taskId, decision: "discard" });
  }

  const subtitle = stepTitle || (stepId ? `step ${stepId}` : undefined);

  return (
    <CardShell
      icon="diff"
      title="Review step changes"
      subtitle={subtitle}
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

      {/* ── Actions row ── */}
      <div className="flex gap-1.5 px-2.5 py-2 border-t border-border">
        {resolved === null ? (
          <>
            <BtnPrimary flex onClick={handleAccept}>
              Accept
            </BtnPrimary>
            <BtnGhost onClick={handleDiscard}>
              Discard
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
