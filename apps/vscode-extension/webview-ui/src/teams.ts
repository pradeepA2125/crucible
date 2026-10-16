import { elapsedMs, formatElapsed, isTerminalAgent } from "./agents";
import type {
  AgentSummaryView, TeamCountsView, TeamDetailView, TeamEventView, TeamLatestView, TeamLiveView,
  TeamMemberView, TeamPostView, TeamRoundProgressView, TeamSummaryView, TeamViewState,
  ToolEventView,
} from "./types";

// Phases a team never leaves (spec v2 §8.2).
export const TERMINAL_TEAM_PHASES: ReadonlySet<string> = new Set(["DONE", "DISBANDED", "FAILED"]);

export function isTerminalTeam(phase: string): boolean {
  return TERMINAL_TEAM_PHASES.has(phase);
}

/** /live carries slow fields only: keep the loaded summary's goal, proposals and usage. */
export function mergeLiveTeam(prev: TeamSummaryView | undefined, live: TeamLiveView): TeamSummaryView {
  if (olderTeamState(live, prev)) return prev!;
  return {
    goal: "", openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "",
    ...prev,
    ...live,
  };
}

export function viewFromTeamDetail(detail: TeamDetailView): TeamViewState {
  return { posts: detail.posts, lastSeq: detail.lastSeq,
           activity: detail.activity ?? [], lastAseq: detail.lastAseq ?? 0,
           runId: detail.runId, round: detail.round, workflowRevision: detail.workflowRevision,
           historyRevision: detail.historyRevision };
}

/** A post or event the backfill already holds (a live broadcast racing it) is dropped. */
export function applyTeamEvent(view: TeamViewState, event: TeamEventView): TeamViewState {
  if (event.type === "team_post") {
    if (event.post.seq <= view.lastSeq) return view;
    return { ...view, posts: [...view.posts, event.post], lastSeq: event.post.seq };
  }
  if (event.type === "team_activity") {
    if (event.activity.aseq <= view.lastAseq) return view;
    const activity = [...view.activity, event.activity].sort((a, b) => a.aseq - b.aseq);
    return { ...view, activity, lastAseq: event.activity.aseq };
  }
  if (olderTeamState(event, view)) return view;
  const restored = isTeamRewind(event, view)
    ? { posts: [], lastSeq: 0, activity: [], lastAseq: 0 } : view;
  return { ...restored, runId: event.runId ?? view.runId, round: event.round,
    workflowRevision: event.workflowRevision ?? view.workflowRevision,
    historyRevision: event.historyRevision ?? view.historyRevision };
}

