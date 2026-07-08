import { useRef, useState } from "react";
import {
  motion,
  useMotionValueEvent,
  useScroll,
  useTransform,
} from "framer-motion";
import DependencySpace, { type EdgeKind, type LayerState } from "./DependencySpace";
import Hud from "./Hud";
import { GITHUB_URL } from "../content";
import { useTheme } from "../theme";
import { Em, GitHubIcon, InstallCommand } from "./ui";

const EASE = [0.22, 1, 0.36, 1] as const;

const rise = (delay: number) => ({
  initial: { opacity: 0, y: 40 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.9, delay, ease: EASE },
});

const STATS = [
  { value: "0 writes", detail: "to your repo without your approval" },
  { value: "1 command", detail: "installs the editor, backend, and indexer" },
  { value: "∞ models", detail: "any provider, cloud or fully local" },
];

export default function Hero({ onOpenPalette }: { onOpenPalette: () => void }) {
  const ref = useRef<HTMLElement>(null);
  const { theme } = useTheme();
  const [layers, setLayers] = useState<LayerState>({
    imports: true,
    calls: true,
    inherits: true,
  });
  const zoomRef = useRef(0);

  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start start", "end start"],
  });
  useMotionValueEvent(scrollYProgress, "change", (v) => {
    zoomRef.current = v;
  });
  const contentY = useTransform(scrollYProgress, [0, 1], [0, 140]);
  const contentOpacity = useTransform(scrollYProgress, [0, 0.75], [1, 0]);

  const toggleLayer = (kind: EdgeKind) =>
    setLayers((prev) => ({ ...prev, [kind]: !prev[kind] }));

  return (
    <section
      ref={ref}
      id="top"
      className="relative min-h-screen flex flex-col justify-center overflow-hidden aura"
    >
      <DependencySpace theme={theme} layers={layers} zoom={zoomRef} />

      {/* scrims: vignette pulls focus, bottom fade hands off to the page */}
      <div className="absolute inset-0 pointer-events-none vignette" />
      <div className="absolute inset-x-0 bottom-0 h-40 pointer-events-none bg-gradient-to-b from-transparent to-void" />

      <Hud layers={layers} onToggleLayer={toggleLayer} onOpenPalette={onOpenPalette} />

      <motion.div
        style={{ y: contentY, opacity: contentOpacity }}
        className="relative z-10 mx-auto max-w-6xl px-5 sm:px-8 pt-28 pb-24 w-full"
      >
        <motion.div {...rise(0.25)} className="flex">
          <span className="inline-flex items-center gap-2 rounded-full border border-accent/25 bg-accent/8 px-4 py-1.5 font-mono text-[11px] tracking-[0.22em] uppercase text-accent-ink backdrop-blur-sm">
            <span className="w-1.5 h-1.5 rounded-full bg-green animate-pulse" />
            crucible · open source · bring your own model
          </span>
        </motion.div>

        <motion.h1
          {...rise(0.4)}
          className="mt-8 text-[13vw] sm:text-7xl lg:text-[92px] font-medium leading-[0.98] tracking-[-0.03em]"
        >
          Every edit is born
          <br />
          in <Em>shadow.</Em>
        </motion.h1>

        <motion.p
          {...rise(0.55)}
          className="mt-8 max-w-xl text-lg text-ink-2 leading-relaxed"
        >
          <span className="text-ink font-semibold">Crucible</span> is an AI
          code editor that explores your repo, proposes
          before it acts, and edits an isolated{" "}
          <span className="text-ink">shadow workspace</span> — your code only
          changes when you hit accept.
        </motion.p>

        <motion.div
          {...rise(0.7)}
          className="mt-10 flex flex-col sm:flex-row sm:items-center gap-4"
        >
          <InstallCommand />
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="btn-glow inline-flex items-center justify-center gap-2.5 rounded-xl px-6 py-3.5 text-[15px] font-medium text-white"
          >
            <GitHubIcon className="w-4.5 h-4.5" />
            View the source
          </a>
        </motion.div>

        <motion.div
          {...rise(0.85)}
          className="mt-14 grid grid-cols-1 sm:grid-cols-3 gap-6 border-t hairline pt-8 max-w-3xl"
        >
          {STATS.map((stat) => (
            <div key={stat.value}>
              <p className="font-mono text-xl text-ink">{stat.value}</p>
              <p className="mt-1 text-[13px] text-ink-3">{stat.detail}</p>
            </div>
          ))}
        </motion.div>
      </motion.div>
    </section>
  );
}
