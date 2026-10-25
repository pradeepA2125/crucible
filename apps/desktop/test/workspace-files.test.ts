import { afterEach, expect, it, vi } from "vitest";
import { mkdtemp, mkdir, writeFile, symlink, rm, rename, open, opendir, realpath, lstat } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createWorkspaceFiles, workspaceRelativePath } from "../src/workspace-files.js";
import { WORKSPACE_FILE_LIMITS as LIMITS } from "../src/workspace-file-types.js";
vi.mock("node:fs/promises", async importOriginal => {
  const actual = await importOriginal<typeof import("node:fs/promises")>();
  return { ...actual, open: vi.fn(actual.open), opendir: vi.fn(actual.opendir), realpath: vi.fn(actual.realpath), lstat: vi.fn(actual.lstat) };
});
const roots: string[] = [];
async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "crucible-files-"))); roots.push(root);
  return { root, files: await createWorkspaceFiles(root) };
}
afterEach(async () => { vi.clearAllMocks(); await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
it("lists hidden and empty directories, protects internal data, and never follows links", async () => {
  const { root, files } = await fixture(), outside = await fixture();
  await mkdir(join(root, "empty")); await mkdir(join(root, ".git")); await mkdir(join(root, ".crucible"));
  await writeFile(join(root, ".hidden"), "visible"); await writeFile(join(outside.root, "secret"), "outside fixture");
  await symlink(outside.root, join(root, "outside")); await symlink(join(root, "empty"), join(root, "inside"));
  const page = await files.list();
  expect(page.entries).toEqual(expect.arrayContaining([
    expect.objectContaining({ path: "empty", kind: "directory" }), expect.objectContaining({ path: ".hidden", hidden: true, kind: "file", size: 7 }),
    expect.objectContaining({ path: ".git", kind: "protected" }), expect.objectContaining({ path: ".crucible", kind: "directory" }),
    expect.objectContaining({ path: "outside", kind: "symlink", targetLocation: "outside", linkTarget: outside.root }), expect.objectContaining({ path: "inside", kind: "symlink", targetLocation: "inside", targetPath: "empty" }),
  ]));
  expect(await files.list("empty")).toMatchObject({ entries: [], captured: 0, limited: false });
  await expect(files.list("outside")).rejects.toMatchObject({ reason: "symlink" });
  await expect(files.preview("outside/secret")).rejects.toMatchObject({ reason: "symlink" });
  await expect(files.list("inside")).rejects.toMatchObject({ reason: "symlink" });
  await mkdir(join(root, ".crucible", "agents")); await mkdir(join(root, ".crucible", "state"));
  await writeFile(join(root, ".crucible", "agents", "review.md"), "user agent");
  expect(await files.preview(".crucible/agents/review.md")).toMatchObject({ kind: "text", text: "user agent" });
  expect((await files.list(".crucible")).entries).toEqual(expect.arrayContaining([expect.objectContaining({ path: ".crucible/state", kind: "protected" })]));
  await expect(files.list(".crucible/state")).rejects.toMatchObject({ reason: "protected" });
  await expect(files.preview(".git/config")).rejects.toMatchObject({ reason: "protected" });
});
it.each(["/etc/passwd", "../outside", "a/../file", "a//file", "./file", "a/", "C:/file", "a\\file", "a\0file", ".GiT/config", "nested/.crucible/state"])("refuses unsafe path %s", path => {
  expect(() => workspaceRelativePath(path)).toThrow();
});
it("paginates sorted rows without dropping hidden entries and refuses stale or cross-directory cursors", async () => {
  const { root, files } = await fixture();
  await Promise.all(Array.from({ length: 205 }, (_, index) => writeFile(join(root, `item-${String(index).padStart(3, "0")}`), "")));
  vi.mocked(opendir).mockClear();
  const first = await files.list(), second = await files.list("", first.nextCursor);
  expect(opendir).toHaveBeenCalledTimes(1);
  expect(first.entries).toHaveLength(200); expect(second.entries).toHaveLength(5);
  expect(new Set([...first.entries, ...second.entries].map(entry => entry.path)).size).toBe(205);
  expect(second.nextCursor).toBeUndefined();
  await mkdir(join(root, "nested"));
  await expect(files.list("", first.nextCursor)).rejects.toMatchObject({ reason: "changed" });
  await expect(files.list("nested", first.nextCursor)).rejects.toMatchObject({ reason: "changed" });
  await expect(files.list("", "!")).rejects.toMatchObject({ reason: "invalid" });
});
it("returns bounded UTF8 text, binary and oversize states without executing content", async () => {
  const { root, files } = await fixture();
  await writeFile(join(root, "note.md"), "# Local <script>not executed</script>\n你好");
  await writeFile(join(root, "binary"), Buffer.from([1, 0, 2])); await writeFile(join(root, "invalid-utf8"), Buffer.from([255, 254]));
  await writeFile(join(root, "large"), Buffer.alloc(LIMITS.textBytes + 1, 65)); await writeFile(join(root, "empty"), "");
  expect(await files.preview("note.md")).toMatchObject({ kind: "text", text: "# Local <script>not executed</script>\n你好", sha256: expect.stringMatching(/^[a-f0-9]{64}$/) });
  expect(await files.preview("binary")).toMatchObject({ kind: "binary", size: 3 });
  expect(await files.preview("invalid-utf8")).toMatchObject({ kind: "binary", size: 2 });
  expect(await files.preview("large")).toMatchObject({ kind: "text", truncated: true, text: "A".repeat(LIMITS.textBytes), size: LIMITS.textBytes + 1 });
  expect(await files.preview("empty")).toMatchObject({ kind: "text", text: "", size: 0 });
  await expect(files.preview("missing")).rejects.toMatchObject({ reason: "missing" });
  await expect(files.preview("")).rejects.toMatchObject({ reason: "special" });
});
it("refuses a replaced workspace root rather than reading the replacement", async () => {
  const { root, files } = await fixture(), replacement = await fixture();
  await writeFile(join(replacement.root, "sample"), "replacement");
  const moved = `${root}-moved`; roots.push(moved); await rename(root, moved); await symlink(replacement.root, root);
  await expect(files.preview("sample")).rejects.toMatchObject({ reason: "changed" });
});

it("bounds a huge directory and keeps the same captured subset across its pages", async () => {
  const { root, files } = await fixture();
  await Promise.all(Array.from({ length: LIMITS.directory + 1 }, (_, index) => mkdir(join(root, `dir-${index}`))));
  const first = await files.list(); expect(first).toMatchObject({ captured: LIMITS.directory, limited: true });
  expect(first.entries).toHaveLength(LIMITS.page);
  const second = await files.list("", first.nextCursor);
  expect(second.captured).toBe(LIMITS.directory); expect(second.limited).toBe(true);
  expect(new Set([...first.entries, ...second.entries].map(entry => entry.path)).size).toBe(LIMITS.page * 2);
});
it("drops an incomplete UTF8 tail from a large head, strips a BOM, and labels UTF16 as binary", async () => {
  const { root, files } = await fixture();
  await writeFile(join(root, "tail"), Buffer.concat([Buffer.alloc(LIMITS.textBytes - 1, 65), Buffer.from("你好")]));
  expect(await files.preview("tail")).toMatchObject({ kind: "text", truncated: true, text: "A".repeat(LIMITS.textBytes - 1) });
  await writeFile(join(root, "bom"), Buffer.from([239, 187, 191, 65]));
  expect(await files.preview("bom")).toMatchObject({ kind: "text", text: "A", truncated: false });
  await writeFile(join(root, "utf16"), Buffer.from([255, 254, 65, 0]));
  expect(await files.preview("utf16")).toMatchObject({ kind: "binary" });
});
it.skipIf(process.platform === "win32")("a FIFO swapped in before open cannot block or be read", async () => {
  const { root, files } = await fixture(), path = join(root, "sample"); await writeFile(path, "regular");
  const actual = await vi.importActual<typeof import("node:fs/promises")>("node:fs/promises");
  vi.mocked(open).mockImplementationOnce(async (requested, flags, mode) => {
    await rm(path); execFileSync("/usr/bin/mkfifo", [path]);
    return actual.open(requested, flags, mode);
  });
  await expect(files.preview("sample")).rejects.toMatchObject({ reason: "changed" });
  expect((await files.list()).entries).toEqual([expect.objectContaining({ kind: "special" })]);
}, 1500);
it("a parent swapped for an outside symlink before open is refused before reading", async () => {
  const { root, files } = await fixture(), outside = await fixture();
  const parent = join(root, "folder"); await mkdir(parent);
  await writeFile(join(parent, "sample"), "inside"); await writeFile(join(outside.root, "sample"), "outside");
  const actual = await vi.importActual<typeof import("node:fs/promises")>("node:fs/promises");
  vi.mocked(open).mockImplementationOnce(async (requested, flags, mode) => {
    await rename(parent, join(root, "original")); await symlink(outside.root, parent);
    return actual.open(requested, flags, mode);
  });
  await expect(files.preview("folder/sample")).rejects.toMatchObject({ reason: "changed" });
});
it("turns unreadable file errors into a local state without leaking absolute host paths", async () => {
  const { root, files } = await fixture(); await writeFile(join(root, "sample"), "content");
  vi.mocked(open).mockRejectedValueOnce(Object.assign(new Error(`denied ${root}`), { code: "EACCES" }));
  await expect(files.preview("sample")).rejects.toMatchObject({ reason: "unreadable", message: expect.not.stringContaining(root) });
});

it("never probes outside symlink targets, including missing targets and in-root link chains", async () => {
  const { root, files } = await fixture(), outside = await fixture();
  await writeFile(join(outside.root, "exists"), "outside"); await writeFile(join(root, "ordinary"), "inside");
  await symlink(join(outside.root, "exists"), join(root, "exists-outside"));
  await symlink(join(outside.root, "missing"), join(root, "missing-outside"));
  await symlink("ordinary", join(root, "inside-link"));
  await symlink("exists-outside", join(root, "chain"));
  vi.mocked(realpath).mockClear(); vi.mocked(lstat).mockClear();
  const entries = (await files.list()).entries;
  expect(entries).toEqual(expect.arrayContaining([
    expect.objectContaining({ name: "exists-outside", targetLocation: "outside" }),
    expect.objectContaining({ name: "missing-outside", targetLocation: "outside" }),
    expect.objectContaining({ name: "inside-link", targetLocation: "inside" }),
    expect.objectContaining({ name: "chain", targetLocation: "unavailable" }),
  ]));
  for (const call of [...vi.mocked(realpath).mock.calls, ...vi.mocked(lstat).mock.calls]) {
    const path = String(call[0]); expect(path === root || path.startsWith(`${root}/`)).toBe(true);
  }
});

it("reports changed, missing and refused metadata without reading contents or following outside links", async () => {
  const { root, files } = await fixture(), outside = await fixture();
  await writeFile(join(root, "sample"), "old"); await writeFile(join(outside.root, "secret"), "outside");
  await symlink(outside.root, join(root, "outside"));
  const preview = await files.preview("sample"); expect(preview.revision).toMatch(/^[a-f0-9]{64}$/);
  vi.mocked(open).mockClear(); vi.mocked(lstat).mockClear();
  expect(await files.metadata(["sample"])).toEqual({ entries: [{ path: "sample", revision: preview.revision }] });
  expect(open).not.toHaveBeenCalled();
  await writeFile(join(root, "sample"), "new contents");
  expect((await files.metadata(["sample"])).entries[0]?.revision).not.toBe(preview.revision);
  const result = await files.metadata(["missing", ".git/config", "outside/secret", "../secret", ""]);
  expect(result.entries).toEqual([{ path: "missing", reason: "missing" }, { path: ".git/config", reason: "protected" }, { path: "outside/secret", reason: "symlink" }, { path: "../secret", reason: "invalid" }, { path: "", reason: "special" }]);
  expect(vi.mocked(lstat).mock.calls.some(call => String(call[0]).startsWith(outside.root))).toBe(false);
  await expect(files.metadata(Array.from({ length: 13 }, () => "sample"))).rejects.toMatchObject({ reason: "invalid" });
});
