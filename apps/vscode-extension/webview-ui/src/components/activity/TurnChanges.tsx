import { memo, useState } from "react";
import { vscode } from "../../vscodeApi";
import type { FileChange } from "../../transcript/groupTranscript";
import { Icon } from "../Icon";
import { DiffPanes } from "../shared/DiffPanes";

const VISIBLE = 3;

function splitPath(path: string): { dir: string; base: string } {
  const slash = path.lastIndexOf("/");
  return slash === -1 ? { dir: "", base: path } : { dir: path.slice(0, slash + 1), base: path.slice(slash + 1) };
}

function Row({ file }: { file: FileChange }) {
  const [open, setOpen] = useState(false);
  const { dir, base } = splitPath(file.path);
  return (
    <div className="grid min-w-0 gap-1">
      <div className="flex min-w-0 items-center gap-2 text-body">
        <button type="button" onClick={() => vscode.postMessage({ type: "openFile", path: file.path })}
          className="min-w-0 truncate text-left hover:underline" title={file.path} aria-label={file.path}>
          {dir && <span className="text-text-3">{dir}</span>}<span className="text-text">{base}</span>
        </button>
        <span className="flex-1" />
        {file.applied ? (
          <span className="shrink-0 font-mono text-meta">
            <span style={{ color: "var(--diff-add)" }}>+{file.additions}</span>{" "}
            <span style={{ color: "var(--diff-del)" }}>−{file.deletions}</span>
          </span>
        ) : <span className="shrink-0 text-meta text-text-3">not applied</span>}
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
  const [expanded, setExpanded] = useState(false);
  const applied = files.filter((f) => f.applied);
  const add = applied.reduce((s, f) => s + f.additions, 0);
  const del = applied.reduce((s, f) => s + f.deletions, 0);
  const title = applied.length ? `Edited ${applied.length} file${applied.length === 1 ? "" : "s"}` : "No changes applied";
  const shown = expanded ? files : files.slice(0, VISIBLE);
  const hidden = files.length - shown.length;
  return (
    <div data-testid="turn-changes" className="grid min-w-0 overflow-hidden rounded-card"
      style={{ border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 px-3 py-2" style={{ background: "var(--surface-2)" }}>
        <Icon name="diff" size={14} />
        <span className="text-body text-text">{title}</span>
        {applied.length > 0 && (
          <span data-testid="turn-changes-totals" className="font-mono text-meta">
            <span style={{ color: "var(--diff-add)" }}>+{add}</span>{" "}
            <span style={{ color: "var(--diff-del)" }}>−{del}</span>
          </span>
        )}
      </div>
      <div className="grid gap-1.5 px-3 py-2" style={{ borderTop: "1px solid var(--border)" }}>
        {shown.map((f) => <Row key={f.path} file={f} />)}
        {hidden > 0 && (
          <button type="button" onClick={() => setExpanded(true)} className="justify-self-start text-meta text-text-2 hover:text-text">
            {`Show ${hidden} more file${hidden === 1 ? "" : "s"}`}
          </button>
        )}
      </div>
    </div>
  );
}, (prev, next) => sameFiles(prev.files, next.files));
