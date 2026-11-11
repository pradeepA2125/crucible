import type { ChatMsg, ToolEventView } from "../types";
import { isVoiceMessage } from "../voiceMessage";

/** Unfinished tool calls in this turn's sealed messages, oldest first. A breadcrumb (e.g.
 *  "Command approved") or a voice exchange can seal the bubble while a command still runs:
 *  its pill then lives in a sealed message of this turn, and the running line must still
 *  name it. The scan stops at the request that started the turn. */
export function sealedRunningEvents(messages: ChatMsg[]): ToolEventView[] {
  const running: ToolEventView[] = [];
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user" && !isVoiceMessage(msg)) break;
    const events = (msg.metadata?.tool_events as ToolEventView[] | undefined) ?? [];
    running.unshift(...events.filter((e) => !e.done));
  }
  return running;
}
