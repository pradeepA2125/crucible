import type { AppState, LiveGateView } from "./types";

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
  state: Pick<AppState, "inputEnabled" | "liveStatus" | "workbar" | "liveGates" | "turnActive">
    & Partial<Pick<AppState, "turnKind">>,
): InputAvailability {
  const { inputEnabled, liveStatus, workbar, liveGates, turnActive } = state;
  const turnKind = state.turnKind ?? null;
  // Composer rules key on the MAIN agent's gates only (spec §6): a background agent's or a
  // team's card waits above without taking the composer away.
  const mainGates = liveGates.filter((g) => !g.agent && !g.team);
  const backgroundGates = liveGates.length - mainGates.length;
  const hasGate = (kind: LiveGateView["kind"]) => mainGates.some((g) => g.kind === kind);
  const taskStop = liveStatus !== null && ABORTABLE_STATUSES.has(liveStatus);

  // ── Controller precedence (spec §5), first match wins, ahead of task rows ──
  // Row 1: per-edit gate — only the EditGate card is interactive.
  if (hasGate("edit")) {
    return {
      disabled: true,
      placeholder: "Waiting for your decision on the card above",
      showStop: false,
      taskStop,
    };
  }
  // Row 2: mode/clarify gate — the card (incl. its in-card field) is the input path.
  if (hasGate("mode")) {
    return {
      disabled: true,
      placeholder: "Choose how to proceed — or chat about it on the card",
      showStop: false,
      taskStop,
    };
  }
  if (hasGate("clarify")) {
    return {
      disabled: true,
      placeholder: "Answer on the card above",
      showStop: false,
      taskStop,
    };
  }
  // Row 2b: a command or MCP approval is pending — a sub-agent's or the main agent's.
  // The card is the input path; Stop stays available because the turn is still running.
  // Every agent's card counts here (v1 Part E): the turn already holds the composer, and
  // this row only points the user at the card while keeping Stop.
  const anyGate = (kind: LiveGateView["kind"]) => liveGates.some((g) => g.kind === kind);
  if (turnActive && (anyGate("command") || anyGate("mcp_tool"))) {
    return {
      disabled: true,
      placeholder: "Answer the card above…",
      showStop: true,
      taskStop,
    };
  }
  // Row 3: a controller turn is running (no gate). The durable reload-window guard:
  // a fresh webview mounts inputEnabled=true while the detached turn still runs.
  // Stop is shown — a controller turn can be stopped (no task is active here).
  // A notice turn (spec §5.3): the user may keep typing; a message sent now is queued.
  if (turnActive && turnKind === "notice") {
    return {
      disabled: false,
      placeholder: "Agents reported — type to add to this turn",
      showStop: true,
      taskStop,
    };
  }
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

  // Background agents' cards wait above; the composer stays usable (spec §6).
  if (backgroundGates > 0) {
    const noun = backgroundGates === 1 ? "card needs" : "cards need";
    return {
      disabled: false,
      placeholder: `${backgroundGates} ${noun} your answer above`,
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
