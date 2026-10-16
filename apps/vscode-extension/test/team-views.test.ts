import type { TeamDetail, SequencedStreamEvent } from "@crucible/editor-client";
import { describe, expect, test } from "vitest";

import { TeamViewManager, teamChannel, type TeamViewEvent } from "../src/team-views.js";

function detail(phase: string, lastSeq: number): TeamDetail {
  return {
    teamId: "team-1", name: "auth", goal: "g", phase, round: 1, maxRounds: 3,
    pausedReason: null, members: [], openProposals: [],
    usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
    posts: [], lastSeq, activity: [], lastAseq: 0,
  };
}

const post = (seq: number) => ({
  team_id: "team-1", seq, author: "alice", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], ref_id: null, round: 1, payload: {}, closed: null,
  created_at: "2026-10-05T00:00:00Z",
});

function channel(events: SequencedStreamEvent[], hold: boolean) {
  return async function* (_id: string, signal?: AbortSignal) {
    for (const e of events) yield e;
    if (hold) {
      await new Promise<void>((_, reject) => signal?.addEventListener(
        "abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }))));
    }
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("TeamViewManager", () => {
  test("activity backfill then follow skips aseq <= lastAseq", async () => {
    const seen: Array<string | number> = [];
    const act = (aseq: number) => ({
      team_id: "team-1", aseq, at: "2026-10-05T00:00:00Z", label: "alice", kind: "woke",
      activation: 1, cause_seq: 1, payload: { cause: "kickoff" },
    });
    const client = {
      getTeam: async () => ({ ...detail("DELIBERATING", 1), activity: [], lastAseq: 3 }),
      streamChannel: channel([
        { type: "team_activity", payload: { event: act(3) }, seq: 3 },
        { type: "team_activity", payload: { event: act(4) }, seq: 4 },
        { type: "team_post", payload: { post: post(2) }, seq: 2 },
      ] as SequencedStreamEvent[], true),
    };
    const m = new TeamViewManager(() => client, {
      detail: () => {},
      event: (_id, e: TeamViewEvent) => seen.push(
        e.type === "team_activity" ? `a${e.activity.aseq}` : e.type === "team_post" ? `p${e.post.seq}` : e.phase),
    }, 0);
    m.setOpen("t", ["team-1"]);
    await flush(); await flush();
    expect(seen).toEqual(["a4", "p2"]);   // activity and posts have independent cursors
    m.closeAll();
  });

  test("backfill then follow skips seq <= lastSeq", async () => {
    const seen: Array<string | number> = [];
    const subscribed: string[] = [];
    const client = {
      getTeam: async () => detail("DELIBERATING", 4),
      streamChannel: (id: string, signal?: AbortSignal) => {
        subscribed.push(id);
        return channel([
          { type: "team_post", payload: { post: post(4) }, seq: 4 },
          { type: "team_post", payload: { post: post(5) }, seq: 5 },
        ] as SequencedStreamEvent[], true)(id, signal);
      },
    };
    const m = new TeamViewManager(() => client, {
      detail: (_id, d) => seen.push(`detail:${d.lastSeq}`),
      event: (_id, e: TeamViewEvent) => seen.push(e.type === "team_post" ? e.post.seq : e.phase),
    }, 0);
    m.setOpen("t", ["team-1"]);
    await flush(); await flush();
    expect(subscribed).toEqual([teamChannel("t", "team-1")]);
    expect(seen).toEqual(["detail:4", 5]);   // the post the backfill had is not repeated
    m.closeAll();
  });

  test("a terminal phase event ends the follow with one final backfill", async () => {
    let backfills = 0;
    const client = {
      getTeam: async () => detail(backfills++ === 0 ? "DELIBERATING" : "DISBANDED", 0),
      streamChannel: channel([{ type: "team_phase", payload: {
        phase: "DISBANDED", round: 1, paused_reason: null } } as SequencedStreamEvent], true),
    };
    const phases: string[] = [];
    const m = new TeamViewManager(() => client, {
      detail: (_id, d) => phases.push(d.phase), event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    for (let i = 0; i < 6; i++) await flush();
    expect(phases).toEqual(["DELIBERATING", "DISBANDED"]);
    expect(backfills).toBe(2);
    m.closeAll();
  });

  test("a terminal team is backfilled once and never subscribed", async () => {
    const subscribed: string[] = [];
    const client = {
      getTeam: async () => detail("DONE", 7),
      streamChannel: (id: string) => { subscribed.push(id); return channel([], false)(id); },
    };
    const m = new TeamViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    await flush(); await flush();
    expect(subscribed).toEqual([]);
  });

  test("an idle channel re-backfills while the team is live; closing stops it", async () => {
    let backfills = 0;
    const client = {
      getTeam: async () => { backfills++; return detail("DELIBERATING", 0); },
      streamChannel: channel([], false),   // ends at once, like the idle timeout
    };
    const m = new TeamViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    for (let i = 0; i < 6; i++) await flush();
    m.setOpen("t", []);
    const after = backfills;
    for (let i = 0; i < 4; i++) await flush();
    expect(after).toBeGreaterThan(1);
    expect(backfills).toBe(after);
  });
});

describe("team workflow subscription boundaries", () => {
  test("an open dormant DONE board rearms for a new run without reopening the window", async () => {
    let current = { ...detail("DONE", 7), runId: 1 };
    let subscriptions = 0;
    const states: string[] = [];
    const client = { getTeam: async () => current,
      streamChannel: (id: string, signal?: AbortSignal) => { subscriptions++; return channel([], true)(id, signal); } };
    const m = new TeamViewManager(() => client, { detail: (_id, d) => states.push(`${d.runId}:${d.phase}`), event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    await flush(); await flush();
    current = { ...detail("FINALIZING", 9), runId: 2 };
    m.noteState("t", "team-1", current);
    await flush(); await flush();
    expect(states).toEqual(["1:DONE", "2:FINALIZING"]);
    expect(subscriptions).toBe(1);
    m.closeAll();
  });

  test("DONE with a pending lead exchange follows and backfills its reply before going dormant", async () => {
    let reads = 0;
    const summaries: string[] = [];
    const client = {
      getTeam: async () => reads++ === 0
        ? { ...detail("DONE", 10), runId: 2, pendingLeadRequestSeq: 10, leadExchangeStatus: "running" }
        : { ...detail("DONE", 11), runId: 2, pendingLeadRequestSeq: null, leadExchangeStatus: null },
      streamChannel: channel([{ type: "team_phase", payload: { phase: "DONE", round: 1,
        paused_reason: null, run_id: 2, pending_lead_request_seq: null, lead_exchange_status: null } } as SequencedStreamEvent], true),
    };
    const m = new TeamViewManager(() => client, { detail: (_id, d) => summaries.push(`${d.lastSeq}:${d.leadExchangeStatus}`), event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    for (let i = 0; i < 6; i++) await flush();
    expect(summaries).toEqual(["10:running", "11:null"]);
    expect(reads).toBe(2);
    m.closeAll();
  });

  test("an older terminal backfill cannot overwrite a newer reopened run", async () => {
    let resolveOld!: (d: TeamDetail) => void;
    let reads = 0;
    const seen: string[] = [];
    const current = { ...detail("IMPLEMENTING", 9), runId: 2 };
    const client = { getTeam: async () => reads++ === 0
      ? new Promise<TeamDetail>((resolve) => { resolveOld = resolve; }) : current,
      streamChannel: channel([], true) };
    const m = new TeamViewManager(() => client, { detail: (_id, d) => seen.push(d.phase), event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    m.noteState("t", "team-1", current);
    await flush();
    resolveOld({ ...detail("DONE", 7), runId: 1 });
    await flush(); await flush();
    expect(seen).toEqual(["IMPLEMENTING"]);
    m.closeAll();
  });

  test("closed boards and another thread's notifications do not start polling", async () => {
    let reads = 0;
    const done = detail("DONE", 0);
    const client = { getTeam: async () => { reads++; return done; }, streamChannel: channel([], false) };
    const m = new TeamViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    await flush();
    m.noteState("other-thread", "team-1", { ...done, phase: "DELIBERATING", runId: 2 });
    m.setOpen("t", []);
    m.noteState("t", "team-1", { ...done, phase: "DELIBERATING", runId: 2 });
    await flush();
    expect(reads).toBe(1);
    m.closeAll();
  });
});
