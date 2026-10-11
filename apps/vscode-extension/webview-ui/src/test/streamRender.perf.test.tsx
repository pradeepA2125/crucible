/**
 * Render cost of streaming into a long thread. Opt-in (CRUCIBLE_WEBVIEW_PERF=1): it prints
 * figures rather than asserting thresholds, since jsdom timings are machine-dependent.
 *
 * Reasoning deltas arrive at ~29/s, each its own postMessage, and every one re-renders the
 * thread; this measures what each of those renders costs as the transcript grows.
 */
import { Profiler, type ProfilerOnRenderCallback } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, vi } from "vitest";
import App from "../App";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const ANSWER = [
  "## Where descriptions come from", "",
  "Tool descriptions are defined on each `ToolDefinition` in `agentd/tools/registry.py`, ",
  "and additive sources contribute their own:", "",
  "| Source | File | Description required |", "|---|---|---|",
  "| Built-in | `agentd/tools/registry.py` | yes |", "| MCP | `agentd/mcp/tool_source.py` | fallback |",
  "| Skills | `agentd/skills/tool_source.py` | yes |", "",
  "```python", "ToolDefinition(name=\"read_file\", description=\"Read a file\", parameters={...})", "```", "",
  "- Parameter descriptions are optional.", "- Tool-level descriptions are always present.",
].join("\n");

function transcript(turns: number) {
  const messages = [];
  for (let i = 0; i < turns; i++) {
    messages.push({ role: "user", type: "text", content: `question ${i} about tools`, timestamp: "t", id: `u${i}` });
    messages.push({
      role: "agent", type: "text", content: ANSWER, timestamp: "t",
      metadata: { tool_events: [
        { id: `p${i}a`, tool: "search_code", args: { pattern: "ToolDefinition" }, source: "execution", output: "--- a.py ---\n  1: x", done: true },
        { id: `p${i}b`, tool: "read_file", args: { path: "agentd/tools/registry.py" }, source: "execution", output: "1: x", done: true },
      ] },
    });
  }
  return messages;
}

function post(data: unknown) {
  act(() => { window.dispatchEvent(new MessageEvent("message", { data })); });
}

async function measure(turns: number) {
  let reactMs = 0;
  let commits = 0;
  const onRender: ProfilerOnRenderCallback = (_id, _phase, actual) => { reactMs += actual; commits += 1; };
  const view = render(<Profiler id="app" onRender={onRender}><App /></Profiler>);
  post({ type: "renderThreadList", activeThreadId: "t1",
         threads: [{ threadId: "t1", title: "Perf thread", createdAt: "t" }] });
  fireEvent.click(screen.getByText("Perf thread"));
  post({ type: "replaceMessages", messages: transcript(turns) });
  post({ type: "setInputEnabled", enabled: false });

  reactMs = 0; commits = 0;
  const started = performance.now();
  for (let i = 0; i < 300; i++) post({ type: "appendThinkingChunk", chunk: "considering the registry " });
  for (let i = 0; i < 60; i++) post({ type: "appendChunk", chunk: "The tool catalog has descriptions. " });
  const wallMs = performance.now() - started;
  view.unmount();
  return { reactMs, commits, wallMs };
}

describe.runIf(process.env.CRUCIBLE_WEBVIEW_PERF === "1")("streaming render cost", () => {
  it("prints per-delta render cost for growing transcripts", async () => {
    for (const turns of [5, 20, 40]) {
      const { reactMs, commits, wallMs } = await measure(turns);
      console.log(`[perf] ${turns * 2} messages: 360 deltas, ${commits} commits, ` +
        `React ${reactMs.toFixed(0)} ms (${(reactMs / 360).toFixed(2)} ms/delta), ` +
        `wall ${wallMs.toFixed(0)} ms (${(wallMs / 360).toFixed(2)} ms/delta)`);
    }
  }, 300_000);
});
