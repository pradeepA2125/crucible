import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { ModelOption } from "../types";
import { vscode } from "../vscodeApi";

function shortModel(model: string): string {
  return model.length > 18 ? `${model.slice(0, 17)}…` : model;
}

/**
 * ModelMenu — composer model chip + upward popover. Lists only providers the
 * host reports as swappable (stored key, or currently active). Selecting one
 * posts setModel; the popover stays open with a row spinner until the host
 * replies with the refreshed modelList (success → close) or modelSwapError
 * (error line, stays open). Swaps apply from the next turn (hot-swap route).
 */
export function ModelMenu() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<{ backend: string; model: string } | null>(null);
  const [options, setOptions] = useState<ModelOption[]>([]);
  const [swapping, setSwapping] = useState<string | null>(null); // backend in flight
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const swappingRef = useRef<string | null>(null);
  swappingRef.current = swapping;

  useEffect(() => {
    vscode.postMessage({ type: "listModels" });
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "modelList") {
        setCurrent((m["current"] as { backend: string; model: string } | null) ?? null);
        setOptions((m["options"] as ModelOption[]) ?? []);
        setError(null);
        if (swappingRef.current !== null) {
          setSwapping(null);
          setOpen(false); // successful swap → close
        }
      } else if (m?.["type"] === "modelSwapError") {
        setSwapping(null);
        setError(m["message"] as string);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Close on outside click / Escape while open.
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

  function choose(opt: ModelOption) {
    if (opt.active || swapping) return;
    setSwapping(opt.backend);
    setError(null);
    vscode.postMessage({ type: "setModel", backend: opt.backend, model: opt.model });
  }

  return (
    <div ref={rootRef} className="relative">
      {/* Chip */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={current ? `Model: ${current.model}` : "Select model"}
        title={current ? `${current.backend} / ${current.model}` : "Select model"}
        className="flex h-6 items-center gap-1 rounded-[7px] border px-1.5 text-[10px] cursor-pointer transition-colors duration-150 hover:text-text"
        style={{
          background: "var(--color-surface-2)",
          borderColor: "var(--color-border-strong)",
          color: "var(--color-text-2)",
        }}
      >
        <span style={{ color: "var(--color-accent)" }}><Icon name="spark" size={9} /></span>
        <span className="max-w-[130px] truncate font-mono" style={{ fontSize: "9.5px" }}>
          {current ? shortModel(current.model) : "model"}
        </span>
        <Icon name="chev-d" size={8} />
      </button>

      {/* Popover (above the composer) */}
      {open && (
        <div
          role="menu"
          className="anim-rise absolute bottom-full left-0 z-50 mb-1.5 w-[240px] rounded-[10px] border p-1"
          style={{
            background: "var(--color-surface-2)",
            borderColor: "var(--color-border-strong)",
            boxShadow: "0 10px 30px -10px rgba(0,0,0,.7), inset 0 1px 0 var(--hairline)",
          }}
        >
          {options.map((opt) => (
            <button
              key={opt.backend}
              type="button"
              role="menuitem"
              onClick={() => choose(opt)}
              className="flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-md border-0 bg-transparent px-2 py-1.5 text-left transition-colors duration-150 hover:bg-[var(--accent-bg)]"
            >
              <span className="flex w-full items-center gap-1.5">
                <span className="flex-1 text-[9.5px] font-semibold uppercase tracking-wide text-text-3">
                  {opt.label}
                </span>
                {opt.active && <span style={{ color: "var(--color-accent)" }}><Icon name="check" size={10} /></span>}
                {swapping === opt.backend && (
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
              <span className="font-mono text-[10.5px] text-text">{opt.model}</span>
            </button>
          ))}
          {options.length === 0 && (
            <p className="px-2 py-2 text-[10.5px] text-text-3">
              No validated providers yet — add one in Settings.
            </p>
          )}
          {error && (
            <p className="px-2 py-1.5 text-[10.5px]" style={{ color: "var(--color-red)" }}>{error}</p>
          )}
          <div className="mt-1 border-t pt-1" style={{ borderColor: "var(--hairline)" }}>
            <button
              type="button"
              role="menuitem"
              onClick={() => { vscode.postMessage({ type: "openSettings" }); setOpen(false); }}
              className="flex w-full cursor-pointer items-center gap-1.5 rounded-md border-0 bg-transparent px-2 py-1.5 text-left text-[10.5px] text-text-2 transition-colors duration-150 hover:bg-surface-3 hover:text-text"
            >
              <Icon name="gear" size={10} /> Provider settings…
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
