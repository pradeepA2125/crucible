import { motion } from "framer-motion";
import { THEMES, useTheme, type ThemeId } from "../theme";
import type { EdgeKind, LayerState } from "./DependencySpace";

const EASE = [0.22, 1, 0.36, 1] as const;

const fade = (delay: number) => ({
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.8, delay, ease: EASE },
});

function HudTitle({ children }: { children: string }) {
  return (
    <p className="font-mono text-[9px] tracking-[0.3em] uppercase text-ink-4">
      {children}
    </p>
  );
}

function Dot({ color }: { color: string }) {
  return <span className="glow-dot" style={{ background: color, color }} />;
}

export function Brackets() {
  const base = "absolute w-7 h-7 border-accent/30 pointer-events-none";
  return (
    <motion.div
      {...fade(1.4)}
      className="absolute inset-5 sm:inset-7 pointer-events-none z-10"
      aria-hidden
    >
      <span className={`${base} top-0 left-0 border-t border-l`} />
      <span className={`${base} top-0 right-0 border-t border-r`} />
      <span className={`${base} bottom-0 left-0 border-b border-l`} />
      <span className={`${base} bottom-0 right-0 border-b border-r`} />
    </motion.div>
  );
}

interface HudProps {
  layers: LayerState;
  onToggleLayer: (kind: EdgeKind) => void;
  onOpenPalette: () => void;
}

export default function Hud({ layers, onToggleLayer, onOpenPalette }: HudProps) {
  const { theme, setTheme } = useTheme();

  const edgeMeta: Array<{ kind: EdgeKind; label: string; color: string }> = [
    { kind: "imports", label: "Imports", color: theme.accent },
    { kind: "calls", label: "Calls", color: theme.secondary },
    { kind: "inherits", label: "Inherits", color: theme.tertiary },
  ];

  return (
    <>
      <Brackets />

      {/* top-left: scene plaque — the hero object is your repo, not stock art */}
      <motion.div
        {...fade(1.45)}
        className="hud-card absolute top-24 left-8 z-20 hidden lg:block px-5 py-3.5"
      >
        <p className="brand-gradient font-semibold text-[15px] tracking-[0.34em] uppercase">
          Axon
        </p>
        <p className="mt-1 font-mono text-[11px] text-ink-3">
          dependency space ·{" "}
          <b className="font-medium" style={{ color: theme.secondary }}>
            your-repo
          </b>{" "}
          <span className="opacity-60">— ships inside the editor</span>
        </p>
      </motion.div>

      {/* top-right: reading the space */}
      <motion.div
        {...fade(1.55)}
        className="hud-card absolute top-24 right-8 z-20 hidden lg:block px-4 py-3.5 w-60"
      >
        <HudTitle>Reading the space</HudTitle>
        <ul className="mt-2.5 space-y-2 font-mono text-[11px] text-ink-3">
          <li className="flex items-center gap-2.5">
            <Dot color={theme.star} /> file — mass = coupling
          </li>
          <li className="flex items-center gap-2.5">
            <Dot color={theme.beacon} /> entry point beacon
          </li>
          <li className="flex items-center gap-2.5">
            <Dot color={theme.accent} /> package nebula
          </li>
          <li className="flex items-center gap-2.5">
            <Dot color={theme.secondary} /> energy = dependencies
          </li>
        </ul>
      </motion.div>

      {/* right-middle: edge layers (live toggles) */}
      <motion.div
        {...fade(1.7)}
        className="hud-card absolute top-[47%] right-8 z-20 hidden lg:block px-3.5 py-3 w-60"
      >
        <div className="px-0.5">
          <HudTitle>Edge layers</HudTitle>
        </div>
        <div className="mt-1.5 space-y-1.5">
          {edgeMeta.map((edge) => {
            const isOn = layers[edge.kind];
            return (
              <button
                key={edge.kind}
                onClick={() => onToggleLayer(edge.kind)}
                className={`w-full flex items-center gap-2.5 rounded-[9px] border px-2.5 py-[7px] font-mono text-[11.5px] transition-all duration-200 cursor-pointer ${
                  isOn
                    ? "border-accent/25 bg-accent/12 text-ink"
                    : "border-transparent bg-white/4 text-ink-4"
                }`}
              >
                <span
                  className="glow-dot !w-2 !h-2"
                  style={{
                    background: edge.color,
                    color: edge.color,
                    boxShadow: isOn ? undefined : "none",
                    opacity: isOn ? 1 : 0.25,
                  }}
                />
                {edge.label}
                <span className="ml-auto text-[9px] tracking-[0.18em] uppercase opacity-60">
                  {isOn ? "on" : "off"}
                </span>
              </button>
            );
          })}
        </div>
      </motion.div>

      {/* bottom-right: theme picker */}
      <motion.div
        {...fade(1.85)}
        className="hud-card absolute bottom-8 right-8 z-20 hidden md:block px-3.5 py-3 w-60"
      >
        <div className="px-0.5">
          <HudTitle>Theme</HudTitle>
        </div>
        <div className="mt-1.5 space-y-1.5">
          {(Object.keys(THEMES) as ThemeId[]).map((id) => {
            const candidate = THEMES[id];
            const isActive = theme.id === id;
            return (
              <button
                key={id}
                onClick={() => setTheme(id)}
                className={`w-full flex items-center gap-2.5 rounded-[9px] border px-2.5 py-[7px] font-mono text-[10.5px] tracking-[0.13em] uppercase transition-all duration-200 cursor-pointer ${
                  isActive
                    ? "border-accent/25 bg-white/9 text-ink"
                    : "border-transparent text-ink-3 hover:bg-white/7 hover:text-ink"
                }`}
              >
                <span className="flex gap-[3px]">
                  {[candidate.accent, candidate.secondary, candidate.tertiary].map((c) => (
                    <i
                      key={c}
                      className="block w-[7px] h-[7px] rounded-full"
                      style={{ background: c, boxShadow: `0 0 6px ${c}` }}
                    />
                  ))}
                </span>
                {candidate.label}
              </button>
            );
          })}
        </div>
      </motion.div>

      {/* bottom-center: fly-to-anything */}
      <motion.button
        {...fade(2)}
        onClick={onOpenPalette}
        className="hud-card absolute bottom-8 left-1/2 -translate-x-1/2 z-20 flex items-center gap-3 !rounded-full px-5 py-2.5 font-mono text-[11.5px] text-ink-3 hover:text-ink-2 hover:border-accent/50 transition-all cursor-pointer"
      >
        <svg
          viewBox="0 0 16 16"
          className="w-3.5 h-3.5 text-accent"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          aria-hidden
        >
          <circle cx="7" cy="7" r="4.4" />
          <path d="m10.5 10.5 3 3" strokeLinecap="round" />
        </svg>
        search this page — fly to anything
        <kbd className="kbd">⌘K</kbd>
      </motion.button>
    </>
  );
}
