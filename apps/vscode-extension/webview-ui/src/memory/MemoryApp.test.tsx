import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, expect, test, vi, beforeEach } from "vitest";

const { postMessage } = vi.hoisted(() => ({ postMessage: vi.fn() }));
vi.mock("./vscodeApi", () => ({ vscode: { postMessage } }));

import MemoryApp from "./MemoryApp";

describe("MemoryApp", () => {
  beforeEach(() => postMessage.mockClear());

  test("shows curation failure and retry without a chat message", () => {
    render(<MemoryApp />);
    act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "curation",
      status: { enabled: true, status: "failed", lastMseq: 21, failures: 1, saved: 0,
        dropped: 2, chunks: 1, retryAt: "tomorrow", error: "provider unavailable", updatedAt: null } } })));
    fireEvent.click(screen.getByRole("button", { name: "Curation" }));
    expect(screen.getByRole("alert").textContent).toBe("provider unavailable");
    expect(screen.getByTestId("curation-status").textContent).toContain("Retry after tomorrow");
  });

  test("posts ready on mount", () => {
    render(<MemoryApp />);
    expect(postMessage).toHaveBeenCalledWith({ type: "ready" });
  });

  test("renders both tab buttons and switches active tab", () => {
    render(<MemoryApp />);
    expect(screen.getByRole("button", { name: /recall decisions/i })).toBeTruthy();
    const browserTab = screen.getByRole("button", { name: /saved memories/i });
    fireEvent.click(browserTab);
    expect(screen.getByText("Loading saved memories…")).toBeTruthy();
  });

  test("refresh button posts refresh", () => {
    render(<MemoryApp />);
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(postMessage).toHaveBeenCalledWith({ type: "refresh" });
  });
});
