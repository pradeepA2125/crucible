import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient, parseWireTeamPost } from "../src/client/http-backend-client";
import { PendingGateSchema } from "../src/contracts/task-contracts";

function respond(body: unknown) {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => body });
}

const POST = {
  team_id: "team-1", seq: 3, author: "alice", kind: "object", recipient: null,
  text: "misses a caller", mentions: ["bob"], ref_id: "P1", round: 1,
  payload: { evidence: { files: ["api/a.py"], line: 4 } }, closed: null,
  created_at: "2026-10-05T00:00:00Z",
};
const SUMMARY = {
  team_id: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  max_rounds: 3, paused_reason: null,
  members: [{ label: "alice", agent_id: "agent-a", status: "running" }],
  open_proposals: [{ id: "P1", author: "main", text: "plan", stances: { alice: "object" } }],
  usage: { requests: 0, budget: 160 }, created_at: "2026-10-05T00:00:00Z",
};

describe("team contracts", () => {
  it("lists, gets and disbands teams with camelCase mapping", async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ teams: [SUMMARY] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        ...SUMMARY, posts: [POST], last_seq: 3 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        team_id: "team-1", phase: "DISBANDED" }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const [team] = await c.listTeams("t");
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/chat/threads/t/teams");
    expect(team).toMatchObject({ teamId: "team-1", maxRounds: 3, pausedReason: null,
      members: [{ label: "alice", agentId: "agent-a", status: "running" }],
      openProposals: [{ id: "P1", stances: { alice: "object" } }] });
    const detail = await c.getTeam("t", "team-1");
    expect(detail.lastSeq).toBe(3);
    expect(detail.posts[0]).toMatchObject({ refId: "P1", mentions: ["bob"], recipient: null,
      payload: { evidence: { files: ["api/a.py"], line: 4 } } });
    expect(await c.disbandTeam("t", "team-1")).toEqual({ phase: "DISBANDED" });
    expect(fetchFn.mock.calls[2][0]).toBe("http://x/v1/chat/threads/t/teams/team-1/disband");
    expect(fetchFn.mock.calls[2][1]).toMatchObject({ method: "POST" });
  });

  it("parses a wire post and maps /live teams and the config flag", async () => {
    expect(parseWireTeamPost(POST)).toMatchObject({ teamId: "team-1", seq: 3, refId: "P1" });
    const live = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, pending_gates: [], plan: null, turn_active: false,
      teams: [{ team_id: "team-1", name: "auth", phase: "DELIBERATING", round: 1,
                max_rounds: 3, paused_reason: null,
                members: [{ label: "alice", agent_id: "agent-a", status: "running" }] }],
    }) });
    const state = await live.getThreadLiveState("t");
    expect(state.teams?.[0]).toMatchObject({ teamId: "team-1", name: "auth", phase: "DELIBERATING",
      round: 1, maxRounds: 3, pausedReason: null,
      members: [{ label: "alice", agentId: "agent-a", status: "running" }] });
    const none = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, pending_gates: [], plan: null }) });
    expect((await none.getThreadLiveState("t")).teams).toBeNull();
    const cfg = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      task_subsystem_enabled: false, chat_controller_enabled: true, memory_enabled: false,
      skills_enabled: false, mcp_enabled: false, subagents_enabled: true,
      teams_enabled: true, provider: null }) });
    expect((await cfg.getConfig()).teamsEnabled).toBe(true);
  });
});

describe("team plan gate and implementation fields", () => {
  it("parses a team_plan gate with its team", async () => {
    const live = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, plan: null, turn_active: false,
      pending_gates: [{ gate_id: "g1", kind: "team_plan", agent: null,
                        team: { id: "team-1", name: "auth" },
                        payload: { proposal_id: "P1", text: "plan", assignments: [] } }],
    }) });
    const state = await live.getThreadLiveState("t");
    expect(state.pendingGates[0]).toMatchObject({
      gateId: "g1", kind: "team_plan", agent: null, team: { id: "team-1", name: "auth" } });
    expect(PendingGateSchema.parse({ gateId: "g", kind: "edit", payload: {} }).team).toBeNull();
  });

  it("posts a plan decision", async () => {
    const fetchFn = respond({ team_id: "team-1", phase: "DELIBERATING" });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    expect(await c.decideTeamPlan("t", "g1", "feedback", "use redis")).toEqual(
      { teamId: "team-1", phase: "DELIBERATING" });
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/chat/threads/t/team-plan-decision");
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual(
      { gate_id: "g1", decision: "feedback", feedback: "use redis" });
  });

  it("maps assignment and end fields on summaries", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({ teams: [{
      ...SUMMARY, phase: "DONE", end_reason: "implemented", approval_gate: true,
      adopted_proposal_id: "P1",
      members: [{ label: "alice", agent_id: "agent-a", status: "completed",
                  assignment: { member: "alice", part: "api", files: ["a.py"] },
                  assignment_done: true }] }] }) });
    const [team] = await c.listTeams("t");
    expect(team).toMatchObject({ endReason: "implemented", approvalGate: true,
      adoptedProposalId: "P1",
      members: [{ assignment: { part: "api", files: ["a.py"] }, assignmentDone: true }] });
  });
});
