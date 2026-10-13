import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentWindow } from "../components/agents/AgentWindow";
import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { ThreadView } from "../components/ThreadView";
import { viewFromDetail } from "../agents";
import type { AgentDetailView, AgentSummaryView, AppState } from "../types";

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
    fireEvent.keyDown(document, { key: "Escape" });
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

describe("Sub-agent windows above team members", () => {
  function fixture() {
    const parent: AgentDetailView = { ...row("parent", "review", "running"), prompt: "Review the code", report: "", filesChanged: [], staleRefusals: 0, lastSeq: 0,
      transcript: [{ role: "agent" as const, content: "", type: "agent_dispatch", timestamp: "", metadata: { agent_ids: ["helper"] } }] };
    const helper: AgentDetailView = { ...row("helper", "helper", "running"), parentAgentId: "parent", depth: 2, prompt: "Check the tests", report: "", filesChanged: [], staleRefusals: 0, lastSeq: 0,
      transcript: [{ role: "agent" as const, content: "", type: "team_created", timestamp: "", metadata: { team_id: "audit", agent_ids: [], name: "audit" } }] };
    const team = { teamId: "team", name: "review team", goal: "Review code", phase: "DELIBERATING", round: 1, maxRounds: 3, pausedReason: null,
      members: [{ label: "review", agentId: "parent", status: "running" }], openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "" };
    const state = {
      view: "thread", threads: [], activeThreadId: "t", streaming: null, thinkingStatus: null, inputEnabled: true,
      liveGates: [], livePlan: null, liveReview: null, liveError: null, contextUsage: null, contextUsageStale: false,
      liveTodos: null, providerAccess: null, threadUsage: null, liveSessions: null, sessionTranscripts: {}, workbar: null,
      retryStatus: null, tokenProgress: null, editFailure: null, liveStatus: null, turnActive: false, turnKind: null,
      queuedIds: [], agentsRunning: 0, planMode: false, stepReview: true,
      agents: { parent, helper }, agentViews: { parent: viewFromDetail(parent), helper: viewFromDetail(helper) },
      teams: { team, audit: { ...team, teamId: "audit", name: "audit", members: [] } }, teamViews: {},
      messages: [{ role: "agent", content: "", type: "team_created", timestamp: "", metadata: { team_id: "team", agent_ids: ["parent"] } }],
    } as AppState;
    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Open board" }));
    fireEvent.click(screen.getByRole("tab", { name: "review" }));
    const opener = screen.getByRole("button", { name: "Open helper in a window" });
    opener.focus(); fireEvent.click(opener);
    return { opener };
  }

  it("puts the child above its retained team and Escape closes only the child", () => {
    const { opener } = fixture();
    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs.map(dialog => dialog.getAttribute("aria-label"))).toEqual(["Team review team", "Sub-agent helper"]);
    expect(screen.getByRole("button", { name: "Close agent window" })).toHaveFocus();
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: ["helper", "parent"] });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Sub-agent helper" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Team review team" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "review" })).toHaveAttribute("aria-selected", "true");
    expect(opener).toHaveFocus();
    expect(postMessage).toHaveBeenLastCalledWith({ type: "setOpenAgents", agentIds: ["parent"] });
  });

  it("traps keyboard focus in the foreground child and leaves the team open when closing", () => {
    fixture();
    const child = screen.getByRole("dialog", { name: "Sub-agent helper" });
    const last = within(child).getByRole("button", { name: "Open board" });
    last.focus(); fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Close agent window" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Close agent window" }));
    expect(screen.getByRole("dialog", { name: "Team review team" })).toBeInTheDocument();
  });

  it("shows a board explicitly opened from the child instead of hiding it behind the child", () => {
    fixture();
    const child = screen.getByRole("dialog", { name: "Sub-agent helper" });
    fireEvent.click(within(child).getByRole("button", { name: "Open board" }));
    expect(screen.queryByRole("dialog", { name: "Sub-agent helper" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Team audit" })).toBeInTheDocument();
  });
});

describe("ThreadView sub-agent wiring", () => {
  it("reports the open set to the host when a row is expanded", () => {
    const state = {
      view: "thread", threads: [], activeThreadId: "t", streaming: null, thinkingStatus: null,
      inputEnabled: true, liveGates: [], livePlan: null, liveReview: null, liveError: null,
      contextUsage: null, contextUsageStale: false,
      liveTodos: null, providerAccess: null, threadUsage: null, liveSessions: null, sessionTranscripts: {}, workbar: null,
      retryStatus: null, tokenProgress: null, editFailure: null, liveStatus: null,
      turnActive: false, turnKind: null, queuedIds: [], agentsRunning: 0, planMode: false, stepReview: true,
      agents: { a: row("a", "limiter", "running") }, agentViews: {}, teams: {}, teamViews: {},
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
