import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useAppState } from "../hooks/useAppState";
import { contextUsage } from "./contextUsageFixture";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const post = (data: unknown) => act(() => {
  window.dispatchEvent(new MessageEvent("message", { data }));
});

describe("dedicated context usage state", () => {
  it("starts unavailable, hydrates the host denominator and keeps stale snapshots", () => {
    const { result } = renderHook(() => useAppState());
    expect(result.current.state.contextUsage).toBeNull();
    expect(result.current.state.contextUsageStale).toBe(false);
    post({ type: "renderContextUsage", usage: contextUsage, stale: false });
    expect(result.current.state.contextUsage).toEqual(contextUsage);
    post({ type: "renderContextUsage", usage: contextUsage, stale: true });
    expect(result.current.state.contextUsageStale).toBe(true);
    post({ type: "appendThinkingChunk", chunk: "progress" });
    post({ type: "renderThreadUsage", usage: null });
    expect(result.current.state.contextUsage).toEqual(contextUsage);
    expect(result.current.state.threadUsage).toBeNull();
  });

  it("clears usage and stale state on null and thread reset", () => {
    const { result } = renderHook(() => useAppState());
    post({ type: "renderContextUsage", usage: contextUsage, stale: true });
    post({ type: "renderContextUsage", usage: null, stale: false });
    expect(result.current.state.contextUsage).toBeNull();
    expect(result.current.state.contextUsageStale).toBe(false);
    post({ type: "renderContextUsage", usage: contextUsage, stale: true });
    post({ type: "clearThread" });
    expect(result.current.state.contextUsage).toBeNull();
    expect(result.current.state.contextUsageStale).toBe(false);
  });
});
