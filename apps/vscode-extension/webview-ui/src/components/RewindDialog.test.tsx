import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RewindDialog } from "./RewindDialog";

const preview = {
  messages: 3,
  files: 2,
  commandsRun: 1,
  blockedByTask: null as string | null,
  sessions: [{ id: "s1", command: "npm run dev" }],
};

describe("RewindDialog", () => {
  it("states what rewind does not undo", () => {
    render(<RewindDialog preview={preview} onCancel={() => {}} onConfirm={() => {}} />);
    expect(screen.getByText(/are not undone/i)).toBeTruthy();
    expect(screen.getByText(/npm run dev/)).toBeTruthy();
  });

  it("disables confirm when a live task blocks the rewind", () => {
    render(
      <RewindDialog
        preview={{ ...preview, blockedByTask: "task-9" }}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /^rewind$/i }).hasAttribute("disabled")).toBe(true);
  });

  it("confirms with the counts shown", () => {
    const onConfirm = vi.fn();
    render(<RewindDialog preview={preview} onCancel={() => {}} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: /^rewind$/i }));
    expect(onConfirm).toHaveBeenCalled();
  });
});
