import { useEffect, useId, useRef, useState } from "react";
import { effortStops, type EffortLevel, type EffortView } from "../effort";
export function EffortSlider({ effort, pending, error, busy = false, onChoose }: {
  effort: EffortView; pending: EffortLevel | "default" | null; error: string | null; busy?: boolean; onChoose(level: EffortLevel | null): void;
}) {
  const id = useId();
  const restoreFocus = useRef<HTMLElement | null>(null);
  const slider = useRef<HTMLInputElement>(null);
  // A mouse drag fires "input" at every notch; each choice validates with the provider and disables
  // the slider, so a drag would stop at its first notch. Preview while dragging, commit on "change".
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const commit = useRef<(control: HTMLInputElement) => void>(() => {});
  useEffect(() => {
    if (pending === null && !busy && restoreFocus.current) {
      if (restoreFocus.current.isConnected) restoreFocus.current.focus();
      restoreFocus.current = null;
    }
  }, [pending, busy]);
  function choose(level: EffortLevel | null, control: HTMLElement) {
    if (document.activeElement === control) restoreFocus.current = control;
    onChoose(level);
  }
  const rows = effortStops(effort.support);
  if (rows.length === 0) return null;
  const current = pending === "default" ? null : pending ?? effort.level;
  const found = rows.findIndex(row => row.value === current);
  const index = Math.max(0, found);
  const shown = dragIndex ?? index;
  const selected = rows[shown] ?? rows[index];
  commit.current = (control) => {
    setDragIndex(null);
    const row = rows[Number(control.value)];
    if (row && row.value !== current) choose(row.value, control);
  };
  const hasSlider = rows.length >= 2;
  useEffect(() => {
    const control = slider.current;
    if (!control) return;
    const onCommit = () => commit.current(control);
    control.addEventListener("change", onCommit);
    return () => control.removeEventListener("change", onCommit);
  }, [hasSlider]);
  const note = effort.note ?? (found < 0 ? "The previous level is not offered by this model. Choose an available level." : null);
  return (
    <div className="grid gap-2 border-t border-border p-3 text-meta text-text-2">
      <label htmlFor={id} className="font-medium text-text">Reasoning effort</label>
      {hasSlider && <input ref={slider} id={id} type="range" min={0} max={rows.length - 1} step={1} value={shown} list={`${id}-stops`}
        disabled={busy || pending !== null} aria-valuetext={selected.label} aria-describedby={`${id}-description`}
        onChange={e => { if (e.nativeEvent.type === "input") setDragIndex(Number(e.target.value)); }}
        className="w-full accent-[var(--accent-fill)]" />}
      <datalist id={`${id}-stops`}>{rows.map((row, i) => <option key={i} value={i} label={row.label} />)}</datalist>
      <div className="flex items-center gap-2">
        <span className="text-text">{selected.label}</span>
        {selected.state === "unknown" && <span className="text-caption text-text-3">unverified</span>}
        <button type="button" disabled={busy || pending !== null} onClick={e => choose(null, e.currentTarget)}
          className="ml-auto text-caption text-text-2 underline hover:text-text" aria-label="Use default effort">Default</button>
      </div>
      <p id={`${id}-description`} className="text-caption text-text-3">{selected.description || (selected.state === "unknown" ? "Unverified for this endpoint." : "")}</p>
      {pending !== null && <p role="status">Checking with the provider…</p>}
      {note && <p className="text-caption text-text-3">{note}</p>}
      {error && <p role="alert" className="text-caption text-red">{error}</p>}
    </div>
  );
}
