import {
  parseWireChatMessage,
  type AgentDetail,
  type BackendTaskClient,
  type SequencedStreamEvent,
} from "@crucible/editor-client";

export const TERMINAL_AGENT_STATUSES: ReadonlySet<string> = new Set([
  "completed", "partial", "failed", "stopped",
]);

export function agentChannel(threadId: string, agentId: string): string {
  return `chat:${threadId}:agent:${agentId}`;
}

export interface AgentViewSink {
  detail(agentId: string, detail: AgentDetail): void;
  event(agentId: string, event: SequencedStreamEvent): void;
}

type AgentClient = Pick<BackendTaskClient, "getAgent" | "streamChannel">;

interface OpenView {
  threadId: string;
  closed: boolean;
  abort: AbortController | null;
}

/**
 * Live data for the sub-agents the user is looking at (spec §10): one subscription per
 * open agent. Backfill with getAgent, then follow the agent's channel, skipping events the
 * backfill already contains (seq <= lastSeq, spec §5.5). The channel ends on its idle
 * timeout while an agent waits at a gate, so a running agent is re-backfilled and
 * re-subscribed; a terminal one gets one final backfill (it carries the report) and stops.
 */
export class AgentViewManager {
  private readonly views = new Map<string, OpenView>();

  constructor(
    private readonly client: () => AgentClient,
    private readonly sink: AgentViewSink,
    private readonly retryDelayMs = 1000,
  ) {}

  /** The full set of agents open in the webview; anything not in it is closed. */
  setOpen(threadId: string, agentIds: readonly string[]): void {
    const wanted = new Set(agentIds);
    for (const [id, view] of [...this.views]) {
      if (!wanted.has(id) || view.threadId !== threadId) this.close(id);
    }
    for (const id of wanted) {
      if (this.views.has(id)) continue;
      const view: OpenView = { threadId, closed: false, abort: null };
      this.views.set(id, view);
      void this.run(id, view);
    }
  }

  /** A roster status: a terminal agent's stream is cut so its final backfill runs. */
  noteStatus(agentId: string, status: string): void {
    if (TERMINAL_AGENT_STATUSES.has(status)) this.views.get(agentId)?.abort?.abort();
  }

  closeAll(): void {
    for (const id of [...this.views.keys()]) this.close(id);
  }

  private close(agentId: string): void {
    const view = this.views.get(agentId);
    if (!view) return;
    view.closed = true;
    view.abort?.abort();
    this.views.delete(agentId);
  }

  private async run(agentId: string, view: OpenView): Promise<void> {
    while (!view.closed) {
      let detail: AgentDetail;
      try {
        detail = await this.client().getAgent(view.threadId, agentId);
      } catch {
        await delay(this.retryDelayMs);
        continue;
      }
      if (view.closed) return;
      this.sink.detail(agentId, detail);
      if (TERMINAL_AGENT_STATUSES.has(detail.status)) return;
      let lastSeq = detail.lastSeq;
      view.abort = new AbortController();
      try {
        const stream = this.client().streamChannel(
          agentChannel(view.threadId, agentId), view.abort.signal);
        for await (const event of stream) {
          if (view.closed) return;
          if (event.seq !== undefined) {
            if (event.seq <= lastSeq) continue;
            lastSeq = event.seq;
          }
          this.sink.event(agentId, normalize(event));
        }
      } catch {
        // Aborted (closed, or the agent ended) or the connection dropped: re-backfill.
      } finally {
        view.abort = null;
      }
    }
  }
}

/** A nested roster arrives as a wire dict; the webview renders contract ChatMessages. */
function normalize(event: SequencedStreamEvent): SequencedStreamEvent {
  if (event.type !== "agent_dispatch") return event;
  return { ...event, payload: { message: parseWireChatMessage(event.payload.message) } };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
