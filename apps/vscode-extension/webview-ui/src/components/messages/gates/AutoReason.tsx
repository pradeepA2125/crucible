import { vscode } from "../../../vscodeApi";
import { BtnGhost } from "../../shared/buttons";

/** Why Auto approval asked about this action, and — after several approved asks in a row —
 * a one-click switch to Accept edits (spec §6.6). Renders nothing outside Auto. */
export function AutoReason({ payload, className }: { payload: Record<string, unknown>; className: string }) {
  const reason = typeof payload.auto_reason === "string" ? payload.auto_reason : "";
  if (!reason) return null;
  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      <p className="min-w-0 flex-1 text-meta text-text-2">Auto: {reason}</p>
      {payload.offer_switch === true && (
        <BtnGhost onClick={() => vscode.postMessage({ type: "setApprovalMode", mode: "accept_edits" })}>
          Switch to Accept edits
        </BtnGhost>
      )}
    </div>
  );
}
