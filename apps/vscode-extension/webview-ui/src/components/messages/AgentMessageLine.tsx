import type { ChatMsg } from "../../types";
import { AgentLiveRow } from "../agents/AgentRosterCard";

/** A resume (spec §6): which agent got a follow-up, its first line, and that agent's live
 * row — the dispatch card holding it may be far up the thread. */
export function AgentMessageLine({ msg }: { msg: ChatMsg }) {
  const agentId = typeof msg.metadata?.agent_id === "string" ? msg.metadata.agent_id : null;
  const label = String(msg.metadata?.label ?? agentId ?? "agent");
  const firstLine = msg.content.split("\n")[0] ?? "";
  return (
    <div className="flex flex-col gap-1.5">
      <div className="px-2 text-xs text-text-2 truncate" title={msg.content}>
        ↳ to <span className="text-text">{label}</span>: {firstLine}
      </div>
      {agentId && <AgentLiveRow agentId={agentId} />}
    </div>
  );
}
