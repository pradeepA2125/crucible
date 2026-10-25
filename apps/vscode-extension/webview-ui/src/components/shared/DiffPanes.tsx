import { useMemo, useState } from "react";
import { ReviewDiffLines } from "./ReviewDiffLines";
import { hash32 } from "../../utils/hash";
import type { ReviewCommentSource } from "../../drafts/reviewDraft";
import type { DiffEntry } from "../../types";

interface Props {
  entries: DiffEntry[];
}

type LineKind = "add" | "del" | "ctx" | "hunk";
export interface DiffLine { kind: LineKind; num: string; marker: string; text: string; oldNumber?: number; newNumber?: number }

/** Parse capped unified-diff text into renderable lines with new-file numbering. */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  let newLine = 0, inHunk = false, oldLine = 0, oldEnd = 0, newEnd = 0, reliable = false;
  for (const raw of diff.split("\n")) {
    const hunk = raw.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      inHunk = true;
      newLine = parseInt(hunk[1], 10);
      const both = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(raw)!;
      oldLine = Number(both[1]); oldEnd = oldLine + Number(both[2] ?? 1); newEnd = newLine + Number(both[4] ?? 1);
      reliable = [oldLine, newLine, oldEnd, newEnd].every(value => Number.isSafeInteger(value) && value >= 0);
      out.push({ kind: "hunk", num: "", marker: "", text: raw });
      continue;
    }
    if (!inHunk) continue;
    if (raw === "") { reliable = false; continue; }
    if (raw.startsWith("\\")) {
      if (raw !== "\\ No newline at end of file") reliable = false;
      out.push({ kind: "hunk", num: "", marker: "", text: raw });
    } else if (raw.startsWith("+")) {
      const newNumber = reliable && newLine > 0 && newLine < newEnd ? newLine : undefined;
      if (newNumber === undefined) reliable = false;
      out.push({ kind: "add", num: String(newLine++), marker: "+", text: raw.slice(1), ...(newNumber === undefined ? {} : { newNumber }) });
    } else if (raw.startsWith("-")) {
      const oldNumber = reliable && oldLine > 0 && oldLine < oldEnd ? oldLine : undefined;
      if (oldNumber === undefined) reliable = false;
      out.push({ kind: "del", num: "", marker: "-", text: raw.slice(1), ...(oldNumber === undefined ? {} : { oldNumber }) }); oldLine++;
    } else {
      if (!raw.startsWith(" ")) reliable = false;
      const numbered = reliable && oldLine > 0 && oldLine < oldEnd && newLine > 0 && newLine < newEnd;
      if (!numbered) reliable = false;
      const numbers = numbered ? { oldNumber: oldLine, newNumber: newLine } : {};
      oldLine++;
      out.push({
        ...numbers, kind: "ctx",
        num: String(newLine++),
        marker: "",
        text: raw.startsWith(" ") ? raw.slice(1) : raw,
      });
    }
  }
  return out;
}

const LINE_STYLE: Record<LineKind, { row: string; marker: string }> = {
  add:  { row: "bg-[var(--diff-add-bg)] text-text", marker: "text-green" },
  del:  { row: "bg-[var(--diff-del-bg)] text-text", marker: "text-red" },
  ctx:  { row: "text-text-3", marker: "" },
  hunk: { row: "text-text-4 italic", marker: "" },
};

/** Unified-diff lines with new-file numbering; shared by DiffPanes and the desktop Changes tool. */
export function DiffLines({ diff, presentation = "compact", path = "", wrap = false, source }: { diff: string; presentation?: "compact" | "review"; path?: string; wrap?: boolean; source?: ReviewCommentSource }) {
  const review = useMemo(() => presentation === "review" ? { lines: parseUnifiedDiff(diff), key: String(hash32(diff)) } : undefined, [diff, presentation]);
  if (review) return <ReviewDiffLines key={`${path}:${review.key}`} lines={review.lines} path={path} wrap={wrap} source={source} />;
  return (
    <>
      {parseUnifiedDiff(diff).map((line, i) => (
        <div
          key={i}
          className={`flex mono text-caption leading-[1.8] whitespace-pre pr-3 ${LINE_STYLE[line.kind].row}`}
        >
          <span className="w-[34px] flex-shrink-0 text-right pr-2.5 text-text-4 select-none tabular-nums">
            {line.num}
          </span>
          <span className={`w-3.5 flex-shrink-0 ${LINE_STYLE[line.kind].marker}`}>{line.marker}</span>
          <span>{line.text}</span>
        </div>
      ))}
    </>
  );
}

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
              "mono text-caption px-2.5 py-[7px] whitespace-nowrap cursor-pointer bg-transparent border-0",
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
        <DiffLines diff={current.unified_diff ?? ""} />
      </div>
    </div>
  );
}
