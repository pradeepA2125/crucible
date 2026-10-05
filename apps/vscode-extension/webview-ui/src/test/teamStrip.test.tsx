import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { TeamStrip } from "../components/teams/TeamStrip";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import { teamPhaseText } from "../teams";
import type { AgentSummaryView, TeamSummaryView } from "../types";

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "deadlock-check", goal: "g", phase: "DELIBERATING", round: 2, maxRounds: 2,
  pausedReason: null, openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "",
  members: [{ label: "review", agentId: "a", status: "completed" },
            { label: "impl", agentId: "b", status: "running" }],
  roundProgress: { round: 2, members: ["review", "impl"], reported: ["review"] },
};
const row = (id: string, label: string, status: string): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "gp", label, status, now: "", toolCount: 0,
  filesChangedCount: 0, startedAt: null, endedAt: null, reportPreview: "",
  activationStartedAt: new Date(Date.now() - 12_000).toISOString(), activationEndedAt: null,
});

function wrap(node: ReactNode, teams: Record<string, TeamSummaryView>) {
  const agentsUi: AgentsUi = {
    agents: { a: row("a", "review", "completed"), b: row("b", "impl", "running") },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams, views: {}, openTeam: vi.fn() };
  render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>{node}</TeamsContext.Provider></AgentsContext.Provider>);
  return teamsUi;
}

describe("the pinned team strip", () => {
  it("shows a live team's progress and opens its board", () => {
    const teamsUi = wrap(<TeamStrip />, { "team-1": TEAM });
    const strip = screen.getByTestId("team-strip-team-1");
    expect(strip).toHaveTextContent("deadlock-check");
    expect(strip).toHaveTextContent(/Round 2 · 1 of 2 reported · waiting on impl \(1\ds\)/);
    fireEvent.click(screen.getByRole("button", { name: "Open board of deadlock-check" }));
    expect(teamsUi.openTeam).toHaveBeenCalledWith("team-1");
  });

  it("names the phase outside a round, and hides ended teams", () => {
    wrap(<TeamStrip />, {
      "team-1": { ...TEAM, phase: "DEADLOCKED", roundProgress: null },
      "team-2": { ...TEAM, teamId: "team-2", name: "old", phase: "DONE" } });
    expect(screen.getByTestId("team-strip-team-1")).toHaveTextContent("Deadlocked — waiting on the main agent");
    expect(screen.queryByTestId("team-strip-team-2")).toBeNull();
  });

  it("renders nothing without a live team", () => {
    const { container } = render(<TeamStrip />);
    expect(container).toBeEmptyDOMElement();
  });

  it("phase wording", () => {
    expect(teamPhaseText({ ...TEAM, phase: "DELIBERATING", round: 1, maxRounds: 3 })).toBe("Round 1 of 3");
    expect(teamPhaseText({ ...TEAM, phase: "PAUSED", pausedReason: "budget reached" })).toBe("Paused — budget reached");
  });
});
