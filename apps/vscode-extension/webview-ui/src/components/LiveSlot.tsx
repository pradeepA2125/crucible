import { sig } from "../hooks/useAppState";
import type { LiveGateView, LivePlanView, LiveReviewView, LiveErrorView, LiveTodosView } from "../types";
import { TodoCard } from "./messages/TodoCard";
import { CommandGate } from "./messages/gates/CommandGate";
import { ScopeGate } from "./messages/gates/ScopeGate";
import { ValidationGate } from "./messages/gates/ValidationGate";
import { StepGate } from "./messages/gates/StepGate";
import { ModeGate } from "./messages/gates/ModeGate";
import { EditGate } from "./messages/gates/EditGate";
import { PlanCard } from "./messages/PlanCard";
import { ReviewCard } from "./messages/ReviewCard";
import { ErrorCard } from "./messages/ErrorCard";

// ── GateDispatch ──────────────────────────────────────────────────────────────

interface GateDispatchProps {
  taskId: string;
  kind: LiveGateView["kind"];
  payload: Record<string, unknown>;
}

/** Routes a live gate to the appropriate card component. */
function GateDispatch({ taskId, kind, payload }: GateDispatchProps) {
  switch (kind) {
    case "command":
      return <CommandGate taskId={taskId} payload={payload} />;
    case "scope":
      return <ScopeGate taskId={taskId} payload={payload} />;
    case "validation":
      return <ValidationGate taskId={taskId} payload={payload} />;
    case "step":
      return <StepGate taskId={taskId} payload={payload} />;
    case "mode":
      return <ModeGate taskId={taskId} payload={payload} />;
    case "edit":
      return <EditGate taskId={taskId} payload={payload} />;
  }
}

// ── LiveSlot ──────────────────────────────────────────────────────────────────

interface Props {
  liveGate: LiveGateView | null;
  livePlan: LivePlanView | null;
  liveReview: LiveReviewView | null;
  /** Already filtered for dismissal by the caller. */
  liveError: LiveErrorView | null;
  liveTodos?: LiveTodosView | null;
  onDismissError: () => void;
}

/**
 * LiveSlot — the pinned interactive slot above the input area.
 *
 * Renders at most one gate card, one plan card, one review card, and one error
 * card at a time. Key props are load-bearing: a second gate of the same kind
 * must REMOUNT to discard the previous card's resolved state. This fixed a real
 * bug class where an "Allow once" resolved card persisted across a new decision.
 *
 * Returns null when all four slots are empty.
 */
export function LiveSlot({ liveGate, livePlan, liveReview, liveError, liveTodos, onDismissError }: Props) {
  const hasTodos = liveTodos != null && liveTodos.items.length > 0;
  const hasContent = liveGate !== null || livePlan !== null || liveReview !== null
    || liveError !== null || hasTodos;
  if (!hasContent) return null;

  return (
    <div className="flex flex-col gap-2 px-3 py-2 flex-shrink-0">
      {liveTodos != null && liveTodos.items.length > 0 && <TodoCard items={liveTodos.items} />}

      {liveGate !== null && (
        // Key is content-addressed so that a new gate for the same task+kind
        // (e.g. a second command decision) remounts the card and discards the
        // previous resolved state. Key stability relies on consistent key insertion
        // order: both SSE and /live payloads pass through JSON.parse, so V8 preserves
        // the backend serializer's order deterministically.
        <GateDispatch
          key={(() => { const p = JSON.stringify(liveGate.payload); return `${liveGate.taskId}:${liveGate.kind}:${p.length.toString(36)}.${sig(p)}`; })()}
          taskId={liveGate.taskId}
          kind={liveGate.kind}
          payload={liveGate.payload}
        />
      )}

      {livePlan !== null && (
        <PlanCard
          key={`${livePlan.taskId}:${sig(livePlan.planMarkdown)}`}
          content={livePlan.planMarkdown}
          taskId={livePlan.taskId}
          // readOnly is intentionally omitted (defaults false) — this is the
          // interactive live instance, not a transcript read-only copy.
        />
      )}

      {liveReview !== null && (
        <ReviewCard
          key={liveReview.taskId}
          {...liveReview}
        />
      )}

      {liveError !== null && (
        <ErrorCard
          key={`${liveError.taskId}:${liveError.status}`}
          {...liveError}
          onDismiss={onDismissError}
        />
      )}
    </div>
  );
}
