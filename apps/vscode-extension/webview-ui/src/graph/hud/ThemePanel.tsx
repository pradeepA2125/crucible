// Top-right palette chips (below Edge layers) — the motion study's palette
// switcher, kept in the right-hand HUD column per user preference.
import { PALETTES, type PaletteName } from "../palette";

interface Props {
  palette: PaletteName;
  onPalette: (name: PaletteName) => void;
}

export function ThemePanel({ palette, onPalette }: Props) {
  const names = Object.keys(PALETTES) as PaletteName[];
  return (
    <div className="ax-glass absolute top-[21rem] right-4 px-2 py-2.5 w-40 text-[var(--ax-ink)]">
      <div className="px-2 pb-1 text-[9px] uppercase tracking-[0.25em] opacity-45">theme</div>
      {names.map((name) => (
        <button
          key={name}
          type="button"
          aria-pressed={name === palette}
          onClick={() => onPalette(name)}
          className={`ax-chip flex justify-between items-center w-full px-2 py-1.5 rounded-lg text-[10.5px] text-left
                      ${name === palette ? "on" : "opacity-70"}`}
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
