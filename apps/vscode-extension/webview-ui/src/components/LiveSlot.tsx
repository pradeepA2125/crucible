import { sig } from "../hooks/useAppState";
import type {
  GateAgentView, GateTeamView, LiveGateView, LivePlanView, LiveReviewView, LiveErrorView, LiveTodosView,
  LiveSessionsView, SessionTranscriptView,
} from "../types";
import { TodoCard } from "./messages/TodoCard";
import { SessionStrip } from "./messages/SessionStrip";
import { CommandGate } from "./messages/gates/CommandGate";
import { ScopeGate } from "./messages/gates/ScopeGate";
import { ValidationGate } from "./messages/gates/ValidationGate";
import { StepGate } from "./messages/gates/StepGate";
import { ModeGate } from "./messages/gates/ModeGate";
import { ClarifyGate } from "./messages/gates/ClarifyGate";
import { EditGate } from "./messages/gates/EditGate";
import { McpGate } from "./messages/gates/McpGate";
import { TeamPlanGate } from "./messages/gates/TeamPlanGate";
import { PlanCard } from "./messages/PlanCard";
import { ReviewCard } from "./messages/ReviewCard";
import { ErrorCard } from "./messages/ErrorCard";
import { AgentChip } from "./agents/AgentChip";

// ── GateDispatch ──────────────────────────────────────────────────────────────

interface GateDispatchProps {
  gateId: string;
  taskId: string;
  kind: LiveGateView["kind"];
  payload: Record<string, unknown>;
  agent: GateAgentView | null;
  team: GateTeamView | null;
}

/** Routes a live gate to its card; a sub-agent's gate gets an agent chip above it. */
function GateDispatch({ agent, team, ...card }: GateDispatchProps) {
  if (agent === null && team === null) return <GateCard {...card} />;
  return (
    <div className="flex flex-col gap-1">
      {agent !== null ? (
        <span className="self-start" title={`Raised by sub-agent ${agent.label} (${agent.name})`}>
          <AgentChip name={agent.name} label={agent.label} />
        </span>
      ) : team !== null && (
        <span className="self-start rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
          style={{ background: "var(--accent-bg)", color: "var(--color-accent-ink)" }}
          title={`Raised by team ${team.name}`}>
          team {team.name}
        </span>
      )}
      <GateCard {...card} />
    </div>
  );
}

function GateCard({ gateId, taskId, kind, payload }: Omit<GateDispatchProps, "agent" | "team">) {
  switch (kind) {
    case "command":
      return <CommandGate gateId={gateId} taskId={taskId} payload={payload} />;
    case "scope":
      return <ScopeGate taskId={taskId} payload={payload} />;
    case "validation":
      return <ValidationGate taskId={taskId} payload={payload} />;
    case "step":
      return <StepGate taskId={taskId} payload={payload} />;
    case "mode":
      return <ModeGate taskId={taskId} payload={payload} />;
    case "clarify":
      return <ClarifyGate taskId={taskId} payload={payload} />;
    case "edit":
      return <EditGate gateId={gateId} taskId={taskId} payload={payload} />;
    case "mcp_tool":
      return <McpGate gateId={gateId} taskId={taskId} payload={payload} />;
    case "team_plan":
      return <TeamPlanGate gateId={gateId} taskId={taskId} payload={payload} />;
  }
}

/** Content-addressed key: a new decision for a stable id (task gates are
 * "task:{id}:{kind}") must REMOUNT the card to discard the previous resolved state. */
function gateKey(gate: LiveGateView): string {
  const p = JSON.stringify(gate.payload);
  return `${gate.gateId}:${p.length.toString(36)}.${sig(p)}`;
}

// ── LiveSlot ──────────────────────────────────────────────────────────────────

interface Props {
  liveGates: LiveGateView[];
  livePlan: LivePlanView | null;
  liveReview: LiveReviewView | null;
  /** Already filtered for dismissal by the caller. */
  liveError: LiveErrorView | null;
  liveTodos?: LiveTodosView | null;
  liveSessions?: LiveSessionsView | null;
  sessionTranscripts?: Record<string, SessionTranscriptView | null>;
  onExpandSession?: (sessionId: string) => void;
  onDismissError: () => void;
}

/**
 * LiveSlot — the pinned interactive slot above the input area.
 *
 * Renders one card per pending gate, one plan card, one review card, and one error
 * card at a time. Key props are load-bearing: a second gate of the same kind
 * must REMOUNT to discard the previous card's resolved state. This fixed a real
 * bug class where an "Allow once" resolved card persisted across a new decision.
 *
 * Returns null when all four slots are empty.
 */
export function LiveSlot({
  liveGates, livePlan, liveReview, liveError, liveTodos,
  liveSessions, sessionTranscripts, onExpandSession, onDismissError,
}: Props) {
  const hasTodos = liveTodos != null && liveTodos.items.length > 0;
  const hasSessions = liveSessions != null && liveSessions.items.length > 0;
  const hasContent = liveGates.length > 0 || livePlan !== null || liveReview !== null
    || liveError !== null || hasTodos || hasSessions;
  if (!hasContent) return null;

  return (
    <div className="flex flex-col gap-2 px-3 py-2 flex-shrink-0">
      {hasSessions && (
        <SessionStrip
          items={liveSessions.items}
          transcripts={sessionTranscripts ?? {}}
          onExpand={onExpandSession ?? (() => {})}
        />
      )}

      {liveTodos != null && liveTodos.items.length > 0 && <TodoCard items={liveTodos.items} />}

      {liveGates.map((gate) => (
        // Key stability relies on consistent key insertion order: both SSE and /live
        // payloads pass through JSON.parse, so V8 preserves the backend serializer's order.
        <GateDispatch
          key={gateKey(gate)}
          gateId={gate.gateId}
          taskId={gate.taskId}
          kind={gate.kind}
          payload={gate.payload}
          agent={gate.agent ?? null}
          team={gate.team ?? null}
        />
      ))}

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
