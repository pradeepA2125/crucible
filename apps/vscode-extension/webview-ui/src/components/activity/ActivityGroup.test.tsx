import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActivityGroup } from "./ActivityGroup";
import type { ActivityStep } from "../../transcript/groupTranscript";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const steps: ActivityStep[] = [
  { type: "tools", events: [{ id: 1, tool: "read_file", args: { path: "a.py" }, source: "explore", done: true }] },
  { type: "edit", applied: true, entries: [{ path: "a.ts", additions: 3, deletions: 1, unified_diff: "@@ -1 +1 @@\n-old_line\n+new_line" }] },
  { type: "outcome", text: "✓ Edit accepted: a.ts" },
];

describe("ActivityGroup", () => {
  it("is one collapsed summary line by default", () => {
    render(<ActivityGroup steps={steps} />);
    expect(screen.getByRole("button", { name: /Read a file, edited a.ts \+3 −1/ })).toBeInTheDocument();
    expect(screen.queryByTestId("activity-detail")).toBeNull();
  });

  it("expands to the steps in order inside a capped scroll box", () => {
    render(<ActivityGroup steps={steps} />);
    fireEvent.click(screen.getByRole("button", { name: /Read a file/ }));
    const detail = screen.getByTestId("activity-detail");
    expect(detail.style.maxHeight).toBe("320px");
    expect(detail.className).toContain("overflow-y-auto");
    expect(detail.textContent).toContain("Edited a.ts");
    expect(detail.textContent).toContain("Edit accepted: a.ts");
  });

  it("exposes the edit row's open state", () => {
    render(<ActivityGroup steps={steps} />);
    fireEvent.click(screen.getByRole("button", { name: /Read a file/ }));
    const row = screen.getByRole("button", { name: /Edited a.ts/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
  });

  it("opens an edit row to its diff", () => {
    render(<ActivityGroup steps={steps} />);
    fireEvent.click(screen.getByRole("button", { name: /Read a file/ }));
    fireEvent.click(screen.getByRole("button", { name: /Edited a.ts/ }));
    expect(screen.getByTestId("activity-detail").textContent).toContain("new_line");
  });

  it("shows a batch's shared thought once above its steps", () => {
    const batch: ActivityStep[] = [{ type: "tools", events: [
      { id: 1, tool: "read_file", args: { path: "a.py" }, thought: "Check both modules", source: "explore", done: true },
      { id: 2, tool: "read_file", args: { path: "b.py" }, thought: "Check both modules", source: "explore", done: true },
      { id: 3, tool: "run_command", args: { command: "pytest" }, thought: "Run the tests", source: "explore", done: true },
    ] }];
    render(<ActivityGroup steps={batch} />);
    fireEvent.click(screen.getByRole("button", { name: /Read 2 files/ }));
    expect(screen.getAllByTestId("activity-thought").map((el) => el.textContent)).toEqual(["Check both modules", "Run the tests"]);
  });

  it("keeps a very long summary on one ellipsized line", () => {
    const long: ActivityStep[] = [{ type: "tools", events: [{ id: 1, tool: "mcp__" + "x".repeat(300), args: {}, source: "explore", done: true }] }];
    render(<ActivityGroup steps={long} />);
    expect(screen.getByTestId("activity-summary").className).toContain("truncate");
  });
  it("lists each tool step as one line, not steps (spec §2)", () => {
    render(<ActivityGroup steps={steps} />);
    fireEvent.click(screen.getByRole("button", { name: /Read a file/ }));
    expect(screen.getByRole("button", { name: /Read a\.py/ })).toBeInTheDocument();
    expect(screen.getByTestId("activity-detail").querySelector("[data-event-id]")).toBeNull();
  });

  it("puts the folded header on a muted meta row over a hairline rule", () => {
    render(<ActivityGroup steps={steps} />);
    const header = screen.getByRole("button", { name: /Read a file/ });
    expect(header.className).toContain("text-meta");
    expect(header.className).toContain("text-text-3");
    expect(header.style.borderBottom).toBe("1px solid var(--border)");
  });
});
