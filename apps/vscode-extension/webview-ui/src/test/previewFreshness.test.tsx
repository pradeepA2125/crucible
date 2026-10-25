import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePreviewFreshness, type FreshnessPreview } from "../desktop/usePreviewFreshness";
import type { WorkspaceFileVersion } from "../../../../desktop/src/workspace-revision";
afterEach(() => { vi.useRealTimers(); });
it("bounds metadata probes, throttles focus and prevents overlapping work", async () => {
  vi.useFakeTimers(); let finish!: (value: { entries: WorkspaceFileVersion[] }) => void;
  const port = { metadata: vi.fn(() => new Promise<{ entries: WorkspaceFileVersion[] }>(resolve => { finish = resolve; })) }, changed = vi.fn();
  const previews = Array.from({ length: 20 }, (_, index) => ({ path: String(index), revision: "old" }));
  const hook = renderHook(() => usePreviewFreshness(port, previews, true, changed, vi.fn()));
  act(() => { window.dispatchEvent(new Event("focus")); }); expect(port.metadata).toHaveBeenCalledWith(previews.slice(0, 12).map(value => value.path));
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); }); expect(port.metadata).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ entries: [{ path: "0", revision: "new" }, { path: "1", reason: "missing" }] }); });
  expect(changed).toHaveBeenCalledWith(["0", "1"]); hook.unmount();
});
it("does not invalidate reloaded or closed previews from an old response", async () => {
  vi.useFakeTimers(); let finish!: (value: { entries: WorkspaceFileVersion[] }) => void;
  const port = { metadata: vi.fn(() => new Promise<{ entries: WorkspaceFileVersion[] }>(resolve => { finish = resolve; })) }, changed = vi.fn();
  const hook = renderHook(({ previews }: { previews: FreshnessPreview[] }) => usePreviewFreshness(port, previews, true, changed, vi.fn()), { initialProps: { previews: [{ path: "a", revision: "old" }, { path: "b", revision: "old" }] } });
  act(() => { window.dispatchEvent(new Event("focus")); }); hook.rerender({ previews: [{ path: "a", revision: "reloaded" }] });
  await act(async () => { finish({ entries: [{ path: "a", revision: "changed" }, { path: "b", reason: "missing" }] }); });
  expect(changed).not.toHaveBeenCalled(); hook.unmount();
});
it("suspends hidden workspace polling and reports failed probes once until recovery", async () => {
  vi.useFakeTimers(); const port = { metadata: vi.fn().mockRejectedValue(new Error("busy")) }, unavailable = vi.fn(), changed = vi.fn();
  const hook = renderHook(({ visible }) => usePreviewFreshness(port, [{ path: "a", revision: "old" }], visible, changed, unavailable), { initialProps: { visible: false } });
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); }); expect(port.metadata).not.toHaveBeenCalled();
  hook.rerender({ visible: true }); await act(async () => { await vi.advanceTimersByTimeAsync(10000); }); expect(unavailable).toHaveBeenCalledTimes(1);
  port.metadata.mockResolvedValue({ entries: [{ path: "a", revision: "old" }] }); await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  port.metadata.mockRejectedValue(new Error("busy")); await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); expect(unavailable).toHaveBeenCalledTimes(2); hook.unmount();
});
