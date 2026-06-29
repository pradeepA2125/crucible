import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi, beforeEach } from "vitest";

const { postMessage } = vi.hoisted(() => ({ postMessage: vi.fn() }));
vi.mock("./vscodeApi", () => ({ vscode: { postMessage } }));

import MemoryApp from "./MemoryApp";

describe("MemoryApp", () => {
  beforeEach(() => postMessage.mockClear());

  test("posts ready on mount", () => {
    render(<MemoryApp />);
    expect(postMessage).toHaveBeenCalledWith({ type: "ready" });
  });

  test("renders both tab buttons and switches active tab", () => {
    render(<MemoryApp />);
    expect(screen.getByRole("button", { name: /recall trace/i })).toBeTruthy();
    const browserTab = screen.getByRole("button", { name: /browser/i });
    fireEvent.click(browserTab);
    expect(screen.getByTestId("memory-browser-tab")).toBeTruthy();
  });

  test("refresh button posts refresh", () => {
    render(<MemoryApp />);
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(postMessage).toHaveBeenCalledWith({ type: "refresh" });
  });
});
