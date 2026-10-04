import type { ChatMsg } from "../../types";
import { useAgentsUi } from "../agents/AgentsContext";

/** The marker a notice turn writes first (spec §5.3, §6): a compact bell line; clicking it
 * opens the agent that woke the main agent. */
export function NoticeLine({ msg }: { msg: ChatMsg }) {
  const { openWindow } = useAgentsUi();
  const target = msg.metadata?.target as { agent_id?: string } | undefined;
  const agentId = typeof target?.agent_id === "string" ? target.agent_id : null;
  const text = msg.content.replace(/^🔔\s*/, "");
  return (
    <div className="flex items-center gap-2 px-2 py-1 text-xs text-text-2">
      <span aria-hidden="true">🔔</span>
      {agentId ? (
        <button
          type="button"
          className="text-left underline-offset-2 hover:underline"
          style={{ color: "var(--color-accent)" }}
          onClick={() => openWindow(agentId, [agentId])}
        >
          {text}
        </button>
      ) : (
        <span>{text}</span>
      )}
    </div>
  );
}
