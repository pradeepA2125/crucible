import { useState } from "react";
import type { DiffEntry } from "../../types";

interface Props {
  entries: DiffEntry[];
}

type LineKind = "add" | "del" | "ctx" | "hunk";
interface DiffLine { kind: LineKind; num: string; marker: string; text: string }

/** Parse capped unified-diff text into renderable lines with new-file numbering. */
function parseUnifiedDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  let newLine = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("+++") || raw.startsWith("---")) continue;
    const hunk = raw.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      newLine = parseInt(hunk[1], 10);
      out.push({ kind: "hunk", num: "", marker: "", text: raw });
      continue;
    }
    if (raw.startsWith("+")) {
      out.push({ kind: "add", num: String(newLine++), marker: "+", text: raw.slice(1) });
    } else if (raw.startsWith("-")) {
      out.push({ kind: "del", num: "", marker: "-", text: raw.slice(1) });
    } else {
      out.push({
        kind: "ctx",
        num: String(newLine++),
        marker: "",
        text: raw.startsWith(" ") ? raw.slice(1) : raw,
      });
    }
  }
  return out;
}

const LINE_STYLE: Record<LineKind, { row: string; marker: string }> = {
  add:  { row: "bg-[rgba(74,222,128,.07)] text-[#b6f0c8]", marker: "text-green" },
  del:  { row: "bg-[rgba(248,113,113,.06)] text-[#f3b8b8]", marker: "text-red" },
  ctx:  { row: "text-text-3", marker: "" },
  hunk: { row: "text-text-4 italic", marker: "" },
};

/**
 * DiffPanes — mockup frame 3 `.tabs` + `.diffpane`: one tab per changed file,
 * unified-diff lines with new-file line numbers. Renders nothing when no entry
 * carries diff text (pre-unified_diff messages fall back to FileRow lists).
 */
export function DiffPanes({ entries }: Props) {
  const withDiff = entries.filter((e) => !!e.unified_diff);
  const [active, setActive] = useState(0);
  if (withDiff.length === 0) return null;
  const current = withDiff[Math.min(active, withDiff.length - 1)];

  return (
    <div className="border-t border-border">
      <div role="tablist" className="flex gap-0.5 px-2 border-b border-border overflow-x-auto">
        {withDiff.map((entry, i) => (
          <button
            key={entry.path}
            role="tab"
            aria-selected={i === active}
            onClick={(e) => { e.stopPropagation(); setActive(i); }}
            className={[
              "mono text-[10.5px] px-2.5 py-[7px] whitespace-nowrap cursor-pointer bg-transparent border-0",
              "border-b-[1.5px] -mb-px",
              i === active
                ? "text-accent-ink border-b-accent"
                : "text-text-3 hover:text-text-2 border-b-transparent",
            ].join(" ")}
            style={{ borderBottomStyle: "solid" }}
          >
            {entry.path.split("/").pop()}
          </button>
        ))}
      </div>
      <div className="max-h-48 overflow-auto py-1.5">
        {parseUnifiedDiff(current.unified_diff ?? "").map((line, i) => (
          <div
            key={i}
            className={`flex mono text-[10.5px] leading-[1.8] whitespace-pre pr-3 ${LINE_STYLE[line.kind].row}`}
          >
            <span className="w-[34px] flex-shrink-0 text-right pr-2.5 text-text-4 select-none tabular-nums">
              {line.num}
            </span>
            <span className={`w-3.5 flex-shrink-0 ${LINE_STYLE[line.kind].marker}`}>{line.marker}</span>
            <span>{line.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
