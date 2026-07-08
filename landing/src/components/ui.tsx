import { useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { INSTALL_CMD } from "../content";

const EASE = [0.22, 1, 0.36, 1] as const;

export function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 34 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-90px" }}
      transition={{ duration: 0.75, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}

export function SectionHeading({
  index,
  eyebrow,
  children,
}: {
  index: string;
  eyebrow: string;
  children: ReactNode;
}) {
  return (
    <Reveal>
      <p className="font-mono text-xs tracking-[0.28em] uppercase text-accent mb-5">
        <span className="text-ink-4">{index} /</span> {eyebrow}
      </p>
      <h2 className="text-4xl sm:text-5xl lg:text-6xl font-medium leading-[1.06] tracking-tight max-w-3xl">
        {children}
      </h2>
    </Reveal>
  );
}

export function Em({ children }: { children: ReactNode }) {
  return (
    <em className="font-serif italic font-normal text-accent-ink">
      {children}
    </em>
  );
}

export function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className={className} aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

export function InstallCommand({ large = false }: { large?: boolean }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard unavailable — the command is selectable text anyway */
    }
  };

  return (
    <div
      className={`group flex items-center gap-3 rounded-xl border border-line-strong bg-panel/80 backdrop-blur transition-colors hover:border-accent/50 ${
        large ? "pl-5 pr-2 py-2" : "pl-4 pr-1.5 py-1.5"
      }`}
    >
      <span className="text-accent font-mono select-none">$</span>
      <code
        className={`font-mono text-ink-2 whitespace-nowrap overflow-x-auto max-w-[62vw] sm:max-w-md ${
          large ? "text-sm" : "text-[13px]"
        }`}
      >
        {INSTALL_CMD}
      </code>
      <button
        onClick={copy}
        aria-label="Copy install command"
        className={`shrink-0 rounded-lg font-mono transition-all cursor-pointer ${
          large ? "px-4 py-2.5 text-sm" : "px-3 py-2 text-xs"
        } ${
          copied
            ? "bg-green/15 text-green"
            : "bg-accent/15 text-accent-ink hover:bg-accent/25"
        }`}
      >
        {copied ? "copied ✓" : "copy"}
      </button>
    </div>
  );
}
