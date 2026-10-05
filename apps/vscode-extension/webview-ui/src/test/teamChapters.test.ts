import { describe, expect, it } from "vitest";
import { buildChapters, countTools, whyText } from "../teamChapters";
import type { ChatMsg, TeamActivityView, TeamPostView } from "../types";

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const msg = (meta: Record<string, unknown>, content = ""): ChatMsg =>
  ({ role: "agent", content, type: "text", timestamp: T(0), metadata: meta });
const pills = (n: number) => msg({ tool_events: Array.from({ length: n }, (_, i) => ({ id: i })) });
const ev = (aseq: number, at: number, kind: string, activation: number,
            payload: Record<string, unknown> = {}, causeSeq: number | null = null): TeamActivityView =>
  ({ teamId: "team-1", aseq, at: T(at), label: "review", kind, activation, causeSeq, payload });
const post = (seq: number, at: number, over: Partial<TeamPostView> = {}): TeamPostView => ({
  teamId: "team-1", seq, author: "review", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], refId: null, round: 1, payload: {}, closed: null, createdAt: T(at), ...over,
});

const MESSAGES = [
  pills(3), msg({ report: true, status: "completed" }, "first report"),
  msg({ divider: true, activation: 2 }, "↩ woken"),
  pills(2),
];
const ACTIVITY = [
  ev(2, 1, "woke", 1, { cause: "kickoff", by: "main", post_seq: 1 }, 1),
  ev(3, 2, "took_up", 1, { posts: [1], from: ["main"] }),
  ev(5, 20, "wrapped_up", 1, { status: "completed", report: "first report", duration_ms: 18000 }),
  ev(6, 30, "woke", 2, { cause: "mention", by: "impl", post_seq: 5 }, 5),
  ev(7, 31, "took_up", 2, { posts: [5], from: ["impl"] }),
  ev(8, 35, "picked_up", 2, { posts: [7], from: ["impl"] }),
];

describe("buildChapters", () => {
  it("cuts at dividers and joins activity by activation", () => {
    const chapters = buildChapters("review", MESSAGES, ACTIVITY,
      [post(4, 10), post(6, 33)], { working: true, fallbackStatus: "running" });
    expect(chapters.map((c) => c.n)).toEqual([1, 2]);
    const [one, two] = chapters;
    expect(one).toMatchObject({ status: "completed", durationMs: 18000, handed: [1], tools: 3,
                                report: "first report", current: false });
    expect(one.why).toEqual({ cause: "kickoff", by: "main", postSeq: 1 });
    expect(one.said.map((p) => p.seq)).toEqual([4]);
    expect(two).toMatchObject({ status: "working", handed: [5], tools: 2, report: null, current: true });
    expect(two.pickedUp).toEqual([{ posts: [7], at: T(35) }]);
    expect(two.said.map((p) => p.seq)).toEqual([6]);
  });

  it("transcript without dividers is one chapter", () => {
    const chapters = buildChapters("review", [pills(2), msg({ report: true }, "done")], [],
      [post(4, 10), post(5, 11, { author: "impl" })], { working: false, fallbackStatus: "completed" });
    expect(chapters).toHaveLength(1);
    expect(chapters[0]).toMatchObject({ n: 1, why: null, status: "completed", report: "done", tools: 2 });
    expect(chapters[0].said.map((p) => p.seq)).toEqual([4]);
  });

  it("a stopped chapter without a transcript report uses the wrap-up's", () => {
    const chapters = buildChapters("review", [pills(1)],
      [ev(1, 0, "wrapped_up", 1, { status: "stopped", report: "" })], [],
      { working: false, fallbackStatus: "stopped" });
    expect(chapters[0]).toMatchObject({ status: "stopped", report: null });
  });

  it("counts tools and words the reason a chapter started", () => {
    expect(countTools([pills(2), msg({}), pills(1)])).toBe(3);
    const isP = (s: number) => s === 1;
    expect(whyText({ cause: "kickoff", by: "main", postSeq: 1 }, isP)).toBe("kickoff — main's proposal P1");
    expect(whyText({ cause: "mention", by: "impl", postSeq: 5 }, isP)).toBe("woken by impl's post #5");
    expect(whyText({ cause: "message", by: "impl", postSeq: 9 }, isP)).toBe("woken by impl's message #9");
    expect(whyText({ cause: "leftover", by: null, postSeq: null }, isP)).toBe("leftover input");
    expect(whyText(null, isP)).toBe("started");
  });
});

import { buildChapters as chapters5a, chapterHeading } from "../teamChapters";

describe("round chapters (spec 2026-10-05 §9)", () => {
  it("a deliberation activation is titled by its round and lists what it was handed", () => {
    const took = { teamId: "t", aseq: 3, at: "2026-10-06T10:00:02Z", label: "alice", kind: "took_up",
      activation: 1, causeSeq: null, payload: { posts: [1], from: ["main"], round: 1 } };
    const [c] = chapters5a("alice", [], [took], [], { working: false, fallbackStatus: "completed" });
    expect(c.why).toEqual({ cause: "round", by: null, postSeq: null, round: 1 });
    expect(chapterHeading(c, (s) => s === 1)).toEqual({ title: "Round 1", why: "handed P1" });
  });

  it("other chapters keep the Chapter title", () => {
    const woke = { teamId: "t", aseq: 3, at: "x", label: "alice", kind: "woke", activation: 1,
      causeSeq: 5, payload: { cause: "mention", by: "bob", post_seq: 5 } };
    const [c] = chapters5a("alice", [], [woke], [], { working: false, fallbackStatus: "completed" });
    expect(chapterHeading(c, () => false)).toEqual({ title: "Chapter 1", why: "woken by bob's post #5" });
  });
});
