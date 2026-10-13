import { useEffect, useId, useRef } from "react";
import { effortStops, type EffortLevel, type EffortView } from "../effort";
export function EffortSlider({ effort, pending, error, busy = false, onChoose }: {
  effort: EffortView; pending: EffortLevel | "default" | null; error: string | null; busy?: boolean; onChoose(level: EffortLevel | null): void;
}) {
  const id = useId();
  const restoreFocus = useRef<HTMLElement | null>(null);
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
  const selected = rows[index];
  const note = effort.note ?? (found < 0 ? "The previous level is not offered by this model. Choose an available level." : null);
  return (
    <div className="grid gap-2 border-t border-border p-3 text-meta text-text-2">
      <label htmlFor={id} className="font-medium text-text">Reasoning effort</label>
      {rows.length >= 2 && <input id={id} type="range" min={0} max={rows.length - 1} step={1} value={index} list={`${id}-stops`}
        disabled={busy || pending !== null} aria-valuetext={selected.label} aria-describedby={`${id}-description`}
        onChange={e => choose(rows[Number(e.target.value)].value, e.currentTarget)} className="w-full accent-[var(--accent-fill)]" />}
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
