import type { EdgeKind } from "../types";

interface Props {
  layers: Record<EdgeKind, boolean>;
  focusLevel: number;
  onToggle: (kind: EdgeKind, on: boolean) => void;
}

const KINDS: EdgeKind[] = ["Imports", "Calls", "Inherits", "References"];
const KIND_VAR: Record<EdgeKind, string> = {
  Imports: "var(--ax-k-imports)",
  Calls: "var(--ax-k-calls)",
  Inherits: "var(--ax-k-inherits)",
  References: "var(--ax-k-references)",
};

export function EdgeLayers({ layers, focusLevel, onToggle }: Props) {
  return (
    <div className="ax-glass absolute top-36 right-4 px-3 py-3 w-40 text-[var(--ax-ink)]">
      <div className="text-[9px] uppercase tracking-[0.2em] opacity-50 mb-1">Edge layers</div>
      {KINDS.map((k) => {
        const disabled = k === "References" && focusLevel < 2;
        return (
          <button
            key={k}
            type="button"
            disabled={disabled}
            aria-pressed={layers[k]}
            onClick={() => onToggle(k, !layers[k])}
            title={disabled ? "References edges are scoped to a focused file (select a star)" : undefined}
            className={`ax-row-btn flex items-center gap-2 w-full px-2 py-1.5 mt-1 rounded-md text-[11px] text-left
                        ${layers[k] ? "on-layer" : "opacity-45"}
                        ${disabled ? "opacity-25 cursor-not-allowed" : ""}`}
            style={layers[k] ? { background: "color-mix(in srgb, var(--ax-accent) 12%, transparent)" } : undefined}
          >
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: KIND_VAR[k], boxShadow: layers[k] ? `0 0 6px ${KIND_VAR[k]}` : "none" }}
            />
            {k}
          </button>
        );
      })}
    </div>
  );
}
