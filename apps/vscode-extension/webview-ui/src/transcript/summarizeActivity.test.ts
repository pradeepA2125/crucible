import { describe, expect, it } from "vitest";
import type { ToolEventView } from "../types";
import type { ActivityStep } from "./groupTranscript";
import { describeTool, summarizeActivity } from "./summarizeActivity";

const ev = (tool: string, args: Record<string, unknown> = {}, isError = false): ToolEventView =>
  ({ id: 0, tool, args, source: "explore", done: true, isError });
const tools = (...events: ToolEventView[]): ActivityStep => ({ type: "tools", events });

describe("summarizeActivity", () => {
  it("counts per tool in first-seen order with distinct file reads", () => {
    const steps = [tools(ev("read_file", { path: "a.py" }), ev("read_file", { path: "a.py" }), ev("read_file", { path: "b.py" })),
      tools(ev("search_code"), ev("search_semantic"), ev("run_command"))];
    expect(summarizeActivity(steps)).toBe("Read 2 files, searched code 2 times, ran a command");
  });

  it("names a single edit and totals several", () => {
    const one: ActivityStep = { type: "edit", applied: true, entries: [{ path: "src/a.ts", additions: 3, deletions: 1 }] };
    const two: ActivityStep = { type: "edit", applied: true, entries: [{ path: "b.ts", additions: 2, deletions: 0 }] };
    expect(summarizeActivity([one])).toBe("Edited `a.ts` +3 −1");
    expect(summarizeActivity([one, two])).toBe("Edited 2 files +5 −1");
  });

  it("marks edits that were not applied and counts failures", () => {
    const rejected: ActivityStep = { type: "edit", applied: false, entries: [{ path: "a.ts", additions: 1, deletions: 0 }] };
    expect(summarizeActivity([tools(ev("run_command", {}, true)), rejected])).toBe("Ran a command, edited `a.ts` (not applied) · 1 failed");
  });

  it("caps at three phrases plus 'and N more'", () => {
    const steps = [tools(ev("read_file", { path: "a" }), ev("search_code"), ev("run_command"), ev("web_search"), ev("web_fetch"))];
    expect(summarizeActivity(steps)).toBe("Read a file, searched code once, ran a command, and 2 more");
  });

  it("falls back to the tool name", () => {
    expect(summarizeActivity([tools(ev("mcp__x__y"), ev("mcp__x__y"))])).toBe("Used mcp__x__y 2 times");
  });
});

describe("describeTool", () => {
  it("describes the running tool", () => {
    expect(describeTool(ev("read_file", { path: "src/controller.py" }))).toBe("Reading `controller.py`");
    expect(describeTool(ev("run_command", { command: "pytest", args: ["-q"] }))).toBe("Running `pytest -q`");
    expect(describeTool(ev("web_search", { query: "ollama vision" }))).toBe("Searching the web for “ollama vision”");
    expect(describeTool(ev("dispatch_agents"))).toBe("Starting agents");
    expect(describeTool(ev("other_tool"))).toBe("Using other_tool");
  });
});
