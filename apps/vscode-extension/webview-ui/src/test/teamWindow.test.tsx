import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { ThreadView } from "../components/ThreadView";
import { TeamWindow } from "../components/teams/TeamWindow";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentSummaryView, AppState, TeamActivityView, TeamPostView, TeamSummaryView } from "../types";

let postMessage: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  postMessage = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "review", agentId: "agent-r", status: "completed", name: "explore", description: "Reads code" },
            { label: "impl", agentId: "agent-i", status: "running", name: "general-purpose", description: "Edits code" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: T(0),
};
const post = (seq: number, at: number, over: Partial<TeamPostView>): TeamPostView => ({
  teamId: "team-1", seq, author: "review", kind: "post", recipient: null, text: "", mentions: [],
  refId: null, round: 1, payload: {}, closed: null, createdAt: T(at), ...over,
});
const ev = (aseq: number, at: number, label: string, kind: string,
            over: Partial<TeamActivityView> = {}): TeamActivityView => ({
  teamId: "team-1", aseq, at: T(at), label, kind, activation: 1, causeSeq: null, payload: {}, ...over,
});
const POSTS: TeamPostView[] = [
  post(1, 1, { author: "main", kind: "proposal", round: 0, text: "**Add discount codes** to the cart",
    payload: { assignments: [{ member: "impl", part: "cart", files: ["shop/cart.py"] }] } }),
  post(2, 20, { kind: "object", refId: "P1", text: "invalid codes keep the old discount",
    payload: { evidence: { files: ["shop/cart.py"], line: 14 } } }),
  post(3, 30, { kind: "agree", refId: "P1", text: "fine with the clearing rule" }),
  post(4, 31, { author: "impl", recipient: "review", text: "which file holds the cap?" }),
];
const ACTIVITY: TeamActivityView[] = [
  ev(1, 0, "team", "phase", { activation: null, payload: { phase: "DELIBERATING", round: 1 } }),
  ev(2, 1, "review", "woke", { causeSeq: 1, payload: { cause: "kickoff", by: "main" } }),
  ev(3, 1, "impl", "woke", { causeSeq: 1, payload: { cause: "kickoff", by: "main" } }),
  ev(4, 2, "review", "took_up", { payload: { posts: [1], from: ["main"] } }),
  ev(5, 29, "review", "wrapped_up", { payload: { status: "completed", report: "All claims **verified**.",
    duration_ms: 27000, tools: 12, posts: 0, messages: 0, stances: 2 } }),
  ev(6, 31, "review", "woke", { activation: 2, causeSeq: 4, payload: { cause: "message", by: "impl", post_seq: 4 } }),
];
const agent = (id: string, label: string, status: string): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "general-purpose", label, status,
  now: "read_file shop/cart.py", toolCount: 3, filesChangedCount: 0, startedAt: T(0),
  endedAt: null, reportPreview: "", activationStartedAt: T(25), activationEndedAt: null,
});

function renderWindow(tab = "board", team = TEAM, posts = POSTS) {
  const onTab = vi.fn();
  const onClose = vi.fn();
  const agentsUi: AgentsUi = {
    agents: { "agent-r": agent("agent-r", "review", "completed"), "agent-i": agent("agent-i", "impl", "running") },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": team },
    views: { "team-1": { posts, lastSeq: 4, activity: ACTIVITY, lastAseq: 6 } }, openTeam: vi.fn() };
  render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>
    <TeamWindow teamId="team-1" tab={tab} onTab={onTab} onClose={onClose} />
  </TeamsContext.Provider></AgentsContext.Provider>);
  return { onTab, onClose };
}

