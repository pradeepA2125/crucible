import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { beatText } from "../components/teams/Journey";
import { AdoptedCard, RoundStrip, Verdict, heldText } from "../components/teams/RoundItems";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentSummaryView, TeamPostView, TeamSummaryView } from "../types";

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 2, maxRounds: 3,
  pausedReason: null, openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "",
  members: [{ label: "alice", agentId: "agent-a", status: "completed" },
            { label: "bob", agentId: "agent-b", status: "running" }],
};
const row = (id: string, label: string, status: string): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "gp", label, status, now: "", toolCount: 0,
  filesChangedCount: 0, startedAt: new Date(Date.now() - 60_000).toISOString(), endedAt: null,
  reportPreview: "", activationStartedAt: new Date(Date.now() - 42_000).toISOString(),
  activationEndedAt: null,
});

function wrap(node: ReactNode) {
  const agentsUi: AgentsUi = {
    agents: { "agent-a": row("agent-a", "alice", "completed"), "agent-b": row("agent-b", "bob", "running") },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, views: {}, openTeam: vi.fn() };
  return render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>{node}</TeamsContext.Provider></AgentsContext.Provider>);
}

describe("round items", () => {
  it("a running round: who woke, what each was handed, progress and who it waits on", () => {
    wrap(<RoundStrip teamId="team-1" roster={["alice", "bob"]} item={{
      kind: "round", key: "a1", at: "2026-10-06T10:00:00Z", round: 2, ended: false, members: [
        { label: "alice", handed: [2, 5], reportedAt: "2026-10-06T10:03:00Z", status: "completed" },
        { label: "bob", handed: [3], reportedAt: null, status: null }] }} />);
    const strip = screen.getByTestId("round-2");
    expect(strip).toHaveTextContent("Round 2 started — both members woke");
    expect(strip).toHaveTextContent("#2");
    expect(strip).toHaveTextContent("✓ reported");
    expect(strip).toHaveTextContent(/1 of 2 reported · waiting on bob \(4\ds\)/);
  });

  it("verdicts: not adopted with the next round, then adopted", () => {
    const { rerender } = wrap(<Verdict roster={["alice", "bob"]} item={{
      kind: "verdict", key: "a2", at: "x", round: 1, adopted: null, nextRound: 2, newPosts: 3,
      proposals: [{ id: "P1", stances: { alice: "agree", bob: "object" }, adopted: false }] }} />);
    expect(screen.getByTestId("verdict-1")).toHaveTextContent(
      "Round 1 ended · P1: alice ✓ bob ✗ — not adopted · round 2 next, with 3 new posts");
    rerender(<Verdict roster={["alice", "bob"]} item={{
      kind: "verdict", key: "a3", at: "x", round: 2, adopted: "P1", nextRound: null, newPosts: 0,
      proposals: [{ id: "P1", stances: { alice: "agree", bob: "agree" }, adopted: true }] }} />);
    expect(screen.getByTestId("verdict-2")).toHaveTextContent("Round 2 ended · P1: alice ✓ bob ✓ — adopted");
  });

  it("the adopted card lists assignments", () => {
    const post: TeamPostView = { teamId: "t", seq: 9, author: "system", kind: "system", recipient: null,
      text: "Adopted P1.", mentions: [], refId: null, round: null, closed: null, createdAt: "x",
      payload: { adopted: "P1", assignments: [{ member: "alice", part: "api", files: ["a.py"] }],
                 shared_files: ["c.py"] } };
    wrap(<AdoptedCard post={post} roster={["alice", "bob"]} />);
    const card = screen.getByTestId("adopted-p9");
    expect(card).toHaveTextContent("Adopted P1");
    expect(card).toHaveTextContent("alice → api");
    expect(card).toHaveTextContent("a.py");
    expect(card).toHaveTextContent("shared: c.py");
  });

  it("held footers and the new beats", () => {
    expect(heldText({ for: ["bob"], everyone: false, untilRound: 2 })).toBe(
      "for bob · held for round 2 — nothing reaches a member mid-round");
    expect(heldText({ for: ["alice", "bob"], everyone: true, untilRound: 3 })).toBe(
      "for everyone · held for round 3");
    const ev = (kind: string, payload: Record<string, unknown>) => ({ teamId: "t", aseq: 1, at: "x",
      label: "bob", kind, activation: null, causeSeq: null, payload });
    expect(beatText(ev("requeued", { retry: 1, of: 2 })).text).toBe(
      "bob restarted after a provider error (retry 1 of 2)");
    expect(beatText(ev("deadline", {})).text).toBe(
      "bob hit the round's time limit — reporting what it has");
  });
});
