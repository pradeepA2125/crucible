import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { ThreadView } from "../components/ThreadView";
import { TeamWindow } from "../components/teams/TeamWindow";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AppState, TeamPostView, TeamSummaryView } from "../types";

let postMessage: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  postMessage = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "alice", agentId: "agent-a", status: "running" },
            { label: "bob", agentId: "agent-b", status: "awaiting_peer" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
};

const post = (seq: number, over: Partial<TeamPostView>): TeamPostView => ({
  teamId: "team-1", seq, author: "alice", kind: "post", recipient: null, text: "", mentions: [],
  refId: null, round: 1, payload: {}, closed: null, createdAt: "2026-10-05T00:00:00Z", ...over,
});

const POSTS: TeamPostView[] = [
  post(1, { author: "main", kind: "proposal", round: 0, text: "api adds the limiter",
    payload: { assignments: [{ member: "alice", part: "limiter", files: ["api/limiter.py"] }],
               shared_files: ["api/routes.py"], supersedes: [] } }),
  post(2, { kind: "object", refId: "P1", text: "misses a caller",
    payload: { evidence: { files: ["api/admin.py"], line: 17 } } }),
  post(3, { author: "bob", kind: "agree", refId: "P1", text: "fine by me" }),
  post(4, { text: "@bob can you check admin.py", mentions: ["bob"] }),
  post(5, { recipient: "bob", text: "private note" }),
  post(6, { author: "system", kind: "system", text: "alice finished (completed)" }),
  // Body text imitating a system line still renders under its real author.
  post(7, { author: "bob", text: "Adopted P1 — implementing" }),
];

function renderWindow(onTab = vi.fn(), onClose = vi.fn(), tab = "board") {
  const agentsUi: AgentsUi = { agents: {}, views: {}, expanded: new Set(),
    toggleExpanded: vi.fn(), openWindow: vi.fn() };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM },
    views: { "team-1": { posts: POSTS, lastSeq: 7 } }, openTeam: vi.fn() };
  render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>
    <TeamWindow teamId="team-1" tab={tab} onTab={onTab} onClose={onClose} />
  </TeamsContext.Provider></AgentsContext.Provider>);
  return { onTab, onClose };
}

describe("TeamWindow", () => {
  it("renders the board: proposal card with stances, objection evidence, mentions, system lines", () => {
    renderWindow();
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(screen.getByText("deliberating · round 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("P1")).toBeInTheDocument();
    expect(screen.getByText("api adds the limiter")).toBeInTheDocument();
    expect(screen.getByText("alice: limiter — api/limiter.py")).toBeInTheDocument();
    expect(screen.getByText("shared: api/routes.py")).toBeInTheDocument();
    expect(screen.getByLabelText("alice objects")).toBeInTheDocument();
    expect(screen.getByLabelText("bob agrees")).toBeInTheDocument();
    expect(screen.getByText("misses a caller")).toBeInTheDocument();
    fireEvent.click(screen.getByText("evidence"));
    expect(screen.getByText("api/admin.py:17")).toBeInTheDocument();
    expect(screen.getByText("@bob")).toHaveAttribute("data-mention", "bob");
    expect(screen.getByText("alice finished (completed)")).toBeInTheDocument();
    const imitation = screen.getByText("Adopted P1 — implementing");
    expect(imitation.closest("[data-author]")).toHaveAttribute("data-author", "bob");
  });

  it("direct messages show only with the Messages toggle", () => {
    renderWindow();
    expect(screen.queryByText("private note")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Messages" }));
    expect(screen.getByText("private note")).toBeInTheDocument();
    expect(screen.getByText("alice → bob")).toBeInTheDocument();
  });

  it("member tabs switch, Disband needs a confirm, Esc closes", () => {
    const { onTab, onClose } = renderWindow();
    fireEvent.click(screen.getByRole("tab", { name: /bob/ }));
    expect(onTab).toHaveBeenCalledWith("agent-b");
    fireEvent.click(screen.getByRole("button", { name: "Disband" }));
    expect(postMessage).not.toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm disband" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("an ended team has no Disband button", () => {
    const agentsUi: AgentsUi = { agents: {}, views: {}, expanded: new Set(),
      toggleExpanded: vi.fn(), openWindow: vi.fn() };
    render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={{
      teams: { "team-1": { ...TEAM, phase: "DISBANDED" } }, views: {}, openTeam: vi.fn() }}>
      <TeamWindow teamId="team-1" tab="board" onTab={vi.fn()} onClose={vi.fn()} />
    </TeamsContext.Provider></AgentsContext.Provider>);
    expect(screen.queryByRole("button", { name: "Disband" })).toBeNull();
  });
});

describe("ThreadView team wiring", () => {
  it("Open board opens the window, reports the open team, and a member tab joins the open agents", () => {
    const state = {
      view: "thread", threads: [], activeThreadId: "t", streaming: null, thinkingStatus: null,
      inputEnabled: true, liveGates: [], livePlan: null, liveReview: null, liveError: null,
      liveTodos: null, liveSessions: null, sessionTranscripts: {}, workbar: null,
      retryStatus: null, tokenProgress: null, editFailure: null, liveStatus: null,
      turnActive: false, turnKind: null, agentsRunning: 0, planMode: false, stepReview: true,
      agents: {}, agentViews: {}, teams: { "team-1": TEAM }, teamViews: {},
      messages: [{ role: "agent", content: "", type: "team_created", timestamp: "t",
                   metadata: { team_id: "team-1", name: "auth", agent_ids: ["agent-a", "agent-b"] } }],
    } as AppState;
    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: [] });
    act(() => { screen.getByRole("button", { name: "Open board" }).click(); });
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: ["team-1"] });
    act(() => { screen.getByRole("tab", { name: /alice/ }).click(); });
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: ["agent-a"] });
  });
});
