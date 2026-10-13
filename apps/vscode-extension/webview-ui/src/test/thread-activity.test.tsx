import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TranscriptItems } from "../components/ThreadView";
import type { ChatMsg, ToolEventView } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const t = "2026-10-13T00:00:00Z";
const msgs: ChatMsg[] = [
  { role: "user", content: "fix it", type: "text", timestamp: t, metadata: {} },
  { role: "agent", content: "", type: "text", timestamp: t, metadata: { tool_events: [{ id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true }] } },
  { role: "agent", content: "Found the bug.", type: "text", timestamp: t, metadata: { progress: true } },
  { role: "agent", content: "", type: "diff_card", timestamp: t, metadata: { resolved: "applied", diff_entries: [{ path: "a.py", additions: 2, deletions: 1 }] } },
  { role: "agent", content: "Fixed.", type: "text", timestamp: t, metadata: {} },
];

const common = { turnActive: false, queuedIds: [], planVersions: new Map(), onRewindRequest: () => {} };

describe("TranscriptItems", () => {
  it("renders activity lines, narration and the changes card in order", () => {
    render(<TranscriptItems messages={msgs} {...common} />);
    expect(screen.getAllByTestId("activity-group")).toHaveLength(2);
    expect(screen.getByText("Found the bug.")).toBeInTheDocument();
    expect(screen.getByTestId("turn-changes")).toBeInTheDocument();
    expect(screen.getAllByTestId("avatar-spacer").length).toBeGreaterThan(0);
  });

  it("renders the same items for a reloaded copy of the thread", () => {
    const { container: a } = render(<TranscriptItems messages={msgs} {...common} />);
    const { container: b } = render(<TranscriptItems messages={JSON.parse(JSON.stringify(msgs))} {...common} />);
    expect(b.innerHTML).toBe(a.innerHTML);
  });

  it("groups finished live pills, and keeps one group once they are sealed into a message", () => {
    const events: ToolEventView[] = [
      { id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true },
      { id: 2, tool: "read_file", args: { path: "b.py" }, source: "explore", done: true },
    ];
    const user: ChatMsg = { role: "user", content: "go", type: "text", timestamp: t, metadata: {} };
    const { container, rerender } = render(
      <TranscriptItems messages={[user]} streamingToolEvents={events} {...common} />);
    expect(screen.getAllByTestId("activity-group")).toHaveLength(1);
    expect(container.textContent).toContain("Read 2 files");
    const sealed: ChatMsg = { role: "agent", content: "", type: "text", timestamp: t, metadata: { tool_events: events } };
    rerender(<TranscriptItems messages={[user, sealed]} streamingToolEvents={[]} {...common} />);
    expect(screen.getAllByTestId("activity-group")).toHaveLength(1);
    expect(container.textContent).toContain("Read 2 files");
  });

  it("renders an answer with pills as one activity line plus the answer, without a pill track", () => {
    const answer: ChatMsg = { role: "agent", content: "All done.", type: "text", timestamp: t, metadata: {
      tool_events: [{ id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true }] } };
    const user: ChatMsg = { role: "user", content: "go", type: "text", timestamp: t, metadata: {} };
    render(<TranscriptItems messages={[user, answer]} {...common} />);
    expect(screen.getAllByTestId("activity-group")).toHaveLength(1);
    expect(screen.getByText("All done.")).toBeInTheDocument();
    expect(document.querySelector("button[data-event-id]")).toBeNull();
  });
});

describe("TranscriptItems — running turn and avatars", () => {
  const user: ChatMsg = { role: "user", content: "go", type: "text", timestamp: t, metadata: {} };
  const applied: ChatMsg = { role: "agent", content: "", type: "diff_card", timestamp: t, metadata: { resolved: "applied", diff_entries: [{ path: "a.py", additions: 2, deletions: 1 }] } };

  it("holds back the turn's changes card while the turn runs and shows it once it ends", () => {
    const { rerender } = render(<TranscriptItems messages={[user, applied]} {...common} turnActive />);
    expect(screen.queryByTestId("turn-changes")).toBeNull();
    rerender(<TranscriptItems messages={[user, applied]} {...common} turnRunning />);
    expect(screen.queryByTestId("turn-changes")).toBeNull();
    rerender(<TranscriptItems messages={[user, applied]} {...common} />);
    expect(screen.getByTestId("turn-changes")).toBeInTheDocument();
  });

  it("keeps a pending diff card interactive instead of folding it into a group", () => {
    const pending: ChatMsg = { ...applied, metadata: { diff_entries: applied.metadata!.diff_entries } };
    render(<TranscriptItems messages={[user, pending]} {...common} />);
    expect(screen.queryByTestId("activity-group")).toBeNull();
    expect(screen.getByRole("button", { name: /Apply|Accept/i })).toBeInTheDocument();
  });

  it("shows the avatar on narration that follows a full-width card", () => {
    const roster: ChatMsg = { role: "agent", content: "", type: "agent_dispatch", timestamp: t, metadata: { agent_ids: ["a1"] } };
    const note: ChatMsg = { role: "agent", content: "Working on it.", type: "text", timestamp: t, metadata: { progress: true } };
    render(<TranscriptItems messages={[user, roster, note]} {...common} />);
    expect(screen.queryAllByTestId("avatar-spacer")).toHaveLength(0);
  });

  it("tells renderTail whether the last item continues an agent run", () => {
    const seen: boolean[] = [];
    const tail = (c: boolean) => { seen.push(c); return null; };
    render(<TranscriptItems messages={[user]} {...common} renderTail={tail} />);
    render(<TranscriptItems messages={[user, applied]} {...common} turnActive renderTail={tail} />);
    expect(seen).toEqual([false, true]);
    render(<TranscriptItems messages={[user, { ...applied, metadata: { resolved: "applied", diff_entries: [] } }, { role: "agent", content: "hi", type: "text", timestamp: t, metadata: {} }]} {...common} renderTail={tail} />);
    expect(seen[2]).toBe(true);
  });
});
