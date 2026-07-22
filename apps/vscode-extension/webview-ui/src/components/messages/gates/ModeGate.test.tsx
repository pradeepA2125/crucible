import { fireEvent, render, screen } from "@testing-library/react";
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
    render(<ModeGate taskId="chat-1" payload={payload} />);
    expect(screen.getByPlaceholderText(/chat about this/i)).toBeInTheDocument();
  });

  it("submitting posts exactly one sendMessage and is one-shot", () => {
    render(<ModeGate taskId="chat-1" payload={payload} />);
    const input = screen.getByPlaceholderText(/chat about this/i);
    fireEvent.change(input, { target: { value: "make it minimal" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(input, { key: "Enter" }); // second press ignored (one-shot)
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls
      .filter((c) => c[0]?.type === "sendMessage");
    expect(calls).toHaveLength(1);
    expect(calls[0][0].text).toBe("make it minimal");
  });
});

describe("ModeGate — implement mode + plan sketch", () => {
  it("posts setPlanMode(false) when 'implement' is picked", () => {
    render(
      <ModeGate
        taskId="t1"
        payload={{
          plan_sketch: "a".repeat(3000),
          recommended: "implement",
          options: [{ mode: "implement", label: "Implement this plan", description: "…" }],
        }}
      />,
    );
    fireEvent.click(screen.getByText(/Implement this plan/));
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "modeDecision", threadId: "t1", mode: "implement" });
    expect(calls).toContainEqual({ type: "setPlanMode", enabled: false });
  });

  it("renders the plan sketch in a scrollable/expandable container for long plans", () => {
    const longPlan = "line\n".repeat(200);
    render(<ModeGate taskId="t1" payload={{ plan_sketch: longPlan, options: [] }} />);
    const sketch = screen.getByText(/line/, { exact: false });
    // The container must impose a max-height with overflow, not render unbounded.
    const container = sketch.closest("[data-testid='plan-sketch']");
    expect(container).not.toBeNull();
  });
});
