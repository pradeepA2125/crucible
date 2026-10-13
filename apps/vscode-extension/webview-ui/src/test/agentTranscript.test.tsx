import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { viewFromDetail } from "../agents";
import { AgentTranscript } from "../components/agents/AgentTranscript";
import { AgentsContext } from "../components/agents/AgentsContext";
import { InlineAgentBox } from "../components/agents/InlineAgentBox";
import type { AgentDetailView, ChatMsg } from "../types";

const AT = "2026-10-01T00:00:00Z";

function detail(status: string, transcript: ChatMsg[], report = ""): AgentDetailView {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "general-purpose", label: "impl",
    status, now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null,
    reportPreview: "", prompt: "Implement the limiter in api/limiter.py", report,
    filesChanged: [], staleRefusals: 0, transcript, lastSeq: 0,
  };
}

function withView(d: AgentDetailView, live = false) {
  const view = viewFromDetail(d);
  return {
    agents: {}, expanded: new Set<string>(), toggleExpanded: () => {}, openWindow: () => {},
    views: { "agent-a": live ? { ...view, live: [{ id: 1, tool: "read_file", args: {}, source: "execution" as const, done: false }] } : view },
  };
}

describe("AgentTranscript", () => {
  it("shows the task, the transcript and the full report", () => {
    const transcript: ChatMsg[] = [
      { role: "agent", content: "Added TokenBucket; wiring next.", type: "text", timestamp: AT, metadata: { progress: true } },
      { role: "agent", content: "All done:\n- limiter added", type: "text", timestamp: AT, metadata: { report: true, status: "completed" } },
    ];
    render(<AgentsContext.Provider value={withView(detail("completed", transcript))}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("TASK FROM PARENT")).toBeInTheDocument();
    expect(screen.getByText("Implement the limiter in api/limiter.py")).toBeInTheDocument();
    expect(screen.getByText("Added TokenBucket; wiring next.")).toBeInTheDocument();
    expect(screen.getByText("REPORT")).toBeInTheDocument();
    expect(screen.getByText("limiter added")).toBeInTheDocument();
  });

  it("falls back to the detail's report when the transcript has none", () => {
    render(<AgentsContext.Provider value={withView(detail("failed", [], "Status: failed — boom"))}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("REPORT · failed")).toBeInTheDocument();
    expect(screen.getByText("Status: failed — boom")).toBeInTheDocument();
  });

  it("renders live steps and a loading state", () => {
    const { rerender } = render(<AgentsContext.Provider value={withView(detail("running", []), true)}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByTestId("transient-text")).toHaveTextContent("Reading");
    expect(screen.queryByRole("button", { name: /^Reading/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show current step" }));
    expect(screen.getByRole("button", { name: /^Reading/ })).toBeInTheDocument();
    rerender(<AgentsContext.Provider value={{ ...withView(detail("running", [])), views: {} }}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("folds completed child work into one summary and keeps failed outputs accessible", () => {
    const tools = [
      { id: 1, tool: "read_file", args: { path: "a.py" }, source: "execution" as const, done: true },
      { id: 2, tool: "read_file", args: { path: "b.py" }, source: "execution" as const, done: true },
      { id: 3, tool: "run_command", args: { command: "pytest" }, source: "execution" as const, done: true, isError: true, output: "One assertion failed." },
    ];
    const transcript: ChatMsg[] = tools.map(event => ({ role: "agent", type: "text", content: "", timestamp: AT, metadata: { tool_events: [event] } }));
    transcript.push({ role: "agent", type: "text", content: "Fixed the limiter.", timestamp: AT, metadata: { report: true, status: "completed" } });
    render(<AgentsContext.Provider value={withView(detail("completed", transcript, "Fixed the limiter."))}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    const summary = screen.getByRole("button", { name: "Read 2 files, ran a command · 1 failed" });
    expect(summary).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Read a.py" })).toBeNull();
    expect(screen.getAllByText("Fixed the limiter.")).toHaveLength(1);
    fireEvent.click(summary);
    expect(screen.getByRole("button", { name: "Read a.py" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ran pytest failed" }));
    expect(screen.getByText("One assertion failed.")).toBeInTheDocument();
  });

  it("keeps live completion grouped and preserves its expansion when sealed into history", () => {
    const ui = withView(detail("running", []));
    const done = { id: 1, tool: "read_file", args: { path: "a.py" }, source: "execution" as const, done: true };
    const pending = { id: 2, tool: "run_command", args: { command: "pytest" }, source: "execution" as const, done: false };
    const view = ui.views["agent-a"];
    const { rerender } = render(<AgentsContext.Provider value={{ ...ui, views: { "agent-a": { ...view, live: [done, pending] } } }}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    const summary = screen.getByRole("button", { name: "Read a file" });
    fireEvent.click(summary);
    expect(screen.getByTestId("transient-text")).toHaveTextContent("Running pytest");
    expect(screen.queryByRole("button", { name: "Running pytest" })).toBeNull();
    const sealed: ChatMsg = { role: "agent", type: "text", content: "", timestamp: AT, metadata: { tool_events: [done, { ...pending, done: true }] } };
    rerender(<AgentsContext.Provider value={{ ...ui, views: { "agent-a": { ...view, messages: [sealed], live: [] } } }}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getAllByTestId("activity-group")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Read a file, ran a command" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Ran pytest" })).toBeInTheDocument();
    expect(screen.queryByTestId("transient-activity")).toBeNull();
  });

  it("keeps narration and activation boundaries between summaries", () => {
    const event = { id: 1, tool: "read_file", args: { path: "a.py" }, source: "execution", done: true };
    const work = { role: "agent", type: "text", content: "", timestamp: AT, metadata: { tool_events: [event] } } as ChatMsg;
    const transcript: ChatMsg[] = [work,
      { role: "agent", type: "text", content: "Found the limiter.", timestamp: AT, metadata: { progress: true } },
      { ...work, metadata: { tool_events: [{ ...event, id: 2, args: { path: "b.py" } }] } },
      { role: "agent", type: "text", content: "↩ Woken for a follow-up", timestamp: AT, metadata: { divider: true, activation: 2 } }, work];
    render(<AgentsContext.Provider value={withView(detail("running", transcript))}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getAllByTestId("activity-group")).toHaveLength(3);
    expect(screen.getByText("Found the limiter.")).toBeInTheDocument();
    expect(screen.getByText("↩ Woken for a follow-up")).toBeInTheDocument();
  });

  it("keeps a stopped child's unfinished call inspectable without an active-work line", () => {
    const ui = withView(detail("stopped", [], "Stopped before the read completed."), true);
    render(<AgentsContext.Provider value={ui}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.queryByTestId("transient-activity")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tool activity" }));
    expect(screen.getByRole("button", { name: "Reading" })).toBeInTheDocument();
    expect(screen.getByText("Stopped before the read completed.")).toBeInTheDocument();
  });

  it("the inline box is a bounded, contained scroller", () => {
    render(<AgentsContext.Provider value={withView(detail("running", []))}><InlineAgentBox agentId="agent-a" /></AgentsContext.Provider>);
    const box = screen.getByTestId("agent-box-agent-a");
    expect(box.style.height).toBe("min(240px, 40vh)");
    expect(box.style.overscrollBehavior).toBe("contain");
  });
});
