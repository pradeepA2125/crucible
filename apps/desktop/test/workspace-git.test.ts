import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, realpathSync, renameSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { COMPARE_BYTES, createWorkspaceFiles } from "../src/workspace-files.js";
import { createGitReadOperations } from "../src/git-read-operation.js";
import { createWorkspaceGit, gitEnvironment, gitRunner, gitUnavailable, type GitScope } from "../src/workspace-git.js";

const roots: string[] = [];
const temp = (name: string) => { const dir = realpathSync(mkdtempSync(join(tmpdir(), `crucible-git-${name}-`))); roots.push(dir); return dir; };
afterEach(() => { for (const dir of roots.splice(0)) rmSync(dir, { recursive: true, force: true }); });
const home = () => temp("home");
const plainEnv = (homeDir: string) => ({ PATH: process.env.PATH, HOME: homeDir, GIT_CONFIG_NOSYSTEM: "1" });
const git = (cwd: string, homeDir: string, ...args: string[]) => execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", ...args], { cwd, env: plainEnv(homeDir), stdio: "pipe" });
const runner = async (root: string, homeDir: string) => { const files = await createWorkspaceFiles(root); return { files, run: gitRunner(files.root, { ...gitEnvironment(process.env), HOME: homeDir }) }; };
const open = async (root: string, homeDir: string) => { const { files, run } = await runner(root, homeDir); return createWorkspaceGit(files, run); };
const ready = (scope: GitScope) => { if (scope.state !== "ready") throw new Error(`unavailable: ${scope.reason}`); return scope; };
const listed = (scope: GitScope) => ready(scope).changes.map(change => `${change.status} ${change.path}${change.from ? ` <- ${change.from}` : ""}`);
const commitAll = (root: string, h: string) => { git(root, h, "add", "-A"); git(root, h, "commit", "-q", "-m", "base"); };

