import { execFile } from "node:child_process";
import { createGitReadOperations, GitReadStopped, type GitReadOptions, type GitRunOptions } from "./git-read-operation.js";
import { isWorkspacePath } from "./workspace-path.js";
export { GitReadStopped, type GitReadOptions } from "./git-read-operation.js";
import { createHash, randomUUID } from "node:crypto";
import { realpath } from "node:fs/promises";
import { isAbsolute, posix, relative, sep } from "node:path";
import { setImmediate } from "node:timers/promises";
import { createTwoFilesPatch } from "diff";
import { createIgnoreWalk } from "./workspace-ignore.js";
import { WorkspaceFileError, type createWorkspaceFiles } from "./workspace-files.js";

/** Read-only "Uncommitted vs HEAD" for an untrusted project.
 * Never runs `git status`/`git diff` on the worktree: those can execute repository-configured
 * clean filters, fsmonitor, textconv or external diff programs. Git only lists objects
 * (rev-parse, ls-tree, ls-files --cached, config --get) and prints raw blobs (cat-file, never
 * --filters); worktree bytes come from the descriptor-safe file core and are hashed as blobs here.
 * Comparison is of raw bytes: .gitattributes eol/filter rules are not applied, and every scope says so. */
export const GIT_LIMITS = { tracked: 20000, scanned: 50000, milliseconds: 10000, renameCandidates: 200, attributeFiles: 100, diffBytes: 1024 * 1024, diffLines: 3000, generations: 2 } as const;
const MINIMUM_GIT: readonly [number, number] = [2, 25];
export type GitStatus = "M" | "A" | "D" | "R" | "U";
export type GitEntryType = "file" | "link";
export interface GitChange {
  path: string; status: GitStatus; from?: string;
  /** Executable bit only (Git records 100644/100755); present when it changed. */
  mode?: { from: string; to: string };
  type?: { from: GitEntryType; to: GitEntryType };
  /** attributes: a .gitattributes conversion rule covers it · too-large: proven changed, content not compared. */
  note?: "attributes" | "too-large";
}
/** Tracked paths that could not be compared; never counted as changes. */
export interface GitUncompared { path: string; reason: "too-large" | "unreadable" | "unsupported"; }
export type GitScope = { state: "unavailable"; reason: string }
  | { state: "ready"; generation: string; head?: string; changes: GitChange[]; uncompared: GitUncompared[]; partial: boolean; rawBytes: true; warnings: string[] };
type ChangeFacts = Pick<GitChange, "mode" | "type"> & { fingerprint?: string };
export type GitDiff = ({ kind: "text"; unified: string; additions: number; deletions: number; truncated: boolean } & ChangeFacts) | ({ kind: "binary" } & ChangeFacts) | { kind: "too-large" } | { kind: "unavailable"; reason: string };
type Files = Awaited<ReturnType<typeof createWorkspaceFiles>>;
type Runner = (args: string[], maxBuffer: number, options?: GitRunOptions) => Promise<Buffer>;
interface Tracked { mode: string; id: string; size: number; }
interface Generation { tracked: Map<string, Tracked>; changes: Map<string, GitChange>; }
type Current = { kind: "missing" } | { kind: "other" } | { kind: "link"; text: string } | { kind: "file"; revision: string };

const SAFE_ARGS = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never", "-c", "core.untrackedCache=false"];
/** Ordinary OS variables only: no GIT_DIR/GIT_WORK_TREE/GIT_CONFIG_* inherited from the host.
 * An empty GIT_ALLOW_PROTOCOL allows no transport at all and, unlike protocol.allow=never, cannot be
 * overridden by a repository's own protocol.<name>.allow, so a lazy fetch of missing objects can
 * neither download nor run an ext:: helper. GIT_NO_LAZY_FETCH additionally stops it on Git 2.44+. */
