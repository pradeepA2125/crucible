import type { Disposable, FileSystemWatcher, RelativePattern, Uri, WorkspaceFolder } from "vscode";
import { minimatch } from "minimatch";
import { realpath, stat } from "node:fs/promises";
import * as path from "node:path";
import type { CatalogPort, RetainedFile } from "./catalog.js";

type Exclusion = boolean | { when: string };
export interface WorkspaceApi {
  RelativePattern: new (base: WorkspaceFolder | Uri | string, pattern: string) => RelativePattern;
  Uri: { joinPath(base: Uri, ...pathSegments: string[]): Uri };
  FileType: { File: number };
  workspace: {
    readonly workspaceFolders: readonly WorkspaceFolder[] | undefined;
    findFiles(include: string | RelativePattern, exclude?: string | RelativePattern | null): PromiseLike<Uri[]>;
    getConfiguration(section?: string, scope?: Uri): {
      get<T>(key: string, defaultValue: T): T;
    };
    fs: { stat(uri: Uri): PromiseLike<{ type: number }> };
    createFileSystemWatcher(pattern: string | RelativePattern): FileSystemWatcher;
    onDidChangeWorkspaceFolders(listener: (event: { added: readonly WorkspaceFolder[]; removed: readonly WorkspaceFolder[] }) => unknown): Disposable;
    onDidChangeConfiguration(listener: (event: { affectsConfiguration(section: string): boolean }) => unknown): Disposable;
  };
}

export const GENERATED_DIRECTORIES = new Set([
  "node_modules", ".git", "dist", "target", "__pycache__", ".venv", "venv", ".crucible",
  ".pytest_cache", ".mypy_cache", ".ruff_cache",
]);

/** URI containment, not fsPath containment: works for remote workspace providers. */
export function relativeUriPath(root: Uri, target: Uri): string | undefined {
  if (root.scheme !== target.scheme || root.authority !== target.authority) return undefined;
  const base = root.path.replace(/\\/g, "/").replace(/\/+$/, "");
  const full = target.path.replace(/\\/g, "/");
  if (!full.startsWith(`${base}/`)) return undefined;
  const relative = full.slice(base.length + 1);
  if (!relative || relative.split("/").some(part => !part || part === "." || part === "..")) return undefined;
  return relative;
}

function missing(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error
    && (error.code === "FileNotFound" || error.code === "ENOENT");
}

