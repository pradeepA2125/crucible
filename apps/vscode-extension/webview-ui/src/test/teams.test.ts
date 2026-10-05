import { describe, expect, it } from "vitest";
import {
  applyTeamEvent, countsText, isTerminalTeam, latestText, memberPhrase, mergeLiveTeam,
  roundProgressText, stanceOf, summaryWithEvent, viewFromTeamDetail, waitingOn,
} from "../teams";
import type { AgentSummaryView, TeamDetailView, TeamPostView, TeamSummaryView } from "../types";

const post = (seq: number, over: Partial<TeamPostView> = {}): TeamPostView => ({
  teamId: "team-1", seq, author: "alice", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], refId: null, round: 1, payload: {}, closed: null,
  createdAt: "2026-10-05T00:00:00Z", ...over,
});

const SUMMARY: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "alice", agentId: "agent-a", status: "running" },
            { label: "bob", agentId: "agent-b", status: "awaiting_peer" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
};

describe("team state", () => {
  it("merges live fields over a summary, and builds one when none is loaded", () => {
    const merged = mergeLiveTeam(SUMMARY, { teamId: "team-1", name: "auth", phase: "DELIBERATING",
      round: 2, maxRounds: 3, pausedReason: null,
      members: [{ label: "alice", agentId: "agent-a", status: "completed" }] });
    expect(merged).toMatchObject({ round: 2, goal: "Add login", usage: { budget: 160 } });
    expect(merged.members[0].status).toBe("completed");
    const fresh = mergeLiveTeam(undefined, { teamId: "team-2", name: "x", phase: "DELIBERATING",
      round: 1, maxRounds: 4, pausedReason: null, members: [] });
    expect(fresh).toMatchObject({ teamId: "team-2", goal: "", openProposals: [] });
  });

  it("appends posts in seq order and drops a repeat", () => {
    const detail: TeamDetailView = { ...SUMMARY, posts: [post(1), post(2)], lastSeq: 2,
                                     activity: [], lastAseq: 0 };
    let view = viewFromTeamDetail(detail);
    view = applyTeamEvent(view, { type: "team_post", post: post(2) });
    view = applyTeamEvent(view, { type: "team_post", post: post(3) });
    expect(view.posts.map((p) => p.seq)).toEqual([1, 2, 3]);
    expect(view.lastSeq).toBe(3);
  });

  it("a phase event updates the summary", () => {
    const next = summaryWithEvent(SUMMARY, { type: "team_phase", phase: "DISBANDED",
      round: 1, pausedReason: null });
    expect(next.phase).toBe("DISBANDED");
    expect(isTerminalTeam(next.phase)).toBe(true);
    expect(isTerminalTeam("DELIBERATING")).toBe(false);
  });

  it("waiting on names the running member and how long it has run", () => {
    const agents: Record<string, AgentSummaryView> = {
      "agent-a": { agentId: "agent-a", parentAgentId: null, depth: 1, name: "general-purpose",
        label: "alice", status: "running", now: "", toolCount: 0, filesChangedCount: 0,
        startedAt: "2026-10-05T00:00:00Z", endedAt: null, reportPreview: "",
        activationStartedAt: "2026-10-05T00:00:10Z", activationEndedAt: null },
    };
    expect(waitingOn(SUMMARY, agents, Date.parse("2026-10-05T00:00:40Z")))
      .toEqual({ label: "alice", ms: 30_000 });
    expect(waitingOn({ ...SUMMARY, members: [SUMMARY.members[1]] }, agents, 0)).toBeNull();
  });

  it("a member's latest stance on a proposal wins", () => {
    const posts = [post(1, { kind: "proposal", author: "main" }),
      post(2, { kind: "object", refId: "P1" }), post(3, { kind: "agree", refId: "P1" }),
      post(4, { kind: "agree", refId: "P1", author: "bob" })];
    expect(stanceOf(posts, 1, "alice")).toBe("agree");
    expect(stanceOf(posts, 1, "carol")).toBeNull();
  });
});