export function summaryWithEvent(team: TeamSummaryView, event: TeamEventView): TeamSummaryView {
  if (event.type !== "team_phase") return team;
  if (olderTeamState(event, team)) return team;
  const { type: _type, ...state } = event;
  return { ...team, ...state };
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

const STATE_WORD: Record<string, string> = {
  running: "working", queued: "working", waiting: "needs your approval",
  awaiting_peer: "waiting on a teammate", failed: "failed", stopped: "stopped",
};

/** A member's state in one or two words, from its agent status; "idle" for one that reported
 * and is resting. Shared by the window header and the member tab so they never disagree. */
export function memberStateWord(status: string): string {
  return STATE_WORD[status] ?? "idle";
}

export type PhraseTone = "work" | "ok" | "wait" | "bad" | "idle";

/** A member's one-line state on the transcript card (spec 2026-10-05 §8): live work from the
 * agent row, everything after from the member's last activity event. */
export function memberPhrase(
  member: TeamMemberView, row: AgentSummaryView | undefined, now: number,
): { text: string; tone: PhraseTone } {
  const status = row?.status ?? member.status;
  if (status === "waiting") return { text: "⏸ needs your approval", tone: "wait" };
  if (!isTerminalAgent(status)) {
    const ms = row ? elapsedMs(row, now) : null;
    const doing = row?.now ? ` · ${row.now}` : "";
    return { text: `● working${doing}${ms !== null ? ` · ${formatElapsed(ms)}` : ""}`, tone: "work" };
  }
  const last = member.last;
  if (last?.kind === "wrapped_up") {
    const ago = `${formatElapsed(Math.max(0, now - Date.parse(last.at)))} ago`;
    switch (last.status) {
      case "completed": return { text: `✓ reported · ${ago}`, tone: "ok" };
      case "partial": return { text: `◐ reported partial · ${ago}`, tone: "wait" };
      case "awaiting_peer": return {
        text: last.waitingOn?.length ? `⏳ waiting on ${last.waitingOn.join(", ")}` : "⏳ waiting on a teammate",
        tone: "idle" };
      case "stopped": return { text: "■ stopped", tone: "idle" };
      case "failed": return { text: "✗ failed", tone: "bad" };
      default: return { text: `${last.status ?? "ended"} · ${ago}`, tone: "idle" };
    }
  }
  if (last?.kind === "capped") return { text: "⏸ wake limit reached this phase", tone: "wait" };
  if (status === "failed") return { text: "✗ failed", tone: "bad" };
  return { text: "💤 idle", tone: "idle" };
}

const PHASE_WORDS: Record<string, string> = {
  FINALIZING: "is finalizing its recommendation", DELIBERATING: "is deliberating", AWAITING_APPROVAL: "is waiting for your approval",
  IMPLEMENTING: "is implementing", REVIEWING: "is reviewing", DEADLOCKED: "is deadlocked",
  PAUSED: "is paused", DONE: "is done", DISBANDED: "was disbanded", FAILED: "failed",
};

export function latestText(latest: TeamLatestView): string {
  if (latest.kind === "post") return `${latest.label} posted: ${latest.text}`;
  if (latest.event === "wrapped_up") return `${latest.label} wrapped up — ${latest.text || latest.status || ""}`.trim();
  return `team ${PHASE_WORDS[latest.text] ?? latest.text.toLowerCase()}`;
}

export function countsText(counts: TeamCountsView | undefined, budget: number, runRequests?: number): string {
  const parts: string[] = [];
  if (counts) {
    parts.push(`${counts.posts} post${counts.posts === 1 ? "" : "s"}`);
    for (const p of counts.proposals) {
      const tally = [p.agree ? `${p.agree} ✓` : "", p.object ? `${p.object} ✗` : "",
                     p.pending ? `${p.pending} pending` : ""].filter(Boolean).join(" ");
      parts.push(`${p.id} ${tally}`.trim());
    }
  }
  if (budget > 0) parts.push(runRequests !== undefined ? `${runRequests} of ${budget} run requests` : `budget ${budget} requests`);
  return parts.join(" · ");
}

/** The card's progress line (spec 2026-10-05 §9): the round's reports, and the member it
 * waits on with that member's active time, ticked by the caller's useNow. */
export function roundProgressText(
  progress: TeamRoundProgressView | null | undefined, team: TeamSummaryView,
  agents: Record<string, AgentSummaryView>, now: number,
): { text: string; pct: number } | null {
  if (!progress || progress.members.length === 0) return null;
  const done = progress.reported.length;
  let text = `Round ${progress.round} · ${done} of ${progress.members.length} reported`;
  const pending = progress.members.filter((label) => !progress.reported.includes(label));
  for (const label of pending) {
    const member = team.members.find((m) => m.label === label);
    const row = member ? agents[member.agentId] : undefined;
    if (!row || isTerminalAgent(row.status)) continue;
    const ms = elapsedMs(row, now);
    text += ` · waiting on ${label}${ms !== null ? ` (${formatElapsed(ms)})` : ""}`;
    break;
  }
  return { text, pct: Math.round((done / progress.members.length) * 100) };
}

/** A live team's phase in a few words, for when no round is running (the pinned strip). */
export function teamPhaseText(team: TeamSummaryView): string {
  if (hasTeamExchange(team)) {
    const state = team.leadExchangeStatus === "running" ? "lead replying"
      : team.leadExchangeStatus === "blocked" ? "lead reply blocked" : "lead reply pending";
    return `${teamCompletionLabel(team)} · ${state}`;
  }
  switch (team.phase) {
    case "FINALIZING": return "Finalizing recommendation";
    case "DONE": return teamCompletionLabel(team);
    case "DELIBERATING": return `Round ${team.round} of ${team.maxRounds}`;
    case "DEADLOCKED": return "Deadlocked — waiting on the main agent";
    case "AWAITING_APPROVAL": return "Waiting for your approval";
    case "IMPLEMENTING": return "Implementing";
    case "REVIEWING": return "Review";
    case "PAUSED": return team.pausedReason ? `Paused — ${team.pausedReason}` : "Paused";
    default: return team.phase.charAt(0) + team.phase.slice(1).toLowerCase();
  }
}

export const TEAM_TOOLS: ReadonlySet<string> = new Set([
  "create_team", "post_board", "team_status", "team_report", "adopt_proposal", "resume_team", "disband_team",
  "message_team_lead", "cancel_team_lead_request", "start_team_implementation", "set_team_mode"]);

/** The team a main-agent tool pill is about, if the thread knows it. */
export function teamForPill(
  event: ToolEventView, teams: Record<string, TeamSummaryView>,
): TeamSummaryView | null {
  if (!TEAM_TOOLS.has(event.tool)) return null;
  if (event.tool === "create_team") {
    const match = /"team_id":\s*"([^"]+)"/.exec(event.output ?? "");
    return match ? teams[match[1]] ?? null : null;
  }
  const ref = typeof event.args.team === "string" ? event.args.team : "";
  if (ref === "") return null;
  if (teams[ref]) return teams[ref];
  const named = Object.values(teams).filter((t) => t.name === ref)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return named[0] ?? null;
}

