import { EMBER } from "../palette";
import type { EdgeKind } from "../types";

interface Props {
  layers: Record<EdgeKind, boolean>;
  focusLevel: number;
  onToggle: (kind: EdgeKind, on: boolean) => void;
}

const KINDS: EdgeKind[] = ["Imports", "Calls", "Inherits", "References"];

export function EdgeLayers({ layers, focusLevel, onToggle }: Props) {
  return (
    <div
      className="absolute top-36 right-4 px-3 py-3 rounded-xl w-40
                 bg-[rgba(22,7,9,0.6)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md"
    >
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
            className={`flex items-center gap-2 w-full px-2 py-1.5 mt-1 rounded-md text-[11px] text-left
                        ${layers[k] ? "bg-[rgba(251,146,60,0.12)]" : "opacity-45"}
                        ${disabled ? "opacity-25 cursor-not-allowed" : "hover:bg-[rgba(255,244,234,0.07)]"}`}
          >
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: EMBER.kinds[k], boxShadow: layers[k] ? `0 0 6px ${EMBER.kinds[k]}` : "none" }}
            />
            {k}
          </button>
        );
      })}
    </div>
  );
}
