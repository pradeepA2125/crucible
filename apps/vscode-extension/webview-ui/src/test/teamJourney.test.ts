import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, buildJourney, chapterTitle, tallyFor } from "../teamJourney";
import type { TeamActivityView, TeamPostView } from "../types";

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const post = (seq: number, at: number, over: Partial<TeamPostView> = {}): TeamPostView => ({
  teamId: "team-1", seq, author: "review", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], refId: null, round: 1, payload: {}, closed: null, createdAt: T(at), ...over,
});
const ev = (aseq: number, at: number, label: string, kind: string,
            over: Partial<TeamActivityView> = {}): TeamActivityView => ({
  teamId: "team-1", aseq, at: T(at), label, kind, activation: 1, causeSeq: null, payload: {}, ...over,
});
const ROSTER = ["review", "impl"];
const kinds = (items: ReturnType<typeof buildJourney>) => items.map((i) => i.kind);

const KICKOFF = post(1, 1, { author: "main", kind: "proposal", round: 0 });
const PHASE = ev(1, 0, "team", "phase", { activation: null, payload: { phase: "DELIBERATING", round: 1 } });

describe("buildJourney", () => {
  it("kickoff wakes fold into the proposal's footer", () => {
    const items = buildJourney([KICKOFF], [PHASE,
      ev(2, 1, "review", "woke", { causeSeq: 1, payload: { cause: "kickoff" } }),
      ev(3, 1, "impl", "woke", { causeSeq: 1, payload: { cause: "kickoff" } }),
      ev(4, 2, "review", "took_up", { payload: { posts: [1], from: ["main"] } }),
    ], ROSTER);
    expect(kinds(items)).toEqual(["chapter", "post", "chapter", "beat"]);
    const card = items[1];
    expect(card.kind === "post" && card.footer).toEqual({ woke: ["review", "impl"], queued: [], held: null });
    expect(items[0]).toMatchObject({ title: "Kickoff", current: false });
    expect(items[2]).toMatchObject({ title: "Round 1 · deliberating", current: true });
  });

  it("a mention's wake folds under the mentioning post; a queued one too", () => {
    const items = buildJourney([KICKOFF, post(2, 10, { mentions: ["impl"] })], [PHASE,
      ev(2, 10, "impl", "notified", { causeSeq: 2, payload: { cause: "mention", by: "review" } }),
    ], ROSTER);
    const card = items.find((i) => i.kind === "post" && i.post.seq === 2);
    expect(card?.kind === "post" && card.footer).toEqual({ woke: [], queued: ["impl"], held: null });
    expect(items.some((i) => i.kind === "beat")).toBe(false);
  });

  it("hidden DM wake becomes a standalone beat", () => {
    const dm = post(2, 10, { author: "impl", recipient: "review" });
    const wake = ev(2, 10, "review", "woke", { causeSeq: 2, payload: { cause: "message", by: "impl" } });
    expect(DEFAULT_FILTERS.messages).toBe(true);     // direct messages show by default
    const hidden = buildJourney([KICKOFF, dm], [PHASE, wake], ROSTER,
                                { ...DEFAULT_FILTERS, messages: false });
    expect(hidden.filter((i) => i.kind === "beat")).toHaveLength(1);
    expect(hidden.some((i) => i.kind === "post" && i.post.seq === 2)).toBe(false);
    const shown = buildJourney([KICKOFF, dm], [PHASE, wake], ROSTER);
    expect(shown.some((i) => i.kind === "beat")).toBe(false);
    const card = shown.find((i) => i.kind === "post" && i.post.seq === 2);
    expect(card?.kind === "post" && card.footer.woke).toEqual(["review"]);
  });

  it("stances become replies; a changed stance replaces the earlier one", () => {
    const items = buildJourney([KICKOFF,
      post(2, 10, { kind: "object", refId: "P1", text: "missing case" }),
      post(3, 20, { kind: "agree", refId: "P1", text: "fine now" }),
    ], [PHASE], ROSTER);
    const stances = items.filter((i) => i.kind === "stance");
    expect(stances.map((i) => i.kind === "stance" && i.replaces)).toEqual([null, 2]);
  });

  it("changed stance shows in the tally with what it was", () => {
    const posts = [KICKOFF,
      post(2, 10, { kind: "object", refId: "P1" }),
      post(3, 20, { kind: "agree", refId: "P1" }),
      post(4, 21, { author: "impl", kind: "agree", refId: "P1" })];
    expect(tallyFor(KICKOFF, posts, ROSTER)).toEqual([
      { label: "review", stance: "agree", was: "object", seq: 3 },
      { label: "impl", stance: "agree", was: null, seq: 4 },
    ]);
    expect(tallyFor(KICKOFF, [KICKOFF], ROSTER)[0]).toEqual(
      { label: "review", stance: "pending", was: null, seq: null });
  });

  it("wrap-ups, phases and system lines", () => {
    const items = buildJourney([KICKOFF, post(2, 30, { author: "system", kind: "system", text: "The team was disbanded." })], [PHASE,
      ev(2, 20, "review", "wrapped_up", { payload: { status: "stopped", report: "" } }),
      ev(3, 31, "team", "phase", { activation: null, payload: { phase: "DISBANDED", round: 1 } }),
    ], ROSTER);
    expect(kinds(items)).toEqual(["chapter", "post", "chapter", "wrap", "system", "chapter"]);
    const last = items[items.length - 1];
    expect(last).toMatchObject({ kind: "chapter", title: "Disbanded", ended: true, current: false });
  });

  it("gaps over a minute get a divider", () => {
    const items = buildJourney([KICKOFF, post(2, 10), post(3, 10 + 180)], [], ROSTER);
    const gap = items.find((i) => i.kind === "gap");
    expect(gap).toMatchObject({ minutes: 3 });
  });

  it("filters hide layers but keep footers", () => {
    const events = [PHASE,
      ev(2, 1, "review", "woke", { causeSeq: 1, payload: { cause: "kickoff" } }),
      ev(3, 2, "review", "took_up", { payload: { posts: [1] } }),
      ev(4, 5, "review", "wrapped_up", { payload: { status: "completed", report: "r" } })];
    const noActivity = buildJourney([KICKOFF], events, ROSTER, { ...DEFAULT_FILTERS, activity: false });
    expect(kinds(noActivity)).toEqual(["chapter", "post", "chapter"]);
    const card = noActivity[1];
    expect(card.kind === "post" && card.footer.woke).toEqual(["review"]);
    const noPosts = buildJourney([KICKOFF], events, ROSTER, { ...DEFAULT_FILTERS, posts: false });
    expect(kinds(noPosts)).toEqual(["chapter", "chapter", "beat", "beat", "wrap"]);
  });

  it("posts without activity (a team from before this change)", () => {
    const items = buildJourney([KICKOFF, post(2, 10)], [], ROSTER);
    expect(kinds(items)).toEqual(["chapter", "post", "post"]);
    expect(items[0]).toMatchObject({ title: "Kickoff", current: true });
  });

  it("chapter titles", () => {
    expect(chapterTitle("DELIBERATING", 2)).toBe("Round 2 · deliberating");
    expect(chapterTitle("IMPLEMENTING", 1)).toBe("Implementing");
    expect(chapterTitle("FAILED", 1)).toBe("Failed");
  });
});

