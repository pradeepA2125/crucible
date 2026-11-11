import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../types";
import { conversationChanges } from "../desktop/conversationChanges";

const t = "2026-11-11T00:00:00Z";
const user = (content: string): ChatMsg => ({ role: "user", content, type: "text", timestamp: t, metadata: {} });
const spoken = (role: "user" | "agent", content: string): ChatMsg =>
  ({ role, content, type: "text", timestamp: t, metadata: { voice: "exchange", voice_decision: "d1" } });
const diff = (path: string): ChatMsg => ({
  role: "agent", content: "", type: "diff_card", timestamp: t,
  metadata: { resolved: "applied", diff_entries: [{ path, additions: 1, deletions: 0, unified_diff: `@@ ${path}` }] },
});

describe("conversationChanges with voice exchanges", () => {
  it("a question asked mid-turn does not renumber that turn's edits", () => {
    const typed = conversationChanges([user("fix it"), diff("a.py"), diff("b.py")]);
    const withVoice = conversationChanges([user("fix it"), diff("a.py"),
      spoken("user", "how is it going"), spoken("agent", "Halfway."), diff("b.py")]);
    expect(withVoice).toEqual(typed);
  });
});
