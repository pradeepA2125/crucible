import { Icon } from "../Icon";
import { vscode } from "../../vscodeApi";
import type { DiffEntry } from "../../types";

/** Returns an inline style for the file-type dot based on path extension. */
function fileDotStyle(path: string): React.CSSProperties {
  if (path.endsWith(".ts") || path.endsWith(".tsx")) {
    return { background: "#3b82f6" };
  }
  if (path.endsWith(".py")) {
    return { background: "var(--color-amber)" };
  }
  return { background: "var(--color-text-4)" };
}

/** Split a path into basename + directory components for display. */
function splitPath(path: string): { base: string; dir: string } {
  const clean = path.endsWith("/") ? path.slice(0, -1) : path;
  const slash = clean.lastIndexOf("/");
  if (slash === -1) return { base: clean, dir: "" };
  return { base: clean.slice(slash + 1), dir: clean.slice(0, slash) };
}

interface Props {
  entry: DiffEntry;
}

/**
 * FileRow — a single file entry row shared between DiffCard and StepGate.
 *
 * Shows file-type dot, basename, directory path, optional ± stats
 * (rendered only when > 0), and a view-diff button that posts viewDiffFile.
 */
export function FileRow({ entry }: Props) {
  const { base, dir } = splitPath(entry.path);

  return (
    <div className="flex items-center gap-2 px-3 py-1.5">
      {/* File-type dot */}
      <span
        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={fileDotStyle(entry.path)}
      />

      {/* Filename + dir */}
      <span className="flex-1 min-w-0 font-mono text-[11px] flex items-baseline gap-1 overflow-hidden">
        <span className="text-text-2 flex-shrink-0">{base}</span>
        {dir && <span className="text-text-4 truncate">{dir}</span>}
      </span>

      {/* Per-file stats — rendered only when > 0 */}
      <span className="font-mono text-[10px] font-semibold flex items-center gap-1 flex-shrink-0">
        {entry.additions > 0 && (
          <span className="text-green">+{entry.additions}</span>
        )}
        {entry.deletions > 0 && (
          <span className="text-red">&minus;{entry.deletions}</span>
        )}
      </span>

      {/* View diff button — always active */}
      <button
        type="button"
        title="Open diff in editor"
        onClick={() =>
          vscode.postMessage({
            type: "viewDiffFile",
            path: entry.path,
            shadowPath: entry.temp_path ?? "",
          })
        }
        className="flex-shrink-0 w-[22px] h-[22px] rounded flex items-center justify-center text-text-3 bg-transparent border border-transparent cursor-pointer hover:border-border-strong hover:text-text-2 transition-colors duration-150"
      >
        <Icon name="file" size={11} />
      </button>
    </div>
  );
}
