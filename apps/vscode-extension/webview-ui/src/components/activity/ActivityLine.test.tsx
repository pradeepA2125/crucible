import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { ActivityLine } from "./ActivityLine";

const done = { id: 7, tool: "run_command", args: { command: "pytest -q" }, source: "explore" as const, done: true, output: "3 passed" };

describe("ActivityLine", () => {
  it("is one line: icon, verb, muted target", () => {
    render(<ActivityLine event={done} />);
    const line = screen.getByRole("button", { name: /Ran pytest -q/ });
    expect(line.className).toContain("text-body");
    expect(screen.getByText("pytest -q").className).toContain("text-text-3");
  });

  it("shows why Auto let the step run", () => {
    render(<ActivityLine event={{ ...done, approvalNote: "Auto: you asked to run the tests" }} />);
    expect(screen.getByText("Auto: you asked to run the tests").className).toContain("text-text-3");
  });

  it("opens the tool's input and output with the keyboard (native button)", async () => {
    render(<ActivityLine event={done} />);
    const line = screen.getByRole("button", { name: /Ran pytest -q/ });
    expect(line.tagName).toBe("BUTTON");
    expect(line).toHaveAttribute("aria-expanded", "false");
    line.focus();
    await userEvent.keyboard("{Enter}");
    expect(line).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("3 passed")).toBeInTheDocument();
  });

  it("marks a failed step", () => {
    render(<ActivityLine event={{ ...done, isError: true }} />);
    expect(screen.getByRole("button", { name: /Ran pytest -q.*failed/ })).toBeInTheDocument();
  });

  it("shimmers only the verb while running", () => {
    render(<ActivityLine event={{ ...done, done: false }} />);
    expect(screen.getByText("Running").className).toContain("shimmer-text");
    expect(screen.getByText("pytest -q").className).not.toContain("shimmer-text");
  });
});


it("opens details with missing arguments without crashing", () => {
  render(<ActivityLine event={{ ...done, args: null as unknown as typeof done.args }} />);
  fireEvent.click(screen.getByRole("button", { name: /^Ran/ }));
  expect(screen.getByText("3 passed")).toBeInTheDocument();
});
