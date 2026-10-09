import { describe, expect, it, vi } from "vitest";
import { createWorkspaceAdapter } from "../src/file-references/workspace-adapter.js";
import type { Uri, WorkspaceFolder } from "vscode";
import { mkdtemp, mkdir, writeFile, symlink, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
const uri = (path: string, scheme = "remote", authority = "host"): Uri => ({ path, scheme, authority, fsPath: path, toString: () => `${scheme}://${authority}${path}` });
function setup() {
  const folders: WorkspaceFolder[] = [
    { name: "same", index: 0, uri: uri("/one") }, { name: "same", index: 1, uri: uri("/two") },
  ];
  const inventories = new Map<string, Uri[]>([["/one", [uri("/one/src/a.ts")]], ["/two", [uri("/two/src/a.ts")]]]);
  const rules: Record<string, Record<string, boolean | { when: string }>> = {};
  const types = new Map<string, number>();
  const callbacks = new Map<string, (uri?: Uri) => void>();
  const disposals: ReturnType<typeof vi.fn>[] = [];
  const event = (name: string) => (listener: (uri?: Uri) => void) => {
    callbacks.set(name, listener); const dispose = vi.fn(); disposals.push(dispose); return { dispose };
  };
  class RelativePattern { constructor(public base: WorkspaceFolder | Uri | string, public pattern: string) {} }
  const findFiles = vi.fn(async (pattern: RelativePattern) => inventories.get((pattern.base as WorkspaceFolder).uri.path) ?? []);
  const api = {
    RelativePattern, Uri: { file: (p: string) => uri(p, "file", ""), parse: (p: string) => uri(p), joinPath: (base: Uri, ...p: string[]) => uri(`${base.path}/${p.join("/")}`, base.scheme, base.authority) },
    FileType: { Unknown: 0, File: 1, Directory: 2, SymbolicLink: 64 },
    workspace: {
      workspaceFolders: folders, findFiles,
      getConfiguration: (section?: string, scope?: Uri) => ({ get: (_key: string, fallback: unknown) => rules[`${scope?.path}/${section}`] ?? fallback }),
      fs: { stat: vi.fn(async (u: Uri) => {
        const type = types.get(u.path);
        if (type === -1) throw Object.assign(new Error("missing"), { code: "FileNotFound" });
        return { type: type ?? 1 };
      }) },
      createFileSystemWatcher: () => ({ onDidCreate: event("create"), onDidChange: event("change"), onDidDelete: event("delete"), dispose: vi.fn() }),
      onDidChangeWorkspaceFolders: event("folders"),
      onDidChangeConfiguration: (listener: (e: { affectsConfiguration(s: string): boolean }) => void) => {
        callbacks.set("config", () => listener({ affectsConfiguration: s => s === "search.exclude" }));
        const dispose = vi.fn(); disposals.push(dispose); return { dispose };
      },
    },
  };
  return { api, folders, inventories, rules, types, callbacks, disposals, findFiles };
}
describe("workspace reference activation safety", () => {
  it("rechecks remote membership, scope, exclusions and stat", async () => {
    const s = setup(); const adapter = createWorkspaceAdapter(s.api);
    const f = (await adapter.port.enumerate())[0]!;
    try {
      expect(await adapter.isCurrentFile(f)).toBe(true);
      expect(await adapter.isCurrentFile({ ...f, uri: uri("/one2/a.ts") })).toBe(false);
      expect(await adapter.isCurrentFile({ ...f, uri: uri("/one/src/a.ts", "remote", "other") })).toBe(false);
      s.rules["/one/search"] = { "**/*.ts": true };
      expect(await adapter.isCurrentFile(f)).toBe(false);
      delete s.rules["/one/search"];
      s.types.set(f.uri.path, 2);
      expect(await adapter.isCurrentFile(f)).toBe(false);
      s.types.set(f.uri.path, -1);
      expect(await adapter.isCurrentFile(f)).toBe(false);
      s.types.delete(f.uri.path);
      s.folders.splice(0, 1);
      expect(await adapter.isCurrentFile(f)).toBe(false);
    } finally { adapter.dispose(); }
  });
  it("checks local canonical containment, directories, deletion and sibling prefixes", async () => {
    const temp = await mkdtemp(join(tmpdir(), "file-reference-"));
    const s = setup();
    const root = join(temp, "ws");
    await mkdir(root); await mkdir(join(temp, "ws2"));
    await writeFile(join(root, "a.ts"), "a");
    await writeFile(join(temp, "ws2", "outside.ts"), "outside");
    const rootUri = uri(root, "file", "");
    s.folders.splice(0, s.folders.length, { name: "local", index: 0, uri: rootUri });
    const adapter = createWorkspaceAdapter(s.api);
    const f = (path: string) => ({ entry: { workspaceId: "local", workspaceLabel: "local", relativePath: path.slice(root.length + 1) }, root: rootUri, uri: uri(path, "file", "") });
    try {
      expect(await adapter.isCurrentFile(f(join(root, "a.ts")))).toBe(true);
      expect(await adapter.isCurrentFile(f(root))).toBe(false);
      expect(await adapter.isCurrentFile(f(join(temp, "ws2", "outside.ts")))).toBe(false);
      await mkdir(join(root, "directory.ts"));
      expect(await adapter.isCurrentFile(f(join(root, "directory.ts")))).toBe(false);
      await rm(join(root, "a.ts"));
      expect(await adapter.isCurrentFile(f(join(root, "a.ts")))).toBe(false);
    } finally { adapter.dispose(); await rm(temp, { recursive: true, force: true }); }
  });
  it("allows inside-target symlinks but rejects outside-target symlinks", async context => {
    const temp = await mkdtemp(join(tmpdir(), "file-reference-symlink-"));
    const s = setup(); const root = join(temp, "ws");
    await mkdir(root); await writeFile(join(root, "a.ts"), "a");
    await writeFile(join(temp, "outside.ts"), "outside");
    const rootUri = uri(root, "file", "");
    s.folders.splice(0, s.folders.length, { name: "local", index: 0, uri: rootUri });
    const adapter = createWorkspaceAdapter(s.api);
    const f = (name: string) => ({ entry: { workspaceId: "local", workspaceLabel: "local", relativePath: name }, root: rootUri, uri: uri(join(root, name), "file", "") });
    try {
      try {
        await symlink(join(root, "a.ts"), join(root, "inside.ts"));
        await symlink(join(temp, "outside.ts"), join(root, "outside.ts"));
      } catch (error) {
        if (typeof error === "object" && error !== null && "code" in error && ["EPERM", "EACCES", "ENOSYS"].includes(String(error.code))) { context.skip(); return; }
        throw error;
      }
      expect(await adapter.isCurrentFile(f("inside.ts"))).toBe(true);
      expect(await adapter.isCurrentFile(f("outside.ts"))).toBe(false);
    } finally { adapter.dispose(); await rm(temp, { recursive: true, force: true }); }
  });
});
describe("workspace reference inventory", () => {
  it("searches every root without maxResults and retains distinct opaque workspace identities", async () => {
    const s = setup(); const adapter = createWorkspaceAdapter(s.api);
    const files = await adapter.port.enumerate();
    expect(files.map(f => f.entry.relativePath)).toEqual(["src/a.ts", "src/a.ts"]);
    expect(new Set(files.map(f => f.entry.workspaceId)).size).toBe(2);
    expect(new Set(files.map(f => f.entry.workspaceLabel)).size).toBe(2);
    expect(JSON.stringify(files.map(f => f.entry))).not.toContain("remote://");
    expect(s.findFiles.mock.calls.every(call => call.length === 2)).toBe(true);
    adapter.dispose();
  });
  it("filters generated directories, non-files and foreign URI scopes, deduplicating URIs", async () => {
    const s = setup();
    s.inventories.set("/one", [uri("/one/src/a.ts"), uri("/one/src/a.ts"), uri("/one/node_modules/b.ts"), uri("/one/dist/b.ts"), uri("/one/dir.ts"), uri("/one2/a.ts"), uri("/one/a.ts", "remote", "other")]);
    s.types.set("/one/dir.ts", 2);
    const adapter = createWorkspaceAdapter(s.api);
    expect((await adapter.port.enumerate()).map(f => f.uri.path)).toEqual(["/one/src/a.ts", "/two/src/a.ts"]);
    adapter.dispose();
  });
  it("applies root-scoped search/files exclusions to paths and parent directories", async () => {
    const s = setup();
    s.rules["/one/search"] = { "**/src": true };
    s.rules["/two/files"] = { "**/*.ts": false };
    const adapter = createWorkspaceAdapter(s.api);
    expect((await adapter.port.enumerate()).map(f => f.uri.path)).toEqual(["/two/src/a.ts"]);
    adapter.dispose();
  });
  it("honors sibling when rules only if the sibling exists", async () => {
    const s = setup();
    s.rules["/one/search"] = { "**/*.ts": { when: "$(basename).js" } };
    s.types.set("/one/src/a.js", -1);
    const adapter = createWorkspaceAdapter(s.api);
    expect(await adapter.port.enumerate()).toHaveLength(2);
    s.types.set("/one/src/a.js", 1);
    expect(await adapter.port.enumerate()).toHaveLength(1);
    adapter.dispose();
  });
  it("fans file/config/folder changes into invalidation and disposes subscriptions", () => {
    const s = setup(); const adapter = createWorkspaceAdapter(s.api);
    const listener = vi.fn(); const sub = adapter.port.onInvalidate(listener);
    for (const callback of s.callbacks.values()) callback();
    expect(listener).toHaveBeenCalledTimes(4);
    sub.dispose(); for (const callback of s.callbacks.values()) callback();
    expect(listener).toHaveBeenCalledTimes(4);
    adapter.dispose(); expect(s.disposals.every(d => d.mock.calls.length === 1)).toBe(true);
  });
  it("does not restart scans for content edits or generated-file churn", () => {
    const s = setup(); const adapter = createWorkspaceAdapter(s.api);
    const listener = vi.fn(); adapter.port.onInvalidate(listener);
    expect(s.callbacks.has("change")).toBe(false);
    s.callbacks.get("create")!(uri("/one/.crucible/runtime.log"));
    s.callbacks.get("delete")!(uri("/one/node_modules/library/index.js"));
    expect(listener).not.toHaveBeenCalled();
    s.callbacks.get("create")!(uri("/one/src/new.ts"));
    s.callbacks.get("delete")!(uri("/one/src/new.ts"));
    expect(listener).toHaveBeenCalledTimes(2);
    adapter.dispose();
  });
  it("prunes generated trees in the search provider", async () => {
    const s = setup(); const adapter = createWorkspaceAdapter(s.api);
    await adapter.port.enumerate();
    const calls = s.findFiles.mock.calls as unknown as [RelativePattern, RelativePattern][];
    expect(calls[0]![1].pattern).toContain("node_modules");
    expect(calls[0]![1].pattern).toContain("venv");
    adapter.dispose();
  });
  it("returns an empty inventory with no workspace", async () => {
    const s = setup(); s.folders.length = 0;
    const adapter = createWorkspaceAdapter(s.api);
    expect(await adapter.port.enumerate()).toEqual([]);
    expect(s.findFiles).not.toHaveBeenCalled(); adapter.dispose();
  });
});
