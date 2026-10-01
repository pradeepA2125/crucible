import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentRosterCard, statusLine } from "../components/agents/AgentRosterCard";
import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { MessageRow } from "../components/MessageRow";
import type { AgentSummaryView } from "../types";

function agent(over: Partial<AgentSummaryView>): AgentSummaryView {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status: "running", now: "read_file api/a.py", toolCount: 7, filesChangedCount: 1,
    startedAt: "2026-10-01T00:00:00Z", endedAt: "2026-10-01T00:01:12Z", reportPreview: "",
    ...over,
  };
}

function ui(agents: AgentSummaryView[], over: Partial<AgentsUi> = {}): AgentsUi {
  return {
    agents: Object.fromEntries(agents.map((a) => [a.agentId, a])), views: {},
    expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(), ...over,
  };
}

describe("AgentRosterCard", () => {
  const rows = [
    agent({ agentId: "agent-a", label: "middleware survey", status: "completed", reportPreview: "4 hooks found\nmore" }),
    agent({ agentId: "agent-b", label: "limiter impl", name: "general-purpose", status: "running" }),
    agent({ agentId: "agent-c", label: "docs", name: "general-purpose", status: "waiting" }),
  ];

  it("shows the count, the status tags and one row per agent", () => {
    render(<AgentsContext.Provider value={ui(rows)}><AgentRosterCard agentIds={["agent-a", "agent-b", "agent-c"]} /></AgentsContext.Provider>);
    expect(screen.getByText("3 agents")).toBeInTheDocument();
    expect(screen.getByText("1 done")).toBeInTheDocument();
    expect(screen.getByText("1 running")).toBeInTheDocument();
    expect(screen.getByText("1 waiting")).toBeInTheDocument();
    expect(screen.getByText("✓ reported: 4 hooks found")).toBeInTheDocument();
    expect(screen.getByText("read_file api/a.py")).toBeInTheDocument();
    expect(screen.getByText("⏸ needs your approval ↓")).toBeInTheDocument();
    expect(screen.getAllByText("7 tools · 1 file")).toHaveLength(3);
  });

  it("▸ toggles the inline box and ⤢ opens the window with the siblings", () => {
    const value = ui(rows);
    render(<AgentsContext.Provider value={value}><AgentRosterCard agentIds={["agent-a", "agent-b"]} /></AgentsContext.Provider>);
    fireEvent.click(screen.getByRole("button", { name: "Expand limiter impl" }));
    expect(value.toggleExpanded).toHaveBeenCalledWith("agent-b");
    fireEvent.click(screen.getByRole("button", { name: "Open limiter impl in a window" }));
    expect(value.openWindow).toHaveBeenCalledWith("agent-b", ["agent-a", "agent-b"]);
  });

  it("renders a placeholder row for an agent whose summary has not arrived", () => {
    render(<AgentsContext.Provider value={ui([])}><AgentRosterCard agentIds={["agent-x"]} /></AgentsContext.Provider>);
    expect(screen.getByText("1 agent")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("MessageRow renders the roster for an agent_dispatch message", () => {
    render(<AgentsContext.Provider value={ui(rows)}><MessageRow msg={{
      role: "agent", content: "", type: "agent_dispatch", timestamp: "t",
      metadata: { agent_ids: ["agent-a"] } }} /></AgentsContext.Provider>);
    expect(screen.getByTestId("agent-roster")).toBeInTheDocument();
  });
});

describe("statusLine", () => {
  it("covers each status", () => {
    expect(statusLine(agent({ status: "queued" }))).toBe("queued");
    expect(statusLine(agent({ status: "failed", reportPreview: "boom" }))).toBe("✗ boom");
    expect(statusLine(agent({ status: "stopped" }))).toBe("■ stopped");
    expect(statusLine(agent({ status: "running", now: "" }))).toBe("working…");
  });
});
