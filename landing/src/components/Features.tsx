import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { Em, Reveal, SectionHeading } from "./ui";

interface Feature {
  title: string;
  body: string;
  tint: string;
  chipBg: string;
  icon: ReactNode;
}

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const FEATURES: Feature[] = [
  {
    title: "It proposes. You choose.",
    body: "A big ask raises a mode gate: a plan sketch with model-authored options — edit now or just explain — and a recommendation. The gate stays closed until you pick.",
    tint: "text-accent",
    chipBg: "bg-accent/12",
    icon: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" {...stroke}>
        <rect x="4" y="4.5" width="16" height="6.5" rx="2" />
        <rect x="4" y="14" width="16" height="6.5" rx="2" opacity="0.45" />
        <path d="m7.2 7.8 1.3 1.3 2.3-2.6" />
      </svg>
    ),
  },
  {
    title: "Big work gets a ledger",
    body: "Multi-part changes get a live todo ledger you can watch. The agent can't declare victory early — finishing is hard-blocked until every item is done, blocked, or cancelled.",
    tint: "text-green",
    chipBg: "bg-green/12",
    icon: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" {...stroke}>
        <path d="M9 5h10M9 12h10M9 19h6" />
        <path d="M4 5.5 5 6.5 7 4.5M4 12.5 5 13.5 7 11.5" />
        <circle cx="5" cy="19" r="1.4" />
      </svg>
    ),
  },
  {
    title: "A symbol graph in Rust",
    body: "An incremental tree-sitter + LSP indexer resolves calls, imports, and inheritance into a live code graph. The agent queries it mid-turn — and you fly through it in Axon, the built-in 3D view.",
    tint: "text-sky",
    chipBg: "bg-sky/12",
    icon: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" {...stroke}>
        <circle cx="6" cy="6" r="2.2" />
        <circle cx="18" cy="8" r="2.2" />
        <circle cx="10" cy="18" r="2.2" />
        <path d="M8 7l7.8.8M7 8l2.2 8M16.5 9.8l-5 6.6" />
      </svg>
    ),
  },
  {
    title: "Memory across sessions",
    body: "Long runs compact into anchored summaries; important facts consolidate into a local SQLite memory with semantic recall. Close the editor, come back — it still knows your codebase.",
    tint: "text-amber",
    chipBg: "bg-amber/12",
    icon: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" {...stroke}>
        <ellipse cx="12" cy="6" rx="7" ry="2.8" />
        <path d="M5 6v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-6" />
      </svg>
    ),
  },
  {
    title: "Gated side effects",
    body: "Shell commands, MCP tool calls, and doc writes all pause at approval cards before running. Approve once, or remember the exact tool per workspace — never a blanket yes.",
    tint: "text-red",
    chipBg: "bg-red/12",
    icon: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" {...stroke}>
        <path d="M12 3l7 3v5c0 4.4-3 8.2-7 9.5C8 19.2 5 15.4 5 11V6l7-3Z" />
        <path d="M9.5 11.8l1.8 1.8 3.4-3.6" />
      </svg>
    ),
  },
  {
    title: "Any model. Even offline.",
    body: "Anthropic, OpenAI, Gemini, Groq and friends — or fully local through Ollama, where nothing ever leaves your machine. Hot-swap providers from settings; it applies on the very next turn.",
    tint: "text-accent-ink",
    chipBg: "bg-accent/12",
    icon: (
      <svg viewBox="0 0 24 24" className="w-5 h-5" {...stroke}>
        <rect x="7" y="7" width="10" height="10" rx="2" />
        <path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" />
      </svg>
    ),
  },
];

export default function Features() {
  return (
    <section id="features" className="relative py-28 sm:py-36">
      <div className="mx-auto max-w-6xl px-5 sm:px-8">
        <SectionHeading index="03" eyebrow="what's in the crucible">
          Built like an editor. <br />
          Behaves like a <Em>careful colleague.</Em>
        </SectionHeading>

        <div className="mt-16 grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {FEATURES.map((feature, i) => (
            <Reveal key={feature.title} delay={(i % 3) * 0.1}>
              <motion.div
                whileHover={{ y: -4 }}
                transition={{ type: "spring", stiffness: 320, damping: 22 }}
                className="h-full rounded-2xl border border-line bg-surface/70 p-6 hover:border-accent/35 hover:bg-surface-2/70 transition-colors"
              >
                <span
                  className={`inline-flex items-center justify-center w-10 h-10 rounded-xl ${feature.chipBg} ${feature.tint}`}
                >
                  {feature.icon}
                </span>
                <h3 className="mt-5 text-[17px] font-medium">{feature.title}</h3>
                <p className="mt-2.5 text-[14px] text-ink-3 leading-relaxed">
                  {feature.body}
                </p>
              </motion.div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