import { buildJourney as build5a } from "../teamJourney";
import type { TeamActivityView as Act, TeamPostView as Post } from "../types";

const T0 = Date.parse("2026-10-06T10:00:00Z");
const at = (s: number) => new Date(T0 + s * 1000).toISOString();
const post5 = (seq: number, author: string, kind: string, s: number, over: Partial<Post> = {}): Post => ({
  teamId: "t", seq, author, kind, recipient: null, text: `text ${seq}`, mentions: [], refId: null,
  round: 1, payload: {}, closed: null, createdAt: at(s), ...over });
const act5 = (aseq: number, label: string, kind: string, s: number, payload: Record<string, unknown> = {},
              over: Partial<Act> = {}): Act => ({
  teamId: "t", aseq, at: at(s), label, kind, activation: null, causeSeq: null, payload, ...over });

describe("journey with rounds (spec 2026-10-05 §9)", () => {
  const posts = [
    post5(1, "main", "proposal", 0, { round: 0 }),
    post5(2, "alice", "post", 10, { mentions: ["bob"] }),
    post5(3, "system", "system", 50, { round: null, text: "Adopted P1.",
      payload: { adopted: "P1", assignments: [{ member: "alice", part: "api", files: ["a.py"] }] } }),
  ];
  const activity = [
    act5(1, "team", "phase", 0, { phase: "DELIBERATING", round: 1 }),
    act5(2, "team", "round_started", 1, { round: 1, members: [
      { label: "alice", handed: [1] }, { label: "bob", handed: [1] }] }),
    act5(3, "alice", "took_up", 2, { posts: [1], from: ["main"], round: 1 }, { activation: 1 }),
    act5(4, "alice", "held", 10, { post_seq: 2, for: ["bob"], everyone: false, until_round: 2 },
         { causeSeq: 2 }),
    act5(5, "alice", "wrapped_up", 20, { status: "completed", report: "r", round: 1 }, { activation: 1 }),
    act5(6, "bob", "requeued", 21, { retry: 1, of: 2, after_ms: 30000 }),
    act5(7, "bob", "wrapped_up", 25, { status: "completed", report: "r", round: 1 }, { activation: 1 }),
    act5(8, "team", "round_ended", 26, { round: 1, adopted: null, new_posts: 1, proposals: [
      { id: "P1", stances: { alice: "agree", bob: "none" }, adopted: false, reason: "bob has no stance" }] }),
    act5(9, "team", "phase", 27, { phase: "DELIBERATING", round: 2 }),
    act5(10, "team", "round_started", 28, { round: 2, members: [
      { label: "alice", handed: [] }, { label: "bob", handed: [2] }] }),
    act5(11, "bob", "deadline", 40, {}),
    act5(12, "team", "round_ended", 49, { round: 2, adopted: "P1", new_posts: 0, proposals: [
      { id: "P1", stances: { alice: "agree", bob: "agree" }, adopted: true, reason: "adopted" }] }),
    act5(13, "team", "phase", 51, { phase: "DONE", round: 2, reason: "adopted" }),
  ];
  const items = build5a(posts, activity, ["alice", "bob"]);

  it("lays out chapters, strips, verdicts and the adopted card in order", () => {
    expect(items.filter((i) => i.kind !== "gap").map((i) => i.kind)).toEqual([
      "chapter", "post", "chapter", "round", "post", "wrap", "beat", "wrap", "verdict",
      "chapter", "round", "beat", "verdict", "adopted", "chapter"]);
  });

  it("folds held into the post footer and took_up into the strip", () => {
    const card = items.find((i) => i.kind === "post" && i.post.seq === 2);
    expect(card && card.kind === "post" && card.footer.held).toEqual(
      { for: ["bob"], everyone: false, untilRound: 2 });
    expect(items.some((i) => i.kind === "beat" && i.event.kind === "took_up")).toBe(false);
  });

  it("round strips know who reported and when", () => {
    const strip = items.find((i) => i.kind === "round" && i.round === 1);
    expect(strip && strip.kind === "round" && strip.members).toEqual([
      { label: "alice", handed: [1], reportedAt: at(20), status: "completed" },
      { label: "bob", handed: [1], reportedAt: at(25), status: "completed" }]);
    expect(strip && strip.kind === "round" && strip.ended).toBe(true);
  });

  it("verdicts name the next round or the adoption", () => {
    const verdicts = items.filter((i) => i.kind === "verdict");
    expect(verdicts.map((v) => v.kind === "verdict" && [v.adopted, v.nextRound, v.newPosts])).toEqual([
      [null, 2, 1], ["P1", null, 0]]);
  });
});
