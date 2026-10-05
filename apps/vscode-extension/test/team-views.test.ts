import type { TeamDetail, SequencedStreamEvent } from "@crucible/editor-client";
import { describe, expect, test } from "vitest";

import { TeamViewManager, teamChannel, type TeamViewEvent } from "../src/team-views.js";

function detail(phase: string, lastSeq: number): TeamDetail {
  return {
    teamId: "team-1", name: "auth", goal: "g", phase, round: 1, maxRounds: 3,
    pausedReason: null, members: [], openProposals: [],
    usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
    posts: [], lastSeq,
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
