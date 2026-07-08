import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { GITHUB_URL } from "../content";
import { GitHubIcon } from "./ui";

const LINKS = [
  { href: "#shadow", label: "The Shadow" },
  { href: "#lifecycle", label: "The Loop" },
  { href: "#features", label: "Features" },
  { href: "#opensource", label: "Open Source" },
];

export function LogoGlyph({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect
        x="10.5"
        y="10.5"
        width="15"
        height="15"
        rx="3.5"
        fill="var(--color-accent)"
        opacity="0.32"
      />
      <rect
        x="5.5"
        y="5.5"
        width="15"
        height="15"
        rx="3.5"
        fill="none"
        stroke="var(--color-accent-ink)"
        strokeWidth="2.4"
      />
    </svg>
  );
}

export default function Nav({ onOpenPalette }: { onOpenPalette: () => void }) {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <motion.header
      initial={{ y: -24, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.15 }}
      className={`fixed top-0 inset-x-0 z-50 transition-all duration-500 ${
        scrolled
          ? "bg-void/75 backdrop-blur-xl border-b hairline"
          : "bg-transparent border-b border-transparent"
      }`}
    >
      <nav className="mx-auto max-w-6xl px-5 sm:px-8 h-16 flex items-center justify-between">
        <a href="#top" className="flex items-center gap-2.5">
          <LogoGlyph className="w-7 h-7" />
          <span className="text-[15px] tracking-tight">
            <span className="font-semibold">cru</span>
            <span className="font-serif italic text-accent-ink text-[17px]">cible</span>
          </span>
        </a>

        <div className="hidden md:flex items-center gap-8">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-[13px] text-ink-3 hover:text-ink transition-colors tracking-wide"
            >
              {link.label}
            </a>
          ))}
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={onOpenPalette}
            aria-label="Open command palette"
            className="hidden sm:flex items-center gap-2 rounded-lg border border-line-strong bg-surface-2/60 px-3 py-2 font-mono text-[11px] text-ink-3 hover:text-ink-2 hover:border-accent/50 transition-all cursor-pointer"
          >
            <svg
              viewBox="0 0 16 16"
              className="w-3 h-3 text-accent"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden
            >
              <circle cx="7" cy="7" r="4.4" />
              <path d="m10.5 10.5 3 3" strokeLinecap="round" />
            </svg>
            ⌘K
          </button>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 rounded-lg border border-line-strong bg-surface-2/80 px-3.5 py-2 text-[13px] text-ink-2 hover:text-ink hover:border-accent/50 transition-all hover:-translate-y-px"
          >
            <GitHubIcon className="w-4 h-4" />
            <span className="hidden sm:inline">Star on GitHub</span>
            <span className="sm:hidden">GitHub</span>
          </a>
        </div>
      </nav>
    </motion.header>
  );
}
