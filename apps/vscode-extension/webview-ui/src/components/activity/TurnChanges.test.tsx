import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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
