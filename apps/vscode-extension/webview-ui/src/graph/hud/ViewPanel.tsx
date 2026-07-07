// Bottom-right navigation panel — the product version of the motion study's
// "Motion studies" tour: each button is a guided way into the space.
interface Props {
  focusLevel: 0 | 1 | 2 | 3;
  canTraceHub: boolean;
  canRideBeam: boolean;
  onOverview: () => void;
  onTraceHub: () => void;
  onRideBeam: () => void;
  onEnterFile: () => void;
}

const BTN =
  "ax-nav-btn flex justify-between items-center w-full px-3 py-1.5 rounded-lg text-[11px] text-left " +
  "text-[var(--ax-ink)] disabled:opacity-30";

export function ViewPanel({
  focusLevel,
  canTraceHub,
  canRideBeam,
  onOverview,
  onTraceHub,
  onRideBeam,
  onEnterFile,
}: Props) {
  const rows: { label: string; hint: string; onClick: () => void; disabled: boolean }[] = [
    { label: "Overview", hint: "reset", onClick: onOverview, disabled: false },
    { label: "Trace the hub", hint: "select", onClick: onTraceHub, disabled: !canTraceHub },
    { label: "Ride a beam", hint: "travel", onClick: onRideBeam, disabled: !canRideBeam },
    { label: "Enter a file", hint: "dive", onClick: onEnterFile, disabled: focusLevel !== 2 },
  ];
  return (
    <div className="ax-glass absolute bottom-5 right-4 w-44 px-2 py-2.5">
      <div className="px-3 pb-1.5 text-[9px] uppercase tracking-[0.25em] opacity-45 text-[var(--ax-ink)]">
        navigate
      </div>
      {rows.map((r) => (
        <button key={r.label} type="button" className={BTN} onClick={r.onClick} disabled={r.disabled}>
          <span>{r.label}</span>
          <span className="text-[9.5px] opacity-40">{r.hint}</span>
        </button>
      ))}
    </div>
  );
}
