// Bottom-right navigation panel — the product version of the motion study's
// "Motion studies" tour: each button is a guided way into the space, plus the
// study's palette chips.
import { PALETTES, type PaletteName } from "../palette";

interface Props {
  focusLevel: 0 | 1 | 2 | 3;
  canTraceHub: boolean;
  canRideBeam: boolean;
  palette: PaletteName;
  onPalette: (name: PaletteName) => void;
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
  palette,
  onPalette,
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
  const paletteNames = Object.keys(PALETTES) as PaletteName[];
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
      <div className="px-3 pt-2 pb-1 text-[9px] uppercase tracking-[0.25em] opacity-45 text-[var(--ax-ink)]">
        theme
      </div>
      {paletteNames.map((name) => (
        <button
          key={name}
          type="button"
          aria-pressed={name === palette}
          onClick={() => onPalette(name)}
          className={`ax-chip flex justify-between items-center w-full px-3 py-1.5 rounded-lg text-[10.5px] text-left
                      text-[var(--ax-ink)] ${name === palette ? "on" : "opacity-70"}`}
        >
          <span>{PALETTES[name].name}</span>
          <span className="flex gap-[3px]">
            {PALETTES[name].clusterTints.slice(0, 3).map((c) => (
              <i key={c} className="block w-[7px] h-[7px] rounded-full" style={{ background: c }} />
            ))}
          </span>
        </button>
      ))}
    </div>
  );
}
