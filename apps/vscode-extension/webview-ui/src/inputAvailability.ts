import type { AppState } from "./types";

// UX Rule 1: Input area disable precedence.
// These sets mirror the backend task status enum.

const GATE_STATUSES = new Set([
  "AWAITING_COMMAND_DECISION",
  "AWAITING_SCOPE_DECISION",
  "AWAITING_STEP_REVIEW",
  "AWAITING_VALIDATION_DECISION",
]);

const RUNNING_STATUSES = new Set([
  "QUEUED",
  "CONTEXT_READY",
  "PLANNED",
  "EXECUTING",
  "VALIDATING",
  "REPAIRING",
  "PROMOTING",
]);

// All task-execution statuses (running + gates + plan approval).
// Stop is only meaningful during a local streaming chat turn — never during
// task execution (the user cannot cancel server-side work from here).
const TASK_ACTIVE_STATUSES = new Set([
  ...RUNNING_STATUSES,
  ...GATE_STATUSES,
  "AWAITING_PLAN_APPROVAL",
]);

export interface InputAvailability {
  disabled: boolean;
  placeholder: string;
  showStop: boolean;
}

export function inputAvailability(
  state: Pick<AppState, "inputEnabled" | "liveStatus" | "workbar">,
): InputAvailability {
  const { inputEnabled, liveStatus, workbar } = state;

  // Precedence 1: a local chat turn is streaming.
  if (!inputEnabled) {
    // Stop only shown when the disable comes from a streaming chat turn, not
    // from task execution — the task being active overrides the chat-turn case.
    const showStop =
      liveStatus === null || !TASK_ACTIVE_STATUSES.has(liveStatus);
    return {
      disabled: true,
      placeholder: "Agent is working…",
      showStop,
    };
  }

  // Precedence 2: awaiting plan approval.
  if (liveStatus === "AWAITING_PLAN_APPROVAL") {
    return {
      disabled: true,
      placeholder: "Review the plan — Implement or Give feedback",
      showStop: false,
    };
  }

  // Precedence 3: gate (waiting for a card decision).
  if (liveStatus !== null && GATE_STATUSES.has(liveStatus)) {
    return {
      disabled: true,
      placeholder: "Waiting for your decision on the card above",
      showStop: false,
    };
  }

  // Precedence 4: task is actively running.
  if (liveStatus !== null && RUNNING_STATUSES.has(liveStatus)) {
    const { stepIndex, totalSteps } = workbar ?? {};
    const placeholder =
      stepIndex !== undefined &&
      stepIndex !== null &&
      totalSteps !== undefined &&
      totalSteps !== null
        ? `Task is running — step ${stepIndex} of ${totalSteps}…`
        : "Task is running…";
    return {
      disabled: true,
      placeholder,
      showStop: false,
    };
  }

  // Precedence 5 (default): enabled.
  return {
    disabled: false,
    placeholder: "Ask anything or describe a change…",
    showStop: false,
  };
}
