import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { MessageRow } from "../components/MessageRow";
import { TeamCard } from "../components/teams/TeamCard";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentSummaryView, TeamSummaryView } from "../types";

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "alice", agentId: "agent-a", status: "running" },
            { label: "bob", agentId: "agent-b", status: "awaiting_peer" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
};

function agent(id: string, label: string, status: string): AgentSummaryView {
  return { agentId: id, parentAgentId: null, depth: 1, name: "general-purpose", label, status,
    now: "read_file api/a.py", toolCount: 2, filesChangedCount: 0,
    startedAt: "2026-10-05T00:00:00Z", endedAt: null, reportPreview: "",
    activationStartedAt: "2026-10-05T00:00:00Z", activationEndedAt: null };
}

function wrap(node: ReactNode, teams: Partial<TeamsUi> = {}) {
  const agentsUi: AgentsUi = {
    agents: { "agent-a": agent("agent-a", "alice", "running"),
              "agent-b": agent("agent-b", "bob", "awaiting_peer") },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, views: {}, openTeam: vi.fn(), ...teams };
  return { teamsUi, ...render(
    <AgentsContext.Provider value={agentsUi}>
      <TeamsContext.Provider value={teamsUi}>{node}</TeamsContext.Provider>
    </AgentsContext.Provider>) };
}

describe("TeamCard", () => {
  it("shows name, phase and round, the waiting line, member rows and the budget", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-a", "agent-b"]} />);
    expect(screen.getByText("auth")).toBeInTheDocument();
    expect(screen.getByText("deliberating · round 1 of 3")).toBeInTheDocument();
    expect(screen.getByText(/^waiting on alice/)).toBeInTheDocument();
    expect(screen.getByText("⏳ waiting on a teammate")).toBeInTheDocument();
    expect(screen.getByText("budget 160 requests")).toBeInTheDocument();
  });

  it("Open board opens the team window", () => {
    const { teamsUi } = wrap(<TeamCard teamId="team-1" agentIds={["agent-a", "agent-b"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open board" }));
    expect(teamsUi.openTeam).toHaveBeenCalledWith("team-1");
  });

  it("shows a paused team's reason, and an ended team without the waiting line", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-a"]} />, {
      teams: { "team-1": { ...TEAM, phase: "DISBANDED", pausedReason: "budget reached" } } });
    expect(screen.getByText("disbanded · round 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("budget reached")).toBeInTheDocument();
    expect(screen.queryByText(/^waiting on/)).toBeNull();
  });

  it("MessageRow renders the card for a team_created message, before summaries load", () => {
    wrap(<MessageRow msg={{ role: "agent", content: "", type: "team_created",
      timestamp: "2026-10-05T00:00:00Z",
      metadata: { team_id: "team-9", name: "cache", agent_ids: ["agent-a"] } }} />, { teams: {} });
    expect(screen.getByText("cache")).toBeInTheDocument();   // from the message's metadata
  });
});
