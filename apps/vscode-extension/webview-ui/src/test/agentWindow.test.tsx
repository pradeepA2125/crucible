import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentWindow } from "../components/agents/AgentWindow";
import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { ThreadView } from "../components/ThreadView";
import type { AgentSummaryView, AppState } from "../types";

let postMessage: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  postMessage = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

const row = (id: string, label: string, status: string): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "general-purpose", label, status,
  now: "", toolCount: 7, filesChangedCount: 1, startedAt: "2026-10-01T00:00:00Z",
  endedAt: "2026-10-01T00:01:12Z", reportPreview: "",
});

const ui: AgentsUi = {
  agents: { a: row("a", "limiter", "running"), b: row("b", "docs", "completed") },
  views: {}, expanded: new Set(), toggleExpanded: () => {}, openWindow: () => {},
};

describe("AgentWindow", () => {
  it("shows the agent's meta, stops it, switches tabs and closes on Esc", () => {
    const onSwitch = vi.fn();
    const onClose = vi.fn();
    render(<AgentsContext.Provider value={ui}>
      <AgentWindow agentId="a" siblings={["a", "b"]} onSwitch={onSwitch} onClose={onClose} />
    </AgentsContext.Provider>);
    expect(screen.getByRole("dialog", { name: "Sub-agent limiter" })).toBeInTheDocument();
    expect(screen.getByText("depth 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "■ Stop" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "stopAgent", agentId: "a" });
    fireEvent.click(screen.getByRole("tab", { name: /docs/ }));
    expect(onSwitch).toHaveBeenCalledWith("b");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("a finished agent has no Stop button and a lone agent has no tabs", () => {
    render(<AgentsContext.Provider value={ui}>
      <AgentWindow agentId="b" siblings={["b"]} onSwitch={() => {}} onClose={() => {}} />
    </AgentsContext.Provider>);
    expect(screen.queryByRole("button", { name: "■ Stop" })).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
  });
});

describe("ThreadView sub-agent wiring", () => {
  it("reports the open set to the host when a row is expanded", () => {
    const state = {
      view: "thread", threads: [], activeThreadId: "t", streaming: null, thinkingStatus: null,
      inputEnabled: true, liveGates: [], livePlan: null, liveReview: null, liveError: null,
      liveTodos: null, liveSessions: null, sessionTranscripts: {}, workbar: null,
      retryStatus: null, tokenProgress: null, editFailure: null, liveStatus: null,
      turnActive: false, planMode: false, stepReview: true,
      agents: { a: row("a", "limiter", "running") }, agentViews: {},
      messages: [{ role: "agent", content: "", type: "agent_dispatch", timestamp: "t",
                   metadata: { agent_ids: ["a"] } }],
    } as AppState;
    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: [] });
    act(() => { screen.getByRole("button", { name: "Expand limiter" }).click(); });
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: ["a"] });
    act(() => { screen.getByRole("button", { name: "Open limiter in a window" }).click(); });
    expect(screen.getByRole("dialog", { name: "Sub-agent limiter" })).toBeInTheDocument();
  });
});
