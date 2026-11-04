import { useMemo, useRef, useState } from "react";
import { BtnGhost, BtnPrimary } from "../shared/buttons";
import { CompactDiffLines } from "../shared/DiffPanes";
import { useModalFocus } from "../shared/useModalFocus";
import { lineDiff, TRUST_EFFECT, TRUST_LABEL, TRUST_PATH } from "./projectTrust";
import type { TrustFile, TrustKind } from "./projectTrust";

/** Review each untrusted file's exact bytes (or what changed since you last trusted it) and
 * trust it on its own. Content is what the model reads, invisible characters shown. */
export function ProjectTrustDialog({ files, error, onTrust, onClose }: {
  files: TrustFile[]; error?: string; onTrust(kind: TrustKind, sha256: string): void; onClose(): void;
}) {
  const root = useRef<HTMLDivElement>(null);
  useModalFocus(root, onClose);
  return (
    <div className="scrim fixed inset-0 z-[70] flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={root} role="dialog" aria-modal="true" aria-labelledby="project-trust-title"
        aria-describedby="project-trust-body" tabIndex={-1}
        className="grid max-h-[85vh] w-full max-w-[640px] grid-rows-[auto_minmax(0,1fr)_auto] gap-3 rounded-card border border-border-strong bg-surface-3 p-4 text-body text-text">
        <div className="grid gap-1">
          <h2 id="project-trust-title" className="font-semibold">Review this project's files</h2>
          <p id="project-trust-body" className="text-meta text-text-2">
            These files came with the project and change what Crucible does. Trust a file only if
            you'd follow it yourself. A later change to it asks again.
          </p>
          {error && <p role="alert" className="text-meta text-[var(--danger)]">{error}</p>}
        </div>
        <div className="grid min-h-0 content-start gap-3 overflow-y-auto overscroll-contain pr-1">
          {files.length === 0
            ? <p className="text-meta text-text-2">Every file is trusted.</p>
            : files.map(file => <TrustSection key={file.kind} file={file} onTrust={onTrust} />)}
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <BtnGhost onClick={onClose}>Not now</BtnGhost>
        </div>
      </div>
    </div>
  );
}

function TrustSection({ file, onTrust }: { file: TrustFile; onTrust(kind: TrustKind, sha256: string): void }) {
  const label = TRUST_LABEL[file.kind];
  const name = /^[a-z]+ /.test(label) ? label.charAt(0).toUpperCase() + label.slice(1) : label;
  const headingId = `trust-${file.kind}`;
  const [full, setFull] = useState(false);
  // null when there's no earlier trusted copy, or the files are too large to diff here.
  const diff = useMemo(
    () => file.diffBase !== null && file.content !== null ? lineDiff(file.diffBase, file.content) : null,
    [file.diffBase, file.content]);
  const reviewable = file.content !== null && file.sha256 !== null;
  return (
    <section role="region" aria-labelledby={headingId}
      className="grid min-w-0 gap-2 rounded-card border border-border bg-surface-2 p-3">
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 id={headingId} className="min-w-0 font-medium">{name}</h3>
        <span className="min-w-0 break-all font-mono text-caption text-text-3">{TRUST_PATH[file.kind]}</span>
      </div>
      <p className="text-meta text-text-2">{TRUST_EFFECT[file.kind]}</p>
      {!reviewable ? (
        <p className="text-meta text-[var(--warning)]">This file is over 64 KB or not a regular file, so it can't be reviewed or trusted here.</p>
      ) : diff && !full ? (
        <div className="grid min-w-0 gap-1">
          <p className="text-meta text-text-2">Changed since you trusted it:</p>
          <div className="max-h-64 min-w-0 overflow-auto rounded-control border border-border bg-surface-1 py-1">
            <CompactDiffLines lines={diff} />
          </div>
          <button type="button" onClick={() => setFull(true)}
            className="justify-self-start rounded-control px-1 text-meta text-[var(--accent)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--focus-ring)]">
            Show full file
          </button>
        </div>
      ) : (
        <pre className="max-h-64 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-surface-1 p-2 font-mono text-caption text-text">{file.content}</pre>
      )}
      <div className="flex justify-end">
        <BtnPrimary disabled={!reviewable} onClick={() => file.sha256 && onTrust(file.kind, file.sha256)}>
          <span aria-label={`Trust ${label}`}>Trust {label}</span>
        </BtnPrimary>
      </div>
    </section>
  );
}
