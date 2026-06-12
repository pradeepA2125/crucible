import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DiffPanes } from "../components/shared/DiffPanes";
import type { DiffEntry } from "../types";

const ENTRIES: DiffEntry[] = [
  {
    path: "src/a.py", additions: 1, deletions: 1, temp_path: "/tmp/a.py",
    unified_diff: "--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3",
  },
  {
    path: "src/b.ts", additions: 1, deletions: 0, temp_path: "/tmp/b.ts",
    unified_diff: "--- a/src/b.ts\n+++ b/src/b.ts\n@@ -5,1 +5,2 @@\n ctx\n+added",
  },
];

describe("DiffPanes", () => {
  it("renders one tab per file and the first pane's lines", () => {
    render(<DiffPanes entries={ENTRIES} />);
    expect(screen.getByRole("tab", { name: /a\.py/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /b\.ts/ })).toBeTruthy();
    expect(screen.getByText("-")).toBeTruthy(); // del marker cell
    expect(screen.getByText("y = 3")).toBeTruthy();
  });

  it("switches panes on tab click", () => {
    render(<DiffPanes entries={ENTRIES} />);
    fireEvent.click(screen.getByRole("tab", { name: /b\.ts/ }));
    expect(screen.getByText("added")).toBeTruthy();
    expect(screen.queryByText("y = 3")).toBeNull();
  });

  it("numbers lines from the hunk header", () => {
    render(<DiffPanes entries={[ENTRIES[1]]} />);
    // @@ -5,1 +5,2 @@ → ctx line is 5, added line is 6 (new-file numbering)
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("6")).toBeTruthy();
  });

  it("returns null when no entry has diff text", () => {
    const bare = ENTRIES.map((e) => ({ ...e, unified_diff: undefined }));
    const { container } = render(<DiffPanes entries={bare} />);
    expect(container.innerHTML).toBe("");
  });
});
