import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { McpGate } from "../components/messages/gates/McpGate";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
beforeEach(() => vi.mocked(vscode.postMessage).mockClear());

const payload = { server: "Exa", tool: "web_search", args: { query: "public docs", max_results: 5 }, native_web: true };

describe("Native search approval", () => {
  it("shows the provider, query and training disclosure", () => {
    render(<McpGate gateId="gate" taskId="thread" payload={payload} />);
    expect(screen.getByText("Search the web with Exa")).toBeTruthy();
    expect(screen.getByText(/public docs/)).toBeTruthy();
    expect(screen.getByText(/may use it for model training/)).toBeTruthy();
  });

  it.each([
    ["Approve once", true, false],
    ["Approve & remember (this workspace)", true, true],
    ["Reject", false, false],
  ] as const)("%s preserves the existing decision transport", (button, approve, remember) => {
    render(<McpGate gateId="gate" taskId="thread" payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: button }));
    expect(vscode.postMessage).toHaveBeenCalledExactlyOnceWith({ type: "mcpDecision", threadId: "thread", gateId: "gate", approve, remember });
  });

  it("keeps ordinary MCP copy", () => {
    render(<McpGate gateId="gate" taskId="thread" payload={{ server: "gh", tool: "search", args: {} }} />);
    expect(screen.getByText("Call MCP tool: gh.search")).toBeTruthy();
    expect(screen.queryByText(/model training/)).toBeNull();
  });

  it("renders query markup as escaped text", () => {
    const { container } = render(<McpGate gateId="gate" taskId="thread" payload={{ ...payload, args: { query: '<img src="x" onerror="alert(1)">' } }} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toContain("<img");
  });
});
