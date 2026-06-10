import { useState } from "react";
import { Icon } from "../../Icon";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnPrimary, BtnGhost, BtnDanger } from "../../shared/buttons";

interface Props {
  taskId: string;
  payload: Record<string, unknown>;
}

/**
 * ScopeGate — out-of-scope file approval card.
 *
 * Shows the reason for scope extension plus a mono file list, then lets the
 * user approve (once), approve & remember (for this task), or reject.
 */
export function ScopeGate({ taskId, payload }: Props) {
  const files: string[] = Array.isArray(payload.files)
    ? payload.files.map(String)
    : [];
  const reason = String(payload.reason ?? "");
  const stepId = String(payload.step_id ?? "");

  const [resolved, setResolved] = useState<string | null>(null);

  function handleApprove() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Approved");
    vscode.postMessage({
      type: "scopeDecision",
      taskId,
      files,
      decision: "approve",
      remember: false,
    });
  }

  function handleApproveRemember() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Approved & remembered");
    vscode.postMessage({
      type: "scopeDecision",
      taskId,
      files,
      decision: "approve",
      remember: true,
    });
  }

  function handleReject() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Rejected");
    vscode.postMessage({
      type: "scopeDecision",
      taskId,
      files,
      decision: "reject",
      remember: false,
    });
  }

  return (
    <CardShell
      icon="file"
      title="Scope extension requested"
      subtitle={stepId ? `step ${stepId}` : undefined}
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      {/* ── Reason ── */}
      {reason && (
        <p className="px-3 pt-2 pb-1 text-[11px] text-text-2">{reason}</p>
      )}

      {/* ── File list ── */}
      {files.length > 0 && (
        <div className="px-3 pb-2.5">
          <ul className="font-mono text-[10.5px] text-text-3 flex flex-col gap-0.5">
            {files.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Actions row ── */}
      <div className="flex gap-1.5 px-2.5 py-2 border-t border-border">
        {resolved === null ? (
          <>
            <BtnPrimary flex onClick={handleApprove}>
              Approve
            </BtnPrimary>
            <BtnGhost onClick={handleApproveRemember}>
              Approve &amp; remember
            </BtnGhost>
            <BtnDanger onClick={handleReject}>
              Reject
            </BtnDanger>
          </>
        ) : (
          <ResolvedRow resolved={resolved} />
        )}
      </div>
    </CardShell>
  );
}

function ResolvedRow({ resolved }: { resolved: string }) {
  const isApproval = resolved !== "Rejected";
  return (
    <div className="flex items-center gap-1.5">
      <span style={{ color: isApproval ? "var(--color-green)" : "var(--color-red)" }}>
        <Icon name={isApproval ? "check" : "x"} size={12} />
      </span>
      <span className="text-[11px] text-text-2">{resolved}</span>
    </div>
  );
}
