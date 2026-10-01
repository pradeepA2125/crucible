import type { AgentDetailView, AgentEventView, AgentViewState, ChatMsg, ToolEventView } from "./types";

export const TERMINAL_AGENT_STATUSES: ReadonlySet<string> = new Set([
  "completed", "partial", "failed", "stopped",
]);

export function isTerminalAgent(status: string): boolean {
  return TERMINAL_AGENT_STATUSES.has(status);
}

/** A fresh view from a backfill: the persisted transcript is authoritative. */
export function viewFromDetail(detail: AgentDetailView): AgentViewState {
  return { detail, messages: detail.transcript, live: [], callIds: {}, nextId: 1 };
}

function rosterKey(m: ChatMsg): string | null {
  if (m.type !== "agent_dispatch") return null;
  const ids = m.metadata?.agent_ids;
  return Array.isArray(ids) ? ids.join(",") : null;
}

/** Appends a durable message, skipping a roster card that is already there: a live
 * broadcast and a backfill (or a reload replay) can both deliver the same one. */
export function appendDurable(messages: ChatMsg[], message: ChatMsg): ChatMsg[] {
  const key = rosterKey(message);
  if (key !== null && messages.some((m) => rosterKey(m) === key)) return messages;
  return [...messages, message];
}

function seal(view: AgentViewState, at: string): AgentViewState {
  if (view.live.length === 0) return view;
  const pills: ChatMsg = {
    role: "agent", content: "", type: "text", timestamp: at, metadata: { tool_events: view.live },
  };
  return { ...view, messages: [...view.messages, pills], live: [], callIds: {} };
}

function text(at: string, content: string, flag: "progress" | "breadcrumb"): ChatMsg {
  return { role: "agent", content, type: "text", timestamp: at, metadata: { [flag]: true } };
}

/** Folds one child-channel event into an open view (spec §10). Events the wireframe
 * does not show (thinking, token counts) are ignored; the final backfill is
 * authoritative anyway. */
export function applyAgentEvent(view: AgentViewState, event: AgentEventView, at: string): AgentViewState {
  const p = event.payload;
  switch (event.type) {
    case "tool_call": {
      const id = view.nextId;
      const pill: ToolEventView = {
        id,
        tool: String(p.tool ?? ""),
        args: (p.args as Record<string, unknown> | undefined) ?? {},
        thought: typeof p.thought === "string" ? p.thought : undefined,
        source: "execution",
        done: false,
      };
      const callIds = typeof p.call_index === "number"
        ? { ...view.callIds, [p.call_index]: id }
        : view.callIds;
      return { ...view, live: [...view.live, pill], callIds, nextId: id + 1 };
    }
    case "tool_result": {
      const byIndex = typeof p.call_index === "number" ? view.callIds[p.call_index] : undefined;
      // No call_index: the newest unfinished pill is the one that returned.
      const id = byIndex ?? [...view.live].reverse().find((t) => !t.done)?.id;
      if (id === undefined) return view;
      return {
        ...view,
        live: view.live.map((t) => (t.id === id
          ? { ...t, output: String(p.output ?? ""), isError: p.is_error === true, done: true }
          : t)),
      };
    }
    case "chat_progress": {
      const v = seal(view, at);
      return { ...v, messages: [...v.messages, text(at, String(p.note ?? ""), "progress")] };
    }
    case "chat_breadcrumb": {
      const v = seal(view, at);
      return { ...v, messages: [...v.messages, text(at, String(p.text ?? ""), "breadcrumb")] };
    }
    case "diff_ready": {
      const v = seal(view, at);
      const card: ChatMsg = {
        role: "agent", content: "", type: "diff_card", timestamp: at,
        metadata: { diff_entries: p.diff_entries ?? [], resolved: p.resolved },
      };
      return { ...v, messages: [...v.messages, card] };
    }
    case "agent_dispatch": {
      const message = p.message as ChatMsg | undefined;
      if (!message) return view;
      const v = seal(view, at);
      return { ...v, messages: appendDurable(v.messages, message) };
    }
    default:
      return view;
  }
}

export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
}

/** Milliseconds an agent has run (or ran), or null before it started. */
export function elapsedMs(agent: { startedAt: string | null; endedAt: string | null }, now: number): number | null {
  if (!agent.startedAt) return null;
  const end = agent.endedAt ? Date.parse(agent.endedAt) : now;
  return end - Date.parse(agent.startedAt);
}