// Each test builds real repositories with many git invocations, which takes seconds on a busy machine.
describe("Uncommitted vs HEAD without Git worktree commands", { timeout: 30000 }, () => {
  it("reports modified, deleted, exact renames, added and untracked files while leaving ignored paths out", async () => {
    const root = temp("repo"), h = home();
    git(root, h, "init", "-q");
    writeFileSync(join(root, "a.txt"), "one\n"); writeFileSync(join(root, "b.txt"), "remove me\n"); writeFileSync(join(root, "e.txt"), "rename me exactly\n");
    writeFileSync(join(root, "keep.txt"), "same\n"); mkdirSync(join(root, "build")); writeFileSync(join(root, "build/tracked.txt"), "tracked but ignored\n");
    writeFileSync(join(root, "image.bin"), Buffer.from([0, 1, 2, 3]));
    commitAll(root, h);
    writeFileSync(join(root, ".gitignore"), "build/\nnode_modules/\n");
    writeFileSync(join(root, "a.txt"), "two\n"); unlinkSync(join(root, "b.txt")); renameSync(join(root, "e.txt"), join(root, "f.txt"));
    writeFileSync(join(root, "c.txt"), "untracked\n"); writeFileSync(join(root, "d.txt"), "staged\n"); git(root, h, "add", "d.txt");
    writeFileSync(join(root, "build/new.txt"), "ignored\n"); mkdirSync(join(root, "node_modules")); writeFileSync(join(root, "node_modules/x.js"), "x");
    writeFileSync(join(root, "image.bin"), Buffer.from([0, 9, 9, 9]));
    const workspace = await open(root, h), scope = ready(await workspace.scope());
    expect(listed(scope)).toEqual(["U .gitignore", "M a.txt", "D b.txt", "U c.txt", "A d.txt", "R f.txt <- e.txt", "M image.bin"]);
    expect([scope.partial, scope.uncompared, scope.rawBytes]).toEqual([false, [], true]);
    expect(scope.warnings.join(" ")).toMatch(/raw bytes/);
    const modified = await workspace.diff(scope.generation, "a.txt");
    expect(modified).toMatchObject({ kind: "text", additions: 1, deletions: 1, truncated: false });
    expect(modified.kind === "text" && modified.unified).toContain("-one"); expect(modified.kind === "text" && modified.unified).toContain("+two");
    expect(await workspace.diff(scope.generation, "b.txt")).toMatchObject({ kind: "text", additions: 0, deletions: 1 });
    expect(await workspace.diff(scope.generation, "c.txt")).toMatchObject({ kind: "text", additions: 1, deletions: 0 });
    expect(await workspace.diff(scope.generation, "image.bin")).toMatchObject({ kind: "binary", fingerprint: expect.stringMatching(/^[0-9a-f]{64}$/) });
  });

  it("is honestly unavailable outside Git and scopes a project that is a repository subfolder", async () => {
    const plain = temp("plain"), h = home();
    writeFileSync(join(plain, "x.txt"), "x");
    expect(await (await open(plain, h)).scope()).toEqual({ state: "unavailable", reason: "Not a Git repository." });
    const repo = temp("parent"); git(repo, h, "init", "-q"); mkdirSync(join(repo, "project"));
    writeFileSync(join(repo, "outside.txt"), "o\n"); writeFileSync(join(repo, "project/in.txt"), "i\n");
    commitAll(repo, h);
    writeFileSync(join(repo, "outside.txt"), "changed\n"); writeFileSync(join(repo, "project/in.txt"), "changed\n");
    expect(ready(await (await open(join(repo, "project"), h)).scope()).changes).toEqual([{ path: "in.txt", status: "M" }]);
  });

  it("diffs type changes by the current type and never claims an unexamined large file changed", async () => {
    const root = temp("links"), h = home();
    git(root, h, "init", "-q");
    writeFileSync(join(root, "a.txt"), "a\n"); writeFileSync(join(root, "b.txt"), "b\n"); writeFileSync(join(root, "became-link"), "was a file\n");
    symlinkSync("a.txt", join(root, "retarget")); symlinkSync("a.txt", join(root, "became-file")); symlinkSync("a.txt", join(root, "same-link"));
    writeFileSync(join(root, "big-same.txt"), Buffer.alloc(COMPARE_BYTES + 1, 0x61)); writeFileSync(join(root, "big-equal-size.txt"), Buffer.alloc(COMPARE_BYTES + 1, 0x61));
    writeFileSync(join(root, "big-grown.txt"), Buffer.alloc(COMPARE_BYTES + 1, 0x61));
    commitAll(root, h);
    unlinkSync(join(root, "retarget")); symlinkSync("b.txt", join(root, "retarget"));
    unlinkSync(join(root, "became-file")); writeFileSync(join(root, "became-file"), "now a file\n");
    unlinkSync(join(root, "became-link")); symlinkSync("b.txt", join(root, "became-link"));
    writeFileSync(join(root, "big-equal-size.txt"), Buffer.alloc(COMPARE_BYTES + 1, 0x62)); writeFileSync(join(root, "big-grown.txt"), Buffer.alloc(COMPARE_BYTES + 2, 0x61));
    const workspace = await open(root, h), scope = ready(await workspace.scope());
    expect(scope.changes).toEqual([
      { path: "became-file", status: "M", type: { from: "link", to: "file" } },
      { path: "became-link", status: "M", type: { from: "file", to: "link" } },
      { path: "big-grown.txt", status: "M", note: "too-large" },
      { path: "retarget", status: "M" },
    ]);
    // Equal sizes above the cap prove nothing either way: listed apart, never counted as M.
    expect(scope.uncompared).toEqual([{ path: "big-equal-size.txt", reason: "too-large" }, { path: "big-same.txt", reason: "too-large" }]);
    expect(scope.partial).toBe(true);
    expect(scope.warnings.join(" ")).toMatch(/2 tracked files were not compared/);
    const toFile = await workspace.diff(scope.generation, "became-file"), toLink = await workspace.diff(scope.generation, "became-link");
    expect(toFile).toMatchObject({ kind: "text", type: { from: "link", to: "file" } });
    expect(toFile.kind === "text" && toFile.unified).toContain("-a.txt"); expect(toFile.kind === "text" && toFile.unified).toContain("+now a file");
    expect(toLink).toMatchObject({ kind: "text", type: { from: "file", to: "link" } });
    expect(toLink.kind === "text" && toLink.unified).toContain("-was a file"); expect(toLink.kind === "text" && toLink.unified).toContain("+b.txt");
    expect(await workspace.diff(scope.generation, "retarget")).toMatchObject({ kind: "text", additions: 1, deletions: 1 });
    expect(await workspace.diff(scope.generation, "big-grown.txt")).toEqual({ kind: "too-large" });
    expect(await workspace.diff(scope.generation, "big-same.txt")).toMatchObject({ kind: "unavailable" });
  });

  it("reports executable-bit changes with zero hunks unless the repository ignores file modes", async () => {
    const root = temp("mode"), h = home();
    git(root, h, "init", "-q");
    writeFileSync(join(root, "run.sh"), "echo hi\n"); writeFileSync(join(root, "tool.sh"), "echo tool\n"); chmodSync(join(root, "tool.sh"), 0o755);
    commitAll(root, h);
    chmodSync(join(root, "run.sh"), 0o755); chmodSync(join(root, "tool.sh"), 0o644);
    const workspace = await open(root, h), scope = ready(await workspace.scope());
    expect(scope.changes).toEqual([
      { path: "run.sh", status: "M", mode: { from: "100644", to: "100755" } },
      { path: "tool.sh", status: "M", mode: { from: "100755", to: "100644" } },
    ]);
    expect(await workspace.diff(scope.generation, "run.sh")).toMatchObject({ kind: "text", unified: "", additions: 0, deletions: 0, truncated: false, mode: { from: "100644", to: "100755" } });
    git(root, h, "config", "core.filemode", "false");
    const ignored = ready(await workspace.scope());
    expect(ignored.changes).toEqual([]);
    expect(ignored.warnings.join(" ")).toMatch(/core\.filemode=false/);
  });

  it("hashes SHA-256 repositories with SHA-256", async () => {
    const root = temp("sha256"), h = home();
    git(root, h, "init", "-q", "--object-format=sha256");
    writeFileSync(join(root, "same.txt"), "same\n"); writeFileSync(join(root, "edit.txt"), "before\n");
    commitAll(root, h);
    writeFileSync(join(root, "edit.txt"), "after\n");
    const workspace = await open(root, h), scope = ready(await workspace.scope());
    expect(listed(scope)).toEqual(["M edit.txt"]);
    expect(await workspace.diff(scope.generation, "edit.txt")).toMatchObject({ kind: "text", additions: 1, deletions: 1 });
  });

  it("marks changes covered by nested attribute rules and always states the raw-byte limit", async () => {
    const root = temp("attrs"), h = home();
    git(root, h, "init", "-q");
    mkdirSync(join(root, "nested")); writeFileSync(join(root, "nested/.gitattributes"), "*.txt text eol=crlf\n");
    writeFileSync(join(root, "nested/a.txt"), "one\n"); writeFileSync(join(root, "top.txt"), "one\n");
    commitAll(root, h);
    writeFileSync(join(root, "nested/a.txt"), "one\r\n"); writeFileSync(join(root, "top.txt"), "two\n"); writeFileSync(join(root, "nested/new.txt"), "n\n");
    const scope = ready(await (await open(root, h)).scope());
    expect(scope.changes).toEqual([
      { path: "nested/a.txt", status: "M", note: "attributes" },
      { path: "nested/new.txt", status: "U", note: "attributes" },
      { path: "top.txt", status: "M" },
    ]);
    expect(scope.warnings.join(" ")).toMatch(/raw bytes; \.gitattributes line-ending and filter rules are not applied/);
  });

  it("answers diffs only from the host's captured comparison, keeping the two newest", async () => {
    const root = temp("gen"), h = home();
    git(root, h, "init", "-q");
    writeFileSync(join(root, "a.txt"), "one\n"); writeFileSync(join(root, "b.txt"), "b\n");
    commitAll(root, h);
    writeFileSync(join(root, "a.txt"), "two\n");
    const workspace = await open(root, h);
    const first = ready(await workspace.scope());
    // The baseline the user chose stays fixed even if HEAD moves before the diff is read.
    git(root, h, "commit", "-q", "-am", "moved");
    writeFileSync(join(root, "a.txt"), "three\n");
    const fixed = await workspace.diff(first.generation, "a.txt");
    expect(fixed.kind === "text" && fixed.unified).toContain("-one");
    expect(await workspace.diff(first.generation, "b.txt")).toEqual({ kind: "unavailable", reason: "This file is not in the current comparison." });
    const second = ready(await workspace.scope()), third = ready(await workspace.scope());
    expect(new Set([first.generation, second.generation, third.generation]).size).toBe(3);
    expect(await workspace.diff(first.generation, "a.txt")).toEqual({ kind: "unavailable", reason: "This comparison is out of date. Refresh Changes." });
    expect((await workspace.diff(second.generation, "a.txt")).kind).toBe("text");
    expect(await workspace.diff("not-a-generation", "a.txt")).toMatchObject({ kind: "unavailable" });
  });

  it("never fetches missing objects of a partial clone, although plain Git would", async () => {
    const origin = temp("origin"), h = home(), parent = temp("clones");
    git(origin, h, "init", "-q"); writeFileSync(join(origin, "a.txt"), "remote only\n"); writeFileSync(join(origin, "b.txt"), "b\n"); commitAll(origin, h);
    git(origin, h, "config", "uploadpack.allowfilter", "true");
    const clone = (name: string, ...extra: string[]) => { git(parent, h, "clone", "-q", "--filter=blob:none", ...extra, `file://${origin}`, name); return join(parent, name); };
    const packs = (repo: string) => readdirSync(join(repo, ".git/objects/pack")).filter(name => name.endsWith(".pack")).length;
    // Positive control: ordinary Git downloads a missing blob on demand.
    const control = clone("control", "--no-checkout"), id = execFileSync("git", ["-C", control, "rev-parse", "HEAD:a.txt"], { env: plainEnv(h) }).toString().trim();
    const before = packs(control);
    expect(execFileSync("git", ["-C", control, "cat-file", "blob", id], { env: plainEnv(h) }).toString()).toBe("remote only\n");
    expect(packs(control)).toBeGreaterThan(before);
    const missing = clone("missing", "--no-checkout"), missingPacks = packs(missing);
    expect(await (await open(missing, h)).scope()).toEqual({ state: "unavailable", reason: "This partial clone is missing objects; comparing would need a download, which Crucible never starts." });
    expect(packs(missing)).toBe(missingPacks);
    // A checked-out partial clone holds its HEAD blobs and compares normally, still without fetching.
    const present = clone("present"), presentPacks = packs(present);
    writeFileSync(join(present, "a.txt"), "edited\n");
    const workspace = await open(present, h), scope = ready(await workspace.scope());
    expect(listed(scope)).toEqual(["M a.txt"]);
    expect(await workspace.diff(scope.generation, "a.txt")).toMatchObject({ kind: "text", additions: 1, deletions: 1 });
    expect(packs(present)).toBe(presentPacks);
  });

  it("keeps fetching closed when the repository allows a protocol itself", async () => {
    const origin = temp("origin"), h = home(), parent = temp("clones"), sentinels = temp("sentinels"), helpers = temp("helpers");
    git(origin, h, "init", "-q"); writeFileSync(join(origin, "a.txt"), "remote only\n"); commitAll(origin, h);
    git(origin, h, "config", "uploadpack.allowfilter", "true");
    const clone = (name: string) => { git(parent, h, "clone", "-q", "--filter=blob:none", "--no-checkout", `file://${origin}`, name); return join(parent, name); };
    const packs = (repo: string) => readdirSync(join(repo, ".git/objects/pack")).filter(name => name.endsWith(".pack")).length;
    const helper = join(helpers, "helper.sh");
    writeFileSync(helper, `#!/bin/sh\ntouch "${join(sentinels, "ext-helper")}"\nexit 1\n`); chmodSync(helper, 0o755);
    const fileAllowed = clone("file-allowed");
    git(fileAllowed, h, "config", "protocol.file.allow", "always");
    const extAllowed = clone("ext-allowed");
    git(extAllowed, h, "config", "protocol.ext.allow", "always"); git(extAllowed, h, "config", "remote.origin.url", `ext::${helper}`);
    // Positive controls: with these repository settings, ordinary Git downloads and runs the helper.
    const control = clone("control"); git(control, h, "config", "protocol.file.allow", "always");
    const controlPacks = packs(control);
    execFileSync("git", ["-c", "protocol.allow=never", "-C", control, "ls-tree", "-r", "-l", "HEAD"], { env: plainEnv(h), stdio: "pipe" });
    expect(packs(control)).toBeGreaterThan(controlPacks);
    const extControl = clone("ext-control"); git(extControl, h, "config", "protocol.ext.allow", "always"); git(extControl, h, "config", "remote.origin.url", `ext::${helper}`);
    try { execFileSync("git", ["-c", "protocol.allow=never", "-C", extControl, "ls-tree", "-r", "-l", "HEAD"], { env: plainEnv(h), stdio: "pipe" }); } catch { /* the helper fails the fetch */ }
    expect(readdirSync(sentinels)).toEqual(["ext-helper"]);
    rmSync(join(sentinels, "ext-helper"));
    const before = packs(fileAllowed);
    expect(await (await open(fileAllowed, h)).scope()).toMatchObject({ state: "unavailable" });
    expect(packs(fileAllowed)).toBe(before);
    expect(await (await open(extAllowed, h)).scope()).toMatchObject({ state: "unavailable" });
    expect(readdirSync(sentinels)).toEqual([]);
  });

  it("requires a Git that reports object formats, and counts rename hashing in the time budget", async () => {
    const root = temp("gates"), h = home();
    git(root, h, "init", "-q"); writeFileSync(join(root, "old.txt"), "same bytes\n"); commitAll(root, h);
    renameSync(join(root, "old.txt"), join(root, "new.txt"));
    const { files, run } = await runner(root, h);
    const oldGit = createWorkspaceGit(files, async (args, max) => args[0] === "--version" ? Buffer.from("git version 2.24.1\n") : run(args, max));
    expect(await oldGit.scope()).toEqual({ state: "unavailable", reason: "Git 2.25 or newer is required for Uncommitted vs HEAD." });
    const oddFormat = createWorkspaceGit(files, async (args, max) => args.includes("--show-object-format") ? Buffer.from("blake3\n") : run(args, max));
    expect(await oddFormat.scope()).toEqual({ state: "unavailable", reason: 'Git object format "blake3" is not supported.' });
    // The clock runs out only after the walk, so the exact rename is never hashed.
    let elapsed = 0;
    const listOriginal = files.list;
    files.list = async (...args) => { const page = await listOriginal(...args); if (!page.nextCursor) elapsed = 60_000; return page; };
    const late = ready(await createWorkspaceGit(files, run, () => elapsed).scope());
    files.list = listOriginal;
    expect(listed(late)).toEqual(["U new.txt", "D old.txt"]);
    expect(late.partial).toBe(true);
  });

  it("never runs repository-configured programs, although plain git status would", async () => {
    const root = temp("hostile"), h = home(), traps = temp("traps"), sentinels = temp("sentinels");
    git(root, h, "init", "-q");
    writeFileSync(join(root, "tracked.txt"), "base\n"); commitAll(root, h);
    const trap = (name: string, tail = "") => { const path = join(traps, `${name}.sh`); writeFileSync(path, `#!/bin/sh\ntouch "${join(sentinels, name)}"\n${tail}`); chmodSync(path, 0o755); return path; };
    mkdirSync(join(traps, "hooks"));
    for (const hook of ["post-index-change", "reference-transaction", "pre-auto-gc"]) { writeFileSync(join(traps, "hooks", hook), `#!/bin/sh\ntouch "${join(sentinels, `hook-${hook}`)}"\n`); chmodSync(join(traps, "hooks", hook), 0o755); }
    for (const [key, value] of [["core.fsmonitor", trap("fsmonitor")], ["filter.evil.clean", trap("clean", "cat\n")], ["filter.evil.smudge", trap("smudge", "cat\n")], ["filter.evil.required", "true"],
      ["diff.evil.textconv", trap("textconv", "cat \"$1\"\n")], ["diff.external", trap("external")], ["core.sshCommand", trap("ssh")], ["core.hooksPath", join(traps, "hooks")], ["core.pager", trap("pager")]] as const)
      git(root, h, "config", key, value);
    writeFileSync(join(root, ".gitattributes"), "* filter=evil diff=evil\n");
    writeFileSync(join(root, "tracked.txt"), "changed by the user\n"); writeFileSync(join(root, "new.txt"), "untracked\n");
    // Positive control: the same repository makes ordinary git status run attacker-chosen programs.
    try { execFileSync("git", ["status", "--porcelain"], { cwd: root, env: plainEnv(h), stdio: "pipe" }); } catch { /* the trap may also fail the command */ }
    expect(readdirSync(sentinels).length).toBeGreaterThan(0);
    for (const name of readdirSync(sentinels)) rmSync(join(sentinels, name));
    const workspace = await open(root, h), scope = ready(await workspace.scope());
    expect(scope.changes.map(change => `${change.status} ${change.path} ${change.note}`)).toEqual(["U .gitattributes attributes", "U new.txt attributes", "M tracked.txt attributes"]);
    for (const change of scope.changes) await workspace.diff(scope.generation, change.path);
    expect(readdirSync(sentinels)).toEqual([]);
    expect(existsSync(join(root, ".git", "index.lock"))).toBe(false);
  });

  it("maps refusals without overriding them and passes no inherited Git variables", () => {
    expect(gitUnavailable({ stderr: "fatal: detected dubious ownership in repository at '/x'" })).toMatch(/ownership/);
    expect(gitUnavailable({ stderr: "fatal: not a git repository (or any of the parent directories): .git" })).toBe("Not a Git repository.");
    expect(gitUnavailable(Object.assign(new Error("spawn git ENOENT"), { code: "ENOENT" }))).toMatch(/not installed/);
    const env = gitEnvironment({ PATH: "/bin", HOME: "/h", GIT_ALLOW_PROTOCOL: "file:ext", GIT_DIR: "/evil", GIT_WORK_TREE: "/evil", GIT_CONFIG_PARAMETERS: "'core.fsmonitor=/evil'", GIT_EXTERNAL_DIFF: "/evil" });
    expect(env).toEqual({ PATH: "/bin", HOME: "/h", GIT_CONFIG_NOSYSTEM: "1", GIT_TERMINAL_PROMPT: "0", GIT_OPTIONAL_LOCKS: "0", GIT_NO_REPLACE_OBJECTS: "1", GIT_NO_LAZY_FETCH: "1", GIT_ALLOW_PROTOCOL: "" });
  });
});

