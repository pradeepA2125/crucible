import {
  parseWireTeamActivity,
  parseWireTeamPost,
  type BackendTaskClient,
  type SequencedStreamEvent,
  type TeamDetail,
  type TeamActivity,
  type TeamPost,
} from "@crucible/editor-client";

// Phases a team never leaves (spec v2 §8.2): a final backfill, then no follow.
export const TERMINAL_TEAM_PHASES: ReadonlySet<string> = new Set(["DONE", "DISBANDED", "FAILED"]);

export function teamChannel(threadId: string, teamId: string): string {
  return `chat:${threadId}:team:${teamId}`;
}

export type TeamViewEvent =
  | { type: "team_post"; post: TeamPost }
  | { type: "team_activity"; activity: TeamActivity }
  | { type: "team_phase"; phase: string; round: number; pausedReason: string | null };

export interface TeamViewSink {
  detail(teamId: string, detail: TeamDetail): void;
  event(teamId: string, event: TeamViewEvent): void;
}

type TeamClient = Pick<BackendTaskClient, "getTeam" | "streamChannel">;

interface OpenTeam {
  threadId: string;
  closed: boolean;
  abort: AbortController | null;
}

/**
 * Live data for the teams the user has open (spec v2 §9), modelled on AgentViewManager:
 * backfill with getTeam, follow the team channel skipping seq <= lastSeq, re-backfill when
 * the channel idles out. A terminal phase (event or backfill) ends the follow after one
 * final backfill. Member tabs use AgentViewManager, not this.
 */
export class TeamViewManager {
  private readonly views = new Map<string, OpenTeam>();

  constructor(
    private readonly client: () => TeamClient,
    private readonly sink: TeamViewSink,
    private readonly retryDelayMs = 1000,
  ) {}

  /** The full set of teams open in the webview; anything not in it is closed. */
  setOpen(threadId: string, teamIds: readonly string[]): void {
    const wanted = new Set(teamIds);
    for (const [id, view] of [...this.views]) {
      if (!wanted.has(id) || view.threadId !== threadId) this.close(id);
    }
    for (const id of wanted) {
      if (this.views.has(id)) continue;
      const view: OpenTeam = { threadId, closed: false, abort: null };
      this.views.set(id, view);
      void this.run(id, view);
    }
  }

  closeAll(): void {
    for (const id of [...this.views.keys()]) this.close(id);
  }

  private close(teamId: string): void {
    const view = this.views.get(teamId);
    if (!view) return;
    view.closed = true;
    view.abort?.abort();
    this.views.delete(teamId);
  }

  private async run(teamId: string, view: OpenTeam): Promise<void> {
    while (!view.closed) {
      let detail: TeamDetail;
      try {
        detail = await this.client().getTeam(view.threadId, teamId);
      } catch {
        await delay(this.retryDelayMs);
        continue;
      }
      if (view.closed) return;
      this.sink.detail(teamId, detail);
      if (TERMINAL_TEAM_PHASES.has(detail.phase)) return;
      let lastSeq = detail.lastSeq;
      let lastAseq = detail.lastAseq;
      view.abort = new AbortController();
      try {
        const stream = this.client().streamChannel(
          teamChannel(view.threadId, teamId), view.abort.signal);
        for await (const raw of stream) {
          if (view.closed) return;
          const event = toViewEvent(raw);
          if (event === null) continue;
          if (event.type === "team_post") {
            if (event.post.seq <= lastSeq) continue;
            lastSeq = event.post.seq;
          } else if (event.type === "team_activity") {
            if (event.activity.aseq <= lastAseq) continue;
            lastAseq = event.activity.aseq;
          }
          this.sink.event(teamId, event);
          // The final backfill carries the closing system post and the ended state.
          if (event.type === "team_phase" && TERMINAL_TEAM_PHASES.has(event.phase)) break;
        }
      } catch {
        // Aborted (closed) or the connection dropped: re-backfill.
      } finally {
        view.abort = null;
      }
      // A channel that ends at once (idle timeout, a closed replay) must not spin: a
      // backfill → stream → backfill loop of resolved promises never yields to timers.
      if (!view.closed) await delay(this.retryDelayMs);
    }
  }
}

function toViewEvent(event: SequencedStreamEvent): TeamViewEvent | null {
  if (event.type === "team_post") {
    return { type: "team_post", post: parseWireTeamPost(event.payload.post) };
  }
  if (event.type === "team_activity") {
    return { type: "team_activity", activity: parseWireTeamActivity(event.payload.event) };
  }
  if (event.type === "team_phase") {
    return { type: "team_phase", phase: event.payload.phase, round: event.payload.round,
             pausedReason: event.payload.paused_reason };
  }
  return null;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