export function createWorkspaceAdapter(api: WorkspaceApi, reportFailure: (error: unknown) => void = error => console.warn("Workspace file inventory failed", error)) {
  const listeners = new Set<() => void>();
  const ids = new Map<string, string>();
  let disposed = false;
  const invalidate = () => { if (!disposed) for (const listener of listeners) listener(); };
  const inventoryChanged = (uri?: Uri) => {
    // File contents do not change inventory. Ignore generated-file churn too:
    // otherwise a running backend/build can invalidate every in-flight scan.
    if (uri && (uri.path.split("/").some(part => GENERATED_DIRECTORIES.has(part))
      || !api.workspace.workspaceFolders?.some(folder => relativeUriPath(folder.uri, uri)))) return;
    invalidate();
  };
  const watcher = api.workspace.createFileSystemWatcher("**/*");
  const subscriptions = [
    watcher.onDidCreate(inventoryChanged), watcher.onDidDelete(inventoryChanged),
    api.workspace.onDidChangeWorkspaceFolders(invalidate),
    api.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration("search.exclude") || event.affectsConfiguration("files.exclude")) invalidate();
    }),
    watcher,
  ];

  async function excluded(folder: WorkspaceFolder, relativePath: string): Promise<boolean> {
    const parts = relativePath.split("/");
    if (parts.some(part => GENERATED_DIRECTORIES.has(part))) return true;
    const candidates = parts.map((_, i) => parts.slice(0, i + 1).join("/"));
    for (const section of ["files", "search"]) {
      const rules = api.workspace.getConfiguration(section, folder.uri).get<Record<string, Exclusion>>("exclude", {});
      for (const [pattern, rule] of Object.entries(rules)) {
        if (!rule) continue;
        for (const candidate of candidates) {
          if (!minimatch(candidate, pattern, { dot: true })) continue;
          if (rule === true) return true;
          if (typeof rule !== "object" || typeof rule.when !== "string") continue;
          const slash = candidate.lastIndexOf("/");
          const name = candidate.slice(slash + 1);
          const dot = name.lastIndexOf(".");
          const basename = dot > 0 ? name.slice(0, dot) : name;
          const sibling = rule.when.replace(/\$\(basename\)/g, basename);
          const parent = slash < 0 ? "" : candidate.slice(0, slash);
          try {
            await api.workspace.fs.stat(api.Uri.joinPath(folder.uri, parent, sibling));
            return true;
          } catch (error) {
            if (!missing(error)) throw error;
          }
        }
      }
    }
    return false;
  }

  const port: CatalogPort<Uri> = {
    async enumerate() {
      const folders = api.workspace.workspaceFolders ?? [];
      const files: RetainedFile<Uri>[] = [];
      const seen = new Set<string>();
      for (const folder of folders) {
        const rootId = folder.uri.toString();
        let workspaceId = ids.get(rootId);
        if (!workspaceId) { workspaceId = `workspace-${ids.size + 1}`; ids.set(rootId, workspaceId); }
        const duplicate = folders.filter(f => f.name === folder.name).length > 1;
        const workspaceLabel = duplicate ? `${folder.name} (${folder.index + 1})` : folder.name;
        // Prune generated trees in the search provider, before materializing
        // and stat-ing their potentially enormous inventories.
        const found = await api.workspace.findFiles(new api.RelativePattern(folder, "**/*"),
          new api.RelativePattern(folder, `**/{${[...GENERATED_DIRECTORIES].join(",")}}/**`));
        const retain = async (uri: Uri) => {
          const relativePath = relativeUriPath(folder.uri, uri);
          if (!relativePath || seen.has(uri.toString()) || await excluded(folder, relativePath)) return;
          let type: number;
          try { type = (await api.workspace.fs.stat(uri)).type; }
          catch (error) { if (missing(error)) return; throw error; }
          if ((type & api.FileType.File) === 0) return;
          if (seen.has(uri.toString())) return;
          seen.add(uri.toString());
          files.push({ entry: { workspaceId, workspaceLabel, relativePath }, uri, root: folder.uri });
        };
        // Bounded parallel stat calls keep large remote/local workspaces usable.
        for (let offset = 0; offset < found.length; offset += 32) {
          await Promise.all(found.slice(offset, offset + 32).map(retain));
        }
      }
      return files.sort((a, b) => {
        const left = `${a.entry.workspaceId}\0${a.entry.relativePath}`;
        const right = `${b.entry.workspaceId}\0${b.entry.relativePath}`;
        return left < right ? -1 : left > right ? 1 : 0;
      });
    },
    onInvalidate(listener) {
      if (!disposed) listeners.add(listener);
      return { dispose: () => { listeners.delete(listener); } };
    },
    reportFailure,
  };
  return {
    port,
    excluded,
    async isCurrentFile(file: RetainedFile<Uri>): Promise<boolean> {
      if (disposed) return false;
      const folder = api.workspace.workspaceFolders?.find(f => f.uri.toString() === file.root.toString());
      if (!folder) return false;
      const relativePath = relativeUriPath(folder.uri, file.uri);
      if (!relativePath || relativePath !== file.entry.relativePath) return false;
      try {
        if (await excluded(folder, relativePath)) return false;
        if (((await api.workspace.fs.stat(file.uri)).type & api.FileType.File) === 0) return false;
        if (file.uri.scheme !== "file") return true;
        const [canonicalRoot, canonicalFile] = await Promise.all([
          realpath(folder.uri.fsPath), realpath(file.uri.fsPath),
        ]);
        const relative = path.relative(canonicalRoot, canonicalFile);
        if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return false;
        return (await stat(canonicalFile)).isFile();
      } catch {
        // Missing or inaccessible files are not valid activation targets.
        return false;
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      listeners.clear();
      for (const subscription of subscriptions) subscription.dispose();
    },
  };
}
