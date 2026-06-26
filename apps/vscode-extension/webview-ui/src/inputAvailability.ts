import type { AppState } from "./types";

// UX Rule 1: Input area disable precedence.
// These sets mirror the backend task status enum.

const GATE_STATUSES = new Set([
  "AWAITING_COMMAND_DECISION",
  "AWAITING_SCOPE_DECISION",
  "AWAITING_STEP_REVIEW",
  "AWAITING_VALIDATION_DECISION",
]);

// VALIDATED and READY_FOR_REVIEW are intentionally excluded: VALIDATED is a
// sub-second transition, and at READY_FOR_REVIEW the user may type freely while
// the ReviewCard sits in the live slot.
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
  // A local streaming chat turn can be stopped (posts stopTurn / SSE disconnect).
  showStop: boolean;
  // Tier B: a running task can be cooperatively aborted (posts abortTask {revert}). Distinct
  // from showStop — stopping a chat turn only disconnects the view; aborting halts the run.
  taskStop: boolean;
}

// Tier B: phases where a cooperative task abort is meaningful — the engine holds a live
// control channel through _execute_plan (EXECUTING/VALIDATING/REPAIRING) and checks it
// between steps and ToolLoop iterations. (Planning/PROMOTING have no control → /abort 409s.)
const ABORTABLE_STATUSES = new Set(["EXECUTING", "VALIDATING", "REPAIRING"]);

export function inputAvailability(
  state: Pick<AppState, "inputEnabled" | "liveStatus" | "workbar" | "liveGate" | "turnActive">,
): InputAvailability {
  const { inputEnabled, liveStatus, workbar, liveGate, turnActive } = state;
  const taskStop = liveStatus !== null && ABORTABLE_STATUSES.has(liveStatus);

  // ── Controller precedence (spec §5), first match wins, ahead of task rows ──
  // Row 1: per-edit gate — only the EditGate card is interactive.
  if (liveGate?.kind === "edit") {
    return {
      disabled: true,
      placeholder: "Waiting for your decision on the card above",
      showStop: false,
      taskStop,
    };
  }
  // Row 2: mode/clarify gate — the card (incl. its in-card field) is the input path.
  if (liveGate?.kind === "mode") {
    return {
      disabled: true,
      placeholder: "Choose how to proceed — or chat about it on the card",
      showStop: false,
      taskStop,
    };
  }
  if (liveGate?.kind === "clarify") {
    return {
      disabled: true,
      placeholder: "Answer on the card above",
      showStop: false,
      taskStop,
    };
  }
  // Row 3: a controller turn is running (no gate). The durable reload-window guard:
  // a fresh webview mounts inputEnabled=true while the detached turn still runs.
  // Stop is shown — a controller turn can be stopped (no task is active here).
  if (turnActive && (liveStatus === null || !TASK_ACTIVE_STATUSES.has(liveStatus))) {
    return {
      disabled: true,
      placeholder: "Agent is working…",
      showStop: true,
      taskStop,
    };
  }

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
      taskStop,
    };
  }

  // Precedence 2: awaiting plan approval.
  if (liveStatus === "AWAITING_PLAN_APPROVAL") {
    return {
      disabled: true,
      placeholder: "Review the plan — Implement or Give feedback",
      showStop: false,
      taskStop,
    };
  }

  // Precedence 3: gate (waiting for a card decision).
  if (liveStatus !== null && GATE_STATUSES.has(liveStatus)) {
    return {
      disabled: true,
      placeholder: "Waiting for your decision on the card above",
      showStop: false,
      taskStop,
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
      taskStop,
    };
  }

  // Precedence 5 (default): enabled.
  return {
    disabled: false,
    placeholder: "Ask anything or describe a change…",
    showStop: false,
    taskStop,
  };
}
