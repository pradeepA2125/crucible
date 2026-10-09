import { describe, expect, it, vi } from "vitest";
import { openFileReference } from "../src/file-references/open-reference.js";
import type { RetainedFile } from "../src/file-references/catalog.js";
const file = (workspaceId = "one", relativePath = "src/ProviderSection.test.tsx"): RetainedFile<string> => ({
  entry: { workspaceId, workspaceLabel: workspaceId, relativePath },
  uri: `file:///${workspaceId}/${relativePath}`, root: `file:///${workspaceId}`,
});
function setup(files = [file()]) {
  return {
    refresh: vi.fn(async () => ({ revision: 1, files })),
    identity: (uri: string) => uri,
    isCurrentFile: vi.fn(async () => true),
    pick: vi.fn(async (_files: readonly RetainedFile<string>[]): Promise<RetainedFile<string> | undefined> => undefined),
    open: vi.fn(async (_uri: string) => {}), notice: vi.fn(),
  };
}
describe("safe file-reference activation", () => {
  it("opens the retained URI for a unique full basename", async () => {
    const f = file(); const deps = setup([f]);
    await openFileReference("ProviderSection.test.tsx", deps);
    expect(deps.open).toHaveBeenCalledWith(f.uri);
    expect(deps.pick).not.toHaveBeenCalled();
    expect(deps.isCurrentFile).toHaveBeenCalledWith(f);
  });
  it("passes the citation location to the editor after resolving the file", async () => {
    const f = file(); const deps = setup([f]);
    await openFileReference("src/ProviderSection.test.tsx:67–87", deps);
    expect(deps.open).toHaveBeenCalledWith(f.uri, { line: 67, endLine: 87 });
  });
  it("uses exact directory paths with an optional ./ and never falls back", async () => {
    const deps = setup();
    await openFileReference("./src/ProviderSection.test.tsx", deps);
    expect(deps.open).toHaveBeenCalledTimes(1);
    await openFileReference("missing/ProviderSection.test.tsx", deps);
    expect(deps.open).toHaveBeenCalledTimes(1);
    expect(deps.notice).toHaveBeenCalledWith("Workspace file not found: missing/ProviderSection.test.tsx");
  });
  it("picks among same relative paths in distinct roots and refreshes after selection", async () => {
    const a = file("one"), b = file("two"); const deps = setup([b, a]);
    deps.pick.mockResolvedValue(b);
    await openFileReference("src/ProviderSection.test.tsx", deps);
    expect(deps.pick).toHaveBeenCalledWith([a, b]);
    expect(deps.refresh).toHaveBeenCalledTimes(2);
    expect(deps.open).toHaveBeenCalledWith(b.uri);
  });
  it("cancels a duplicate picker silently", async () => {
    const deps = setup([file("one"), file("two")]);
    await openFileReference("ProviderSection.test.tsx", deps);
    expect(deps.open).not.toHaveBeenCalled();
    expect(deps.notice).not.toHaveBeenCalled();
  });
  it("does not substitute another same-name file removed while picker was open", async () => {
    const a = file("one"), b = file("two"); const deps = setup([a, b]);
    deps.pick.mockResolvedValue(a);
    deps.refresh.mockResolvedValueOnce({ revision: 1, files: [a, b] }).mockResolvedValueOnce({ revision: 2, files: [b] });
    await openFileReference("ProviderSection.test.tsx", deps);
    expect(deps.open).not.toHaveBeenCalled();
    expect(deps.notice).toHaveBeenCalledWith("Workspace file not found: ProviderSection.test.tsx");
  });
  it("rejects a forged picker result even if it appears in a subsequent inventory", async () => {
    const a = file("one"), b = file("two"), forged = file("three"); const deps = setup([a, b]);
    deps.pick.mockResolvedValue(forged);
    deps.refresh.mockResolvedValueOnce({ revision: 1, files: [a, b] }).mockResolvedValueOnce({ revision: 2, files: [a, b, forged] });
    await openFileReference("ProviderSection.test.tsx", deps);
    expect(deps.open).not.toHaveBeenCalled();
  });
  it("handles missing files, empty workspace and a failed current-file check", async () => {
    const empty = setup([]);
    await openFileReference("ProviderSection.test.tsx", empty);
    expect(empty.open).not.toHaveBeenCalled();
    expect(empty.notice).toHaveBeenCalled();
    const deps = setup(); deps.isCurrentFile.mockResolvedValue(false);
    await openFileReference("ProviderSection.test.tsx", deps);
    expect(deps.open).not.toHaveBeenCalled();
    expect(deps.notice).toHaveBeenCalledWith("Workspace file not found: ProviderSection.test.tsx");
  });
  it.each([null, {}, "../a.ts", "/a.ts", "file:///a.ts", "https://x/a.ts", "a.ts:0", "a.ts:2-1", "a.ts#L1", "a%2ets", "a\\b.ts", " a.ts", "a", "a.ts?x", "a\u0000.ts"])("rejects unsafe input %j before inventory", async value => {
    const deps = setup();
    await openFileReference(value, deps);
    expect(deps.refresh).not.toHaveBeenCalled();
    expect(deps.open).not.toHaveBeenCalled();
    expect(deps.notice).toHaveBeenCalledWith("Cannot open an invalid file reference.");
  });
  it("reports opener and inventory rejection without throwing into the chat bridge", async () => {
    const deps = setup(); deps.open.mockRejectedValue(new Error("failed"));
    await expect(openFileReference("ProviderSection.test.tsx", deps)).resolves.toBeUndefined();
    expect(deps.notice).toHaveBeenCalledWith("Unable to open workspace file: ProviderSection.test.tsx");
    deps.refresh.mockRejectedValue(new Error("failed"));
    await expect(openFileReference("ProviderSection.test.tsx", deps)).resolves.toBeUndefined();
  });
});
