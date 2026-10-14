import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalMenu, ApprovalNotice } from "./ApprovalMenu";
import type { ApprovalModeView } from "../types";
import { vscode } from "../vscodeApi";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const approval: ApprovalModeView = { tier: "supported", mode: "ask", confirmed: true, pending: false, warningDismissed: false, trusted: true, error: null, notice: null,
  snapshot: { supported: true, autoAccept: false, shellPolicy: "ask", owned: true, version: 1, processId: "p1" } };
function open(value = approval) { const view = render(<ApprovalMenu approval={value} />); fireEvent.click(screen.getByRole("button", { name: /Approval mode/ })); return view; }
beforeEach(() => vi.clearAllMocks());
describe("ApprovalMenu", () => {
  it("shows effective policy, exceptions, and the reserved Auto mode", () => {
    open(); expect(screen.getByRole("menu")).toHaveTextContent("Commands: ask unless remembered");
    expect(screen.getByRole("menu")).toHaveTextContent("Protected paths and untrusted-agent edits");
    expect(screen.getByRole("menuitemradio", { name: /Auto/ })).toBeDisabled();
  });
  it("waits for acknowledgement and surfaces failed requests without changing the chip", () => {
    const { rerender } = open(); fireEvent.click(screen.getByRole("menuitemradio", { name: /^Accept edits/ }));
    expect(screen.getByRole("button", { name: /Approval mode: Ask/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /^Full access/ })).toBeDisabled();
    rerender(<ApprovalMenu approval={{ ...approval, error: "Backend unavailable" }} />);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    rerender(<ApprovalMenu approval={{ ...approval, mode: "accept_edits" }} />);
    expect(screen.getByRole("button", { name: /Approval mode: Accept edits/ })).toBeInTheDocument();
  });
  it("cancels Full access by Escape without posting and restores focus", () => {
    open(); const chip = screen.getByRole("button", { name: /Approval mode/ });
    fireEvent.click(screen.getByRole("menuitemradio", { name: /^Full access/ }));
    expect(screen.getByRole("checkbox", { name: "Don’t show again" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull(); expect(vscode.postMessage).not.toHaveBeenCalled(); expect(chip).toHaveFocus();
  });
  it("traps Tab in Full access confirmation and posts the remembered choice", () => {
    open(); fireEvent.click(screen.getByRole("menuitemradio", { name: /^Full access/ }));
    const checkbox = screen.getByRole("checkbox"); const allow = screen.getByRole("button", { name: "Allow Full access" });
    fireEvent.keyDown(checkbox, { key: "Tab", shiftKey: true }); expect(allow).toHaveFocus();
    fireEvent.keyDown(allow, { key: "Tab" }); expect(checkbox).toHaveFocus();
    fireEvent.click(checkbox); fireEvent.click(allow);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "setApprovalMode", mode: "full_access", confirmedFullAccess: true, dontShowAgain: true });
  });
  it("skips the remembered warning, while older and legacy capabilities constrain modes", () => {
    const { rerender } = open({ ...approval, warningDismissed: true });
    fireEvent.click(screen.getByRole("menuitemradio", { name: /^Full access/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    rerender(<ApprovalMenu approval={{ ...approval, tier: "older", confirmed: false }} />);
    expect(screen.getByRole("button", { name: /unconfirmed/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Approval mode/ }));
    expect(screen.getByRole("menuitemradio", { name: /^Full access/ })).toBeDisabled();
    rerender(<ApprovalMenu approval={{ ...approval, tier: "legacy" }} />);
    expect(screen.getByRole("button", { name: /Approval unavailable/ })).toBeDisabled();
  });
  it("shows errors before notices on their own full row", () => {
    const { rerender } = render(<ApprovalNotice approval={{ ...approval, error: "Backend unavailable", notice: "Changed elsewhere" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Backend unavailable");
    expect(screen.getByRole("alert").className).toContain("basis-full");
    rerender(<ApprovalNotice approval={{ ...approval, notice: "Changed elsewhere" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Changed elsewhere");
  });
  it("disables Full access in an untrusted workspace and says why", () => {
    open({ ...approval, trusted: false });
    expect(screen.getByRole("menu")).toHaveTextContent("This workspace isn’t trusted");
    expect(screen.getByRole("button", { name: /Approval mode/ })).toHaveAttribute("title", expect.stringContaining("until you trust"));
    const full = screen.getByRole("menuitemradio", { name: /^Full access/ });
    expect(full).toBeDisabled(); expect(full).toHaveTextContent("Needs a trusted workspace.");
    expect(screen.getByRole("menuitemradio", { name: /^Accept edits/ })).toBeEnabled();
  });
  it("keeps connecting visible and blocks requests until the backend confirms", () => {
    open({ ...approval, tier: "unknown", confirmed: false });
    expect(screen.getByRole("button", { name: /Connecting/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /^Accept edits/ })).toBeDisabled();
  });
});
