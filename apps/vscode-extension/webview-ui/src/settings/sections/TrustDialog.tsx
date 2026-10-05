import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import type { AgentView } from "../types";

/** Shows the exact bytes the trust record will cover (spec §10.2), then trusts that hash. */
export function TrustDialog({ agent, onTrust, onCancel }: {
  agent: AgentView; onTrust: () => void; onCancel: () => void;
}) {
  return (
    <div className="surface-card anim-slide-down mb-3 flex flex-col gap-2 p-3" role="dialog"
         aria-label={`Trust ${agent.name}`}>
      <div className="text-xs font-medium text-text">Trust {agent.name}?</div>
      <div className="text-[11px] text-text-3">
        Trusted, this definition runs with its declared permission
        ({agent.declaredPermission}) and its edits follow your review setting. Any later change
        to the file revokes the trust. Read the whole file first:
      </div>
      <div className="font-mono text-[10px] text-text-3">{agent.path}</div>
      {agent.content === null ? (
        <div className="text-[11px]" style={{ color: "var(--color-amber)" }}>
          This file is over 64 KB and cannot be reviewed or trusted here.
        </div>
      ) : (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-border-strong
                        bg-surface-2 p-2 font-mono text-[11px] text-text">{agent.content}</pre>
      )}
      <div className="flex justify-end gap-2">
        <BtnGhost onClick={onCancel}>Cancel</BtnGhost>
        <BtnPrimary disabled={agent.content === null || agent.sha256 === null} onClick={onTrust}>
          Trust this file
        </BtnPrimary>
      </div>
    </div>
  );
}
