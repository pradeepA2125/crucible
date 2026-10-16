import {
  mapTeamWorkflow,
  TeamWorkflowSchema,
  parseWireTeamActivity,
  parseWireTeamPost,
  type BackendTaskClient,
  type SequencedStreamEvent,
  type TeamDetail,
  type TeamActivity,
  type TeamPost,
  type TeamWorkflow,
} from "@crucible/editor-client";

export const TERMINAL_TEAM_PHASES: ReadonlySet<string> = new Set(["DONE", "DISBANDED", "FAILED"]);

export function teamChannel(threadId: string, teamId: string): string {
  return `chat:${threadId}:team:${teamId}`;
}

export type TeamViewEvent =
  | { type: "team_post"; post: TeamPost }
  | { type: "team_activity"; activity: TeamActivity }
  | ({ type: "team_phase"; phase: string; round: number; pausedReason: string | null } & Partial<TeamWorkflow>);

export interface TeamViewSink {
  detail(teamId: string, detail: TeamDetail): void;
  event(teamId: string, event: TeamViewEvent): void;
}

type TeamClient = Pick<BackendTaskClient, "getTeam" | "streamChannel">;
type TeamState = Pick<TeamDetail, "phase" | "round"> & Partial<TeamWorkflow>;

interface OpenTeam {
  threadId: string;
  closed: boolean;
  abort: AbortController | null;
  generation: number;
  following: boolean;
  state?: TeamState;
  lastSeq: number;
  lastAseq: number;
}

/** DONE may still have communication activity. Blocked requests stay visible without
 * keeping a stream open until another state change explicitly rearms the view. */
export function shouldFollowTeam(state: TeamState): boolean {
  return !TERMINAL_TEAM_PHASES.has(state.phase)
    || (state.phase === "DONE" && (state.leadExchangeStatus === "running"
      || state.leadExchangeStatus === "pending"
      || (state.pendingLeadRequestSeq != null && state.leadExchangeStatus == null)));
}

function stateKey(state: TeamState): string {
  return JSON.stringify([state.runId ?? 1, state.historyRevision ?? 0, state.workflowRevision ?? 0, state.phase, state.round, state.resultSeq,
    state.pendingLeadRequestSeq, state.leadExchangeStatus, state.resumePhase]);
}

function olderState(incoming: TeamState, current?: TeamState): boolean {
  if (!current) return false;
  if ((incoming.historyRevision ?? 0) !== (current.historyRevision ?? 0)) {
    return (incoming.historyRevision ?? 0) < (current.historyRevision ?? 0);
  }
  const revision = incoming.workflowRevision ?? 0;
  const currentRevision = current.workflowRevision ?? 0;
  // A rewind restores an earlier run with a newer durable revision.
  if (revision > 0 && currentRevision > 0 && revision !== currentRevision) {
    return revision < currentRevision;
  }
  const run = incoming.runId ?? current.runId ?? 1;
  const currentRun = current.runId ?? 1;
  if (run !== currentRun) return run < currentRun;
  if (revision !== currentRevision) return revision < currentRevision;
  return incoming.round < current.round;
}

function rewoundState(incoming: TeamState, current?: TeamState): boolean {
  if (current && (incoming.historyRevision ?? 0) > (current.historyRevision ?? 0)) return true;
  return !!current && (incoming.workflowRevision ?? 0) > (current.workflowRevision ?? 0)
    && (current.workflowRevision ?? 0) > 0
    && ((incoming.runId ?? 1) < (current.runId ?? 1)
      || ((incoming.runId ?? 1) === (current.runId ?? 1) && incoming.round < current.round));
}

/** Follow only open boards. Dormant open entries retain their identity and cursors so
 * /live or a summary refresh can rearm them without continuously polling ended teams. */
export class TeamViewManager {
  private readonly views = new Map<string, OpenTeam>();

  constructor(
    private readonly client: () => TeamClient,
    private readonly sink: TeamViewSink,
    private readonly retryDelayMs = 1000,
  ) {}

  setOpen(threadId: string, teamIds: readonly string[]): void {
    const wanted = new Set(teamIds);
    for (const [id, view] of [...this.views]) {
      if (!wanted.has(id) || view.threadId !== threadId) this.close(id);
    }
    for (const id of wanted) {
      if (this.views.has(id)) continue;
      const view: OpenTeam = { threadId, closed: false, abort: null, generation: 0,
        following: false, lastSeq: 0, lastAseq: 0 };
      this.views.set(id, view);
      this.start(id, view);
    }
  }

