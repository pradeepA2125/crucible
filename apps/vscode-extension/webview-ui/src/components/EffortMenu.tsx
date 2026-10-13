import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { vscode } from "../vscodeApi";

type Level = "off" | "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra";
type Pending = Level | "default";
type RowState = "supported" | "unsupported" | "unknown";

const LADDER: Level[] = ["off", "low", "medium", "high", "max"];
const LABEL: Record<Level, string> = {
  off: "Off",
  none: "None",
  minimal: "Minimal",
  xhigh: "Extra high",
  ultra: "Ultra",
  low: "Low",
  medium: "Medium",
  high: "High",
  max: "Max",
};

interface Option {
  value: Level;
  label: string;
  description: string;
  state: RowState;
}
interface Control {
  options: Option[];
  source: "catalog" | "transport" | "fallback";
  default: Level | null;
  reason: string | null;
}
interface Support {
  supported: Level[];
  unsupported: Record<string, string>;
  control?: Control;
}

/**
 * EffortMenu — composer reasoning-effort chip + upward popover.
 *
 * Provider descriptors own native options, labels and defaults. The five-level
 * ladder is only a compatibility fallback when capability metadata is absent.
 * An explicit empty descriptor disables the control, rather than inventing
 * selectable levels for models that do not support effort.
 *
 * State arrives on the existing `modelList` message rather than a channel of its
 * own, because capability is per-(backend, model) and must refresh on model swap
 * anyway.
 *
 * Selecting a rung mirrors ModelMenu's swap pattern: the popover stays OPEN with
 * a pending indicator on the chosen row until the host replies — a refreshed
 * `modelList` (success → close) or `effortSwapError` (error line, stays open).
 * Closing before the reply lands would strand the error message off-screen,
 * which is exactly the silent-failure symptom this control exists to avoid.
 */
