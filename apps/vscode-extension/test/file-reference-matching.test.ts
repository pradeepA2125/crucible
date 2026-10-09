import { describe, expect, it } from "vitest";
import { buildReferenceIndex, matchReference, parseReference } from "../src/file-references/matching.js";
const entry = (workspaceId: string, relativePath: string) => ({ workspaceId, workspaceLabel: workspaceId, relativePath });
const index = buildReferenceIndex([
  entry("one", "src/ProviderSection.test.tsx"), entry("one", "test/ProviderSection.test.tsx"),
  entry("two", "src/ProviderSection.test.tsx"), entry("one", "src/文件.test.ts"),
]);
describe("exact file references", () => {
  it("matches full basenames without case folding or substring guesses", () => {
    expect(matchReference(index, "ProviderSection.test.tsx")).toHaveLength(3);
    expect(matchReference(index, "providersection.test.tsx")).toEqual([]);
    expect(matchReference(index, "Section.test.tsx")).toEqual([]);
  });
  it("requires exact directory paths and accepts one leading ./", () => {
    expect(matchReference(index, "./src/ProviderSection.test.tsx")).toHaveLength(2);
    expect(matchReference(index, "other/ProviderSection.test.tsx")).toEqual([]);
    expect(matchReference(index, "./ProviderSection.test.tsx")).toHaveLength(3);
    expect(matchReference(index, "文件.test.ts")).toHaveLength(1);
  });
  it.each([null, {}, [], 42, "", "../a.ts", "src/../a.ts", "/a.ts", "C:/a.ts", "C:\\a.ts", "\\\\host\\a.ts", "command:a.ts", "file:a.ts", "https://a.ts", "a.ts:0", "a.ts:9-2", "a.ts:1:2", "a.ts#12", "a.ts?x", "a%2fts", "a b.ts", "a\u0000.ts", "src//a.ts", "././a.ts", "README", "a.ts/"])("rejects unsafe or non-file input %j", value => {
    expect(parseReference(value)).toBeNull();
    expect(matchReference(index, value)).toEqual([]);
  });
  it("matches line and line-range citations without losing their location", () => {
    expect(matchReference(index, "src/ProviderSection.test.tsx:67–87")).toHaveLength(2);
    expect(parseReference("src/ProviderSection.test.tsx:67-87")).toEqual({
      path: "src/ProviderSection.test.tsx", hasDirectory: true, line: 67, endLine: 87,
    });
  });
  it("does not truncate the inventory at 5000 entries", () => {
    const entries = Array.from({ length: 5001 }, (_, i) => entry("w", `src/f${i}.ts`));
    expect(matchReference(buildReferenceIndex(entries), "f5000.ts")).toEqual([entries[5000]]);
  });
});
