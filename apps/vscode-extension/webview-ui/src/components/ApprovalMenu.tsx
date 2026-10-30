import { useCallback, useEffect, useRef, useState } from "react";
import type { ApprovalModeView, ApprovalMode } from "../types";
import { vscode } from "../vscodeApi";
import { Icon } from "./Icon";
import { usePopover } from "./shared/usePopover";
import { useModalFocus } from "./shared/useModalFocus";
export const APPROVAL_MODES = [
  { id: "ask", icon: "shield", label: "Ask for approval", description: "Review edits; ask before commands unless you remembered a rule.", policy: { autoAccept: false, shellPolicy: "ask" } },
  { id: "accept_edits", icon: "check", label: "Accept edits", description: "Apply edits automatically; ask before commands unless you remembered a rule.", policy: { autoAccept: true, shellPolicy: "ask" } },
  { id: "auto", icon: "spark", label: "Auto", description: "Edits apply; safe commands run, risky ones ask.", policy: { autoAccept: true, shellPolicy: "auto" } },
  { id: "full_access", icon: "bolt", label: "Full access", description: "Apply edits and run commands without asking.", policy: { autoAccept: true, shellPolicy: "allow_all" } },
] as const;
function FullAccessWarning({ onCancel, onConfirm, returnTo }: {
  onCancel(): void; onConfirm(remember: boolean): void; returnTo: React.RefObject<HTMLButtonElement>;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [remember, setRemember] = useState(false);
  useModalFocus(root, onCancel, returnTo);
  return (
    <div className="scrim fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={e => { if (e.target === e.currentTarget) onCancel(); }}>
      <div ref={root} role="dialog" aria-modal="true" aria-labelledby="full-access-title" tabIndex={-1}
        className="grid w-full max-w-[360px] gap-3 rounded-card border border-border-strong bg-surface-3 p-4 text-body text-text">
        <h2 id="full-access-title" className="font-semibold">Allow Full access?</h2>
        <p>Crucible will apply edits and run commands without asking. Commands can change or delete files and run programs.</p>
        <p className="text-meta text-text-2">Protected paths and untrusted-agent edits still need review. MCP tools keep their own approval.</p>
        <label className="flex items-center gap-2 text-meta"><input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />Don’t show again</label>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-control border border-border px-3 py-1.5 hover:bg-surface-2">Cancel</button>
          <button type="button" onClick={() => onConfirm(remember)} className="rounded-control bg-[var(--accent-fill)] px-3 py-1.5 text-[var(--on-accent)] hover:bg-[var(--accent-fill-hover)]">Allow Full access</button>
        </div>
      </div>
    </div>
  );
}
/** Errors and notices take a full row under the composer controls; inside the chip's
 * flex item they squeezed the whole footer row. */
export function ApprovalNotice({ approval }: { approval: ApprovalModeView }) {
  if (approval.error) return <p role="alert" className="basis-full text-caption text-red [overflow-wrap:anywhere]">{approval.error}</p>;
  if (approval.notice) return <p role="status" className="basis-full text-caption text-text-3 [overflow-wrap:anywhere]">{approval.notice}</p>;
  return null;
}
/** Full access and Auto run commands without asking first: they need a trusted workspace and
 * a backend that supports them. Returns whether the row is disabled and the line shown under it. */
function modeAvailability(mode: (typeof APPROVAL_MODES)[number], approval: ApprovalModeView): { disabled: boolean; note: string } {
  if (mode.id !== "full_access" && mode.id !== "auto") return { disabled: false, note: mode.description };
  if (!approval.trusted) return { disabled: true, note: "Needs a trusted workspace." };
  const backendOffers = approval.tier === "supported"
    && (mode.id !== "auto" || (approval.snapshot?.shellPolicies?.includes("auto") ?? false));
  if (!backendOffers) return { disabled: true, note: mode.id === "auto" ? "Needs a newer backend." : mode.description };
  return { disabled: false, note: mode.description };
}
export function ApprovalMenu({ approval }: { approval: ApprovalModeView }) {
  const root = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [warning, setWarning] = useState(false);
  const [requested, setRequested] = useState(false);
  const requestRef = useRef(false);
  const close = useCallback(() => setOpen(false), []);
  const position = usePopover(root, open, close);
  useEffect(() => {
    if (requestRef.current && !approval.pending) {
      requestRef.current = false; setRequested(false);
      if (!approval.error) { setOpen(false); chip.current?.focus(); }
    }
  }, [approval]);
  useEffect(() => { if (open) root.current?.querySelector<HTMLButtonElement>('button[role="menuitemradio"][aria-checked="true"]:not(:disabled), button[role="menuitemradio"]:not(:disabled)')?.focus(); }, [open]);
  const selected = APPROVAL_MODES.find(mode => mode.id === approval.mode);
  const connecting = approval.tier === "unknown" || approval.tier === "supported" && !approval.confirmed;
  const label = connecting ? "Connecting…" : approval.tier === "legacy" ? "Approval unavailable" : (selected?.label ?? "Custom policy") + (approval.tier === "older" && !approval.confirmed ? " · unconfirmed" : "");
  function choose(mode: ApprovalMode, confirmedFullAccess = false, dontShowAgain = false) {
    if (approval.pending || requestRef.current) return;
    if (mode === "full_access" && !approval.warningDismissed && !confirmedFullAccess) { setOpen(false); setWarning(true); return; }
    requestRef.current = true; setRequested(true);
    vscode.postMessage({ type: "setApprovalMode", mode, confirmedFullAccess, dontShowAgain });
  }
  return (
    <div ref={root} className="relative min-w-0 max-w-full">
      <button ref={chip} type="button" aria-haspopup="menu" aria-expanded={open} aria-label={`Approval mode: ${label}`}
        disabled={approval.tier === "legacy"}
        title={approval.tier === "legacy" ? "Approval modes need the chat controller"
          : approval.trusted ? label : `${label} — Full access is off until you trust this workspace`}
        onClick={() => setOpen(!open)} className="flex h-7 min-w-0 max-w-full items-center gap-1 rounded-control px-1.5 text-meta text-text-2 hover:bg-surface-2 hover:text-text disabled:opacity-60">
        <Icon name="shield" size={13} /><span className="min-w-0 truncate">{label}</span>
        {approval.pending || requested ? <span aria-label="Updating approval mode">…</span> : <Icon name="chev-d" size={10} />}
      </button>
      {open && <div role="menu" aria-label="Approval modes" style={position}
        className="absolute bottom-full z-50 mb-2 max-h-[60vh] overflow-y-auto overscroll-contain rounded-card border border-border-strong bg-surface-3 p-1 text-meta"
        onKeyDown={e => {
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
          const items = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="menuitemradio"]:not(:disabled)')];
          const current = items.indexOf(document.activeElement as HTMLButtonElement);
          const index = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (current + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
          e.preventDefault(); items[index]?.focus();
        }}>
        {APPROVAL_MODES.map(mode => {
          const { disabled, note } = modeAvailability(mode, approval);
          return <button key={mode.id} type="button" role="menuitemradio" aria-checked={approval.mode === mode.id}
            disabled={connecting || approval.pending || requested || disabled}
            onClick={() => choose(mode.id)} className="grid w-full gap-0.5 rounded-control px-2 py-2 text-left hover:bg-surface-2 disabled:opacity-50">
            <span className="flex items-center gap-2 text-body text-text"><Icon name={mode.icon} size={13} />{mode.label}{approval.mode === mode.id ? " ✓" : ""}</span>
            <span className="text-caption text-text-3">{note}</span>
          </button>;
        })}
        <div className="grid gap-1 border-t border-border p-2 text-caption text-text-3">
          {approval.tier === "older" && <p>Command approval is set in Settings. This window’s setting{approval.confirmed ? "" : " is unconfirmed — choose a mode"}.</p>}
          {!approval.trusted && <p className="text-text-2">This workspace isn’t trusted, so Full access is off. Trust the workspace to use it.</p>}
          {approval.tier === "supported" && approval.snapshot && <p>Edits: {approval.snapshot.autoAccept ? "accepted automatically" : "reviewed"}. Commands: {approval.snapshot.shellPolicy === "allow_all" ? "run without asking"
            : approval.snapshot.shellPolicy === "auto" ? "checked against what you asked for; risky ones ask"
            : "ask unless remembered"}.</p>}
          <p>Protected paths and untrusted-agent edits always need review. Remembered command rules run without asking.</p>
          <p>Sub-agents follow their definitions: Plan cannot edit; Accept edits and Don’t ask apply edits automatically.</p>
          <p>{approval.snapshot?.shellPolicy === "auto"
            ? "MCP tools and input to running terminal sessions are checked the same way."
            : "MCP tools keep their own approval. Input to an already-running terminal session isn’t gated."}</p>
        </div>
      </div>}
      {warning && <FullAccessWarning returnTo={chip} onCancel={() => setWarning(false)} onConfirm={remember => { setWarning(false); choose("full_access", true, remember); }} />}
    </div>
  );
}
