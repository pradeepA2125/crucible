import { useState, useEffect } from "react";
import type { WorkbarInfo } from "../types";

// Status-to-label map (tier 3 of the label precedence hierarchy).
const STATUS_LABELS: Record<string, string> = {
  QUEUED: "Queued…",
  CONTEXT_READY: "Planning — exploring the codebase…",
  PLANNED: "Generating execution plan…",
  EXECUTING: "Executing…",
  VALIDATING: "Running validation…",
  REPAIRING: "Repairing validation errors…",
  PROMOTING: "Applying changes…",
};

interface Props {
  workbar: WorkbarInfo | null;
  liveStatus: string | null;
  thinkingStatus: string | null;
  visible: boolean;
}

function formatElapsed(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * WorkBar — spinner + label + elapsed timer.
 *
 * Visible while the task is actively running (not waiting on user).
 * Stop affordance intentionally omitted — InputArea owns the Stop button.
 *
 * Label precedence (F14 three tiers):
 *   1. stepIndex + totalSteps → "Step {i} of {n} — {stepTitle}"
 *   2. workbar.phaseLabel
 *   3. STATUS_LABELS map keyed by liveStatus
 *   4. fallback: thinkingStatus ?? "Working…"
 */
export function WorkBar({ workbar, liveStatus, thinkingStatus, visible }: Props) {
  const [elapsed, setElapsed] = useState(0);

  // Reset timer when bar becomes visible; count while visible.
  useEffect(() => {
    if (!visible) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [visible]);

  if (!visible) return null;

  // Compute label (tier 1 → 2 → 3 → 4).
  let label: React.ReactNode;
  if (
    workbar?.stepIndex !== undefined &&
    workbar?.stepIndex !== null &&
    workbar?.totalSteps !== undefined &&
    workbar?.totalSteps !== null
  ) {
    label = (
      <>
        <span
          className="font-semibold"
          style={{ color: "var(--color-accent-ink)" }}
        >
          Step {workbar.stepIndex} of {workbar.totalSteps}
        </span>
        {workbar.stepTitle ? (
          <span style={{ color: "var(--color-text-2)" }}>
            {" — "}
            {workbar.stepTitle}
          </span>
        ) : null}
      </>
    );
  } else if (workbar?.phaseLabel) {
    label = (
      <span style={{ color: "var(--color-text-2)" }}>{workbar.phaseLabel}</span>
    );
  } else if (liveStatus && STATUS_LABELS[liveStatus]) {
    label = (
      <span style={{ color: "var(--color-text-2)" }}>
        {STATUS_LABELS[liveStatus]}
      </span>
    );
  } else {
    label = (
      <span style={{ color: "var(--color-text-2)" }}>
        {thinkingStatus ?? "Working…"}
      </span>
    );
  }

  return (
    <div
      className="relative flex items-center gap-2 px-3 py-2 flex-shrink-0"
      style={{
        borderTop: "1px solid var(--color-border)",
        background: "var(--color-surface)",
      }}
    >
      {/* Animated top hairline */}
      <span className="workbar-line" aria-hidden="true" />

      {/* Spinner */}
      <span
        className="flex-shrink-0 rounded-full border-2"
        style={{
          width: 9,
          height: 9,
          borderColor: "var(--color-accent-ink) var(--accent-bg) var(--accent-bg) var(--accent-bg)",
          animation: "spin 0.75s linear infinite",
        }}
        aria-hidden="true"
      />

      {/* Label — takes remaining space, truncated */}
      <span
        className="flex-1 min-w-0 truncate text-[11px]"
        aria-live="polite"
      >
        {label}
      </span>

      {/* Elapsed timer */}
      <span
        className="flex-shrink-0 font-mono tabular-nums"
        style={{ fontSize: "10px", color: "var(--color-text-4)" }}
        aria-label={`Elapsed ${formatElapsed(elapsed)}`}
      >
        {formatElapsed(elapsed)}
      </span>
    </div>
  );
}