export const TEAM_MODE_LABELS = {
  deliberate: "Deliberate", deliberate_and_implement: "Deliberate and implement", implement: "Implement",
} as const;

export function hasTeamExchange(team: TeamSummaryView): boolean {
  return team.phase === "DONE" && (team.pendingLeadRequestSeq != null
    || ["pending", "running", "blocked"].includes(team.leadExchangeStatus ?? ""));
}

export function resultReadyLabel(kind?: string | null): string {
  const labels: Record<string, string> = { plan: "Plan ready", review: "Review ready",
    diagnosis: "Diagnosis ready", comparison: "Comparison ready", recommendation: "Recommendation ready" };
  return (kind && labels[kind]) || "Result ready";
}

export function teamCompletionLabel(team: Pick<TeamSummaryView, "mode" | "deliverable" | "endReason" | "resultSummary" | "runId">): string {
  const currentResult = team.resultSummary && team.resultSummary.runId === (team.runId ?? 1);
  if (team.mode === "deliberate" || team.endReason === "deliberation_complete"
      || (currentResult && team.resultSummary?.outcome !== "actionable")) {
    return resultReadyLabel(team.deliverable?.kind ?? team.resultSummary?.kind);
  }
  if (team.mode) return team.endReason === "unresolved objections"
    ? "Implementation reviewed · unresolved" : "Implementation reviewed";
  return team.endReason === "adopted" ? "Plan adopted" : "Done";
}

export function olderTeamState(
  incoming: { runId?: number; workflowRevision?: number; historyRevision?: number; round?: number },
  current?: { runId?: number; workflowRevision?: number; historyRevision?: number; round?: number },
): boolean {
  if (!current) return false;
  if ((incoming.historyRevision ?? 0) !== (current.historyRevision ?? 0)) {
    return (incoming.historyRevision ?? 0) < (current.historyRevision ?? 0);
  }
  const revision = incoming.workflowRevision ?? 0;
  const currentRevision = current.workflowRevision ?? 0;
  // Server-side rewind can restore a lower run/round at a newer revision.
  if (revision > 0 && currentRevision > 0 && revision !== currentRevision) {
    return revision < currentRevision;
  }
  const run = incoming.runId ?? 1;
  const currentRun = current.runId ?? 1;
  if (run !== currentRun) return run < currentRun;
  if (revision !== currentRevision) return revision < currentRevision;
  return (incoming.round ?? 0) < (current.round ?? 0);
}

/** Backfills may race either stream independently; retain all newer durable rows. */
export function mergeTeamDetail(view: TeamViewState | undefined, detail: TeamDetailView): TeamViewState {
  if (view && olderTeamState(detail, view)) return view;
  if (!view || isTeamRewind(detail, view)) return viewFromTeamDetail(detail);
  const posts = new Map(detail.posts.map((p) => [p.seq, p]));
  for (const p of view.posts) if (p.seq > detail.lastSeq) posts.set(p.seq, p);
  const activity = new Map(detail.activity.map((a) => [a.aseq, a]));
  for (const a of view.activity) if (a.aseq > detail.lastAseq) activity.set(a.aseq, a);
  return { posts: [...posts.values()].sort((a, b) => a.seq - b.seq),
    lastSeq: Math.max(view.lastSeq, detail.lastSeq), activity: [...activity.values()].sort((a, b) => a.aseq - b.aseq),
    lastAseq: Math.max(view.lastAseq, detail.lastAseq),
    runId: detail.runId, round: detail.round, workflowRevision: detail.workflowRevision,
    historyRevision: detail.historyRevision };
}

/** A newer server snapshot may intentionally discard a future run after rewind. */
export function isTeamRewind(
  incoming: { runId?: number; round?: number; workflowRevision?: number; historyRevision?: number },
  current?: { runId?: number; round?: number; workflowRevision?: number; historyRevision?: number },
): boolean {
  if (current && (incoming.historyRevision ?? 0) > (current.historyRevision ?? 0)) return true;
  return !!current && (incoming.workflowRevision ?? 0) > (current.workflowRevision ?? 0)
    && (current.workflowRevision ?? 0) > 0
    && ((incoming.runId ?? 1) < (current.runId ?? 1)
      || ((incoming.runId ?? 1) === (current.runId ?? 1) && (incoming.round ?? 0) < (current.round ?? 0)));
}
