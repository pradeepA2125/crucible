import { expect, it } from "vitest";
import { DraftStore } from "../src/drafts/draft-store.js";
const draft = (text: string) => ({ text, imageAttachments: [], mentionedPaths: [] });
it("conditionally clears only accepted feedback, preserving newer feedback and composer text", () => {
  const store = new DraftStore(), key = '@gate:["a","g","mode"]';
  store.save("a", 1, draft("composer")); const old = store.save(key, 1, draft("feedback"));
  store.save(key, 2, draft("newer feedback"));
  expect(store.acceptFeedback(key, old.revision, old.version)).toBeUndefined();
  expect(store.read(key).draft.text).toBe("newer feedback"); expect(store.read("a").draft.text).toBe("composer");
  const latest = store.read(key); expect(store.acceptFeedback(key, latest.revision, latest.version)?.draft.text).toBe("");
  expect(store.read("a").draft.text).toBe("composer");
});
