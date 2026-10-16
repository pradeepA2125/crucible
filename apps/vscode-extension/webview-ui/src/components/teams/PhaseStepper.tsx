import { isTerminalTeam, teamCompletionLabel } from "../../teams";
import type { TeamWorkflowView } from "../../types";

type Props = TeamWorkflowView & {
  phase: string; round: number; maxRounds: number; endReason?: string | null; mini?: boolean;
};

/** Mode expresses the requested path; phase expresses work actually happening. */
export function PhaseStepper(props: Props) {
  const { phase, round, maxRounds, endReason = null, mini = false, mode, resumePhase } = props;
  const path = mode === "implement"
    ? ["Kickoff", "Implementing", "Review", "Done"]
    : mode === "deliberate"
      ? ["Kickoff", "Deliberating", "Finalizing recommendation", "Done"]
      : mode === "deliberate_and_implement"
        ? ["Kickoff", "Deliberating", "Finalizing recommendation", "Implementing", "Review", "Done"]
        : ["Kickoff", "Deliberating", "Implementing", "Review", "Done"];
  const phaseStep: Record<string, string> = { DELIBERATING: "Deliberating", DEADLOCKED: "Deliberating",
    FINALIZING: "Finalizing recommendation", IMPLEMENTING: "Implementing", REVIEWING: "Review", DONE: "Done" };
  const activePhase = phase === "PAUSED" ? resumePhase ?? "DELIBERATING" : phase;
  const activeStep = activePhase === "AWAITING_APPROVAL" ? "Implementing" : phaseStep[activePhase] ?? "Deliberating";
  const at = Math.max(1, path.indexOf(activeStep));
  const planningDone = phase === "DONE" && (mode === "deliberate" || endReason === "deliberation_complete"
    || (!mode && endReason === "adopted")
    || (props.resultSummary?.runId === (props.runId ?? 1) && props.resultSummary.outcome !== "actionable"));
  const stopped = phase === "FAILED" || phase === "DISBANDED";
  const visible = planningDone ? path.filter((s) => s !== "Implementing" && s !== "Review") : path;
  const label = (step: string) => {
    if (step === "Done" && phase === "DONE") return teamCompletionLabel(props);
    if (step !== activeStep || isTerminalTeam(phase)) return step;
    if (phase === "PAUSED") return !mode && !resumePhase ? "Paused" : `Paused · ${step.toLowerCase()}`;
    if (phase === "AWAITING_APPROVAL") return "Awaiting implementation approval";
    if (phase === "DEADLOCKED") return `Deadlocked · round ${round} of ${maxRounds}`;
    if (step === "Deliberating") return `Deliberating · round ${round} of ${maxRounds}`;
    return step;
  };
  return <div className={`flex flex-wrap items-center gap-y-1 ${mini ? "text-[10.5px]" : "text-[11px]"}`} aria-label="Phase">
    {visible.filter((step) => !stopped || path.indexOf(step) <= at).map((step, i) => {
      const now = step === activeStep && !isTerminalTeam(phase);
      const done = phase === "DONE" || path.indexOf(step) < at;
      return <span key={step} className="inline-flex items-center whitespace-nowrap">
        {i > 0 && <span className={mini ? "mx-1 h-px w-2.5" : "mx-1.5 h-px w-[18px]"} style={{ background: "var(--color-border-strong)" }} />}
        <span className="mr-1.5 h-2 w-2 rounded-full" style={{ background: now ? "var(--color-accent)" : done ? "var(--color-green)" : "transparent",
          border: `1.5px solid ${now ? "var(--color-accent)" : done ? "var(--color-green)" : "var(--color-text-4)"}` }} />
        <span style={{ color: now ? "var(--color-accent-ink)" : done ? "var(--color-text-2)" : "var(--color-text-4)", fontWeight: now ? 600 : undefined }}>{label(step)}</span>
      </span>;
    })}
    {planningDone && mode === "deliberate_and_implement" && <span className="ml-2 text-text-3">Implementation and review skipped</span>}
    {stopped && <span className="ml-2 font-semibold" style={{ color: phase === "FAILED" ? "var(--color-red)" : "var(--color-text-3)" }}>{phase === "FAILED" ? "Failed" : "Disbanded"}</span>}
  </div>;
}
