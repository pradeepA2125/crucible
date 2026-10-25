import { afterEach, expect, it, vi } from "vitest";
import { mkdtemp, mkdir, writeFile, realpath, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createWorkspaceFiles } from "../src/workspace-files.js";
import { createIgnoreWalk } from "../src/workspace-ignore.js";
const roots: string[] = [];
async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "crucible-ignore-"))); roots.push(root);
  await mkdir(join(root, ".git", "info"), { recursive: true });
  return { root, files: await createWorkspaceFiles(root) };
}
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
it("combines local exclude/root/nested rules, negation and excluded-parent semantics without extra folder heuristics", async () => {
  const { root, files } = await fixture(); await mkdir(join(root, "nested"));
  await writeFile(join(root, ".git", "info", "exclude"), "local-only\n*.cache\n");
  await writeFile(join(root, ".gitignore"), "*.log\n!keep.log\nignored/\n*.cache\n");
  await writeFile(join(root, "nested", ".gitignore"), "!child.log\n!keep.cache\n/only-here\n");
  await mkdir(join(root, "ignored")); await writeFile(join(root, "ignored", ".gitignore"), "!child.txt\n");
  const walk = createIgnoreWalk(files);
  for (const path of ["local-only", "other.log", "nested/other.log", "ignored/child.txt", "nested/only-here", "file.cache"]) expect(await walk.classify(path, false)).toBe("ignored");
  for (const path of ["keep.log", "nested/child.log", "nested/keep.cache", "only-here", "build", "dist", ".hidden"]) expect(await walk.classify(path, false)).toBe("included");
  expect(walk.warnings.size).toBe(0);
  await expect(files.preview(".git/info/exclude")).rejects.toMatchObject({ reason: "protected" });
});
it("fresh operations see changed rules and unreadable/oversize rules are unknown rather than silently ignored", async () => {
  const { root, files } = await fixture();
  await writeFile(join(root, ".gitignore"), "first\n");
  expect(await createIgnoreWalk(files).classify("first", false)).toBe("ignored");
  await writeFile(join(root, ".gitignore"), "second\n");
  expect(await createIgnoreWalk(files).classify("first", false)).toBe("included");
  await writeFile(join(root, ".gitignore"), "x".repeat(65537));
  const walk = createIgnoreWalk(files); expect(await walk.classify("first", false)).toBe("unknown"); expect(walk.warnings.size).toBe(1);
});
it("refuses symlinked ignore sources, including the private exclude reader", async () => {
  const { root, files } = await fixture(), outside = await fixture();
  await writeFile(join(outside.root, "rules"), "secret\n");
  await symlink(join(outside.root, "rules"), join(root, ".git", "info", "exclude"));
  await expect(files.readIgnoreExclude()).rejects.toMatchObject({ reason: "symlink" });
  expect(await createIgnoreWalk(files).classify("secret", false)).toBe("unknown");
});

it("matches mixed-case patterns on insensitive volumes while preserving sensitive-volume behavior", async () => {
  const { root, files } = await fixture(); await writeFile(join(root, ".gitignore"), "Build/\nlogs/\n*.LOG\n");
  const insensitive = createIgnoreWalk({ ...files, ignoreCase: vi.fn(async () => true) });
  const sensitive = createIgnoreWalk({ ...files, ignoreCase: vi.fn(async () => false) });
  for (const path of ["build/out.txt", "Logs/a.txt", "err.log"]) {
    expect(await insensitive.classify(path, false)).toBe("ignored");
    expect(await sensitive.classify(path, false)).toBe("included");
  }
  const unknown = createIgnoreWalk({ ...files, ignoreCase: vi.fn(async () => undefined) });
  expect(await unknown.classify("err.log", false)).toBe("unknown"); expect(unknown.warnings.size).toBe(1);
});
it("probes an existing local name read-only and reports unknown for a root without caseable names", async () => {
  const { root, files } = await fixture();
  expect(typeof await files.ignoreCase()).toBe("boolean");
  await rm(join(root, ".git"), { recursive: true }); await writeFile(join(root, "123"), "unchanged");
  expect(await files.ignoreCase()).toBeUndefined();
});
