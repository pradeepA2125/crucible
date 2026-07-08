import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Em, Reveal, SectionHeading } from "./ui";

interface Seg {
  t: string;
  c?: string;
}

interface Stage {
  id: string;
  step: string;
  label: string;
  blurb: string;
  file: string;
  status: string;
  statusClass: string;
  lines: Seg[][];
}

const KW = "text-accent-ink";
const STR = "text-sky";
const DIM = "text-ink-4";
const OK = "text-green";
const ADD = "text-green";
const HEAD = "text-ink font-medium";

const STAGES: Stage[] = [
  {
    id: "decide",
    step: "01",
    label: "Decide",
    blurb:
      "Each chat turn is one reactive loop: explore with real tools, then commit to exactly one action — answer, clarify, propose, or edit.",
    file: "controller · this turn",
    status: "exploring · deciding",
    statusClass: "text-sky bg-sky/10 border-sky/30",
    lines: [
      [
        { t: "you  ", c: DIM },
        { t: "add rate limiting to the tasks API", c: HEAD },
      ],
      [{ t: "" }],
      [
        { t: "→ search_code ", c: DIM },
        { t: '"create_task"', c: STR },
      ],
      [
        { t: "→ read_file ", c: DIM },
        { t: "agentd/api/routes.py", c: STR },
      ],
      [
        { t: "→ query_graph ", c: DIM },
        { t: "routes.py", c: STR },
        { t: " — 6 inbound callers", c: DIM },
      ],
      [{ t: "" }],
      [
        { t: "decide: ", c: KW },
        { t: "edit — wire a TokenBucket into the tasks route" },
      ],
      [{ t: "one decisive action per turn · no silent side quests", c: DIM }],
    ],
  },
  {
    id: "gate",
    step: "02",
    label: "Propose",
    blurb:
      "Ambitious asks raise a mode gate — a plan sketch with model-authored options. Unclear ones raise a clarify card. Nothing runs until you pick.",
    file: "mode gate · live card",
    status: "awaiting your pick",
    statusClass: "text-accent-ink bg-accent/10 border-accent/30",
    lines: [
      [{ t: "PLAN SKETCH", c: DIM }],
      [
        { t: "1. ", c: DIM },
        { t: "agentd/api/ratelimit.py", c: STR },
        { t: " — TokenBucket, per-client keys" },
      ],
      [
        { t: "2. ", c: DIM },
        { t: "agentd/api/routes.py", c: STR },
        { t: " — middleware, 429 + Retry-After" },
      ],
      [
        { t: "3. ", c: DIM },
        { t: "tests/test_ratelimit.py", c: STR },
        { t: " — burst · refill · isolation" },
      ],
      [{ t: "" }],
      [{ t: "▸ Edit now — recommended", c: "text-accent-ink font-medium" }],
      [{ t: "▸ Just explain the approach", c: DIM }],
      [{ t: "" }],
      [{ t: "zero bytes written while this card is open", c: "text-accent-ink" }],
    ],
  },
  {
    id: "edit",
    step: "03",
    label: "Edit in shadow",
    blurb:
      "Edits run in an isolated shadow session with a live todo ledger for multi-part work — finishing is hard-blocked until every item is done.",
    file: ".agentd shadow · session-4f21",
    status: "editing · shadow only",
    statusClass: "text-amber bg-amber/10 border-amber/30",
    lines: [
      [
        { t: "+ ", c: ADD },
        { t: "from", c: KW },
        { t: " agentd.api.ratelimit " },
        { t: "import", c: KW },
        { t: " TokenBucket" },
      ],
      [
        { t: "+     ", c: ADD },
        { t: "if not", c: KW },
        { t: " bucket.take(req.client_id):" },
      ],
      [
        { t: "+         ", c: ADD },
        { t: "raise", c: KW },
        { t: " HTTPException(" },
        { t: "429", c: STR },
        { t: ", " },
        { t: '"rate limited"', c: STR },
        { t: ")" },
      ],
      [{ t: "" }],
      [
        { t: "todos  ", c: DIM },
        { t: "✓ ratelimit.py  ✓ middleware  ", c: OK },
        { t: "○ tests", c: "text-amber" },
      ],
      [{ t: "finish blocked — 1 todo still open", c: "text-amber" }],
      [{ t: "" }],
      [{ t: "→ your working tree: untouched", c: DIM }],
    ],
  },
  {
    id: "promote",
    step: "04",
    label: "Promote",
    blurb:
      "The edit gate shows the full diff. Accept promotes instantly and atomically to your repo; reject restores every file. Your call, always.",
    file: "edit gate → workspace",
    status: "your call",
    statusClass: "text-green bg-green/10 border-green/30",
    lines: [
      [{ t: "✓ 3 files changed in the shadow", c: OK }],
      [
        { t: "    agentd/api/ratelimit.py   ", c: DIM },
        { t: "+64", c: ADD },
      ],
      [
        { t: "    agentd/api/routes.py      ", c: DIM },
        { t: "+9 −1", c: ADD },
      ],
      [
        { t: "    tests/test_ratelimit.py   ", c: DIM },
        { t: "+58", c: ADD },
      ],
      [{ t: "" }],
      [
        { t: "accept ", c: OK },
        { t: "→ instant, atomic promote", c: OK },
      ],
      [
        { t: "reject ", c: DIM },
        { t: "→ every file restored, like it never happened", c: DIM },
      ],
      [{ t: "" }],
      [
        { t: "Your repo was untouched until this exact moment.", c: "text-accent-ink" },
      ],
    ],
  },
];