  noteState(threadId: string, teamId: string, state: TeamState): void {
    const view = this.views.get(teamId);
    if (!view || view.closed || view.threadId !== threadId) return;
    if (olderState(state, view.state)) return;
    if (view.state && stateKey(view.state) === stateKey(state)) return;
    if (rewoundState(state, view.state)) view.lastSeq = view.lastAseq = 0;
    view.state = state;
    // Invalidate an in-flight terminal backfill/stream before starting the fresh one.
    view.generation++;
    view.abort?.abort();
    view.following = false;
    this.start(teamId, view);
  }

  closeAll(): void {
    for (const id of [...this.views.keys()]) this.close(id);
  }

  private close(teamId: string): void {
    const view = this.views.get(teamId);
    if (!view) return;
    view.closed = true;
    view.generation++;
    view.abort?.abort();
    this.views.delete(teamId);
  }

  private start(teamId: string, view: OpenTeam): void {
    if (view.following || view.closed) return;
    view.following = true;
    const generation = view.generation;
    void this.run(teamId, view, generation).finally(() => {
      if (generation === view.generation) view.following = false;
    });
  }

  private async run(teamId: string, view: OpenTeam, generation: number): Promise<void> {
    const current = () => !view.closed && view.generation === generation;
    while (current()) {
      let detail: TeamDetail;
      try {
        detail = await this.client().getTeam(view.threadId, teamId);
      } catch {
        if (current()) await delay(this.retryDelayMs);
        continue;
      }
      if (!current()) return;
      if (rewoundState(detail, view.state)) view.lastSeq = view.lastAseq = 0;
      // Posts and activity have independent clocks. Never let stale backfill erase a
      // streamed result or end a newer run, including a still-open reopened board.
      if (olderState(detail, view.state)
          || detail.lastSeq < view.lastSeq || detail.lastAseq < view.lastAseq) {
        await delay(this.retryDelayMs);
        continue;
      }
      view.lastSeq = detail.lastSeq;
      view.lastAseq = detail.lastAseq;
      view.state = detail;
      this.sink.detail(teamId, detail);
      if (!shouldFollowTeam(detail)) return;
      const abort = new AbortController();
      view.abort = abort;
      try {
        const stream = this.client().streamChannel(teamChannel(view.threadId, teamId), abort.signal);
        for await (const raw of stream) {
          if (!current()) return;
          const event = toViewEvent(raw);
          if (event === null) continue;
          if (event.type === "team_post") {
            if (event.post.seq <= view.lastSeq) continue;
            view.lastSeq = event.post.seq;
          } else if (event.type === "team_activity") {
            if (event.activity.aseq <= view.lastAseq) continue;
            view.lastAseq = event.activity.aseq;
          } else {
            if (olderState(event, view.state)) continue;
            // Backfill already represents this revision. Replaying an earlier DONE
            // must not close a newer consultation in the same run.
            if ((event.workflowRevision ?? 0) > 0
                && event.workflowRevision === view.state?.workflowRevision
                && (event.historyRevision ?? 0) === (view.state?.historyRevision ?? 0)
                && (event.runId ?? view.state?.runId) === view.state?.runId) continue;
            if (rewoundState(event, view.state)) {
              view.lastSeq = view.lastAseq = 0;
              view.state = { ...view.state, ...event };
              this.sink.event(teamId, event);
              break; // Re-backfill the restored board before following its new history.
            }
            view.state = { ...view.state, ...event };
          }
          this.sink.event(teamId, event);
          // Always get the final result/reply backfill after communication or lifecycle ends.
          if (event.type === "team_phase" && !shouldFollowTeam(view.state!)) break;
        }
      } catch {
        // Closed, rearmed, or disconnected: the current generation alone may backfill.
      } finally {
        if (view.abort === abort) view.abort = null;
      }
      if (current()) await delay(this.retryDelayMs);
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
    const workflow = "run_id" in event.payload || "mode" in event.payload
      || "workflow_revision" in event.payload || "history_revision" in event.payload
      ? TeamWorkflowSchema.parse(mapTeamWorkflow(event.payload)) : {};
    return { type: "team_phase", phase: event.payload.phase, round: event.payload.round,
      pausedReason: event.payload.paused_reason, ...workflow };
  }
  return null;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
