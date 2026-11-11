import { describe, expect, it } from "vitest";
import type { ChatMsg, ToolEventView } from "../types";
import { sealedRunningEvents } from "./sealedRunning";

const t = "2026-11-11T00:00:00Z";
const command = (done: boolean): ToolEventView =>
  ({ id: 1, tool: "run_command", args: { command: "pytest" }, source: "execution", done });
const pills = (events: ToolEventView[]): ChatMsg =>
  ({ role: "agent", content: "", type: "text", timestamp: t, metadata: { tool_events: events } });
const spoken = (role: "user" | "agent", content: string): ChatMsg =>
  ({ role, content, type: "text", timestamp: t, metadata: { voice: "exchange", voice_decision: "d1" } });

describe("sealedRunningEvents", () => {
  it("finds this turn's unfinished command", () => {
    const messages = [{ ...pills([]), role: "user" as const, content: "fix it" }, pills([command(false)])];
    expect(sealedRunningEvents(messages).map(e => e.id)).toEqual([1]);
  });

  it("still finds it after a question asked by voice mid-turn", () => {
    const messages = [{ ...pills([]), role: "user" as const, content: "fix it" }, pills([command(false)]),
      spoken("user", "how is it going"), spoken("agent", "The tests are running.")];
    expect(sealedRunningEvents(messages).map(e => e.id)).toEqual([1]);
  });

  it("stops at the turn's own request", () => {
    const messages = [pills([command(false)]), { ...pills([]), role: "user" as const, content: "next" }];
    expect(sealedRunningEvents(messages)).toEqual([]);
  });
});
