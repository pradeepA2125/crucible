import { AgentTranscript, viewToken } from "./AgentTranscript";
import { useAgentsUi } from "./AgentsContext";
import { useFollowBottom } from "./useFollowBottom";

/** ▸ — the agent's transcript in a bounded box inside the thread. Scrolling it never
 * drags the thread (overscroll contained), and it follows the newest entry. */
export function InlineAgentBox({ agentId }: { agentId: string }) {
  const { views } = useAgentsUi();
  const { ref, onScroll } = useFollowBottom(viewToken(views[agentId]));
  return (
    <div ref={ref} onScroll={onScroll} data-testid={`agent-box-${agentId}`}
      className="mb-2.5 ml-4 mr-2.5 overflow-y-auto rounded-lg px-2.5 py-2"
      style={{
        height: "min(240px, 40vh)", overscrollBehavior: "contain",
        background: "var(--color-surface)", border: "1px solid var(--color-border)",
        borderLeft: "2px solid var(--color-accent)",
      }}>
      <AgentTranscript agentId={agentId} />
    </div>
  );
}