export function gitEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of ["PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SystemRoot", "USERPROFILE"]) if (typeof source[key] === "string") env[key] = source[key];
  return { ...env, GIT_CONFIG_NOSYSTEM: "1", GIT_TERMINAL_PROMPT: "0", GIT_OPTIONAL_LOCKS: "0", GIT_NO_REPLACE_OBJECTS: "1", GIT_NO_LAZY_FETCH: "1", GIT_ALLOW_PROTOCOL: "" };
}
export function gitRunner(root: string, env = gitEnvironment(process.env)): Runner {
  return (args, maxBuffer, options = {}) => new Promise((resolve, reject) => {
    execFile("git", [...SAFE_ARGS, "-C", root, ...args], { env, shell: false, timeout: Math.min(5000, options.timeout ?? 5000), signal: options.signal, maxBuffer, encoding: "buffer", windowsHide: true },
      (error, stdout, stderr) => error ? reject(Object.assign(error, { stderr: String(stderr) })) : resolve(stdout));
  });
}
/** Translate Git's refusals into an honest unavailable reason; never override ownership checks. */
export function gitUnavailable(error: unknown): string {
  const text = String((error as { stderr?: string })?.stderr ?? (error as Error)?.message ?? "");
  if (/dubious ownership/i.test(text)) return "Git refused this repository's ownership; Uncommitted vs HEAD is unavailable.";
  if (/not a git repository/i.test(text)) return "Not a Git repository.";
  if ((error as NodeJS.ErrnoException)?.code === "ENOENT") return "Git is not installed or not on PATH.";
  return "Git could not inspect this project. Refresh to retry.";
}
class GitUnavailable extends Error {}
const nulSplit = (buffer: Buffer) => buffer.toString("utf8").split("\0").filter(Boolean);
const declaresConversion = (text: string) => text.split(/\r?\n/).some(line => !line.trim().startsWith("#") && /\b(filter|eol|text|diff|working-tree-encoding|ident)\b/.test(line));
const covers = (folder: string, path: string) => folder === "" || path.startsWith(`${folder}/`);
const gitMode = (executable: boolean) => executable ? "100755" : "100644";

