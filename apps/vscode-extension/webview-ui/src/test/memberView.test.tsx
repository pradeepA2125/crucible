import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { MemberView } from "../components/teams/MemberView";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentDetailView, ChatMsg, TeamActivityView, TeamPostView, TeamSummaryView } from "../types";

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const msg = (meta: Record<string, unknown>, content = ""): ChatMsg =>
  ({ role: "agent", content, type: "text", timestamp: T(0), metadata: meta });
const ev = (aseq: number, at: number, kind: string, activation: number,
            payload: Record<string, unknown> = {}, causeSeq: number | null = null): TeamActivityView =>
  ({ teamId: "team-1", aseq, at: T(at), label: "review", kind, activation, causeSeq, payload });
const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1, maxRounds: 3,
  pausedReason: null,
  members: [{ label: "review", agentId: "agent-r", status: "running", name: "explore", description: "Reads code and cites path:line" },
            { label: "impl", agentId: "agent-i", status: "completed", name: "general-purpose", description: "Edits" }],
  openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: T(0),
};
const POSTS: TeamPostView[] = [
  { teamId: "team-1", seq: 1, author: "main", kind: "proposal", recipient: null, text: "Plan **one**",
    mentions: [], refId: null, round: 0, payload: {}, closed: null, createdAt: T(0) },
  { teamId: "team-1", seq: 4, author: "review", kind: "object", recipient: null, text: "gap", mentions: [],
    refId: "P1", round: 1, payload: {}, closed: null, createdAt: T(10) },
  { teamId: "team-1", seq: 5, author: "impl", kind: "post", recipient: null, text: "@review see this", mentions: ["review"],
    refId: null, round: 1, payload: {}, closed: null, createdAt: T(25) },
];
const DETAIL = {
  agentId: "agent-r", parentAgentId: null, depth: 1, name: "explore", label: "review", status: "running",
  now: "read_file shop/cart.py", toolCount: 3, filesChangedCount: 0, startedAt: T(0), endedAt: null,
  reportPreview: "", prompt: "Member review", report: "", filesChanged: [], staleRefusals: 0,
  transcript: [], lastSeq: 0,
} as AgentDetailView;

function renderView() {
  const messages = [msg({ tool_events: [{ id: 1, tool: "read_file", args: {}, source: "execution", done: true }] }),
    msg({ report: true, status: "completed" }, "Objected: **missing case**"),
    msg({ divider: true, activation: 2 }, "↩ woken")];
  const agentsUi: AgentsUi = {
    agents: { "agent-r": { ...DETAIL } }, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
    views: { "agent-r": { detail: DETAIL, messages, live: [], callIds: {}, nextId: 1 } },
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, openTeam: vi.fn(), views: { "team-1": {
    posts: POSTS, lastSeq: 5, lastAseq: 7, activity: [
      ev(1, 1, "woke", 1, { cause: "kickoff", by: "main", post_seq: 1 }, 1),
      ev(2, 2, "took_up", 1, { posts: [1], from: ["main"] }),
      ev(3, 20, "wrapped_up", 1, { status: "completed", report: "Objected", duration_ms: 18000, tools: 1 }),
      ev(4, 26, "woke", 2, { cause: "mention", by: "impl", post_seq: 5 }, 5),
      ev(5, 27, "took_up", 2, { posts: [5], from: ["impl"] }),
    ] } } };
  render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>
    <MemberView teamId="team-1" agentId="agent-r" />
  </TeamsContext.Provider></AgentsContext.Provider>);
}

describe("MemberView", () => {
  it("profile: name, role, description, state, stats and stances", () => {
    renderView();
    const profile = screen.getByTestId("member-profile");
    expect(profile).toHaveTextContent("review");
    expect(profile).toHaveTextContent("explore");
    expect(profile).toHaveTextContent("Reads code and cites path:line");
    expect(profile).toHaveTextContent("working");
    expect(profile).toHaveTextContent("2 chapters");
    expect(profile).toHaveTextContent("2 wakes");
    expect(screen.getByText("P1 ✗ objected")).toBeInTheDocument();
  });

  it("chapters: why each started, the latest open with what it was handed", () => {
    renderView();
    expect(screen.getByRole("button", { name: /Chapter 1 · kickoff — main's proposal P1/ })).toBeInTheDocument();
    const two = screen.getByRole("button", { name: /Chapter 2 · woken by impl's post #5/ });
    expect(two).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("handed-2")).toHaveTextContent("#5");
    expect(screen.getByTestId("chapter-2")).toHaveTextContent("working — read_file shop/cart.py");
  });

  it("an earlier chapter is collapsed to its report's first line and opens on click", () => {
    renderView();
    const one = screen.getByRole("button", { name: /Chapter 1/ });
    expect(one).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("chapter-1")).toHaveTextContent("Objected: missing case");
    fireEvent.click(one);
    expect(screen.getByText("missing case").tagName).toBe("STRONG");
    expect(screen.getByTestId("chapter-1")).toHaveTextContent("objects to");
  });
});