describe("team activity state", () => {
  const act = (aseq: number) => ({ teamId: "team-1", aseq, at: `2026-10-05T00:00:0${aseq}Z`,
    label: "alice", kind: "woke", activation: 1, causeSeq: 1, payload: {} });
  it("keeps activity in aseq order and drops repeats", () => {
    let view = viewFromTeamDetail({ ...SUMMARY, posts: [], lastSeq: 0,
      activity: [act(1), act(2)], lastAseq: 2 });
    view = applyTeamEvent(view, { type: "team_activity", activity: act(2) });
    view = applyTeamEvent(view, { type: "team_activity", activity: act(3) });
    expect(view.activity.map((e) => e.aseq)).toEqual([1, 2, 3]);
    expect(view.lastAseq).toBe(3);
  });
});

describe("card wording", () => {
  const NOW = Date.parse("2026-10-05T17:12:00Z");
  const member = (over: object = {}) => ({ label: "review", agentId: "a", status: "completed", last: null, ...over });
  const row = (over: object = {}) => ({ agentId: "a", parentAgentId: null, depth: 1, name: "explore",
    label: "review", status: "running", now: "read_file shop/cart.py", toolCount: 3, filesChangedCount: 0,
    startedAt: "2026-10-05T17:00:00Z", endedAt: null, reportPreview: "",
    activationStartedAt: "2026-10-05T17:11:28Z", activationEndedAt: null, ...over });
  it("working comes from the agent row, with elapsed time", () => {
    expect(memberPhrase(member(), row(), NOW)).toEqual(
      { text: "● working · read_file shop/cart.py · 32s", tone: "work" });
  });
  it("finished states come from the member's last event", () => {
    const at = "2026-10-05T17:10:00Z";
    const done = member({ last: { kind: "wrapped_up", at, causeSeq: null, by: null, status: "completed", activation: 1 } });
    expect(memberPhrase(done, row({ status: "completed" }), NOW)).toEqual({ text: "✓ reported · 2m00s ago", tone: "ok" });
    const waiting = member({ last: { kind: "wrapped_up", at, causeSeq: null, by: null, status: "awaiting_peer", activation: 1 } });
    expect(memberPhrase(waiting, row({ status: "awaiting_peer" }), NOW).text).toBe("⏳ waiting on a teammate");
    const failed = member({ last: { kind: "wrapped_up", at, causeSeq: null, by: null, status: "failed", activation: 1 } });
    expect(memberPhrase(failed, row({ status: "failed" }), NOW)).toEqual({ text: "✗ failed", tone: "bad" });
  });
  it("needs approval, and no history", () => {
    expect(memberPhrase(member(), row({ status: "waiting" }), NOW)).toEqual({ text: "⏸ needs your approval", tone: "wait" });
    expect(memberPhrase(member({ status: "awaiting_peer" }), undefined, NOW)).toEqual({ text: "💤 idle", tone: "idle" });
  });
  it("latest and counts", () => {
    expect(latestText({ kind: "post", label: "review", text: "@impl findings for P1", at: "x" })).toBe("review posted: @impl findings for P1");
    expect(latestText({ kind: "activity", label: "impl", text: "Agreed with P1", at: "x", event: "wrapped_up", status: "completed" }))
      .toBe("impl wrapped up — Agreed with P1");
    expect(countsText({ posts: 9, proposals: [{ id: "P1", agree: 2, object: 0, pending: 0 }] }, 60))
      .toBe("9 posts · P1 2 ✓ · budget 60 requests");
    expect(countsText({ posts: 3, proposals: [{ id: "P1", agree: 1, object: 1, pending: 1 }] }, 0))
      .toBe("3 posts · P1 1 ✓ 1 ✗ 1 pending");
  });
});

describe("round progress on the card", () => {
  it("counts reports and names whom it waits on", () => {
    const now = Date.parse("2026-10-06T10:01:00Z");
    const team = { ...SUMMARY, round: 2, members: [
      { label: "alice", agentId: "a", status: "completed" }, { label: "bob", agentId: "b", status: "running" }] };
    const agents = { b: { agentId: "b", parentAgentId: null, depth: 1, name: "gp", label: "bob",
      status: "running", now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null,
      reportPreview: "", activationStartedAt: "2026-10-06T10:00:18Z", activationEndedAt: null } };
    expect(roundProgressText({ round: 2, members: ["alice", "bob"], reported: ["alice"] }, team, agents, now))
      .toEqual({ text: "Round 2 · 1 of 2 reported · waiting on bob (42s)", pct: 50 });
    expect(roundProgressText(null, team, agents, now)).toBeNull();
  });
});
