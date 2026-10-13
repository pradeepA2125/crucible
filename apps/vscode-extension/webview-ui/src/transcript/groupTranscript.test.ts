import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../types";
import { groupTranscript } from "./groupTranscript";

const t = "2026-10-13T00:00:00Z";
const user = (content: string): ChatMsg => ({ role: "user", content, type: "text", timestamp: t, metadata: {} });
const pills = (...tools: string[]): ChatMsg => ({
  role: "agent", content: "", type: "text", timestamp: t,
  metadata: { tool_events: tools.map((tool, id) => ({ id, tool, args: { path: `${tool}${id}.py` }, source: "explore", done: true })) },
});
const diff = (path: string, add: number, del: number, resolved: "applied" | "discarded" = "applied"): ChatMsg => ({
  role: "agent", content: "", type: "diff_card", timestamp: t,
  metadata: { resolved, diff_entries: [{ path, additions: add, deletions: del, unified_diff: `@@ ${path}` }] },
});
const crumb = (content: string, extra: Record<string, unknown> = {}): ChatMsg => ({
  role: "agent", content, type: "text", timestamp: t, metadata: { breadcrumb: true, ...extra },
});
const progress = (content: string): ChatMsg => ({ role: "agent", content, type: "text", timestamp: t, metadata: { progress: true } });
const answer = (content: string): ChatMsg => ({ role: "agent", content, type: "text", timestamp: t, metadata: {} });

describe("groupTranscript", () => {
  it("merges consecutive pills, edits and outcome breadcrumbs into one group", () => {
    const items = groupTranscript([user("go"), pills("read_file"), diff("a.ts", 3, 1), crumb("✓ Edit accepted: a.ts", { edit_outcome: true }), pills("run_command")]);
    expect(items.map((i) => i.kind)).toEqual(["message", "activity", "changes"]);
    const group = items[1];
    expect(group.kind === "activity" && group.steps.map((s) => s.type)).toEqual(["tools", "edit", "outcome", "tools"]);
  });

  it("splits groups at progress notes and answers, and keeps them as messages", () => {
    const items = groupTranscript([user("go"), pills("read_file"), progress("found it"), pills("run_command"), answer("done")]);
    expect(items.map((i) => i.kind)).toEqual(["message", "activity", "message", "activity", "message"]);
  });

  it("recognises legacy outcome breadcrumbs by their text", () => {
    const items = groupTranscript([user("go"), diff("a.ts", 1, 0, "discarded"), crumb("✗ Not applied: stale")]);
    const group = items[1];
    expect(group.kind === "activity" && group.steps.map((s) => s.type)).toEqual(["edit", "outcome"]);
  });

  it("keeps other breadcrumbs and messages with content plus pills as boundaries", () => {
    const qa: ChatMsg = { ...answer("here is the answer"), metadata: pills("read_file").metadata };
    const items = groupTranscript([user("go"), crumb("✓ Implement chosen"), qa]);
    expect(items.map((i) => i.kind)).toEqual(["message", "message", "activity", "message"]);
    const last = items[3];
    expect(last.kind === "message" && last.index).toBe(2);
    expect(last.kind === "message" && last.msg.metadata?.tool_events).toBeUndefined();
    expect(last.kind === "message" && last.msg.content).toBe("here is the answer");
  });

  it("sums a turn's applied edits per file and lists rejected files as not applied", () => {
    const items = groupTranscript([user("go"), diff("a.ts", 3, 1), diff("a.ts", 2, 0), diff("b.ts", 5, 5, "discarded"), answer("done")]);
    const changes = items[items.length - 1];
    expect(changes.kind).toBe("changes");
    expect(changes.kind === "changes" && changes.files).toEqual([
      { path: "a.ts", additions: 5, deletions: 1, applied: true, diff: "@@ a.ts" },
      { path: "b.ts", additions: 0, deletions: 0, applied: false, diff: undefined },
    ]);
  });

  it("emits one changes item per edited turn and none for a turn without edits", () => {
    const items = groupTranscript([user("1"), diff("a.ts", 1, 0), answer("ok"), user("2"), pills("read_file"), answer("ok")]);
    expect(items.filter((i) => i.kind === "changes")).toHaveLength(1);
    expect(items.findIndex((i) => i.kind === "changes")).toBe(3);
  });

  it("emits no changes item for a turn whose diff cards carry no entries", () => {
    const empty: ChatMsg = { role: "agent", content: "", type: "diff_card", timestamp: t, metadata: { resolved: "applied", diff_entries: [] } };
    const items = groupTranscript([user("go"), empty, answer("done")]);
    expect(items.some((i) => i.kind === "changes")).toBe(false);
  });

  it("ignores a pills-only message without tool events", () => {
    const empty: ChatMsg = { ...answer(""), metadata: { tool_events: [] } };
    expect(groupTranscript([user("go"), empty]).map((i) => i.kind)).toEqual(["message"]);
  });

  it("keeps a pending diff card as an interactive message and a boundary", () => {
    const pending: ChatMsg = { ...diff("a.ts", 1, 0), metadata: { diff_entries: [{ path: "a.ts", additions: 1, deletions: 0 }] } };
    const items = groupTranscript([user("go"), pills("read_file"), pending, pills("run_command")]);
    expect(items.map((i) => i.kind)).toEqual(["message", "activity", "message", "activity"]);
    expect(items[2].kind === "message" && items[2].msg).toBe(pending);
    expect(items.some((i) => i.kind === "changes")).toBe(false);
  });

  it("emits a legacy diff card's own pills as a tools step before its edit", () => {
    const card = diff("a.ts", 1, 0);
    card.metadata = { ...card.metadata, tool_events: pills("read_file").metadata!.tool_events };
    const group = groupTranscript([user("go"), card])[1];
    expect(group.kind === "activity" && group.steps.map((s) => s.type)).toEqual(["tools", "edit"]);
  });

  it("does not let the live compaction notice split a group, and still renders it after the group", () => {
    const notice = crumb("🗜️ Compacted 4 earlier messages into memory (v2)");
    const items = groupTranscript([user("go"), pills("read_file"), notice, pills("run_command")]);
    expect(items.map((i) => i.kind)).toEqual(["message", "activity", "message"]);
    expect(items[1].kind === "activity" && items[1].steps).toHaveLength(2);
    expect(items[2].kind === "message" && items[2].msg).toBe(notice);
  });

  it("returns the same split message object across regroups", () => {
    const qa: ChatMsg = { ...answer("here"), metadata: pills("read_file").metadata };
    const first = groupTranscript([user("go"), qa]).find((i) => i.kind === "message" && i.msg.content === "here");
    const second = groupTranscript([user("go"), qa]).find((i) => i.kind === "message" && i.msg.content === "here");
    expect(first!.kind === "message" && first!.msg).toBe(second!.kind === "message" && second!.msg);
  });
});
