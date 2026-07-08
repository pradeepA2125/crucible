import { GITHUB_URL } from "../content";
import { Em, GitHubIcon, Reveal, SectionHeading } from "./ui";

const SERVICES = [
  {
    path: "apps/",
    lang: "TypeScript",
    langColor: "bg-sky",
    body: "VS Code extension, React webview UI, and a Zod-typed client — every API shape validated at the boundary.",
  },
  {
    path: "services/agentd/",
    lang: "Python",
    langColor: "bg-green",
    body: "The orchestration brain: the reactive controller, approval gates, shadow workspaces, memory, MCP, and skills.",
  },
  {
    path: "services/indexer/",
    lang: "Rust",
    langColor: "bg-amber",
    body: "Incremental symbol-graph indexing — tree-sitter parsing with LSP-resolved call and inheritance edges.",
  },
];

const WAYS_IN = [
  {
    title: "Read the design docs",
    body: "Every feature ships with a spec and an implementation plan in-repo. The architecture is documented because it was built document-first.",
  },
  {
    title: "Run it fully local",
    body: "Point it at Ollama and nothing — code, prompts, memory — ever leaves your machine. No account, no server, no telemetry decision to trust.",
  },
  {
    title: "Extend it your way",
    body: "Drop SKILL.md folders in your repo, connect MCP servers from a JSON file, add prompt files — the extension points are files, not plugins.",
  },
];

export default function OpenSource() {
  return (
    <section
      id="opensource"
      className="relative py-28 sm:py-36 bg-panel/40 border-y hairline"
    >
      <div className="mx-auto max-w-6xl px-5 sm:px-8">
        <SectionHeading index="05" eyebrow="open source">
          Built in the open, <Em>owned by no one.</Em>
        </SectionHeading>

        <Reveal delay={0.1}>
          <p className="mt-6 max-w-2xl text-lg text-ink-2 leading-relaxed">
            An editor that can modify your code should let you read{" "}
            <span className="text-ink">its</span> code. The whole system — UI,
            orchestration, indexer — is on GitHub, one polyglot monorepo.
          </p>
        </Reveal>

        <div className="mt-14 grid lg:grid-cols-[1.1fr_1fr] gap-6">
          {/* repo card */}
          <Reveal>
            <div className="surface-card p-7 h-full flex flex-col">
              <div className="flex items-center gap-3">
                <GitHubIcon className="w-5 h-5 text-ink-2" />
                <span className="font-mono text-[15px] text-ink">
                  pradeepA2125/<span className="text-accent-ink">crucible</span>
                </span>
              </div>
              <p className="mt-4 text-[14.5px] text-ink-3 leading-relaxed">
                Production-grade AI editor foundation — a reactive chat
                controller, shadow-workspace edits, gated tools, and durable
                memory, split across runtimes that each do what they're best
                at.
              </p>
              <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2">
                {SERVICES.map((s) => (
                  <span
                    key={s.lang}
                    className="flex items-center gap-2 font-mono text-xs text-ink-3"
                  >
                    <span className={`w-2.5 h-2.5 rounded-full ${s.langColor}`} />
                    {s.lang}
                  </span>
                ))}
              </div>
              <div className="mt-auto pt-7 flex flex-wrap gap-3">
                <a
                  href={GITHUB_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-xl bg-ink text-void px-5 py-2.5 text-sm font-medium hover:-translate-y-0.5 transition-transform"
                >
                  <GitHubIcon className="w-4 h-4" />
                  Star the repo
                </a>
                <a
                  href={`${GITHUB_URL}/fork`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-xl border border-line-strong px-5 py-2.5 text-sm text-ink-2 hover:border-accent/50 hover:text-ink transition-all"
                >
                  Fork it
                </a>
                <a
                  href={`${GITHUB_URL}/issues`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-xl border border-line-strong px-5 py-2.5 text-sm text-ink-2 hover:border-accent/50 hover:text-ink transition-all"
                >
                  Open issues
                </a>
              </div>
            </div>
          </Reveal>

          {/* architecture rows */}
          <div className="space-y-4">
            {SERVICES.map((service, i) => (
              <Reveal key={service.path} delay={0.08 + i * 0.1}>
                <div className="rounded-2xl border border-line bg-surface/70 p-5 hover:border-line-strong transition-colors">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-[13px] text-ink">
                      {service.path}
                    </span>
                    <span className="flex items-center gap-2 font-mono text-[11px] text-ink-4">
                      <span className={`w-2 h-2 rounded-full ${service.langColor}`} />
                      {service.lang}
                    </span>
                  </div>
                  <p className="mt-2 text-[13.5px] text-ink-3 leading-relaxed">
                    {service.body}
                  </p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>

        <div className="mt-14 grid sm:grid-cols-3 gap-5">
          {WAYS_IN.map((way, i) => (
            <Reveal key={way.title} delay={i * 0.1}>
              <div className="border-t-2 border-accent/40 pt-5">
                <h3 className="text-[15px] font-medium">{way.title}</h3>
                <p className="mt-2 text-[13.5px] text-ink-3 leading-relaxed">
                  {way.body}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
