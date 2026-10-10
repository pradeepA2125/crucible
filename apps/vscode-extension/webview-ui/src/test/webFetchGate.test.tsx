import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { McpGate } from "../components/messages/gates/McpGate";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
beforeEach(() => vi.mocked(vscode.postMessage).mockClear());
const payload = { server: "Web", tool: "web_fetch", args: { url: "https://example.com/docs", format: "markdown", timeout: 30 }, native_fetch: true };

describe("Native fetch approval", () => {
  it("shows the URL and destination disclosure", () => {
    render(<McpGate gateId="gate" taskId="thread" payload={payload} />);
    expect(screen.getByText("Fetch web page")).toBeTruthy();
    expect(screen.getByText(/https:\/\/example.com\/docs/)).toBeTruthy();
    expect(screen.getByText(/website receives this URL and your network address/)).toBeTruthy();
    expect(screen.queryByText(/Exa|model training/)).toBeNull();
  });
  it.each([
    ["Approve once", true, false],
    ["Approve & remember (this workspace)", true, true],
    ["Reject", false, false],
  ] as const)("%s uses existing decisions", (button, approve, remember) => {
    render(<McpGate gateId="gate" taskId="thread" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: button }));
    expect(vscode.postMessage).toHaveBeenCalledExactlyOnceWith({ type: "mcpDecision", threadId: "thread", gateId: "gate", approve, remember });
  });
  it("escapes URL text", () => {
    const { container } = render(<McpGate gateId="gate" taskId="thread" payload={{ ...payload, args: { url: '<img src="x">' } }} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toContain("<img");
  });
});
