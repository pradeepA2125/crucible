import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, test, vi, beforeEach } from "vitest";

const { postMessage } = vi.hoisted(() => ({ postMessage: vi.fn() }));
vi.mock("./vscodeApi", () => ({ vscode: { postMessage } }));

import { BrowserTab } from "./BrowserTab";
import type { MemoryView } from "./types";

function mem(id: string, over: Partial<MemoryView> = {}): MemoryView {
  return {
    id, scopeKind: "workspace", scopeId: "/ws", kind: "semantic", content: `content ${id}`,
    entities: ["src/a.py"], importance: 5, validFrom: "2026-06-29T00:00:00Z", validTo: null,
    supersededBy: null, sourceKind: "consolidation", sourceRef: "r",
    sourceSeqLo: 10, sourceSeqHi: 20, createdAt: "2026-06-29T00:00:00Z", ...over,
  };
}

describe("BrowserTab", () => {
  beforeEach(() => postMessage.mockClear());

  test("lists memories with kind badge and snippet", () => {
    render(<BrowserTab memories={[mem("a"), mem("b", { kind: "episodic" })]} chains={{}} />);
    const list = screen.getByTestId("memory-list");
    expect(within(list).getByText(/content a/)).toBeTruthy();
    expect(within(list).getByText("episodic")).toBeTruthy(); // badge, not the filter <option>
  });

  test("toggling include-retired posts a browse message", () => {
    render(<BrowserTab memories={[]} chains={{}} />);
    fireEvent.click(screen.getByLabelText(/include retired/i));
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "browse", filter: expect.objectContaining({ includeRetired: true }) })
    );
  });

  test("selecting a memory shows detail + posts loadChain", () => {
    render(<BrowserTab memories={[mem("a")]} chains={{}} />);
    fireEvent.click(screen.getByText(/content a/));
    const detail = screen.getByTestId("memory-detail");
    expect(within(detail).getByText("src/a.py")).toBeTruthy(); // entity chip
    expect(within(detail).getByText(/10.*20/)).toBeTruthy(); // seq span
    expect(postMessage).toHaveBeenCalledWith({ type: "loadChain", memoryId: "a" });
  });

  test("renders the supersede chain oldest→newest when present", () => {
    const chains = { a: [mem("old", { content: "v1" }), mem("a", { content: "v2" })] };
    render(<BrowserTab memories={[mem("a", { content: "v2" })]} chains={chains} />);
    fireEvent.click(screen.getByText("v2"));
    const timeline = screen.getByTestId("supersede-chain");
    const ids = within(timeline).getAllByTestId(/chain-node-/).map((n) => n.getAttribute("data-testid"));
    expect(ids).toEqual(["chain-node-old", "chain-node-a"]);
  });
});
