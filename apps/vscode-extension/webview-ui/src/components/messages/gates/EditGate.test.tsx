import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { EditGate } from "./EditGate";
import { vscode } from "../../../vscodeApi";

vi.mock("../../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const payload = {
  diff_entries: [{ path: "pkg/commitlog/log.go", additions: 5, deletions: 1 }],
};

describe("EditGate", () => {
  beforeEach(() => (vscode.postMessage as ReturnType<typeof vi.fn>).mockClear());

  it("posts editDecision accept with an empty reason", () => {
    render(<EditGate taskId="t1" payload={payload} />);
    fireEvent.click(screen.getByText("Accept"));
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "editDecision", threadId: "t1", decision: "accept", reason: "" });
  });

  it("clicking Reject opens a reason box instead of rejecting immediately", () => {
    render(<EditGate taskId="t1" payload={payload} />);
    fireEvent.click(screen.getByText("Reject"));
    expect(vscode.postMessage).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText(/what's wrong/i)).toBeInTheDocument();
  });

  it("posts editDecision reject with the typed reason on confirm", () => {
    render(<EditGate taskId="t1" payload={payload} />);
    fireEvent.click(screen.getByText("Reject"));
    fireEvent.change(screen.getByPlaceholderText(/what's wrong/i), {
      target: { value: "Keep the flush, drop the extra brace." },
    });
    fireEvent.click(screen.getByText("Reject"));
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "editDecision", threadId: "t1", decision: "reject",
      reason: "Keep the flush, drop the extra brace." });
  });

  it("Back returns to Accept/Reject without posting", () => {
    render(<EditGate taskId="t1" payload={payload} />);
    fireEvent.click(screen.getByText("Reject"));
    fireEvent.click(screen.getByText("Back"));
    expect(vscode.postMessage).not.toHaveBeenCalled();
    expect(screen.getByText("Accept")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
  });

  it("allows confirming reject with an empty reason", () => {
    render(<EditGate taskId="t1" payload={payload} />);
    fireEvent.click(screen.getByText("Reject"));
    fireEvent.click(screen.getByText("Reject"));
    expect(vscode.postMessage).toHaveBeenCalledWith({
      type: "editDecision", threadId: "t1", decision: "reject", reason: "" });
  });
});
