import { useState } from "react";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnDanger, BtnPrimary } from "../../shared/buttons";

interface Props {
  /** Carries the threadId (controller gates have no task — LiveSlot passes activeTaskId ?? threadId). */
  taskId: string;
  payload: Record<string, unknown>;
}

/**
 * DocWriteGate — approval card for a write_doc file write (kind="doc_write").
 * Shows the target path and a content preview (new file) or unified diff (existing).
 * No remember option — every write is unique content.
 */
export function DocWriteGate({ taskId, payload }: Props) {
  const path = String(payload.path ?? "");
  const exists = payload.exists === true;
  const preview = String(payload.preview ?? "");
  const [resolved, setResolved] = useState<string | null>(null);

  function submit(approve: boolean) {
    if (resolved !== null) return; // one-shot guard
    setResolved(approve ? "Approved" : "Rejected");
    vscode.postMessage({ type: "docDecision", threadId: taskId, approve });
  }

  return (
    <CardShell
      icon="file"
      title={`Write file: ${path}`}
      subtitle={exists ? "Modifies existing file" : "New file"}
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      <pre className="max-h-40 overflow-auto px-2.5 py-2 text-[11px] text-text-2 border-t border-border whitespace-pre-wrap">
        {preview}
      </pre>
      {resolved === null ? (
        <div className="flex flex-wrap items-center gap-1.5 px-2.5 py-2 border-t border-border">
          <BtnPrimary onClick={() => submit(true)}>Approve</BtnPrimary>
          <BtnDanger onClick={() => submit(false)}>Reject</BtnDanger>
        </div>
      ) : (
        <div className="px-2.5 py-2 text-[12px] text-text-3 border-t border-border">{resolved}</div>
      )}
    </CardShell>
  );
}
