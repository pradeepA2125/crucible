import type { FocusState } from "../types";

interface Props {
  focus: FocusState;
  onPop: () => void;
  onReset: () => void;
}

export function Breadcrumb({ focus, onPop, onReset }: Props) {
  const parts: string[] = ["workspace"];
  if (focus.level >= 1) parts.push((focus as { pkg: string }).pkg || "orphans");
  if (focus.level >= 2) {
    const fileId = (focus as { fileId: string }).fileId;
    parts.push(fileId.slice(fileId.lastIndexOf("/") + 1));
  }
  if (focus.level === 3 && focus.symbolId) {
    parts.push(focus.symbolId.slice(focus.symbolId.lastIndexOf(":") + 1));
  }
  return (
    <div
      className="ax-glass absolute top-4 left-4 flex items-center gap-1.5 px-3 py-2 text-[11px] font-mono"
    >
      {parts.map((p, i) => (
        <span key={`${p}-${i}`} className="flex items-center gap-1.5">
          {i > 0 && <span className="opacity-30">▸</span>}
          <button
            type="button"
            className={i === parts.length - 1 ? "text-[var(--ax-ink)]" : "text-[var(--ax-accent)] hover:underline"}
            onClick={() => (i === 0 ? onReset() : i < parts.length - 1 ? onPop() : undefined)}
          >
            {p}
          </button>
        </span>
      ))}
    </div>
  );
}
