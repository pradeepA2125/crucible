import { expect, it, vi } from "vitest";
import { ThreadMetadata } from "../src/thread-metadata.js";
function fixture() { let stored: unknown; const update = vi.fn(async (_key: string, value: unknown) => { stored = value; }); return { store: new ThreadMetadata({ get: () => stored, update }), update, set: (value: unknown) => { stored = value; } }; }
it("persists pins/local names/unread and recoverable archive in project state only", async () => {
  const f = fixture(); await f.store.change({ threadId: "a", action: "pin", value: true });
  await f.store.change({ threadId: "a", action: "name", value: "Local name" });
  await f.store.change({ threadId: "a", action: "archive", value: true });
  expect(f.store.read().a).toMatchObject({ name: "Local name", pinned: true, archived: true });
  await f.store.change({ threadId: "a", action: "archive", value: false });
  await f.store.change({ threadId: "a", action: "unread" }); expect(f.store.read().a?.unread).toBe(true);
  await f.store.change({ threadId: "a", action: "viewed" }); expect(f.store.read().a).toMatchObject({ unread: false, viewedAt: expect.any(Number) });
  expect(f.update.mock.calls.every(([key]) => key === "desktop.threadMetadata")).toBe(true);
});
it("preserves corrupt preferences instead of replacing them", async () => {
  const f = fixture(); f.set({ a: { surprise: true } });
  await expect(f.store.change({ threadId: "a", action: "pin", value: true })).rejects.toThrow("preserved"); expect(f.update).not.toHaveBeenCalled();
});
it("keeps preferences isolated between canonical project hosts and rejects malformed actions", async () => {
  const a = fixture(), b = fixture(); await a.store.change({ threadId: "same", action: "name", value: "A" }); expect(b.store.read()).toEqual({});
  await expect(a.store.change({ threadId: "same", action: "archive", value: "true" })).rejects.toThrow();
  await expect(a.store.change({ threadId: "same", action: "delete" })).rejects.toThrow();
  expect(a.store.read().same?.name).toBe("A");
});
