import { describe, expect, it } from "vitest";
import type { ToolEventView } from "../types";
import type { ActivityStep } from "./groupTranscript";
import { describeTool, toolLine, summarizeActivity } from "./summarizeActivity";

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
    expect(summarizeActivity([one])).toBe("Edited a.ts +3 −1");
    expect(summarizeActivity([one, two])).toBe("Edited 2 files +5 −1");
  });

  it("counts distinct files, so two edits to one file read as one file", () => {
    const first: ActivityStep = { type: "edit", applied: true, entries: [{ path: "a.ts", additions: 3, deletions: 1 }] };
    const second: ActivityStep = { type: "edit", applied: true, entries: [{ path: "a.ts", additions: 2, deletions: 0 }] };
    expect(summarizeActivity([first, second])).toBe("Edited a.ts +5 −1");
  });

  it("marks edits that were not applied and counts failures", () => {
    const rejected: ActivityStep = { type: "edit", applied: false, entries: [{ path: "a.ts", additions: 1, deletions: 0 }] };
    expect(summarizeActivity([tools(ev("run_command", {}, true)), rejected])).toBe("Ran a command, edited a.ts (not applied) · 1 failed");
  });

  it("caps at three phrases plus 'and N more'", () => {
    const steps = [tools(ev("read_file", { path: "a" }), ev("search_code"), ev("run_command"), ev("web_search"), ev("web_fetch"))];
    expect(summarizeActivity(steps)).toBe("Read a file, searched code once, ran a command, and 2 more");
  });

  it("leaves tools that are still running out of the counts", () => {
    const running: ToolEventView = { id: 1, tool: "run_command", args: {}, source: "explore", done: false };
    expect(summarizeActivity([tools(ev("read_file", { path: "a" }), running)])).toBe("Read a file");
  });

  it("falls back to the first outcome without its marker when a group has nothing else to count", () => {
    expect(summarizeActivity([{ type: "outcome", text: "✓ Edit accepted: a.ts" }, { type: "outcome", text: "✗ Not applied: b" }]))
      .toBe("Edit accepted: a.ts");
  });

  it("falls back to the tool name", () => {
    expect(summarizeActivity([tools(ev("mcp__x__y"), ev("mcp__x__y"))])).toBe("Used mcp__x__y 2 times");
  });
});

describe("describeTool", () => {
  it("describes the running tool", () => {
    expect(describeTool(ev("read_file", { path: "src/controller.py" }))).toBe("Reading controller.py");
    expect(describeTool(ev("run_command", { command: "pytest", args: ["-q"] }))).toBe("Running pytest -q");
    expect(describeTool(ev("web_search", { query: "ollama vision" }))).toBe("Searching the web for “ollama vision”");
    expect(describeTool(ev("dispatch_agents"))).toBe("Starting agents");
    expect(describeTool(ev("other_tool"))).toBe("Using other_tool");
    expect(describeTool(ev("write_stdin", { session_id: "s1" }))).toBe("Sending input to a session");
    expect(describeTool(ev("kill_session"))).toBe("Stopping a session");
    expect(describeTool(ev("list_sessions"))).toBe("Listing sessions");
    expect(describeTool(ev("search_code"))).toBe("Searching code");
    expect(describeTool(ev("search_semantic", { query: "" }))).toBe("Searching code");
  });
});

const lineEvent = (tool: string, args: Record<string, unknown> | null, done = true, extra: Partial<ToolEventView> = {}): ToolEventView =>
  ({ id: 1, tool, args: args as ToolEventView["args"], source: "explore", done, ...extra });

describe("toolLine (one activity line per step, spec §2)", () => {
  it.each([
    ["read_file", { path: "src/a/upload_client.py" }, "Read", "upload_client.py"],
    ["list_directory", { path: "src" }, "Listed", "src"],
    ["search_code", { pattern: "retry|backoff" }, "Searched for", "“retry|backoff”"],
    ["run_command", { command: "pytest", args: ["tests/test_upload.py"] }, "Ran", "pytest tests/test_upload.py"],
    ["web_search", { query: "vite chunk names" }, "Searched the web for", "“vite chunk names”"],
    ["web_fetch", { url: "https://example.com" }, "Fetched", "https://example.com"],
    ["query_graph", { node: "src/a.ts" }, "Queried the code graph for", "src/a.ts"],
    ["write_todos", { items: [] }, "Updated the todo list", ""],
  ])("%s done → %s %s", (tool, args, verb, target) => {
    expect(toolLine(lineEvent(tool, args))).toEqual({ verb, target });
  });

  it("uses the present tense while the step runs", () => {
    expect(toolLine(lineEvent("read_file", { path: "a.py" }, false))).toEqual({ verb: "Reading", target: "a.py" });
    expect(toolLine(lineEvent("run_command", { command: "pytest" }, false))).toEqual({ verb: "Running", target: "pytest" });
  });

  it("never crashes or prints undefined on missing or odd arguments (Review Focus 2)", () => {
    for (const args of [null, {}, { path: 42 }, { command: null, args: "nope" }]) {
      for (const tool of ["read_file", "run_command", "search_code", "web_fetch", "mcp__github__create_issue"]) {
        const { verb, target } = toolLine(lineEvent(tool, args as Record<string, unknown> | null));
        expect(verb.length).toBeGreaterThan(0);
        expect(`${verb} ${target}`).not.toContain("undefined");
      }
    }
  });

  it("names an unknown tool instead of guessing", () => {
    expect(toolLine(lineEvent("mcp__github__create_issue", {}))).toEqual({ verb: "Used", target: "mcp__github__create_issue" });
  });
});

describe("describeTool keeps its live wording", () => {
  it.each([
    [lineEvent("read_file", { path: "a/b.py" }, false), "Reading b.py"],
    [lineEvent("search_code", { pattern: "x" }, false), "Searching code for “x”"],
    [lineEvent("run_command", { command: "pytest" }, false), "Running pytest"],
    [lineEvent("list_directory", {}, false), "Listing ."],
  ])("%#", (event, text) => {
    expect(describeTool(event)).toBe(text);
  });
});
