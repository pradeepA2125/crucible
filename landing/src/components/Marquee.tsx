const TERMS = [
  "shadow workspace",
  "mode gate",
  "clarify gate",
  "todo ledger",
  "instant promote",
  "axon dependency space",
  "symbol graph",
  "memory harness",
  "mcp tools",
  "agent skills",
  "agents.md",
  "prompt files",
  "web search",
  "gated commands",
  "local models",
];

export default function Marquee() {
  const row = TERMS.map((term, i) => (
    <span key={i} className="flex items-center gap-8 shrink-0">
      <span className="font-mono text-[13px] tracking-[0.3em] uppercase text-ink-4 whitespace-nowrap">
        {term}
      </span>
      <span className="text-accent/50 text-xs">✦</span>
    </span>
  ));

  return (
    <div className="relative border-y hairline bg-panel/40 py-5 overflow-hidden">
      <div className="marquee-track flex items-center gap-8 w-max">
        {row}
        {row}
      </div>
      <div className="absolute inset-y-0 left-0 w-32 bg-gradient-to-r from-void to-transparent pointer-events-none" />
      <div className="absolute inset-y-0 right-0 w-32 bg-gradient-to-l from-void to-transparent pointer-events-none" />
    </div>
  );
}
