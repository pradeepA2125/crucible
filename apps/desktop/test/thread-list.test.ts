import { describe, expect, it, vi } from "vitest";
import { ThreadListPoller } from "../src/thread-list.js";
import type { ChatThreadSummary } from "@crucible/editor-client";
const threads: ChatThreadSummary[] = [{ threadId: "a", workspacePath: "/project", title: "Chat", createdAt: "", agentsRunning: 0, turnActive: true, pendingGates: 1 }];
describe("desktop sidebar metadata polling", () => {
  it("skips unfocused polling, deduplicates identical lists and keeps the current selected id", async () => {
    let focused = false, selected = "a"; const list = vi.fn(async () => threads), render = vi.fn();
    const poller = new ThreadListPoller({ list, render, focused: () => focused, activeThread: () => selected, identity: () => "view", log: vi.fn() });
    await poller.refresh(); expect(list).not.toHaveBeenCalled(); await poller.refresh(true); expect(render).toHaveBeenCalledOnce();
    focused = true; await poller.refresh(); expect(render).toHaveBeenCalledOnce();
    selected = "b"; await poller.refresh(); expect(render).toHaveBeenLastCalledWith(threads, "b"); poller.dispose();
    await poller.refresh(true); expect(list).toHaveBeenCalledTimes(3);
  });
  it("drops late replies from an old document/backend or a disposed host", async () => {
    let identity = "old", resolve!: (value: ChatThreadSummary[]) => void;
    const list = vi.fn(() => new Promise<ChatThreadSummary[]>(yes => { resolve = yes; })), render = vi.fn();
    const poller = new ThreadListPoller({ list, render, focused: () => true, activeThread: () => "a", identity: () => identity, log: vi.fn() });
    const pending = poller.refresh(); identity = "new"; resolve(threads); await pending; expect(render).not.toHaveBeenCalled();
    const closing = poller.refresh(); poller.dispose(); resolve(threads); await closing; expect(render).not.toHaveBeenCalled();
  });
  it("drops pre-controller lists and queues a forced refresh while one is pending", async () => {
    let resolve!: (value: ChatThreadSummary[]) => void;
    const list = vi.fn(() => new Promise<ChatThreadSummary[]>(yes => { resolve = yes; })), render = vi.fn();
    const poller = new ThreadListPoller({ list, render, focused: () => true, activeThread: () => "a", identity: () => "view", log: vi.fn() });
    const pending = poller.refresh(); poller.invalidate(); await poller.refresh(true);
    resolve([]); await pending; expect(render).not.toHaveBeenCalled(); expect(list).toHaveBeenCalledTimes(2);
    resolve(threads); await Promise.resolve(); expect(render).toHaveBeenLastCalledWith(threads, "a"); poller.dispose();
  });

});
