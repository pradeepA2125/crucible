import { CardShell } from "../components/shared/CardShell";
import { BtnGhost } from "../components/shared/buttons";
import type { AppState } from "../types";
import type { ConversationChanges } from "./conversationChanges";
export function PinnedSummary({ state, changes, review }: { state: AppState; changes: ConversationChanges; review(): void }) {
  const current = state.historyState?.phase === "current";
  const recorded = state.messages.slice(-1000).flatMap(message => Array.isArray(message.metadata.tool_events) ? message.metadata.tool_events.slice(-200) : []);
  const tools = [...new Set([...recorded, ...(state.streaming?.toolEvents ?? [])].flatMap(event => typeof event?.tool === "string" ? [event.tool.slice(0,128)] : []))].slice(-8);
  return <aside className="desktop-pinned-summary" aria-label="Pinned conversation summary">
    <CardShell icon="list" title="Conversation summary"><div className="desktop-summary-body">
    <p>{!current ? "Saved history · live status reconnecting" : state.liveGates.length ? `${state.liveGates.length} decision${state.liveGates.length === 1 ? "" : "s"} waiting` : state.turnActive ? "Working" : "No active turn"}</p>
    {current && state.liveGates.length > 0 && <p>{[...new Set(state.liveGates.map(gate => gate.kind.replace(/_/g, " ")))].join(" · ")}</p>}
    <BtnGhost className="desktop-summary-review" onClick={review}>Conversation edits <span>{changes.files.length}{changes.omitted ? "+" : ""} files</span></BtnGhost>
    {changes.files.length > 0 && <small>Recorded edits · +{changes.files.reduce((sum,file) => sum+file.additions,0)} −{changes.files.reduce((sum,file) => sum+file.deletions,0)}</small>}
    {tools.length > 0 ? <><strong>Observed tools</strong><p>{tools.join(" · ")}</p></> : <p>No tool activity recorded in this view.</p>}
    </div></CardShell>
  </aside>;
}
