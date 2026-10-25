import { afterEach, describe, expect, it, vi } from "vitest";
import { DraftSession } from "../src/drafts/draft-session.js";
import { DraftStore } from "../src/drafts/draft-store.js";
import type { Draft } from "../src/drafts/types.js";
const draft = (text: string): Draft => ({ text, imageAttachments: [{ name: "a.png", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 }], mentionedPaths: ["a.ts"] });
function setup() {
  let identity: unknown = {};
  const post = vi.fn(), admission = vi.fn();
  const session = new DraftSession({ post, admission, identity: () => identity });
  const handle = (type: string, fields: Record<string, unknown> = {}) => {
    post.mockClear(); session.handle({ type: `draft/${type}`, requestId: "request", ...fields });
    return post.mock.calls.find(([message]) => message.type === "draft/result")?.[0];
  };
  const select = (threadId: string) => session.observe({ type: "clearThread", threadId });
  return { session, post, admission, handle, select, replace: () => { identity = {}; } };
}
afterEach(() => vi.useRealTimers());
describe("shared project draft session", () => {
  it("validates both hosts' draft protocol and never sends malformed requests through chat recovery", () => {
    const { handle, post } = setup();
    expect(handle("save", { threadId: "a", revision: -1, version: 0, draft: draft("bad") }).error).toBeTruthy();
    expect(handle("begin", { threadId: "a", revision: 1, draft: draft("bad"), draftId: "x", extra: true }).error).toBeTruthy();
    expect(post.mock.calls.every(([message]) => message.type === "draft/result")).toBe(true);
    expect(handle("save", { threadId: "a", revision: 1, version: 0, draft: draft("kept") }).snapshot.draft.text).toBe("kept");
    const encoded = vi.spyOn(Buffer, "byteLength").mockReturnValueOnce(48 * 1024 * 1024 + 1);
    expect(handle("read", { threadId: "a" }).error).toContain("size limit"); encoded.mockRestore();
  });
  it("reuses compact image/mention snapshots and keeps project stores separate", () => {
    const a = setup(), b = setup();
    a.handle("save", { threadId: "a", revision: 1, version: 0, draft: draft("one") });
    const compact = a.handle("save", { threadId: "a", revision: 2, version: 0, draft: { text: "two", mentionedPaths: ["b.ts"] } });
    expect(compact.snapshot).toMatchObject({ imagesUnchanged: true, draft: { text: "two", mentionedPaths: ["b.ts"] } });
    expect(compact.snapshot.draft.imageAttachments).toBeUndefined();
    expect(a.handle("read", { threadId: "a" }).snapshot.draft.imageAttachments).toEqual(draft("").imageAttachments);
    expect(b.handle("read", { threadId: "a" }).snapshot.draft.text).toBe("");
  });
  it("holds navigation from preparation through dispatch and separates card feedback", async () => {
    const { session, handle, select, admission } = setup(); select("a"); handle("read", { threadId: "a" });
    expect(handle("begin", { threadId: "b", revision: 1, draft: draft("wrong"), draftId: "wrong" }).error).toContain("changed");
    expect(handle("begin", { threadId: "a", revision: 1, draft: draft("submit"), draftId: "id" }).error).toBeUndefined();
    expect(admission).toHaveBeenLastCalledWith({ threadId: "a", state: "pending", checking: false });
    await expect(session.navigate(async () => {})).rejects.toThrow("pending");
    expect(() => session.dispatch({ type: "sendMessage", text: "feedback" })).toThrow("pending composer");
    expect(session.dispatch({ type: "sendMessage", draftId: "id" })).toBe(true);
    expect(() => session.dispatch({ type: "sendMessage", draftId: "id" })).toThrow("duplicate");
    session.resetView(); expect(session.hasSubmission).toBe(true);
    session.renderAdmission({ threadId: "a", state: "unknown", checking: false });
    session.reject("id");
    expect(admission).toHaveBeenLastCalledWith({ threadId: "a", state: "unknown", checking: false });
    session.renderAdmission(null);
    expect(session.dispatch({ type: "sendMessage", text: "feedback" })).toBe(false);
    expect(handle("read", { threadId: "a" }).snapshot.returned.draft.text).toBe("submit");
  });
  it("adopts a new thread and returns refused messages without overwriting later text or images", () => {
    const { session, handle, select } = setup(); select(""); handle("read", { threadId: "new" });
    handle("begin", { threadId: "new", revision: 1, draft: draft("sent"), draftId: "id" });
    session.dispatch({ type: "sendMessage", draftId: "id" });
    handle("save", { threadId: "new", revision: 2, version: 0, draft: draft("later") });
    session.created("id", "created"); select("created"); session.reject("id");
    const result = handle("read", { threadId: "created" }).snapshot;
    expect(result.draft).toEqual(draft("later")); expect(result.returned.draft).toEqual(draft("sent"));
    expect(handle("choose", { threadId: "created", revision: 3, version: 0, draft: draft("stale"), choice: "restore" }).error).toContain("changed");
    const restored = handle("choose", { threadId: "created", revision: 3, version: result.version, draft: draft("newest"), choice: "restore" }).snapshot;
    expect(restored.draft).toEqual(draft("sent")); expect(restored.returned.draft).toEqual(draft("newest"));
  });
  it("accepts once, preserves later edits, and abandons only undispatched preparation on close", () => {
    const { session, handle, select } = setup(); select("a"); handle("read", { threadId: "a" });
    handle("begin", { threadId: "a", revision: 1, draft: draft("sent"), draftId: "id" });
    session.dispatch({ type: "sendMessage", draftId: "id" });
    handle("save", { threadId: "a", revision: 2, version: 0, draft: draft("later") });
    session.accepted("id", "a"); session.reject("id");
    expect(handle("read", { threadId: "a" }).snapshot).toMatchObject({ draft: draft("later"), version: 1 });
    handle("begin", { threadId: "a", revision: 3, draft: draft("prepared"), draftId: "next" });
    session.resetView(); expect(session.hasSubmission).toBe(false);
    expect(handle("read", { threadId: "a" }).snapshot.returned.draft.text).toBe("prepared");
    session.dispose(); expect(handle("read", { threadId: "a" }).snapshot.draft.text).toBe("");
  });
  it("waits for a matching mounted view's save and refuses overlapping navigation and new preparation", async () => {
    const { session, handle, post, select } = setup(); select("a"); handle("read", { threadId: "a" });
    const action = vi.fn(async () => {}); const navigation = session.navigate(action);
    const requestId = post.mock.calls.find(([message]) => message.type === "draft/flush")![0].requestId;
    expect(action).not.toHaveBeenCalled();
    expect(handle("begin", { threadId: "a", revision: 1, draft: draft("race"), draftId: "id" }).error).toContain("changed");
    await expect(session.navigate(action)).rejects.toThrow("progress");
    session.handle({ type: "draft/flushAck", requestId }, {});
    expect(action).not.toHaveBeenCalled();
    session.handle({ type: "draft/flushAck", requestId }); await navigation;
    expect(action).toHaveBeenCalledTimes(1);
  });
  it("rechecks activity and view lifetime after flush; failures retain the current conversation", async () => {
    const { session, handle, post, replace } = setup(); handle("read", { threadId: "new" });
    const action = vi.fn(async () => {});
    const first = session.navigate(action);
    let requestId = post.mock.calls.find(([message]) => message.type === "draft/flush")![0].requestId;
    session.observe({ type: "liveStatus", turnActive: true });
    session.handle({ type: "draft/flushAck", requestId }); await expect(first).rejects.toThrow("current turn");
    session.observe({ type: "liveStatus", turnActive: false }); post.mockClear();
    const second = session.navigate(action);
    requestId = post.mock.calls[0][0].requestId;
    session.handle({ type: "draft/flushAck", requestId }); replace();
    await expect(second).rejects.toThrow("view changed"); expect(action).not.toHaveBeenCalled();
  });
  it("reports a flush timeout without selecting another thread", async () => {
    vi.useFakeTimers(); const { session, handle } = setup(); handle("read", { threadId: "new" });
    const action = vi.fn(async () => {});
    const pending = expect(session.navigate(action)).rejects.toThrow("Navigation was cancelled");
    await vi.advanceTimersByTimeAsync(2001); await pending; expect(action).not.toHaveBeenCalled();
  });
  it("returns a dispatched draft on an outer host failure but never resurrects an accepted message", async () => {
    const { session, handle, select } = setup(); select("a"); handle("read", { threadId: "a" });
    handle("begin", { threadId: "a", revision: 1, draft: draft("sent"), draftId: "id" });
    session.dispatch({ type: "sendMessage", draftId: "id" });
    await expect(session.send("id", async () => { throw new Error("Backend unavailable before admission"); })).rejects.toThrow("unavailable");
    expect(session.hasSubmission).toBe(false); expect(handle("read", { threadId: "a" }).snapshot.returned.draft.text).toBe("sent");
    handle("choose", { threadId: "a", revision: 2, version: 1, draft: draft(""), choice: "discard" });
    handle("begin", { threadId: "a", revision: 3, draft: draft("accepted"), draftId: "second" });
    session.dispatch({ type: "sendMessage", draftId: "second" }); session.accepted("second", "a");
    await expect(session.send("second", async () => { throw new Error("Later stream failure"); })).rejects.toThrow("Later");
    expect(handle("read", { threadId: "a" }).snapshot.returned).toBeUndefined();
  });
  it("retains the extracted store's original byte/count eviction behavior", () => {
    const store = new DraftStore(1200, 2);
    store.save("a", 1, { ...draft("text survives"), imageAttachments: [{ ...draft("").imageAttachments[0]!, dataUrl: "x".repeat(600) }] });
    store.save("b", 1, { ...draft("active"), imageAttachments: [{ ...draft("").imageAttachments[0]!, dataUrl: "x".repeat(600) }] });
    expect(store.read("a").draft.text).toBe("text survives"); expect(store.read("a").draft.imageAttachments).toEqual([]);
    store.save("c", 1, draft("third")); expect(store.read("b").notice).toContain("could not be retained");
  });
});


