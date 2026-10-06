import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient, parseWireTeamActivity } from "../src/client/http-backend-client";

const EVENT = {
  team_id: "team-1", aseq: 2, at: "2026-10-05T11:40:39+00:00", label: "alice", kind: "woke",
  activation: 1, cause_seq: 1, payload: { cause: "kickoff", by: "main", post_seq: 1 },
};
const TEAM = {
  team_id: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1, max_rounds: 3,
  paused_reason: null, members: [], open_proposals: [], usage: { requests: 0, budget: 160 },
  created_at: "2026-10-05T00:00:00Z",
};

describe("team activity contracts", () => {
  it("parses a wire activity event", () => {
    expect(parseWireTeamActivity(EVENT)).toEqual({
      teamId: "team-1", aseq: 2, at: "2026-10-05T11:40:39+00:00", label: "alice",
      kind: "woke", activation: 1, causeSeq: 1,
      payload: { cause: "kickoff", by: "main", post_seq: 1 },
    });
  });

  it("maps getTeam activity and lastAseq", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ ...TEAM, posts: [], last_seq: 0, activity: [EVENT], last_aseq: 2 }),
    }) });
    const detail = await c.getTeam("t", "team-1");
    expect(detail.lastAseq).toBe(2);
    expect(detail.activity[0]).toMatchObject({ kind: "woke", causeSeq: 1 });
  });

  it("an old backend without activity parses as empty", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ ...TEAM, posts: [], last_seq: 0 }),
    }) });
    const detail = await c.getTeam("t", "team-1");
    expect(detail.activity).toEqual([]);
    expect(detail.lastAseq).toBe(0);
  });

  it("maps /live member last, latest and counts", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: vi.fn().mockResolvedValue({
      ok: true, json: async () => ({
        active_task_id: null, status: null, pending_gates: [], plan: null,
        teams: [{ team_id: "team-1", name: "auth", phase: "DELIBERATING", round: 1,
          max_rounds: 3, paused_reason: null,
          members: [{ label: "alice", agent_id: "a", status: "completed",
            last: { kind: "wrapped_up", at: "2026-10-05T11:45:00+00:00", cause_seq: null,
                    by: null, status: "completed", activation: 1 } },
            { label: "bob", agent_id: "b", status: "running", last: null }],
          latest: { kind: "post", label: "bob", text: "ok", at: "2026-10-05T11:46:00+00:00" },
          counts: { posts: 3, proposals: [{ id: "P1", agree: 1, object: 0, pending: 1 }] } }],
      }),
    }) });
    const team = (await c.getThreadLiveState("t")).teams![0];
    expect(team.members[0].last).toEqual({ kind: "wrapped_up", at: "2026-10-05T11:45:00+00:00",
      causeSeq: null, by: null, status: "completed", activation: 1, waitingOn: [] });
    expect(team.members[1].last).toBeNull();
    expect(team.latest).toMatchObject({ kind: "post", label: "bob" });
    expect(team.counts.proposals[0]).toEqual({ id: "P1", agree: 1, object: 0, pending: 1 });
  });

  it("maps /live round_progress, null when absent", async () => {
    const live = (teams: unknown[]) => new HttpBackendClient({ baseUrl: "http://x",
      fetchFn: vi.fn().mockResolvedValue({ ok: true, json: async () => ({
        active_task_id: null, status: null, pending_gates: [], plan: null, teams }) }) });
    const base = { team_id: "team-1", name: "auth", phase: "DELIBERATING", round: 2,
      max_rounds: 3, paused_reason: null, members: [] };
    const withProgress = await live([{ ...base,
      round_progress: { round: 2, members: ["alice", "bob"], reported: ["alice"] } }])
      .getThreadLiveState("t");
    expect(withProgress.teams![0].roundProgress).toEqual(
      { round: 2, members: ["alice", "bob"], reported: ["alice"] });
    const without = await live([base]).getThreadLiveState("t");
    expect(without.teams![0].roundProgress).toBeNull();
  });
});
