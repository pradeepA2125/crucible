import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { MessageRow } from "../components/MessageRow";
import { PhaseStepper } from "../components/teams/PhaseStepper";
import { TeamCard } from "../components/teams/TeamCard";
import { TeamLinks } from "../components/teams/TeamLinks";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentSummaryView, TeamSummaryView } from "../types";

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "discount-feature", goal: "g", phase: "DELIBERATING", round: 1, maxRounds: 3,
  pausedReason: null,
  members: [
    { label: "review", agentId: "agent-r", status: "completed", name: "explore",
      last: { kind: "wrapped_up", at: new Date(Date.now() - 120_000).toISOString(), causeSeq: null, by: null, status: "completed", activation: 1 } },
    { label: "impl", agentId: "agent-i", status: "running", name: "general-purpose", last: null },
  ],
  openProposals: [], usage: { requests: 0, budget: 60 }, createdAt: "2026-10-05T00:00:00Z",
  latest: { kind: "post", label: "review", text: "confirmed the 50% cap", at: new Date().toISOString() },
  counts: { posts: 9, proposals: [{ id: "P1", agree: 2, object: 0, pending: 0 }] },
};
const row = (id: string, label: string, status: string, tools: number, acts: number): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "x", label, status, now: "read_file shop/discounts.py",
  toolCount: tools, filesChangedCount: 0, startedAt: new Date(Date.now() - 60_000).toISOString(), endedAt: null,
  reportPreview: "", activationCount: acts, activationStartedAt: new Date(Date.now() - 32_000).toISOString(),
  activationEndedAt: null,
});

function wrap(node: ReactNode, teams: Partial<TeamsUi> = {}) {
  const agentsUi: AgentsUi = {
    agents: { "agent-r": row("agent-r", "review", "completed", 14, 2), "agent-i": row("agent-i", "impl", "running", 7, 3) },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, views: {}, openTeam: vi.fn(), ...teams };
  return { teamsUi, ...render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>{node}</TeamsContext.Provider></AgentsContext.Provider>) };
}

describe("TeamCard", () => {
  it("shows the stepper, a state row per member, the latest line and counts", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />);
    const card = screen.getByTestId("team-card");
    expect(card).toHaveTextContent("discount-feature");
    expect(card).toHaveTextContent("Deliberating · round 1 of 3");
    expect(screen.getByTestId("member-row-review")).toHaveTextContent(/✓ reported · 2m0\ds ago/);
    expect(screen.getByTestId("member-row-review")).toHaveTextContent("2 ch · 14 tools");
    expect(screen.getByTestId("member-row-impl")).toHaveTextContent("● working · read_file shop/discounts.py");
    expect(card).toHaveTextContent("review posted: confirmed the 50% cap");
    expect(card).toHaveTextContent("9 posts · P1 2 ✓ · budget 60 requests");
  });

  it("shows the team's token total once usage loads", () => {
    const total = { requests: 31, input: 690_000, output: 12_000, cached: 345_000 };
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />, {
      usage: { total, main: null, agents: {}, teams: { "team-1": { total, members: {} } } } });
    expect(screen.getByTestId("team-card-usage")).toHaveTextContent("↑690k ↓12k · 31 req · 50% cached");
  });

  it("Open board opens the team window", () => {
    const { teamsUi } = wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open board" }));
    expect(teamsUi.openTeam).toHaveBeenCalledWith("team-1");
  });

  it("a paused team shows its reason; an ended team shows how it ended", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r"]} />, {
      teams: { "team-1": { ...TEAM, phase: "FAILED", pausedReason: "budget reached" } } });
    expect(screen.getByTestId("team-card")).toHaveTextContent("Failed");
    expect(screen.getByTestId("team-card")).toHaveTextContent("budget reached");
  });

  it("MessageRow renders the card for a team_created message, before summaries load", () => {
    wrap(<MessageRow msg={{ role: "agent", content: "", type: "team_created", timestamp: "2026-10-05T00:00:00Z",
      metadata: { team_id: "team-9", name: "cache", agent_ids: ["agent-r"] } }} />, { teams: {} });
    expect(screen.getByText("cache")).toBeInTheDocument();
  });

  it("shows the round's progress bar while deliberating", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />, {
      teams: { "team-1": { ...TEAM, roundProgress: { round: 1, members: ["review", "impl"], reported: ["review"] } } } });
    expect(screen.getByTestId("round-progress")).toHaveTextContent(/Round 1 · 1 of 2 reported · waiting on impl/);
  });

  it("a deadlocked team reads as such on the stepper", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r"]} />, {
      teams: { "team-1": { ...TEAM, phase: "DEADLOCKED", round: 3 } } });
    expect(screen.getByTestId("team-card")).toHaveTextContent("Deadlocked · round 3 of 3");
  });

  it("an adopted (DONE) team ends its road with the plan", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r"]} />, {
      teams: { "team-1": { ...TEAM, phase: "DONE", endReason: "adopted" } } });
    expect(screen.getByTestId("team-card")).toHaveTextContent("Plan adopted");
    expect(screen.getByTestId("team-card")).not.toHaveTextContent("Implementing");
  });
});

describe("team links and the stepper", () => {
  it("opens an ended team's board from a pill", () => {
    const openTeam = vi.fn();
    render(
      <TeamsContext.Provider value={{ teams: { "team-1": { ...TEAM, phase: "DONE" } },
                                          views: {}, openTeam }}>
        <TeamLinks events={[
          { id: 1, tool: "team_status", args: { team: "team-1" }, source: "execution", done: true },
          { id: 2, tool: "post_board", args: { team: "team-1" }, source: "execution", done: true },
        ]} />
      </TeamsContext.Provider>);
    const buttons = screen.getAllByRole("button", { name: `Open board of ${TEAM.name}` });
    expect(buttons).toHaveLength(1);                         // one link per team
    fireEvent.click(buttons[0]);
    expect(openTeam).toHaveBeenCalledWith("team-1");
  });

  it("shows the implemented road and pauses", () => {
    const { rerender } = render(
      <PhaseStepper phase="DONE" endReason="implemented" round={1} maxRounds={3} />);
    expect(screen.getByText("Implementing")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
    rerender(<PhaseStepper phase="DONE" endReason="adopted" round={1} maxRounds={3} />);
    expect(screen.getByText("Plan adopted")).toBeInTheDocument();
    rerender(<PhaseStepper phase="PAUSED" round={1} maxRounds={3} />);
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });
});
