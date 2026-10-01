import { render, screen } from "@testing-library/react";
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

  it("renders live pills and a loading state", () => {
    const { rerender } = render(<AgentsContext.Provider value={withView(detail("running", []), true)}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("read_file")).toBeInTheDocument();
    rerender(<AgentsContext.Provider value={{ ...withView(detail("running", [])), views: {} }}><AgentTranscript agentId="agent-a" /></AgentsContext.Provider>);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("the inline box is a bounded, contained scroller", () => {
    render(<AgentsContext.Provider value={withView(detail("running", []))}><InlineAgentBox agentId="agent-a" /></AgentsContext.Provider>);
    const box = screen.getByTestId("agent-box-agent-a");
    expect(box.style.height).toBe("min(240px, 40vh)");
    expect(box.style.overscrollBehavior).toBe("contain");
  });
});
