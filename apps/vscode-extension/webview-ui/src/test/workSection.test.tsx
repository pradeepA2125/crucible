import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TranscriptItems } from "../components/shared/TranscriptItems";
import type { ChatMsg, ToolEventView } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const timestamp = "2026-10-14T00:00:00Z";
const user: ChatMsg = { role: "user", id: "request", content: "fix", type: "text", timestamp, metadata: {} };
const note = (content: string): ChatMsg => ({ role: "agent", type: "text", content, timestamp, metadata: { progress: true } });
const tools = (path: string): ChatMsg => ({ role: "agent", content: "", type: "text", timestamp,
  metadata: { tool_events: [{ id: 1, tool: "read_file", args: { path }, source: "explore", done: true }] } });

describe("turn work disclosure", () => {
  it("shows progress as prose and collapses tools independently between notes", () => {
    render(<TranscriptItems messages={[user, note("Checking the source."), tools("a.py"), tools("b.py"), note("Found the cause."), tools("c.py")]}
      turnActive renderWorkTail={() => <div>Thinking</div>} />);
    const work = screen.getByTestId("work-detail");
    expect(screen.getByRole("button", { name: "Working" })).toHaveAttribute("aria-expanded", "true");
    expect(work).toBeVisible();
    expect(within(work).getByText("Checking the source.").tagName).toBe("P");
    expect(within(work).getByText("Found the cause.").tagName).toBe("P");
    expect(within(work).getAllByTestId("activity-group")).toHaveLength(2);
    const first = within(work).getByRole("button", { name: "Read 2 files" });
    const second = within(work).getByRole("button", { name: "Read a file" });
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(second).toHaveAttribute("aria-expanded", "false");
    expect([...work.children].map(e => e.textContent)).toEqual([
      "Checking the source.Copy", "Read 2 files", "Found the cause.Copy", "Read a file", "Thinking",
    ]);
    fireEvent.click(first);
    expect(screen.getByRole("button", { name: "Read a.py" })).toBeVisible();
    expect(second).toHaveAttribute("aria-expanded", "false");
  });

  it("folds the completed turn, leaving the submit summary and changes outside", () => {
    const { rerender } = render(<TranscriptItems messages={[user, note("Checking."), tools("a.py")]} turnActive />);
    const done: ChatMsg = { role: "agent", content: "Submitted the fix.", type: "diff_summary", timestamp, metadata: {} };
    const applied: ChatMsg = { role: "agent", content: "", type: "diff_card", timestamp,
      metadata: { resolved: "applied", diff_entries: [{ path: "a.py", additions: 2, deletions: 1 }] } };
    rerender(<TranscriptItems messages={[user, note("Checking."), tools("a.py"), applied, done]} />);
    expect(screen.getByRole("button", { name: "Worked" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Checking.")).not.toBeVisible();
    expect(screen.getByText("Submitted the fix.")).toBeVisible();
    expect(screen.getByTestId("turn-changes")).toBeVisible();
    expect(screen.getByTestId("work-section")).not.toContainElement(screen.getByText("Submitted the fix."));
  });

  it("preserves inspected tools through closing, reopening and completion", () => {
    const messages = [user, note("Checking."), tools("a.py")];
    const { rerender } = render(<TranscriptItems messages={messages} turnActive />);
    fireEvent.click(screen.getByRole("button", { name: "Read a file" }));
    fireEvent.click(screen.getByRole("button", { name: "Working" }));
    expect(screen.getByText("Checking.")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Working" }));
    expect(screen.getByRole("button", { name: "Read a.py" })).toBeVisible();
    rerender(<TranscriptItems messages={[...messages, { ...note("Done."), metadata: {} }]} />);
    expect(screen.getByRole("button", { name: "Worked" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Read a.py" })).toBeVisible();
  });

  it("keeps each turn and child activation separate, and reports visible", () => {
    const divider = { ...note("Woken again"), metadata: { divider: true, activation: 2 } };
    render(<TranscriptItems messages={[user, note("First note"), tools("a.py"), { ...note("First answer"), metadata: {} },
      { ...user, id: "second", content: "continue" }, note("Second note"), divider, note("New activation"),
      { ...note("Report"), metadata: { report: true } }]} />);
    expect(screen.getAllByRole("button", { name: "Worked" })).toHaveLength(3);
    expect(screen.getByText("First answer")).toBeVisible();
    expect(screen.getByText("Woken again")).toBeVisible();
    expect(screen.getByText("Report")).toBeVisible();
  });

  it("keeps a streamed answer outside the current work and hides only its status", () => {
    render(<TranscriptItems messages={[user, note("Checking.")]} turnActive
      renderWorkTail={() => <div>Current tool</div>} renderTail={() => <div>Streaming answer</div>} />);
    fireEvent.click(screen.getByRole("button", { name: "Working" }));
    expect(screen.getByText("Current tool")).not.toBeVisible();
    expect(screen.getByText("Streaming answer")).toBeVisible();
  });

  it("retains a deliberate live collapse when a fresh tool batch seals", () => {
    const { rerender } = render(<TranscriptItems messages={[user]} turnActive
      streamingToolEvents={tools("a.py").metadata.tool_events as ToolEventView[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Working" }));
    rerender(<TranscriptItems messages={[user, tools("a.py"), note("Checking next.")]} turnActive />);
    expect(screen.getByRole("button", { name: "Working" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Checking next.")).not.toBeVisible();
  });

  it("keeps plans and unresolved diff controls outside folded work", () => {
    const plan: ChatMsg = { ...note("Proposed plan"), type: "plan_card", metadata: {} };
    const pending: ChatMsg = { ...note(""), type: "diff_card", metadata: {
      diff_entries: [{ path: "a.py", additions: 2, deletions: 0 }],
    } };
    render(<TranscriptItems messages={[user, note("Planning."), tools("a.py"), plan, note("Editing."), pending]} />);
    expect(screen.getByText("Planning.")).not.toBeVisible();
    expect(screen.getByText("Proposed plan")).toBeVisible();
    expect(screen.getByRole("button", { name: /Apply|Accept/i })).toBeVisible();
    expect(screen.getAllByTestId("work-section")).toHaveLength(2);
  });
});
