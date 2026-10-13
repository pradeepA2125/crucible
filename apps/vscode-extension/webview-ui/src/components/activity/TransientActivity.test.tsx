import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TransientActivity } from "./TransientActivity";

const bubble = (over: Partial<Parameters<typeof TransientActivity>[0]["bubble"]> = {}) => ({
  text: "", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [], ...over,
});

describe("TransientActivity", () => {
  it("shows the running tool", () => {
    render(<TransientActivity startedAt={Date.now()} bubble={bubble({ toolEvents: [
      { id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true },
      { id: 2, tool: "run_command", args: { command: "pytest" }, source: "explore", done: false },
    ] })} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Running pytest");
  });

  it("names the oldest unfinished member of a parallel batch and counts the rest", () => {
    render(<TransientActivity startedAt={Date.now()} bubble={bubble({ toolEvents: [
      { id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true },
      { id: 2, tool: "read_file", args: { path: "b.py" }, source: "explore", done: false },
      { id: 3, tool: "search_code", args: { pattern: "x" }, source: "explore", done: false },
      { id: 4, tool: "run_command", args: { command: "pytest" }, source: "explore", done: false },
    ] })} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Reading b.py +2 more");
  });

  it("says it waits on the user, with no clock, while a gate is pending", () => {
    render(<TransientActivity startedAt={Date.now()} waiting bubble={bubble({ toolEvents: [
      { id: 2, tool: "run_command", args: { command: "pytest" }, source: "explore", done: false },
    ] })} />);
    const text = screen.getByTestId("transient-activity").textContent ?? "";
    expect(text).toContain("Waiting for your approval");
    expect(text).not.toMatch(/\d+s/);
    expect(screen.queryByRole("button", { name: /Show current step/ })).toBeNull();
  });

  it("falls back to the thinking text, then to Working…", () => {
    const { rerender } = render(<TransientActivity startedAt={Date.now()} bubble={bubble({ activeThinkingChunk: "Checking the cache path" })} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Checking the cache path");
    rerender(<TransientActivity startedAt={Date.now()} bubble={bubble()} />);
    expect(screen.getByTestId("transient-activity").textContent).toContain("Working…");
  });

  it("is one ellipsized line and expands to the in-flight pills", () => {
    render(<TransientActivity startedAt={Date.now()} bubble={bubble({ toolEvents: [
      { id: 1, tool: "read_file", args: { path: "x".repeat(400) }, source: "explore", done: false },
    ] })} />);
    expect(screen.getByTestId("transient-text").className).toContain("truncate");
    fireEvent.click(screen.getByRole("button", { name: /Show current step/ }));
    expect(screen.getByTestId("transient-detail")).toBeInTheDocument();
  });
});