it("marks unsupported tracked names uncompared instead of falsely deleted, and never offers their diff", async () => {
  const root = temp("unsupported"), h = home(); git(root, h, "init", "-q");
  for (const path of ["a:b.txt", "bad\ufffdname.txt", "okay.txt"]) writeFileSync(join(root, path), "before\n");
  commitAll(root, h); unlinkSync(join(root, "a:b.txt")); writeFileSync(join(root, "fresh:name.txt"), "fresh");
  const workspace = await open(root, h), scope = ready(await workspace.scope());
  expect(scope.changes).toEqual([]);
  expect(scope.uncompared).toEqual([{ path: "a:b.txt", reason: "unsupported" }, { path: "bad\ufffdname.txt", reason: "unsupported" }]);
  expect(scope.partial).toBe(true); expect(scope.warnings.join(" ")).toMatch(/names cannot be shown/);
  expect(await workspace.diff(scope.generation, "a:b.txt")).toMatchObject({ kind: "unavailable", reason: expect.stringMatching(/filename/) });
});
it("stops an aborted scan before late filesystem completion can publish and evict a generation", async () => {
  const root = temp("late"), h = home(); git(root, h, "init", "-q"); writeFileSync(join(root, "a.txt"), "one\n"); commitAll(root, h); writeFileSync(join(root, "a.txt"), "two\n");
  const { files, run } = await runner(root, h); let hold = false, release!: () => void, started!: () => void;
  const readyRead = new Promise<void>(resolve => { started = resolve; });
  const workspace = createWorkspaceGit({ ...files, metadata: async paths => {
    if (hold) { hold = false; started(); await new Promise<void>(resolve => { release = resolve; }); }
    return files.metadata(paths);
  } }, run);
  const first = ready(await workspace.scope()); await workspace.scope();
  hold = true; const controller = new AbortController(), aborted = workspace.scope({ signal: controller.signal });
  const rejected = expect(aborted).rejects.toMatchObject({ reason: "cancelled" }); await readyRead; controller.abort(); await rejected;
  release(); await new Promise(resolve => setTimeout(resolve, 10));
  expect(await workspace.diff(first.generation, "a.txt")).toMatchObject({ kind: "text" });
});

