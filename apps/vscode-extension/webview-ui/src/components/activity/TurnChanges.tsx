import { memo, useState } from "react";
import { vscode } from "../../vscodeApi";
import type { FileChange } from "../../transcript/groupTranscript";
import { DiffPanes } from "../shared/DiffPanes";

function Row({ file }: { file: FileChange }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="grid min-w-0 gap-1">
      <div className="flex min-w-0 items-center gap-2 text-[11.5px]">
        <button type="button" onClick={() => vscode.postMessage({ type: "openFile", path: file.path })}
          className="min-w-0 truncate text-left font-mono text-[11px] text-[var(--color-code)] hover:underline">{file.path}</button>
        <span className="flex-1" />
        {file.applied ? (
          <>
            <span style={{ color: "var(--color-green)" }}>+{file.additions}</span>
            <span style={{ color: "var(--color-red)" }}>−{file.deletions}</span>
          </>
        ) : <span className="text-text-3">not applied</span>}
        {file.diff && (
          <button type="button" aria-label={`Show diff for ${file.path}`} aria-expanded={open} onClick={() => setOpen(!open)}
            className="px-1.5 py-0.5 text-text-3 hover:text-text">{open ? "▾" : "›"}</button>
        )}
      </div>
      {open && file.diff && <DiffPanes entries={[{ path: file.path, additions: file.additions, deletions: file.deletions, unified_diff: file.diff }]} />}
    </div>
  );
}

function sameFiles(a: FileChange[], b: FileChange[]): boolean {
  return a.length === b.length && a.every((f, i) => f.path === b[i].path && f.additions === b[i].additions
    && f.deletions === b[i].deletions && f.applied === b[i].applied && f.diff === b[i].diff);
}

export const TurnChanges = memo(function TurnChanges({ files }: { files: FileChange[] }) {
  const applied = files.filter((f) => f.applied);
  const add = applied.reduce((s, f) => s + f.additions, 0);
  const del = applied.reduce((s, f) => s + f.deletions, 0);
  const title = applied.length ? `Edited ${applied.length} file${applied.length === 1 ? "" : "s"}` : "No changes applied";
  return (
    <div data-testid="turn-changes" className="grid min-w-0 gap-1.5 rounded-[10px] px-3 py-2"
      style={{ border: "1px solid var(--color-border-strong)", background: "var(--color-surface)" }}>
      <div className="flex items-center gap-2 text-[12px] text-text">
        <span>{title}</span>
        {applied.length > 0 && (
          <span>
            <span style={{ color: "var(--color-green)" }}>+{add}</span>{" "}
            <span style={{ color: "var(--color-red)" }}>−{del}</span>
          </span>
        )}
      </div>
      {files.map((f) => <Row key={f.path} file={f} />)}
    </div>
  );
}, (prev, next) => sameFiles(prev.files, next.files));
