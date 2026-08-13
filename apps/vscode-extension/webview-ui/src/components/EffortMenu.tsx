import { useEffect, useRef, useState } from "react";
import { vscode } from "../vscodeApi";

type Level = "off" | "low" | "medium" | "high" | "max";
type RowState = "supported" | "unsupported" | "unknown";

const LADDER: Level[] = ["off", "low", "medium", "high", "max"];
const LABEL: Record<Level, string> = {
  off: "Off",
  low: "Low",
  medium: "Medium",
  high: "High",
  max: "Max",
};

interface Support {
  supported: Level[];
  unsupported: Record<string, string>;
}

/**
 * EffortMenu — composer reasoning-effort chip + upward popover.
 *
 * All five rungs are always listed. Ones the active (backend, model) cannot
 * express are disabled with the provider's own reason; ones we have no evidence
 * about are selectable but flagged unverified — an arbitrary OpenAI-compatible
 * endpoint genuinely cannot be vouched for, and claiming support we don't have is
 * the silent-degradation failure this control exists to avoid.
 *
 * State arrives on the existing `modelList` message rather than a channel of its
 * own, because capability is per-(backend, model) and must refresh on model swap
 * anyway.
 */
export function EffortMenu() {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<Level | null>(null);
  const [support, setSupport] = useState<Support | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "modelList") {
        const effort = m["effort"] as
          | { level: Level | null; support: Support | null; note?: string | null }
          | undefined;
        if (effort) {
          setLevel(effort.level);
          setSupport(effort.support);
          setNote(effort.note ?? null);
        }
        setError(null);
      } else if (m?.["type"] === "effortSwapError") {
        setError(m["message"] as string);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function stateOf(candidate: Level): RowState {
    if (support?.supported.includes(candidate)) return "supported";
    if (support?.unsupported[candidate] !== undefined) return "unsupported";
    return "unknown";
  }

  function choose(candidate: Level) {
    if (stateOf(candidate) === "unsupported") return;
    setOpen(false);
    vscode.postMessage({ type: "setReasoningEffort", level: candidate });
  }

  // "max unavailable here; using high." collapses to a chip-sized "no max".
  const clampHint = note ? note.split(" ")[0] : null;
  const chipLabel = level ? LABEL[level] : "Effort";

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={
          clampHint ? `Reasoning effort: ${chipLabel}, no ${clampHint}` : `Reasoning effort: ${chipLabel}`
        }
        title={clampHint ? `Reasoning effort: ${chipLabel} (no ${clampHint})` : `Reasoning effort: ${chipLabel}`}
        className="menu-item flex h-6 items-center gap-1 rounded-[7px] border px-1.5 text-[10px] cursor-pointer"
        style={{
          background: "var(--color-surface-2)",
          borderColor: "var(--color-border-strong)",
          color: "var(--color-text-2)",
        }}
      >
        <span>{chipLabel}</span>
        {clampHint ? <span style={{ color: "var(--color-text-4)" }}>· no {clampHint}</span> : null}
      </button>

      {open ? (
        <div
          role="menu"
          className="anim-rise surface-card absolute bottom-full left-0 z-50 mb-1.5 min-w-[220px] p-1"
        >
          {LADDER.map((candidate) => {
            const state = stateOf(candidate);
            return (
              <button
                key={candidate}
                type="button"
                role="menuitem"
                disabled={state === "unsupported"}
                onClick={() => choose(candidate)}
                className="menu-item flex w-full flex-col items-start gap-0.5 rounded-md border-0 bg-transparent px-2 py-1.5 text-left text-[11px] disabled:cursor-not-allowed disabled:opacity-60"
                style={{ color: "var(--color-text)" }}
              >
                <span>{LABEL[candidate]}</span>
                {state === "unsupported" ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>
                    {support?.unsupported[candidate]}
                  </span>
                ) : null}
                {state === "unknown" ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>
                    unverified for this endpoint
                  </span>
                ) : null}
              </button>
            );
          })}
          {error ? (
            <div className="px-2 py-1.5 text-[10.5px]" style={{ color: "var(--color-red)" }}>
              {error}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
