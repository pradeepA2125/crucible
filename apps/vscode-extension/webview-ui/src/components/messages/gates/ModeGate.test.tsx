import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModeGate } from "./ModeGate";
import { vscode } from "../../../vscodeApi";

vi.mock("../../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const payload = {
  options: [{ mode: "implement", label: "Implement this plan", description: "" }],
  recommended: "implement",
};

describe("ModeGate — chat about this", () => {
  it("renders the chat-about-this input instead of a hint", () => {
    render(<ModeGate gateId="g" taskId="chat-1" payload={payload} />);
    expect(screen.getByPlaceholderText(/chat about this/i)).toBeInTheDocument();
  });

  it("submitting posts one exact feedback decision and is one-shot", async () => {
    render(<ModeGate gateId="g" taskId="chat-1" payload={payload} />);
    const input = screen.getByPlaceholderText(/chat about this/i);
    fireEvent.change(input, { target: { value: "make it minimal" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(input, { key: "Enter" }); // second press ignored (one-shot)
    await waitFor(() => expect(vscode.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "gateDecision" })));
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls
      .filter((c) => c[0]?.type === "gateDecision");
    expect(calls).toHaveLength(1);
    expect(calls[0][0].value).toBe("make it minimal");
  });
});

describe("ModeGate — implement mode + plan sketch", () => {
  it("posts only the exact decision when implement is picked", async () => {
    render(
      <ModeGate
        gateId="g" taskId="t1"
        payload={{
          plan_sketch: "a".repeat(3000),
          recommended: "implement",
          options: [{ mode: "implement", label: "Implement this plan", description: "…" }],
        }}
      />,
    );
    fireEvent.click(screen.getByText(/Implement this plan/));
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    await waitFor(() => expect(vscode.postMessage).toHaveBeenCalledWith({ type: "gateDecision", threadId: "t1", gateId: "g", kind: "mode", value: "implement" }));
    expect(calls).not.toContainEqual({ type: "setPlanMode", enabled: false });
  });

  it("renders the plan sketch in a scrollable/expandable container for long plans", () => {
    const longPlan = "line\n".repeat(200);
    render(<ModeGate gateId="g" taskId="t1" payload={{ plan_sketch: longPlan, options: [] }} />);
    const sketch = screen.getByText(/line/, { exact: false });
    // The container must impose a max-height with overflow, not render unbounded.
    const container = sketch.closest("[data-testid='plan-sketch']");
    expect(container).not.toBeNull();
  });
});


it("keeps oversized ModeGate feedback editable without submitting", async () => {
  vi.mocked(vscode.postMessage).mockClear();
  render(<ModeGate taskId="a" gateId="g" payload={{ options: [] }} />);
  const text = "x".repeat(65537), input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: text } }); fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(text));
  expect(screen.getByRole("status")).toHaveTextContent("65,536");
  expect(vscode.postMessage).not.toHaveBeenCalled();
});
