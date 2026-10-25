import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ClarifyGate } from "./ClarifyGate";
import { vscode } from "../../../vscodeApi";

vi.mock("../../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const payload = { question: "Which module?", options: ["a.py", "b.py"] };

describe("ClarifyGate", () => {
  beforeEach(() => (vscode.postMessage as ReturnType<typeof vi.fn>).mockClear());

  it("renders the question, each option, and a free-text row", () => {
    render(<ClarifyGate gateId="g" taskId="t1" payload={payload} />);
    expect(screen.getByText("Which module?")).toBeInTheDocument();
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByText("b.py")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/something else/i)).toBeInTheDocument();
  });

  it("posts exact gateDecision with the option text on click", async () => {
    render(<ClarifyGate gateId="g" taskId="t1" payload={payload} />);
    fireEvent.click(screen.getByText("a.py"));
    await waitFor(() => expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "gateDecision", threadId: "t1", gateId: "g", kind: "clarify", value: "a.py" }));
  });

  it("posts exact gateDecision with the typed free text on Enter", async () => {
    render(<ClarifyGate gateId="g" taskId="t1" payload={payload} />);
    const input = screen.getByPlaceholderText(/something else/i);
    fireEvent.change(input, { target: { value: "c.py" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "gateDecision", threadId: "t1", gateId: "g", kind: "clarify", value: "c.py" }));
  });

  it("ignores a second pick (one-shot guard)", async () => {
    render(<ClarifyGate gateId="g" taskId="t1" payload={payload} />);
    // Capture both buttons BEFORE the first click — picking one swaps the card to its
    // "Answered:" view, detaching the other button.
    const a = screen.getByText("a.py");
    const b = screen.getByText("b.py");
    fireEvent.click(a);
    fireEvent.click(b); // detached node → no second dispatch
    await waitFor(() => expect(vscode.postMessage).toHaveBeenCalledTimes(1));
  });

  it("renders a free-text-only card when the model gave no options", () => {
    render(<ClarifyGate gateId="g" taskId="t1" payload={{ question: "Anything?", options: [] }} />);
    expect(screen.getByText("Anything?")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/something else/i)).toBeInTheDocument();
  });
});


it("keeps oversized ClarifyGate feedback editable without submitting", async () => {
  vi.mocked(vscode.postMessage).mockClear();
  render(<ClarifyGate taskId="a" gateId="g" payload={{ options: [] }} />);
  const text = "x".repeat(65537), input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: text } }); fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(text));
  expect(screen.getByRole("status")).toHaveTextContent("65,536");
  expect(vscode.postMessage).not.toHaveBeenCalled();
});
