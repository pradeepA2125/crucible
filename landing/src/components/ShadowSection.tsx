import { motion } from "framer-motion";
import { Em, Reveal, SectionHeading } from "./ui";

const EASE = [0.22, 1, 0.36, 1] as const;

const DIFF_LINES: Array<{ text: string; kind: "add" | "ctx" }> = [
  { text: "+ from agentd.api.ratelimit import TokenBucket", kind: "add" },
  { text: "+ bucket = TokenBucket(rate=20, burst=40)", kind: "add" },
  { text: "  async def create_task(req: TaskSubmission):", kind: "ctx" },
  { text: "+     if not bucket.take(req.client_id):", kind: "add" },
  { text: '+         raise HTTPException(429, "rate limited")', kind: "add" },
  { text: "      record = await store.create(req)", kind: "ctx" },
];

const GUARANTEES = [
  {
    title: "Nothing lands unseen",
    body: "Every change is made to a shadow copy of your repo. Accept promotes it to your workspace instantly and atomically; reject restores every file exactly as it was.",
  },
  {
    title: "Side effects ask first",
    body: "Shell commands, MCP tool calls, and doc writes each pause at an approval gate. Approve once, or remember the decision per workspace.",
  },
  {
    title: "It asks instead of guessing",
    body: "An ambiguous request doesn't get a lucky guess — it raises a clarify card with candidate answers and a free-text escape, then resumes exactly where it paused.",
  },
];

function WorkspaceCard() {
  return (
    <div className="surface-card p-5 w-full">
      <div className="flex items-center justify-between mb-4">
        <span className="font-mono text-xs text-ink-3">~/your-repo</span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-green/10 border border-green/25 px-2.5 py-1 font-mono text-[10px] tracking-wider text-green uppercase">
          <svg viewBox="0 0 12 12" className="w-2.5 h-2.5" fill="currentColor" aria-hidden>
            <path d="M6 1a2.5 2.5 0 0 0-2.5 2.5V5H3a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-.5V3.5A2.5 2.5 0 0 0 6 1Zm1.5 4h-3V3.5a1.5 1.5 0 1 1 3 0V5Z" />
          </svg>
          untouched
        </span>
      </div>
      <div className="space-y-1.5 font-mono text-[13px] text-ink-3">
        <p>├─ agentd/</p>
        <p className="pl-4">├─ api/routes.py</p>
        <p className="pl-4">└─ domain/models.py</p>
        <p>├─ tests/</p>
        <p>└─ pyproject.toml</p>
      </div>
    </div>
  );
}

function ShadowCard() {
  return (
    <div className="relative w-full rounded-[14px] border border-accent/35 bg-surface-2/70 backdrop-blur-sm p-5 shadow-[0_0_60px_-18px_rgba(167,139,250,0.45)]">
      <div className="flex items-center justify-between mb-4">
        <span className="font-mono text-xs text-accent-ink">
          .agentd/shadows/session-4f21
        </span>
        <span className="rounded-full bg-accent/12 border border-accent/30 px-2.5 py-1 font-mono text-[10px] tracking-wider text-accent-ink uppercase">
          shadow
        </span>
      </div>
      <div className="space-y-1 font-mono text-[12.5px] leading-relaxed">
        {DIFF_LINES.map((line, i) => (
          <motion.p
            key={i}
            initial={{ opacity: 0, x: -14 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.5, delay: 0.5 + i * 0.14, ease: EASE }}
            className={
              line.kind === "add"
                ? "text-green bg-green/6 rounded px-2 -mx-2"
                : "text-ink-4 px-2 -mx-2"
            }
          >
            {line.text}
          </motion.p>
        ))}
      </div>
    </div>
  );
}

export default function ShadowSection() {
  return (
    <section id="shadow" className="relative py-28 sm:py-36">
      <div className="mx-auto max-w-6xl px-5 sm:px-8">
        <SectionHeading index="01" eyebrow="the shadow workspace">
          Your repo is <Em>sacred ground.</Em> The agent never sets foot on it.
        </SectionHeading>

        <div className="mt-16 grid lg:grid-cols-2 gap-14 lg:gap-20 items-start">
          <div className="space-y-8">
            {GUARANTEES.map((g, i) => (
              <Reveal key={g.title} delay={i * 0.12}>
                <div className="flex gap-5">
                  <span className="font-mono text-sm text-accent mt-1 shrink-0">
                    0{i + 1}
                  </span>
                  <div>
                    <h3 className="text-lg font-medium">{g.title}</h3>
                    <p className="mt-2 text-[15px] text-ink-3 leading-relaxed">
                      {g.body}
                    </p>
                  </div>
                </div>
              </Reveal>
            ))}
          </div>

          <Reveal delay={0.15}>
            <div className="relative">
              <WorkspaceCard />

              {/* the promote beam between the two worlds */}
              <div className="relative h-14 mx-10 overflow-hidden" aria-hidden>
                <div className="absolute left-1/2 top-0 bottom-0 w-px bg-gradient-to-b from-accent/40 via-accent/15 to-accent/40" />
                <motion.div
                  initial={{ opacity: 0 }}
                  whileInView={{ opacity: 1 }}
                  viewport={{ once: true }}
                  transition={{ delay: 1.6 }}
                  className="absolute inset-x-0 top-1/2 -translate-y-1/2 flex items-center justify-center gap-2"
                >
                  <motion.span
                    animate={{ y: [3, -3, 3] }}
                    transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut" }}
                    className="text-accent text-lg leading-none"
                  >
                    ↑
                  </motion.span>
                  <span className="font-mono text-[10px] tracking-[0.25em] uppercase text-accent-ink bg-void px-2">
                    promote on approve
                  </span>
                  <motion.span
                    animate={{ y: [3, -3, 3] }}
                    transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut", delay: 0.4 }}
                    className="text-accent text-lg leading-none"
                  >
                    ↑
                  </motion.span>
                </motion.div>
              </div>

              <ShadowCard />
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
