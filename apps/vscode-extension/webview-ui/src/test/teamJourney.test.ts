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
    expect(card.kind === "post" && card.footer).toEqual({ woke: ["review", "impl"], queued: [] });
    expect(items[0]).toMatchObject({ title: "Kickoff", current: false });
    expect(items[2]).toMatchObject({ title: "Round 1 · deliberating", current: true });
  });

  it("a mention's wake folds under the mentioning post; a queued one too", () => {
    const items = buildJourney([KICKOFF, post(2, 10, { mentions: ["impl"] })], [PHASE,
      ev(2, 10, "impl", "notified", { causeSeq: 2, payload: { cause: "mention", by: "review" } }),
    ], ROSTER);
    const card = items.find((i) => i.kind === "post" && i.post.seq === 2);
    expect(card?.kind === "post" && card.footer).toEqual({ woke: [], queued: ["impl"] });
    expect(items.some((i) => i.kind === "beat")).toBe(false);
  });

  it("hidden DM wake becomes a standalone beat", () => {
    const dm = post(2, 10, { author: "impl", recipient: "review" });
    const wake = ev(2, 10, "review", "woke", { causeSeq: 2, payload: { cause: "message", by: "impl" } });
    const hidden = buildJourney([KICKOFF, dm], [PHASE, wake], ROSTER);
    expect(hidden.filter((i) => i.kind === "beat")).toHaveLength(1);
    expect(hidden.some((i) => i.kind === "post" && i.post.seq === 2)).toBe(false);
    const shown = buildJourney([KICKOFF, dm], [PHASE, wake], ROSTER,
                               { ...DEFAULT_FILTERS, messages: true });
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