const CYCLE_MS = 4600;

export default function Lifecycle() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const timer = setInterval(
      () => setActive((current) => (current + 1) % STAGES.length),
      CYCLE_MS,
    );
    return () => clearInterval(timer);
  }, [paused]);

  const stage = STAGES[active];

  return (
    <section id="lifecycle" className="relative py-28 sm:py-36 bg-panel/40 border-y hairline">
      <div className="mx-auto max-w-6xl px-5 sm:px-8">
        <SectionHeading index="02" eyebrow="the reactive loop">
          Watch a change <Em>earn</Em> its way into your repo.
        </SectionHeading>

        <Reveal delay={0.15} className="mt-16">
          <div
            className="grid lg:grid-cols-[300px_1fr] gap-6"
            onMouseEnter={() => setPaused(true)}
            onMouseLeave={() => setPaused(false)}
          >
            {/* stage rail */}
            <div className="flex lg:flex-col gap-2 overflow-x-auto lg:overflow-visible">
              {STAGES.map((s, i) => (
                <button
                  key={s.id}
                  onClick={() => setActive(i)}
                  className={`relative text-left rounded-xl border px-4 py-3.5 transition-all shrink-0 lg:shrink w-56 lg:w-auto cursor-pointer ${
                    i === active
                      ? "border-accent/40 bg-accent/8"
                      : "border-line bg-surface/60 hover:border-line-strong"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <span
                      className={`font-mono text-xs ${
                        i === active ? "text-accent" : "text-ink-4"
                      }`}
                    >
                      {s.step}
                    </span>
                    <span
                      className={`text-[15px] font-medium ${
                        i === active ? "text-ink" : "text-ink-3"
                      }`}
                    >
                      {s.label}
                    </span>
                  </div>
                  <p
                    className={`mt-1.5 text-[12.5px] leading-snug transition-colors ${
                      i === active ? "text-ink-3" : "text-ink-4 hidden lg:block"
                    }`}
                  >
                    {s.blurb}
                  </p>
                  {i === active && !paused && (
                    <motion.div
                      key={`bar-${active}`}
                      initial={{ scaleX: 0 }}
                      animate={{ scaleX: 1 }}
                      transition={{ duration: CYCLE_MS / 1000, ease: "linear" }}
                      className="absolute bottom-0 left-3 right-3 h-px bg-accent/60 origin-left"
                    />
                  )}
                </button>
              ))}
            </div>

            {/* editor pane */}
            <div className="surface-card overflow-hidden min-h-[380px] flex flex-col">
              <div className="accent-wash flex items-center justify-between border-b hairline px-5 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex gap-1.5" aria-hidden>
                    <span className="w-2.5 h-2.5 rounded-full bg-red/50" />
                    <span className="w-2.5 h-2.5 rounded-full bg-amber/50" />
                    <span className="w-2.5 h-2.5 rounded-full bg-green/50" />
                  </div>
                  <span className="font-mono text-xs text-ink-3">{stage.file}</span>
                </div>
                <span
                  className={`rounded-full border px-2.5 py-1 font-mono text-[10px] tracking-wider uppercase ${stage.statusClass}`}
                >
                  {stage.status}
                </span>
              </div>

              <AnimatePresence mode="wait">
                <motion.div
                  key={stage.id}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
                  className="flex-1 p-6 font-mono text-[13px] leading-[1.85]"
                >
                  {stage.lines.map((line, i) => (
                    <motion.p
                      key={i}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: 0.08 + i * 0.055 }}
                      className="whitespace-pre-wrap"
                    >
                      {line.map((seg, j) => (
                        <span key={j} className={seg.c ?? "text-ink-2"}>
                          {seg.t}
                        </span>
                      ))}
                      {line.length === 1 && line[0].t === "" ? " " : null}
                    </motion.p>
                  ))}
                  <motion.p
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.2 + stage.lines.length * 0.055 }}
                    className="caret"
                  />
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