describe("TeamWindow board", () => {
  it("labels a closing proposal and its changed files", () => {
    const closing = post(9, 40, { author: "system", kind: "proposal", round: null,
      text: "Implementation complete — verify.",
      payload: { closing: true, cycle: 2, assignments: [], files_changed: { impl: ["a.py"] } } });
    renderWindow("board", { ...TEAM, phase: "REVIEWING" }, [...POSTS, closing]);
    expect(screen.getByText("closing proposal · review cycle 2")).toBeInTheDocument();
    expect(screen.getByTestId("closing-files-9")).toHaveTextContent("impl: a.py");
  });

  it("reads as a journey: chapters, the proposal with markdown, its wake footer and tally", () => {
    renderWindow();
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Kickoff" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Round 1 · deliberating" })).toBeInTheDocument();
    expect(screen.getByText("Add discount codes").tagName).toBe("STRONG");
    expect(screen.getByTestId("footer-p1")).toHaveTextContent("woke review · impl");
    expect(screen.getByRole("button", { name: "review agrees, was objecting" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "impl no stance yet" })).toBeInTheDocument();
  });

  it("stances are replies; a later one says what it replaces", () => {
    renderWindow();
    expect(screen.getByText("objects to")).toBeInTheDocument();
    expect(screen.getByText("replaces #2")).toBeInTheDocument();
    fireEvent.click(screen.getByText("evidence"));
    expect(screen.getByText("shop/cart.py:14")).toBeInTheDocument();
  });

  it("beats and wrap-ups; a report expands as markdown", () => {
    renderWindow();
    expect(screen.getByTestId("beat-a4")).toHaveTextContent("review took up P1 · 1 new post");
    const wrap = screen.getByTestId("wrap-a5");
    expect(wrap).toHaveTextContent("review wrapped up");
    expect(wrap).toHaveTextContent("27s · 12 tools · 2 stances");
    fireEvent.click(screen.getByRole("button", { name: "Show review's report" }));
    expect(screen.getByText("verified").tagName).toBe("STRONG");
  });

  it("a wake whose message is hidden stands alone; Messages shows the message with the footer", () => {
    renderWindow();
    expect(screen.getByTestId("beat-a6")).toHaveTextContent("review woke — direct message from impl #4");
    fireEvent.click(screen.getByRole("button", { name: "Messages" }));
    expect(screen.queryByTestId("beat-a6")).toBeNull();
    expect(screen.getByText("which file holds the cap?")).toBeInTheDocument();
    expect(screen.getByTestId("footer-p4")).toHaveTextContent("woke review");
  });

  it("the now strip shows who is working and who is idle", () => {
    renderWindow();
    const now = screen.getByTestId("now-strip");
    expect(now).toHaveTextContent("impl is working — read_file shop/cart.py");
    expect(now).toHaveTextContent("review is idle");
  });

  it("header: stepper, presence opens a member tab, disband needs a confirm, Esc closes", () => {
    const { onTab, onClose } = renderWindow();
    expect(screen.getByText("Deliberating · round 1 of 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open impl's tab" }));
    expect(onTab).toHaveBeenCalledWith("agent-i");
    fireEvent.click(screen.getByRole("button", { name: "Disband" }));
    expect(postMessage).not.toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm disband" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("an ended team has no Disband and no now strip", () => {
    renderWindow("board", { ...TEAM, phase: "DISBANDED" });
    expect(screen.queryByRole("button", { name: "Disband" })).toBeNull();
    expect(screen.queryByTestId("now-strip")).toBeNull();
  });
});

describe("ThreadView team wiring", () => {
  it("Open board opens the window, reports the open team, and a member tab joins the open agents", () => {
    const state = {
      view: "thread", threads: [], activeThreadId: "t", streaming: null, thinkingStatus: null,
      inputEnabled: true, liveGates: [], livePlan: null, liveReview: null, liveError: null,
      liveTodos: null, liveSessions: null, sessionTranscripts: {}, workbar: null,
      retryStatus: null, tokenProgress: null, editFailure: null, liveStatus: null,
      turnActive: false, turnKind: null, queuedIds: [], agentsRunning: 0, planMode: false, stepReview: true,
      agents: {}, agentViews: {}, teams: { "team-1": TEAM }, teamViews: {},
      messages: [{ role: "agent", content: "", type: "team_created", timestamp: "t",
                   metadata: { team_id: "team-1", name: "auth", agent_ids: ["agent-r", "agent-i"] } }],
    } as AppState;
    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: [] });
    act(() => { screen.getByRole("button", { name: "Open board" }).click(); });
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: ["team-1"] });
    act(() => { screen.getByRole("tab", { name: /review/ }).click(); });
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: ["agent-r"] });
  });
});