export function createWorkspaceGit(sourceFiles: Files, rawRun: Runner = gitRunner(sourceFiles.root), now: () => number = Date.now) {
  const perform = createGitReadOperations();
  const boundedFiles = (file: <T>(work: () => Promise<T>) => Promise<T>): Files => ({ ...sourceFiles,
    list: (...args) => file(() => sourceFiles.list(...args)), metadata: (...args) => file(() => sourceFiles.metadata(...args)),
    preview: (...args) => file(() => sourceFiles.preview(...args)), readIgnoreExclude: () => file(sourceFiles.readIgnoreExclude),
    ignoreCase: () => file(sourceFiles.ignoreCase).catch(() => undefined), compareBytes: (...args) => file(() => sourceFiles.compareBytes(...args)),
    linkText: (...args) => file(() => sourceFiles.linkText(...args)),
  });
  const cache = new Map<string, { revision: string; id: string; executable: boolean }>();
  const generations = new Map<string, Generation>();
  let version: Promise<void> | undefined;
  const checkVersion = (run: Runner) => version ??= run(["--version"], 4096).then(output => {
    const [major = 0, minor = 0] = (/git version (\d+)\.(\d+)/.exec(output.toString("utf8")) ?? []).slice(1).map(Number);
    if (major < MINIMUM_GIT[0] || major === MINIMUM_GIT[0] && minor < MINIMUM_GIT[1]) throw new GitUnavailable(`Git ${MINIMUM_GIT.join(".")} or newer is required for Uncommitted vs HEAD.`);
  }).catch((error: unknown) => { version = undefined; throw error; });
  /** The current entry by its own type, never following a link. */
  const current = async (path: string, files: Files): Promise<Current> => {
    const entry = (await files.metadata([path])).entries[0];
    if (entry?.revision) return { kind: "file", revision: entry.revision };
    if (entry?.reason === "missing") return { kind: "missing" };
    if (entry?.reason !== "symlink") return { kind: "other" };
    try { return { kind: "link", text: await files.linkText(path) }; }
    catch (error) { return error instanceof WorkspaceFileError && error.reason === "missing" ? { kind: "missing" } : { kind: "other" }; }
  };

  const scope = (options: GitReadOptions = {}): Promise<GitScope> => perform(30000, options, async op => {
    const files = boundedFiles(op.file), run = op.run;
    const started = now(), warnings = new Set<string>(), uncompared: GitUncompared[] = [];
    let partial = false;
    const overBudget = () => { op.check(); if (now() - started > GIT_LIMITS.milliseconds) partial = true; return partial; };
    try {
      await checkVersion(run);
      const topPath = (await run(["rev-parse", "--show-toplevel"], 65536)).toString("utf8").trim();
      const top = await op.file(() => realpath(topPath));
      const within = relative(top, files.root);
      if (within === ".." || within.startsWith(`..${sep}`) || isAbsolute(within)) return { state: "unavailable", reason: "This project is outside its Git repository." };
      const prefix = within ? `${within.split(sep).join("/")}/` : "";
      const format = (await run(["rev-parse", "--show-object-format"], 4096)).toString("utf8").trim();
      if (format !== "sha1" && format !== "sha256") return { state: "unavailable", reason: `Git object format "${format.slice(0, 32)}" is not supported.` };
      const blobId = (bytes: Uint8Array) => createHash(format).update(`blob ${bytes.length}\0`).update(bytes).digest("hex");
      // A config read only; an unset value means Git's default (true).
      const fileMode = await run(["config", "--bool", "--get", "core.filemode"], 4096).then(value => value.toString("utf8").trim() !== "false", () => true);
      if (!fileMode) warnings.add("This repository ignores executable-bit changes (core.filemode=false).");
      let head: string | undefined;
      try { head = (await run(["rev-parse", "--verify", "--quiet", "HEAD"], 65536)).toString("utf8").trim(); } catch { head = undefined; }
      const tracked = new Map<string, Tracked>(), attributeFiles = new Set<string>();
      // -l needs every blob's size; in a partial clone missing blobs fail closed (no fetch) and are reported, not downloaded.
      const listing = head ? await run(["ls-tree", "-r", "-z", "-l", "--full-tree", head], 64 * 1024 * 1024).catch(async (error: unknown) => {
        const refused = /promisor/i.test(String((error as { stderr?: string })?.stderr ?? ""));
        const promisor = refused || await run(["config", "--get-regexp", "^(remote\\..*\\.promisor|extensions\\.partialclone)$"], 65536)
          .then(value => value.toString("utf8").split("\n").some(line => /\s(?!false\b)\S/.test(line)), () => false);
        if (promisor) throw new GitUnavailable("This partial clone is missing objects; comparing would need a download, which Crucible never starts.");
        throw error;
      }) : undefined;
      if (listing) for (const record of nulSplit(listing)) {
        const tab = record.indexOf("\t"), [mode, type, id, size] = record.slice(0, tab).split(/\s+/), full = record.slice(tab + 1);
        if (!full.startsWith(prefix) || type !== "blob") { if (type === "commit") warnings.add("Submodules are listed by Git but not compared."); continue; }
        const path = full.slice(prefix.length);
        if (!isWorkspacePath(path, false)) {
          if (uncompared.length >= GIT_LIMITS.tracked) return { state: "unavailable", reason: "Too many unsupported Git paths to compare." };
          uncompared.push({ path, reason: "unsupported" }); continue;
        }
        tracked.set(path, { mode: mode!, id: id!, size: Number(size) });
        if (posix.basename(path) === ".gitattributes") attributeFiles.add(path);
        if (tracked.size > GIT_LIMITS.tracked) return { state: "unavailable", reason: `More than ${GIT_LIMITS.tracked.toLocaleString()} tracked files; Uncommitted vs HEAD is unavailable for this project.` };
      }
      for (const path of cache.keys()) if (!tracked.has(path)) cache.delete(path);
      const indexed = new Set(nulSplit(await run(["ls-files", "-z", "--cached", "--full-name"], 64 * 1024 * 1024)).filter(path => path.startsWith(prefix)).map(path => path.slice(prefix.length)));
      const changes: GitChange[] = [], deleted = new Map<string, string>();
      let visited = 0;
      for (const [path, entry] of tracked) {
        if (overBudget()) break;
        if (++visited % 200 === 0) await setImmediate();
        const headType: GitEntryType = entry.mode === "120000" ? "link" : "file";
        let found: Current;
        try { found = await current(path, files); } catch { found = { kind: "other" }; }
        if (found.kind === "missing") { changes.push({ path, status: "D" }); deleted.set(entry.id, path); continue; }
        if (found.kind === "other") { uncompared.push({ path, reason: "unsupported" }); continue; }
        if (found.kind === "link") {
          if (headType === "file") changes.push({ path, status: "M", type: { from: "file", to: "link" } });
          else if (blobId(Buffer.from(found.text)) !== entry.id) changes.push({ path, status: "M" });
          continue;
        }
        if (headType === "link") { changes.push({ path, status: "M", type: { from: "link", to: "file" } }); continue; }
        let facts = cache.get(path);
        if (facts?.revision !== found.revision) {
          let read;
          try { read = await files.compareBytes(path); } catch { uncompared.push({ path, reason: "unreadable" }); continue; }
          const executable = (read.mode & 0o100) !== 0;
          if (read.truncated) {
            const mode = fileMode && gitMode(executable) !== entry.mode ? { from: entry.mode, to: gitMode(executable) } : undefined;
            // A size difference proves a change; equal sizes above the cap prove nothing.
            if (read.size !== entry.size || mode) changes.push({ path, status: "M", note: "too-large", ...(mode ? { mode } : {}) });
            else uncompared.push({ path, reason: "too-large" });
            continue;
          }
          facts = { revision: read.revision, id: blobId(read.content), executable };
          cache.set(path, facts);
        }
        const mode = fileMode && gitMode(facts.executable) !== entry.mode ? { from: entry.mode, to: gitMode(facts.executable) } : undefined;
        if (facts.id !== entry.id || mode) changes.push({ path, status: "M", ...(mode ? { mode } : {}) });
      }
      // Untracked/added files: the same bounded, ignore-aware walk as Files (ignored paths never listed).
      op.check();
      const walk = createIgnoreWalk(files), queue = [""], fresh: { path: string; size: number | undefined }[] = [];
      let scanned = 0;
      outer: for (let index = 0; index < queue.length; index++) {
        let cursor: string | undefined;
        do {
          if (overBudget()) break outer;
          const page = await files.list(queue[index]!, cursor).catch(() => undefined);
          if (!page) { warnings.add("Some folders could not be inspected."); partial = true; break; }
          if (page.limited) { warnings.add("A folder exceeds 10,000 entries; only its captured subset was compared."); partial = true; }
          for (const entry of page.entries) {
            if (++scanned > GIT_LIMITS.scanned) { partial = true; break outer; }
            if (!isWorkspacePath(entry.path, false)) { warnings.add("Some workspace names cannot be shown in Desktop and were not compared."); partial = true; continue; }
            if (entry.kind !== "directory" && entry.kind !== "file" && entry.kind !== "symlink") continue;
            if (await walk.classify(entry.path, entry.kind === "directory") === "ignored") continue;
            if (entry.kind === "directory") { queue.push(entry.path); continue; }
            if (entry.kind === "file" && posix.basename(entry.path) === ".gitattributes") attributeFiles.add(entry.path);
            if (!tracked.has(entry.path)) fresh.push({ path: entry.path, size: entry.size });
          }
          cursor = page.nextCursor; await setImmediate();
        } while (cursor);
      }
      const deletedSizes = new Set([...deleted.values()].map(path => tracked.get(path)!.size));
      let hashed = 0;
      for (const item of fresh) {
        let renamedFrom: string | undefined;
        if (item.size !== undefined && deletedSizes.has(item.size) && hashed++ < GIT_LIMITS.renameCandidates && !overBudget()) {
          const read = await files.compareBytes(item.path).catch(() => undefined);
          if (read && !read.truncated) renamedFrom = deleted.get(blobId(read.content));
        }
        if (renamedFrom) {
          for (const [id, path] of deleted) if (path === renamedFrom) deleted.delete(id);
          changes.splice(changes.findIndex(change => change.path === renamedFrom && change.status === "D"), 1);
          changes.push({ path: item.path, status: "R", from: renamedFrom });
        } else changes.push({ path: item.path, status: indexed.has(item.path) ? "A" : "U" });
      }
      warnings.add("Compared as raw bytes; .gitattributes line-ending and filter rules are not applied, so results can differ from git status.");
      if (attributeFiles.size > GIT_LIMITS.attributeFiles) {
        warnings.add("Too many .gitattributes files to inspect; any change may be affected by attribute rules.");
        for (const change of changes) change.note ??= "attributes";
      } else for (const path of attributeFiles) {
        op.check();
        const rules = await files.preview(path).then(value => value.kind === "text" && declaresConversion(value.text), () => false);
        if (!rules) continue;
        const folder = posix.dirname(path) === "." ? "" : posix.dirname(path);
        for (const change of changes) if (covers(folder, change.path)) change.note ??= "attributes";
      }
      if (uncompared.length) { partial = true; warnings.add(`${uncompared.length.toLocaleString()} tracked ${uncompared.length === 1 ? "file was" : "files were"} not compared.`); }
      if (partial) warnings.add("Partial comparison: some files were not compared, or time, size or scan limits were reached.");
      const byPath = (left: { path: string }, right: { path: string }) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0;
      changes.sort(byPath); uncompared.sort(byPath);
      op.check();
      const generation = randomUUID();
      generations.set(generation, { tracked, changes: new Map(changes.map(change => [change.path, change])) });
      for (const key of generations.keys()) if (generations.size > GIT_LIMITS.generations) generations.delete(key);
      return { state: "ready", generation, ...(head ? { head } : {}), changes, uncompared, partial, rawBytes: true, warnings: [...warnings] };
    } catch (error) { op.check(); if (error instanceof GitReadStopped) throw error; return { state: "unavailable", reason: error instanceof GitUnavailable ? error.message : gitUnavailable(error) }; }
  }, rawRun);

  const decode = (bytes: Uint8Array): string | undefined => {
    if (bytes.subarray(0, 8192).includes(0)) return undefined;
    try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { return undefined; }
  };
  /** The renderer names only a generation and a path; status, baseline and rename source come from the host's captured comparison. */
  const diff = (generation: string, path: string, options: GitReadOptions = {}): Promise<GitDiff> => perform(15000, options, async op => {
    const files = boundedFiles(op.file), run = op.run;
    if (!isWorkspacePath(path, false)) return { kind: "unavailable", reason: "This filename cannot be shown in Desktop." };
    const captured = generations.get(generation);
    if (!captured) return { kind: "unavailable", reason: "This comparison is out of date. Refresh Changes." };
    const change = captured.changes.get(path);
    if (!change) return { kind: "unavailable", reason: "This file is not in the current comparison." };
    const facts: ChangeFacts = { ...(change.mode ? { mode: change.mode } : {}), ...(change.type ? { type: change.type } : {}) };
    try {
      const base = change.status === "R" ? captured.tracked.get(change.from!) : change.status === "M" || change.status === "D" ? captured.tracked.get(change.path) : undefined;
      if (change.note === "too-large" || (base && base.size > GIT_LIMITS.diffBytes)) return { kind: "too-large" };
      const before: Uint8Array = base ? await run(["cat-file", "blob", base.id], GIT_LIMITS.diffBytes + 1) : Buffer.alloc(0);
      let after: Uint8Array = Buffer.alloc(0);
      if (change.status !== "D") {
        const found = await current(change.path, files);
        if (found.kind === "link") after = Buffer.from(found.text);
        else if (found.kind === "file") {
          const read = await files.compareBytes(change.path);
          if (read.truncated || read.content.length > GIT_LIMITS.diffBytes) return { kind: "too-large" };
          after = read.content;
        } else return { kind: "unavailable", reason: "This file changed since the comparison. Refresh Changes." };
      }
      const fingerprint = createHash("sha256").update(JSON.stringify(facts)).update(String(before.length) + ":" + String(after.length) + ":").update(before).update(after).digest("hex");
      const fullFacts = { ...facts, fingerprint };
      const oldText = decode(before), newText = decode(after);
      if (oldText === undefined || newText === undefined) return { kind: "binary", ...fullFacts };
      const patch = createTwoFilesPatch(`a/${change.from ?? change.path}`, `b/${change.path}`, oldText, newText, undefined, undefined, { context: 3, maxEditLength: 20000, timeout: 2000 });
      if (patch === undefined) return { kind: "too-large" };
      const lines = patch.split("\n"), start = lines.findIndex(line => line.startsWith("@@")), body = start < 0 ? [] : lines.slice(start).filter((line, index, all) => line || index < all.length - 1);
      const truncated = body.length > GIT_LIMITS.diffLines, shown = truncated ? body.slice(0, GIT_LIMITS.diffLines) : body;
      return { kind: "text", unified: shown.join("\n"), additions: body.filter(line => line.startsWith("+")).length, deletions: body.filter(line => line.startsWith("-")).length, truncated, ...fullFacts };
    } catch (error) { op.check(); if (error instanceof GitReadStopped) throw error; return { kind: "unavailable", reason: error instanceof WorkspaceFileError ? error.message : gitUnavailable(error) }; }
  }, rawRun);
  return { scope, diff };
}
