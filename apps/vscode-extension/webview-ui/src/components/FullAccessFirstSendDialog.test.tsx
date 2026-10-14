import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FullAccessFirstSendDialog } from "./FullAccessFirstSendDialog";
import { vscode } from "../vscodeApi";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
function ask(requestId = "r1") {
  act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "confirmFullAccessFirstSend", requestId, workspace: "AI editor" } })));
}
beforeEach(() => vi.clearAllMocks());
describe("FullAccessFirstSendDialog", () => {
  it("shows nothing until the host asks", () => {
    render(<FullAccessFirstSendDialog />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it.each([["Continue with Full access", "continue"], ["Switch to Accept edits", "accept_edits"], ["Cancel", "cancel"]] as const)("%s answers %s and closes", (label, choice) => {
    render(<FullAccessFirstSendDialog />); ask();
    expect(screen.getByRole("dialog", { name: "Full access is on in this workspace" })).toHaveTextContent("run commands in AI editor without asking");
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "fullAccessFirstSendChoice", requestId: "r1", choice });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("starts on the safer choice and Escape cancels", () => {
    render(<FullAccessFirstSendDialog />); ask("r2");
    expect(screen.getByRole("button", { name: "Switch to Accept edits" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "fullAccessFirstSendChoice", requestId: "r2", choice: "cancel" });
  });
});