it("fails closed for identified composer sends before read and after a view reset", () => {
  const { session, handle, select } = setup(); select("a");
  expect(() => session.dispatch({ type: "sendMessage", draftId: "unprepared" })).toThrow("Prepare");
  handle("read", { threadId: "a" }); handle("begin", { threadId: "a", revision: 1, draft: draft("kept"), draftId: "old" });
  session.resetView(); expect(() => session.dispatch({ type: "sendMessage", draftId: "old" })).toThrow("Prepare");
  expect(handle("read", { threadId: "a" }).snapshot.returned.draft.text).toBe("kept");
  expect(() => session.dispatch({ type: "sendMessage", draftId: "old" })).toThrow("Prepare");
});


it("historical view blocks draft preparation while retaining saved drafts", () => {
  const f = setup(); f.select("a"); f.handle("read", { threadId: "a" });
  f.session.observe({ type: "historyState", history: { threadId: "a", phase: "unavailable", cached: true } });
  expect(f.handle("save", { threadId: "a", revision: 1, version: 0, draft: draft("kept") }).snapshot.draft.text).toBe("kept");
  expect(f.handle("begin", { threadId: "a", revision: 2, draft: draft("kept"), draftId: "x" }).error).toContain("history");
  f.session.observe({ type: "historyState", history: { threadId: "old-thread", phase: "current", cached: true } });
  expect(f.handle("begin", { threadId: "a", revision: 2, draft: draft("kept"), draftId: "x" }).error).toContain("history");
  f.session.observe({ type: "historyState", history: { threadId: "a", phase: "current", cached: true } });
  expect(f.handle("begin", { threadId: "a", revision: 2, draft: draft("kept"), draftId: "x" }).error).toBeUndefined();
});

it("prepares a notice-turn queued message while keeping ordinary activity and unknown receipt blocked", () => {
  const { session, handle, select } = setup(); select("a"); handle("read", { threadId: "a" });
  session.observe({ type: "historyState", history: { threadId: "a", phase: "current", navigationSafe: true } });
  session.observe({ type: "setInputEnabled", enabled: true });
  session.observe({ type: "liveStatus", turnActive: true, turnKind: "user" });
  expect(handle("begin", { threadId: "a", revision: 1, draft: draft("blocked"), draftId: "ordinary" }).error).toBeTruthy();
  session.observe({ type: "liveStatus", turnActive: true, turnKind: "notice" });
  expect(handle("begin", { threadId: "a", revision: 1, draft: draft("queued"), draftId: "notice" }).error).toBeUndefined();
  expect(session.dispatch({ type: "sendMessage", draftId: "notice" })).toBe(true);
  session.reject("notice"); session.renderAdmission({ threadId: "a", state: "unknown", checking: false });
  expect(handle("begin", { threadId: "a", revision: 2, draft: draft("unsafe"), draftId: "unknown" }).error).toBeTruthy();
});
