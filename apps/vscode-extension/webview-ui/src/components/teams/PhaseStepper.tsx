import { isTerminalTeam } from "../../teams";

const STEPS = ["Kickoff", "Deliberating", "Implementing", "Review", "Done"];
const INDEX: Record<string, number> = {
  DELIBERATING: 1, AWAITING_APPROVAL: 1, DEADLOCKED: 1, PAUSED: 1,
  IMPLEMENTING: 2, REVIEWING: 3, DONE: 4,
};

/** The road the team travels (spec 2026-10-05 §6/§8). An ended team shows how it ended. */
export function PhaseStepper({ phase, round, maxRounds, mini = false }: {
  phase: string; round: number; maxRounds: number; mini?: boolean;
}) {
  const at = INDEX[phase] ?? 1;
  // In 5A a team reaches DONE only by adopting a plan; 5B restores the full road once
  // implementation exists.
  const ended = isTerminalTeam(phase);
  const label = (step: string, i: number) =>
    i === 1 && at === 1 && !ended
      ? (phase === "DEADLOCKED" ? `Deadlocked · round ${round} of ${maxRounds}` : `Deliberating · round ${round} of ${maxRounds}`)
      : step;
  return (
    <div className={`flex flex-wrap items-center gap-y-1 ${mini ? "text-[10.5px]" : "text-[11px]"}`} aria-label="Phase">
      {STEPS.map((step, i) => {
        const done = i < at || (ended && i <= 1);
        const now = i === at && !ended;
        if (ended && i > 1) return null;
        return (
          <span key={step} className="inline-flex items-center whitespace-nowrap">
            {i > 0 && <span className={mini ? "mx-1 h-px w-2.5" : "mx-1.5 h-px w-[18px]"} style={{ background: "var(--color-border-strong)" }} />}
            <span className="mr-1.5 h-2 w-2 rounded-full" style={{
              background: now ? "var(--color-accent)" : done ? "var(--color-green)" : "transparent",
              border: `1.5px solid ${now ? "var(--color-accent)" : done ? "var(--color-green)" : "var(--color-text-4)"}`,
              boxShadow: now ? "0 0 0 3px var(--accent-bg-2)" : undefined }} />
            <span style={{ color: now ? "var(--color-accent-ink)" : done ? "var(--color-text-2)" : "var(--color-text-4)",
                           fontWeight: now ? 600 : undefined }}>{label(step, i)}</span>
          </span>
        );
      })}
      {ended && (
        <span className="inline-flex items-center whitespace-nowrap">
          <span className={mini ? "mx-1 h-px w-2.5" : "mx-1.5 h-px w-[18px]"} style={{ background: "var(--color-border-strong)" }} />
          <span className="mr-1.5 h-2 w-2 rounded-full" style={{ background: phase === "FAILED" ? "var(--color-red)" : phase === "DONE" ? "var(--color-green)" : "var(--color-text-3)" }} />
          <span style={{ color: phase === "FAILED" ? "var(--color-red)" : phase === "DONE" ? "var(--color-green)" : "var(--color-text-2)", fontWeight: 600 }}>
            {phase === "FAILED" ? "Failed" : phase === "DONE" ? "Plan adopted" : "Disbanded"}
          </span>
        </span>
      )}
    </div>
  );
}
