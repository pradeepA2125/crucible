import { describe, expect, it, vi } from "vitest";
import { FileReferenceCatalog } from "../src/file-references/catalog.js";
import { createFileReferenceBridge } from "../src/file-references/bridge.js";

function setup() {
  const entry = { workspaceId: "w", workspaceLabel: "workspace", relativePath: "src/a.ts" };
  const enumerate = vi.fn(async () => [{ entry, uri: "private-uri", root: "private-root" }]);
  const catalog = new FileReferenceCatalog({ enumerate, onInvalidate: () => ({ dispose() {} }), reportFailure: vi.fn() });
  const open = vi.fn(async (_reference: unknown) => {});
  const post = vi.fn();
  const bridge = createFileReferenceBridge(catalog, open, post);
  return { entry, enumerate, catalog, open, post, bridge };
}

describe("dedicated file-reference bridge", () => {
  it.each([null, [], 1, "requestFileReferenceCatalog", {}, { type: "openFile" }, { type: "listWorkspaceFiles" }])("ignores unrelated or malformed envelopes %j", async message => {
    const s = setup();
    try {
      expect(await s.bridge.handle(message)).toBe(false);
      expect(s.enumerate).not.toHaveBeenCalled();
      expect(s.open).not.toHaveBeenCalled();
      expect(s.post).not.toHaveBeenCalled();
    } finally { s.bridge.dispose(); s.catalog.dispose(); }
  });
  it("requests a fresh catalog and posts relative metadata exactly once", async () => {
    const s = setup();
    try {
      expect(await s.bridge.handle({ type: "requestFileReferenceCatalog" })).toBe(true);
      expect(s.post).toHaveBeenCalledTimes(1);
      expect(s.post).toHaveBeenCalledWith({ type: "fileReferenceCatalog", revision: 1, entries: [s.entry] });
      expect(JSON.stringify(s.post.mock.calls)).not.toContain("private-");
    } finally { s.bridge.dispose(); s.catalog.dispose(); }
  });
  it("recognizes opens and passes even malformed references to the safe opener", async () => {
    const s = setup();
    try {
      expect(await s.bridge.handle({ type: "openFileReference", reference: "a.ts" })).toBe(true);
      expect(s.open).toHaveBeenCalledWith("a.ts");
      expect(await s.bridge.handle({ type: "openFileReference", reference: { uri: "forged" } })).toBe(true);
      expect(s.open).toHaveBeenCalledWith({ uri: "forged" });
      expect(await s.bridge.handle({ type: "openFileReference" })).toBe(true);
      expect(s.open).toHaveBeenCalledWith(undefined);
      expect(s.enumerate).not.toHaveBeenCalled();
    } finally { s.bridge.dispose(); s.catalog.dispose(); }
  });
  it("republishes accepted catalog changes without per-message requests", async () => {
    const s = setup();
    try {
      await s.catalog.refresh();
      s.enumerate.mockResolvedValue([]);
      await s.catalog.refresh();
      expect(s.post).toHaveBeenLastCalledWith({ type: "fileReferenceCatalog", revision: 2, entries: [] });
      expect(s.post).toHaveBeenCalledTimes(2);
    } finally { s.bridge.dispose(); s.catalog.dispose(); }
  });
  it("stops publication and handling after disposal without disposing the shared catalog", async () => {
    const s = setup();
    try {
      s.bridge.dispose(); s.bridge.dispose();
      await s.catalog.refresh();
      expect(s.enumerate).toHaveBeenCalledTimes(1);
      expect(s.post).not.toHaveBeenCalled();
      expect(await s.bridge.handle({ type: "openFileReference", reference: "a.ts" })).toBe(false);
      expect(s.open).not.toHaveBeenCalled();
    } finally { s.catalog.dispose(); }
  });
});