export function EffortMenu() {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<Level | null>(null);
  const [support, setSupport] = useState<Support | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [swapping, setSwapping] = useState<Pending | null>(null); // choice in flight
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const [menuPosition, setMenuPosition] = useState({ left: 0, width: 260 });
  const swappingRef = useRef<Pending | null>(null);
  swappingRef.current = swapping;

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "modelList") {
        const effort = m["effort"] as
          | { level: Level | null; support: Support | null; note?: string | null }
          | undefined;
        setLevel(effort?.level ?? null);
        setSupport(effort?.support ?? null);
        setNote(effort?.note ?? null);
        setError(null);
        if (swappingRef.current !== null) {
          setSwapping(null);
          setOpen(false); // successful swap → close
        }
      } else if (m?.["type"] === "effortSwapError") {
        setSwapping(null);
        setError(m["message"] as string);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = rootRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const width = Math.min(280, window.innerWidth - 16);
      const left = Math.max(8 - anchor.left, Math.min(0, window.innerWidth - 8 - anchor.left - width));
      setMenuPosition({ left, width });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);

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

  const control = support?.control;
  const rows: Option[] = control?.options ?? LADDER.map((value) => ({
    value, label: LABEL[value], description: support?.unsupported[value] ?? "", state: stateOf(value),
  }));
  const unavailable = control !== undefined && rows.length === 0;
  const labelOf = (value: Pending) => value === "default" ? "Default" :
    rows.find((row) => row.value === value)?.label ?? LABEL[value];

  function choose(candidate: Level | null) {
    if (unavailable || (candidate !== null && stateOf(candidate) === "unsupported") || swapping) return;
    setSwapping(candidate ?? "default");
    setError(null);
    vscode.postMessage({ type: "setReasoningEffort", level: candidate });
  }

  // "max unavailable here; using high." collapses to a chip-sized "no max".
  const clampHint = note ? note.split(" ")[0] : null;
  // While a swap is in flight the chip tracks the TARGET rung (not the stale
  // current one) so a click reads as acknowledged instead of doing nothing.
  const chipLabel = swapping ? labelOf(swapping) : unavailable ? "Effort" : level ? labelOf(level) : control ? "Default" : "Effort";
  const swapLabel = swapping
    ? `Switching reasoning effort to ${labelOf(swapping)} — checking with the provider`
    : null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={unavailable}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={
          swapLabel
            ? swapLabel
            : clampHint
              ? `Reasoning effort: ${chipLabel}, no ${clampHint}`
              : `Reasoning effort: ${chipLabel}`
        }
        title={
          unavailable ? control?.reason ?? "Effort is unavailable for this model" : swapLabel
            ? swapLabel
            : clampHint
              ? `Reasoning effort: ${chipLabel} (no ${clampHint})`
              : `Reasoning effort: ${chipLabel}`
        }
        className="flex h-6 items-center gap-1 rounded-[7px] border px-1.5 text-[10px] cursor-pointer transition-colors duration-150 hover:text-text disabled:cursor-default disabled:opacity-60"
        style={{
          background: "var(--color-surface-2)",
          borderColor: "var(--color-border-strong)",
          color: "var(--color-text-2)",
        }}
      >
        <span style={{ color: "var(--color-accent)" }}><Icon name="chip" size={9} /></span>
        <span>{chipLabel}</span>
        {swapping ? (
          <span
            aria-hidden="true"
            className="inline-block flex-shrink-0 rounded-full border-2"
            style={{
              width: 9,
              height: 9,
              borderColor: "var(--color-accent-ink) var(--accent-bg) var(--accent-bg) var(--accent-bg)",
              animation: "spin 0.75s linear infinite",
            }}
          />
        ) : (
          <>
            {clampHint ? <span style={{ color: "var(--color-text-4)" }}>· no {clampHint}</span> : null}
            <Icon name="chev-d" size={8} />
          </>
        )}
      </button>

      {open && !unavailable ? (
        <div
          role="menu"
          className="anim-rise surface-card absolute bottom-full z-50 mb-1.5 p-1"
          style={{ ...menuPosition, maxHeight: "min(60vh, 360px)", overflowY: "auto" }}
        >
          <button
            type="button" role="menuitem" disabled={swapping !== null}
            onClick={() => choose(null)}
            className="menu-item flex w-full flex-col items-start gap-0.5 rounded-md border-0 bg-transparent px-2 py-1.5 text-left text-[11px]"
            style={{ color: "var(--color-text)" }}
          >
            <span>Default{level === null ? " ✓" : ""}</span>
            <span style={{ color: "var(--color-text-4)" }}>
              {swapping === "default" ? "checking with the provider…" : control?.default
                ? `Model default: ${labelOf(control.default)}` : "Let the model choose"}
            </span>
          </button>
          {rows.map((row) => {
            const candidate = row.value;
            const state = row.state;
            return (
              <button
                key={candidate}
                type="button"
                role="menuitem"
                disabled={state === "unsupported" || swapping !== null}
                onClick={() => choose(candidate)}
                className="menu-item flex w-full flex-col items-start gap-0.5 rounded-md border-0 bg-transparent px-2 py-1.5 text-left text-[11px] disabled:cursor-not-allowed disabled:opacity-60"
                style={{ color: "var(--color-text)" }}
              >
                <span className="flex w-full items-center gap-1.5">
                  <span className="flex-1">{row.label}{level === candidate ? " ✓" : ""}</span>
                  {swapping === candidate && (
                    <span
                      className="inline-block rounded-full border-2"
                      style={{
                        width: 9,
                        height: 9,
                        borderColor: "var(--color-accent-ink) var(--accent-bg) var(--accent-bg) var(--accent-bg)",
                        animation: "spin 0.75s linear infinite",
                      }}
                    />
                  )}
                </span>
                {swapping === candidate ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>
                    checking with the provider…
                  </span>
                ) : state === "unsupported" ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>
                    {row.description}
                  </span>
                ) : state === "unknown" ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>
                    unverified for this endpoint
                  </span>
                ) : row.description ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>{row.description}</span>
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
