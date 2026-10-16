import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { TeamStrip } from "../components/teams/TeamStrip";
import { TeamCard } from "../components/teams/TeamCard";
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
  const tree = (nextTeams = teams, nextAgents = agentsUi.agents) => (
    <AgentsContext.Provider value={{ ...agentsUi, agents: nextAgents }}>
      <TeamsContext.Provider value={{ ...teamsUi, teams: nextTeams }}>{node}</TeamsContext.Provider>
    </AgentsContext.Provider>);
  const view = render(tree());
  return { ...teamsUi, update: (nextTeams: Record<string, TeamSummaryView>, nextAgents = agentsUi.agents) =>
    view.rerender(tree(nextTeams, nextAgents)) };
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

  it("expands into the live card, keeps updating and opens the board without minimizing", () => {
    const ui = wrap(<TeamStrip />, { "team-1": TEAM });
    expect(screen.queryByTestId("team-card")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Expand deadlock-check" }));
    expect(screen.getByTestId("team-card")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Minimize deadlock-check" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Minimize deadlock-check" })).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "Open board" }));
    expect(ui.openTeam).toHaveBeenCalledExactlyOnceWith("team-1");
    expect(screen.getByTestId("team-card")).toBeInTheDocument();

    ui.update({ "team-1": { ...TEAM, phase: "FINALIZING", roundProgress: null,
      mode: "deliberate", deliverable: { kind: "diagnosis", description: "Investigate" },
      latest: { kind: "post", label: "impl", text: "Found the cause", at: new Date().toISOString() } } },
      { a: row("a", "review", "completed"), b: { ...row("b", "impl", "running"), now: "Reading source" } });
    expect(screen.getByTestId("team-card")).toHaveTextContent("Finalizing recommendation");
    expect(screen.getByTestId("team-card")).toHaveTextContent("Reading source");
    expect(screen.getByTestId("team-card")).toHaveTextContent("Found the cause");
    fireEvent.click(screen.getByRole("button", { name: "Minimize deadlock-check" }));
    expect(screen.queryByTestId("team-card")).toBeNull();
    expect(screen.getByRole("button", { name: "Expand deadlock-check" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Expand deadlock-check" })).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps teams' expansion independent", () => {
    wrap(<TeamStrip />, { "team-1": TEAM,
      "team-2": { ...TEAM, teamId: "team-2", name: "other" } });
    fireEvent.click(screen.getByRole("button", { name: "Expand deadlock-check" }));
    expect(within(screen.getByTestId("team-strip-team-1")).getByTestId("team-card")).toBeInTheDocument();
    expect(within(screen.getByTestId("team-strip-team-2")).queryByTestId("team-card")).toBeNull();
    expect(screen.getByRole("button", { name: "Expand other" })).toHaveAttribute("aria-expanded", "false");
  });

  it("retains expanded paused work, then removes only the pinned card on Done", () => {
    const ui = wrap(<><TeamCard teamId="team-1" agentIds={["a", "b"]} /><TeamStrip /></>, { "team-1": TEAM });
    fireEvent.click(screen.getByRole("button", { name: "Expand deadlock-check" }));
    expect(screen.getAllByTestId("team-card")).toHaveLength(2);
    ui.update({ "team-1": { ...TEAM, phase: "PAUSED", pausedReason: "budget", roundProgress: null } });
    expect(within(screen.getByTestId("team-strip-team-1")).getByTestId("team-card")).toHaveTextContent("budget");
    ui.update({ "team-1": { ...TEAM, phase: "DONE", roundProgress: null } });
    expect(screen.queryByTestId("team-strip-team-1")).toBeNull();
    expect(screen.getAllByTestId("team-card")).toHaveLength(1);
  });

  it("phase wording", () => {
    expect(teamPhaseText({ ...TEAM, phase: "DELIBERATING", round: 1, maxRounds: 3 })).toBe("Round 1 of 3");
    expect(teamPhaseText({ ...TEAM, phase: "PAUSED", pausedReason: "budget reached" })).toBe("Paused — budget reached");
  });
});

it("keeps a completed result visible while the lead replies without claiming implementation", () => {
  wrap(<TeamStrip />, { "team-1": { ...TEAM, phase: "DONE", roundProgress: null,
    mode: "deliberate", runId: 1, deliverable: { kind: "plan", description: "Propose work" },
    pendingLeadRequestSeq: 12, leadExchangeStatus: "running" } });
  expect(screen.getByTestId("team-strip-team-1")).toHaveTextContent("Plan ready · lead replying");
  expect(screen.getByTestId("team-strip-team-1")).not.toHaveTextContent("Implementing");
});
