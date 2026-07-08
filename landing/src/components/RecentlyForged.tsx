import { Em, Reveal, SectionHeading } from "./ui";

interface Entry {
  date: string;
  title: string;
  body: string;
}

const ENTRIES: Entry[] = [
  {
    date: "jul '26",
    title: "Composer intelligence",
    body: "@-mention any file — its content rides only that turn, so context never bloats — plus one / dropdown merging prompt files and skills, fully keyboard-driven.",
  },
  {
    date: "jul '26",
    title: "One-command install",
    body: "A curl one-liner installs the extension; the setup wizard provisions the whole runtime — backend, indexer, ripgrep, language servers — supervised per workspace.",
  },
  {
    date: "jul '26",
    title: "MCP client",
    body: "Connect any MCP tool server, stdio or HTTP, from .crucible/mcp.json or the settings panel. Every call pauses at an approval card — remembered per workspace if you say so.",
  },
  {
    date: "jul '26",
    title: "write_doc + web search",
    body: "Gated doc and data writes straight from chat — diff preview, per-write approval — and a bundled web-search MCP server for web_search / web_fetch out of the box.",
  },
  {
    date: "jun '26",
    title: "Agent Skills",
    body: "Teach the editor with SKILL.md folders in your repo. The agent loads one only when the task calls for it — or force one with /skill.",
  },
  {
    date: "jun '26",
    title: "AGENTS.md instructions",
    body: "Drop an AGENTS.md in your repo and every turn honors it. Edit it mid-session — the very next turn picks it up, no restart.",
  },
  {
    date: "jun '26",
    title: "Prompt files",
    body: "Reusable .crucible/prompts snippets that expand inline with /name — $ARGUMENTS and positional placeholders, reviewed in the composer before anything sends.",
  },
];

export default function RecentlyForged() {
  return (
    <section id="fresh" className="relative py-28 sm:py-36">
      <div className="mx-auto max-w-6xl px-5 sm:px-8">
        <SectionHeading index="04" eyebrow="fresh from the forge">
          Shipping in the open, <Em>weekly.</Em>
        </SectionHeading>

        {/* featured: Axon, freshly merged */}
        <Reveal delay={0.1}>
          <div className="mt-14 relative overflow-hidden rounded-2xl border border-accent/35 bg-surface/70 p-7 sm:p-9 shadow-[0_0_70px_-28px_color-mix(in_srgb,var(--color-accent)_65%,transparent)]">
            <div
              className="absolute inset-0 pointer-events-none opacity-60 accent-wash"
              aria-hidden
            />
            <div className="relative flex flex-col sm:flex-row sm:items-start gap-5 sm:gap-10">
              <div className="shrink-0">
                <span className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-3 py-1 font-mono text-[10px] tracking-[0.22em] uppercase text-accent-ink">
                  <span className="w-1.5 h-1.5 rounded-full bg-green animate-pulse" />
                  just merged
                </span>
              </div>
              <div>
                <h3 className="text-2xl font-medium tracking-tight">
                  Axon — <Em>the dependency space</Em>
                </h3>
                <p className="mt-3 max-w-2xl text-[15px] text-ink-3 leading-relaxed">
                  Your repo as a navigable galaxy inside the editor: packages as
                  nebulae, files as stars weighted by coupling, energy riding the
                  import beams. Search-warp to any file or symbol, ride a beam
                  across the codebase, open a file straight from its star.
                </p>
                <p className="mt-3 font-mono text-xs text-accent-ink">
                  ↑ the hero up top speaks its design language
                </p>
              </div>
            </div>
          </div>
        </Reveal>

        {/* the rest of the changelog */}
        <div className="mt-10 grid sm:grid-cols-2 gap-x-12">
          {ENTRIES.map((entry, i) => (
            <Reveal key={entry.title} delay={(i % 2) * 0.08}>
              <div className="group border-t hairline py-6 pr-2">
                <div className="flex items-baseline gap-4">
                  <span className="font-mono text-[10.5px] tracking-[0.18em] uppercase text-ink-4 shrink-0 w-14">
                    {entry.date}
                  </span>
                  <div>
                    <h3 className="text-[15.5px] font-medium group-hover:text-accent-ink transition-colors">
                      {entry.title}
                    </h3>
                    <p className="mt-1.5 text-[13.5px] text-ink-3 leading-relaxed">
                      {entry.body}
                    </p>
                  </div>
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