it("reports typed filesystem busy at start, mid-scan and diff without publishing a partial generation", async () => {
  const root = temp("cap"), h = home(); git(root, h, "init", "-q"); writeFileSync(join(root, "a.txt"), "one\n"); commitAll(root, h); writeFileSync(join(root, "a.txt"), "two\n");
  const { files, run } = await runner(root, h); let armed = false, releaseIgnore: (() => void) | undefined;
  const workspace = createWorkspaceGit({ ...files, ignoreCase: () => armed
    ? new Promise<boolean>(resolve => { releaseIgnore = () => resolve(false); }) : files.ignoreCase() }, run);
  const first = ready(await workspace.scope()); await workspace.scope();
  const releases: Array<() => void> = [], controllers: AbortController[] = [];
  const jobs: Promise<unknown>[] = []; const perform = createGitReadOperations();
  try {
    for (let i = 0; i < 3; i++) {
      const controller = new AbortController(); controllers.push(controller);
      let began!: () => void; const started = new Promise<void>(resolve => { began = resolve; });
      jobs.push(perform(30000, { signal: controller.signal }, op => op.file(() => new Promise<void>(resolve => {
        releases.push(resolve); began();
      })), run).catch(error => error));
      await started;
    }
    // Three parked calls leave one slot: tracked reads succeed, then the background
    // ignore-case read occupies that last slot and list() must stop the whole scope.
    armed = true;
    await expect(workspace.scope()).rejects.toMatchObject({ reason: "busy" });
    expect(releaseIgnore).toBeTypeOf("function");
    await expect(workspace.scope()).rejects.toMatchObject({ reason: "busy" });
    await expect(workspace.diff(first.generation, "a.txt")).rejects.toMatchObject({ reason: "busy" });
  } finally {
    controllers.forEach(controller => controller.abort()); releases.forEach(resolve => resolve()); releaseIgnore?.();
    await Promise.all(jobs); await new Promise(resolve => setTimeout(resolve, 10));
  }
  // Two generations existed before the busy scan. A fabricated partial generation
  // would evict this oldest one; it must still answer after real waits settle.
  expect(await workspace.diff(first.generation, "a.txt")).toMatchObject({ kind: "text" });
});

it("fingerprints actual binary bytes consistently across refresh and invalidates changed bytes", async () => {
  const root = temp("fingerprint"), h = home(); git(root, h, "init", "-q"); writeFileSync(join(root, "image.bin"), Buffer.from([0, 1])); commitAll(root, h);
  writeFileSync(join(root, "image.bin"), Buffer.from([0, 2])); const workspace = await open(root, h);
  const a = ready(await workspace.scope()), first = await workspace.diff(a.generation, "image.bin");
  const b = ready(await workspace.scope()), same = await workspace.diff(b.generation, "image.bin"); expect(same).toEqual(first);
  writeFileSync(join(root, "image.bin"), Buffer.from([0, 3])); const c = ready(await workspace.scope()), changed = await workspace.diff(c.generation, "image.bin");
  expect(first.kind).toBe("binary"); expect(changed.kind).toBe("binary"); expect(changed).not.toEqual(first);
});
