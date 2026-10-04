import type { ChatMsg } from "../../types";

/** A resume (spec §6): which agent got a follow-up, and its first line. */
export function AgentMessageLine({ msg }: { msg: ChatMsg }) {
  const label = String(msg.metadata?.label ?? msg.metadata?.agent_id ?? "agent");
  const firstLine = msg.content.split("\n")[0] ?? "";
  return (
    <div className="px-2 py-1 text-xs text-text-2 truncate" title={msg.content}>
      ↳ to <span className="text-text">{label}</span>: {firstLine}
    </div>
  );
}
