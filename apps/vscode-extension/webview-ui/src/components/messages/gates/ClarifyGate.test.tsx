import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ClarifyGate } from "./ClarifyGate";
import { vscode } from "../../../vscodeApi";

vi.mock("../../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const payload = { question: "Which module?", options: ["a.py", "b.py"] };

describe("ClarifyGate", () => {
  beforeEach(() => (vscode.postMessage as ReturnType<typeof vi.fn>).mockClear());

  it("renders the question, each option, and a free-text row", () => {
    render(<ClarifyGate taskId="t1" payload={payload} />);
    expect(screen.getByText("Which module?")).toBeInTheDocument();
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByText("b.py")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/something else/i)).toBeInTheDocument();
  });

  it("posts clarifyDecision with the option text on click", () => {
    render(<ClarifyGate taskId="t1" payload={payload} />);
    fireEvent.click(screen.getByText("a.py"));
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "clarifyDecision", threadId: "t1", answer: "a.py" });
  });

  it("posts clarifyDecision with the typed free text on Enter", () => {
    render(<ClarifyGate taskId="t1" payload={payload} />);
    const input = screen.getByPlaceholderText(/something else/i);
    fireEvent.change(input, { target: { value: "c.py" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "clarifyDecision", threadId: "t1", answer: "c.py" });
  });

  it("ignores a second pick (one-shot guard)", () => {
    render(<ClarifyGate taskId="t1" payload={payload} />);
    // Capture both buttons BEFORE the first click — picking one swaps the card to its
    // "Answered:" view, detaching the other button.
    const a = screen.getByText("a.py");
    const b = screen.getByText("b.py");
    fireEvent.click(a);
    fireEvent.click(b); // detached node → no second dispatch
    expect(vscode.postMessage).toHaveBeenCalledTimes(1);
  });

  it("renders a free-text-only card when the model gave no options", () => {
    render(<ClarifyGate taskId="t1" payload={{ question: "Anything?", options: [] }} />);
    expect(screen.getByText("Anything?")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/something else/i)).toBeInTheDocument();
  });
});
