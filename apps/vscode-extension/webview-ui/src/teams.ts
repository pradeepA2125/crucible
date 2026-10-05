import type {
  AgentSummaryView, TeamDetailView, TeamEventView, TeamLiveView, TeamPostView, TeamSummaryView,
  TeamViewState,
} from "./types";

// Phases a team never leaves (spec v2 §8.2).
export const TERMINAL_TEAM_PHASES: ReadonlySet<string> = new Set(["DONE", "DISBANDED", "FAILED"]);

export function isTerminalTeam(phase: string): boolean {
  return TERMINAL_TEAM_PHASES.has(phase);
}

/** /live carries slow fields only: keep the loaded summary's goal, proposals and usage. */
export function mergeLiveTeam(prev: TeamSummaryView | undefined, live: TeamLiveView): TeamSummaryView {
  return {
    goal: "", openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "",
    ...prev,
    ...live,
  };
}

export function viewFromTeamDetail(detail: TeamDetailView): TeamViewState {
  return { posts: detail.posts, lastSeq: detail.lastSeq };
}

/** A post the backfill already holds (a live broadcast racing the backfill) is dropped. */
export function applyTeamEvent(view: TeamViewState, event: TeamEventView): TeamViewState {
  if (event.type !== "team_post" || event.post.seq <= view.lastSeq) return view;
  return { posts: [...view.posts, event.post], lastSeq: event.post.seq };
}

export function summaryWithEvent(team: TeamSummaryView, event: TeamEventView): TeamSummaryView {
  if (event.type !== "team_phase") return team;
  return { ...team, phase: event.phase, round: event.round, pausedReason: event.pausedReason };
}

/** The running member the team waits on, and for how long (spec §9) — the seconds come
 * from the member's activation start, ticked by the caller's useNow. */
export function waitingOn(
  team: TeamSummaryView, agents: Record<string, AgentSummaryView>, now: number,
): { label: string; ms: number } | null {
  for (const member of team.members) {
    const row = agents[member.agentId];
    const status = row?.status ?? member.status;
    if (status !== "running" && status !== "queued") continue;
    const started = row?.activationStartedAt ?? row?.startedAt;
    return { label: member.label, ms: started ? Math.max(0, now - Date.parse(started)) : 0 };
  }
  return null;
}

/** A member's current stance on proposal P<seq>: its latest agree/object wins. */
export function stanceOf(posts: TeamPostView[], proposalSeq: number, label: string): "agree" | "object" | null {
  let stance: "agree" | "object" | null = null;
  for (const p of posts) {
    if (p.author !== label || p.refId !== `P${proposalSeq}`) continue;
    if (p.kind === "agree" || p.kind === "object") stance = p.kind;
  }
  return stance;
}
