import { useState } from "react";
import { Icon } from "../Icon";
import { ProjectTrustDialog } from "./ProjectTrustDialog";
import { trustNames, untrustedFiles, useProjectTrust } from "./projectTrust";
import type { TrustChannel } from "./projectTrust";

/** One line above the composer while a project file isn't trusted, with Review. Dismissing
 * hides it for this session only; the gate itself stays in force. */
export function ProjectTrustStrip({ channel }: { channel?: TrustChannel }) {
  const { view, trust } = useProjectTrust(channel);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const pending = view ? untrustedFiles(view.files) : [];
  return (
    <>
      {pending.length > 0 && !dismissed && (
        <div className="mx-auto min-w-0 w-full max-w-[760px] flex-shrink-0 px-4 pt-2">
          <div role="status"
            className="surface-card flex min-w-0 flex-wrap items-center gap-2 px-2.5 py-1.5 text-meta">
            <span className="flex-none text-[var(--warning)]"><Icon name="shield" size={14} /></span>
            <span className="min-w-0 flex-1 text-text-2">{trustNames(pending)}. Until you review them, Crucible ignores them.</span>
            <span className="ml-auto flex flex-none items-center gap-1">
              <button type="button" onClick={() => setOpen(true)}
                className="h-6 cursor-pointer whitespace-nowrap rounded-control border px-2.5 text-meta"
                style={{ borderColor: "var(--accent-border)", color: "var(--accent)", background: "var(--accent-bg)" }}>
                Review
              </button>
              <button type="button" aria-label="Dismiss for this session" title="Dismiss for this session"
                onClick={() => setDismissed(true)}
                className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-control text-text-2 hover:bg-surface-2">
                <Icon name="x" size={12} />
              </button>
            </span>
          </div>
        </div>
      )}
      {open && view && (
        <ProjectTrustDialog files={untrustedFiles(view.files)} error={view.error}
          onTrust={trust} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
