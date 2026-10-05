import { elapsedMs, formatElapsed, isTerminalAgent } from "./agents";
import type {
  AgentSummaryView, TeamCountsView, TeamDetailView, TeamEventView, TeamLatestView, TeamLiveView,
  TeamMemberView, TeamPostView, TeamRoundProgressView, TeamSummaryView, TeamViewState,
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
  return { posts: detail.posts, lastSeq: detail.lastSeq,
           activity: detail.activity ?? [], lastAseq: detail.lastAseq ?? 0 };
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
  return view;
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
      case "awaiting_peer": return { text: "⏳ waiting on a teammate", tone: "idle" };
      case "stopped": return { text: "■ stopped", tone: "idle" };
      case "failed": return { text: "✗ failed", tone: "bad" };
      default: return { text: `${last.status ?? "ended"} · ${ago}`, tone: "idle" };
    }
  }
  if (last?.kind === "capped") return { text: "⏸ wake limit reached this phase", tone: "wait" };
  if (status === "failed") return { text: "✗ failed", tone: "bad" };
  return { text: "💤 idle", tone: "idle" };
}

export function latestText(latest: TeamLatestView): string {
  if (latest.kind === "post") return `${latest.label} posted: ${latest.text}`;
  if (latest.event === "wrapped_up") return `${latest.label} wrapped up — ${latest.text || latest.status || ""}`.trim();
  return `team ${latest.text.toLowerCase()}`;
}

export function countsText(counts: TeamCountsView | undefined, budget: number): string {
  const parts: string[] = [];
  if (counts) {
    parts.push(`${counts.posts} post${counts.posts === 1 ? "" : "s"}`);
    for (const p of counts.proposals) {
      const tally = [p.agree ? `${p.agree} ✓` : "", p.object ? `${p.object} ✗` : "",
                     p.pending ? `${p.pending} pending` : ""].filter(Boolean).join(" ");
      parts.push(`${p.id} ${tally}`.trim());
    }
  }
  if (budget > 0) parts.push(`budget ${budget} requests`);
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
