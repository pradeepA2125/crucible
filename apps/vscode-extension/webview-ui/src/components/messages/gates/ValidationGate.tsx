import { useState } from "react";
import { Icon } from "../../Icon";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnPrimary, BtnDanger } from "../../shared/buttons";
import type { Diagnostic } from "../../../types";

interface Props {
  taskId: string;
  payload: Record<string, unknown>;
}

/** Parse a diagnostic level string to a display color CSS var. */
function levelColor(level: string): string {
  const l = level.toLowerCase();
  if (l === "error") return "var(--color-red)";
  if (l === "warning") return "var(--color-amber)";
  return "var(--color-text-3)";
}

/**
 * ValidationGate — validation failure review card.
 *
 * Shows diagnostics with level tags; lets the user accept (proceed to review)
 * or reject (fail the task).
 */
export function ValidationGate({ taskId, payload }: Props) {
  const summary = String(payload.summary ?? "");
  const diagnostics: Diagnostic[] = Array.isArray(payload.diagnostics)
    ? (payload.diagnostics as Array<Record<string, unknown>>).map((d) => ({
        level: String(d.level ?? "info"),
        message: String(d.message ?? ""),
        source: d.source ? String(d.source) : undefined,
      }))
    : [];

  const [resolved, setResolved] = useState<string | null>(null);

  function handleAccept() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Accepted");
    vscode.postMessage({ type: "validationDecision", taskId, decision: "accept" });
  }

  function handleReject() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Rejected");
    vscode.postMessage({ type: "validationDecision", taskId, decision: "reject" });
  }

  return (
    <CardShell
      icon="warn"
      iconColor="var(--color-amber)"
      title="Validation failed — review"
      subtitle={summary || undefined}
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      {/* ── Explainer ── */}
      <p className="px-3 pt-2 pb-1 text-[11px] text-text-3">
        These errors remained after auto-repair. They may be pre-existing — accept to proceed to
        review, or reject to fail.
      </p>

      {/* ── Diagnostic list ── */}
      {diagnostics.length > 0 && (
        <div
          className="mx-3 mb-2.5 font-mono text-[10.5px] overflow-y-auto"
          style={{ maxHeight: "8rem" }}
        >
          {diagnostics.map((d, i) => (
            <div key={i} className="flex items-start gap-1.5 py-0.5">
              <span
                className="flex-shrink-0 font-semibold"
                style={{ color: levelColor(d.level) }}
              >
                [{d.level}]
              </span>
              <span className="text-text-2">{d.message.slice(0, 400)}</span>
            </div>
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
            <BtnDanger onClick={handleReject}>
              Reject
            </BtnDanger>
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
