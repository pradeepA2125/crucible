import { describe, expect, it, vi } from "vitest";
import { DraftStore, type Draft } from "../src/draft-store.js";
const draft = (text: string): Draft => ({ text, imageAttachments: [], mentionedPaths: ["sample.txt"] });
describe("project-scoped in-memory drafts", () => {
  it("orders renderer revisions independently of main recovery versions and separates projects", () => {
    const store = new DraftStore(), other = new DraftStore();
    store.save("a", 1, draft("first")); store.begin("a", 2, draft("sent"), "id"); store.save("a", 3, draft("typed during admission"));
    const returned = store.reject("id")!; expect(returned.revision).toBe(3); expect(returned.version).toBe(1);
    store.save("a", 4, draft("typed during admission, more")); store.save("a", 3, draft("late older save"));
    expect(store.read("a").draft.text).toBe("typed during admission, more"); expect(other.read("a").draft.text).toBe("");
    store.clear(); expect(store.read("a").draft.text).toBe("");
  });
  it("never hydrates in-flight sends, refuses duplicate dispatch and abandons only preparation", () => {
    const store = new DraftStore(); store.begin("a", 1, draft("held"), "send-a");
    expect(store.read("a").draft.text).toBe(""); expect(() => store.begin("b", 1, draft("duplicate"), "other")).toThrow("already");
    expect(store.isPrepared("send-a")).toBe(true); expect(store.dispatch("send-a")).toBe(true); expect(store.dispatch("send-a")).toBe(false);
    expect(store.abandonPrepared()).toBeUndefined();
    store.save("b", 1, draft("visible")); expect(store.reject("send-a")?.threadId).toBe("a"); expect(store.read("b").draft.text).toBe("visible");
    store.choose("a", 2, 1, draft(""), "discard"); store.begin("a", 3, draft("not dispatched"), "next");
    expect(store.abandonPrepared()?.returned?.draft.text).toBe("not dispatched");
  });
  it("swaps a returned message with the supplied current draft and discards without losing unflushed text", () => {
    const store = new DraftStore(); store.begin("a", 1, draft("sent original"), "id"); store.save("a", 2, draft("saved older text")); store.reject("id");
    const result = store.choose("a", 3, 1, draft("latest unsaved text"), "restore");
    expect(result.draft.text).toBe("sent original"); expect(result.returned?.draft.text).toBe("latest unsaved text");
    expect(() => store.choose("a", 4, 1, draft("stale choice"), "discard")).toThrow("changed");
    store.choose("a", 4, 2, draft("typing after swap, not yet saved"), "discard");
    expect(store.read("a").draft.text).toBe("typing after swap, not yet saved"); expect(store.read("a").returned).toBeUndefined();
  });
  it("accepts once, preserves later edits, and moves new-chat state before acceptance or refusal", () => {
    const store = new DraftStore(); store.begin("new", 1, draft("submit"), "id"); store.save("new", 2, draft("later"));
    store.move("id", "created"); expect(store.accept("id", "created")?.draft.text).toBe("later"); expect(store.reject("id")).toBeUndefined();
    store.begin("new", 1, draft("submit"), "second"); store.move("second", "created2"); store.reject("second");
    expect(store.read("created2").returned?.draft.text).toBe("submit");
  });
  it("protects the active images, drops older images before text, and retains revision floors after eviction", () => {
    const store = new DraftStore(1200, 2);
    const image = { name: "image", mediaType: "image/png", dataUrl: "x".repeat(600), bytes: 450 };
    store.save("a", 1, { ...draft("kept"), imageAttachments: [image] }); store.save("b", 1, { ...draft("active"), imageAttachments: [image] });
    const older = store.read("a"); expect(older.draft.imageAttachments).toEqual([]); expect(older.draft.text).toBe("kept"); expect(older.notice).toContain("images again");
    expect(store.read("a").notice).toBeUndefined(); expect(store.read("b").draft.imageAttachments).toHaveLength(1);
    store.save("a", 2, draft("next edit applies")); expect(store.read("a").draft.text).toBe("next edit applies");
    store.save("c", 1, draft("third")); const evicted = store.read("b"); expect(evicted.notice).toContain("could not be retained"); expect(store.read("b").notice).toBeUndefined();
  });
  it("keeps a maximal active image draft while a maximal submission occupies its separate reservation", () => {
    const store = new DraftStore();
    const dataUrl = "data:image/png;base64," + "A".repeat(4 * 1024 * 1024);
    const maximal = { ...draft("images"), imageAttachments: Array.from({ length: 8 }, (_, i) => ({ name: `image${i}`, mediaType: "image/png", dataUrl, bytes: 3 * 1024 * 1024 })) };
    store.begin("send", 1, maximal, "id"); store.save("active", 1, maximal);
    expect(store.read("active").draft.imageAttachments).toHaveLength(8); expect(store.hasSubmission).toBe(true);
    const serialize = vi.spyOn(JSON, "stringify");
    store.save("active", 2, { text: "typing does not serialize images again", mentionedPaths: [] });
    expect(serialize.mock.calls.some(([value]) => Array.isArray(value) && value.length === 8)).toBe(false);
    serialize.mockRestore(); expect(store.read("active").draft.imageAttachments).toHaveLength(8);
    store.reject("id"); expect(store.read("send").returned?.draft.text).toBe("images");
  });
});
