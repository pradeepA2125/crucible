import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FileChange } from "../../transcript/groupTranscript";
import { TurnChanges } from "./TurnChanges";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe("TurnChanges", () => {
  it("totals applied files and lists not-applied ones without counting them", () => {
    render(<TurnChanges files={[
      { path: "src/a.ts", additions: 5, deletions: 1, applied: true, diff: "@@ -1 +1 @@\n-x\n+y" },
      { path: "b.ts", additions: 0, deletions: 0, applied: false },
    ]} />);
    expect(screen.getByTestId("turn-changes").textContent).toContain("Edited 1 file");
    expect(screen.getAllByText("+5")).toHaveLength(2); // header total and the a.ts row
    expect(screen.getByText("not applied")).toBeInTheDocument();
  });

  it("says no changes were applied when every edit was rejected", () => {
    render(<TurnChanges files={[{ path: "b.ts", additions: 0, deletions: 0, applied: false }]} />);
    expect(screen.getByTestId("turn-changes").textContent).toContain("No changes applied");
  });

  it("opens a file and expands its diff", async () => {
    const { vscode } = await import("../../vscodeApi");
    render(<TurnChanges files={[{ path: "src/a.ts", additions: 1, deletions: 0, applied: true, diff: "@@ -1 +1 @@\n+hello" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "src/a.ts" }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFile", path: "src/a.ts" });
    fireEvent.click(screen.getByRole("button", { name: "Show diff for src/a.ts" }));
    expect(screen.getByTestId("turn-changes").textContent).toContain("hello");
  });
});

const f = (path: string): FileChange => ({ path, additions: 2, deletions: 1, applied: true });

describe("TurnChanges card (spec §2)", () => {
  it("is one card: radius card, header 'Edited N files' with totals", () => {
    render(<TurnChanges files={[f("a/b.py"), f("c.ts")]} />);
    const card = screen.getByTestId("turn-changes");
    expect(card.className).toContain("rounded-card");
    expect(screen.getByText("Edited 2 files")).toBeInTheDocument();
    expect(screen.getByTestId("turn-changes-totals").textContent).toBe("+4 −2");
  });

  it("shows the basename in primary text and the folder muted", () => {
    render(<TurnChanges files={[f("services/agentd/client.py")]} />);
    expect(screen.getByText("client.py").className).toContain("text-text");
    expect(screen.getByText("services/agentd/").className).toContain("text-text-3");
  });

  it("shows three files with no toggle, and four with 'Show 1 more file' (Review Focus 5)", () => {
    const { rerender } = render(<TurnChanges files={[f("a"), f("b"), f("c")]} />);
    expect(screen.queryByRole("button", { name: /Show \d+ more/ })).toBeNull();
    rerender(<TurnChanges files={[f("a"), f("b"), f("c"), f("d")]} />);
    expect(screen.queryByText("d")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 more file" }));
    expect(screen.getByText("d")).toBeInTheDocument();
  });
});
