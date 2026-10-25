import { setImmediate } from "node:timers/promises";
import { createIgnoreWalk, type IgnoreStatus } from "./workspace-ignore.js";
import { WorkspaceFileError, workspaceRelativePath, type createWorkspaceFiles } from "./workspace-files.js";
import type { WorkspaceDirectory, WorkspaceEntry } from "./workspace-file-types.js";
export const FILE_SEARCH_LIMITS = { results: 500, scanned: 50000, folders: 5000, milliseconds: 5000 } as const;
export type BrowserEntry = WorkspaceEntry & { ignoreStatus: IgnoreStatus; unsupportedName?: boolean };
export type BrowserDirectory = Omit<WorkspaceDirectory, "entries"> & { entries: BrowserEntry[]; warnings: string[] };
export interface BrowserSearch { entries: BrowserEntry[]; scanned: number; partial: boolean; warnings: string[]; }
type Files = Awaited<ReturnType<typeof createWorkspaceFiles>>;
export function createWorkspaceBrowser(files: Files) {
  const annotate = async (entries: WorkspaceEntry[], walk: ReturnType<typeof createIgnoreWalk>, signal?: AbortSignal): Promise<BrowserEntry[]> => {
    const result: BrowserEntry[] = [];
    for (const entry of entries) {
      signal?.throwIfAborted();
      if (entry.kind === "protected") { result.push({ ...entry, ignoreStatus: "included" }); continue; }
      try { workspaceRelativePath(entry.path); }
      catch { result.push({ ...entry, unsupportedName: true, ignoreStatus: "unknown" }); continue; }
      result.push({ ...entry, ignoreStatus: await walk.classify(entry.path, entry.kind === "directory") });
    }
    signal?.throwIfAborted(); return result;
  };
  const list = async (path: string, cursor?: string): Promise<BrowserDirectory> => {
    const directory = await files.list(path, cursor), walk = createIgnoreWalk(files);
    const entries = await annotate(directory.entries, walk);
    return { ...directory, entries, warnings: [...walk.warnings] };
  };
  const search = async (query: string, includeIgnored: boolean, signal: AbortSignal): Promise<BrowserSearch> => {
    if (!query.trim() || query.length > 256 || /[\x00-\x1f\x7f]/.test(query)) throw new WorkspaceFileError("invalid", "Enter a path filter of 1–256 characters.");
    const needle = query.toLowerCase(), walk = createIgnoreWalk(files), entries: BrowserEntry[] = [], queue = [""];
    const warnings = new Set<string>(), started = Date.now(); let scanned = 0, partial = false;
    const check = () => { signal.throwIfAborted(); if (Date.now() - started > FILE_SEARCH_LIMITS.milliseconds) { partial = true; return false; } return true; };
    outer: for (let folder = 0; folder < queue.length; folder++) {
      let cursor: string | undefined;
      do {
        if (!check()) break outer;
        let page: WorkspaceDirectory;
        try { page = await files.list(queue[folder]!, cursor); }
        catch { signal.throwIfAborted(); warnings.add("Some folders could not be searched. Refresh or browse them directly."); partial = true; break; }
        if (!check()) break outer;
        if (page.limited) { partial = true; warnings.add("A folder exceeds 10,000 entries; search covers its captured subset only."); }
        for (const entry of await annotate(page.entries, walk, signal)) {
          if (++scanned > FILE_SEARCH_LIMITS.scanned) { partial = true; break outer; }
          if (!check()) break outer;
          if (entry.ignoreStatus === "ignored" && !includeIgnored) continue;
          if (entry.path.toLowerCase().includes(needle)) {
            entries.push(entry);
            if (entries.length === FILE_SEARCH_LIMITS.results) { partial = true; break outer; }
          }
          if (entry.kind === "directory" && !entry.unsupportedName) {
            if (queue.length < FILE_SEARCH_LIMITS.folders && entry.path.split("/").length < 128) queue.push(entry.path);
            else partial = true;
          }
        }
        cursor = page.nextCursor;
        await setImmediate(); // Relay chat/approval events between bounded directory pages.
      } while (cursor);
    }
    signal.throwIfAborted();
    if (partial) warnings.add("Partial path results: time, scan, folder or result limits were reached, or a folder was unavailable.");
    return { entries, scanned: Math.min(scanned, FILE_SEARCH_LIMITS.scanned), partial, warnings: [...new Set([...warnings, ...walk.warnings])] };
  };
  return { list, search, metadata: files.metadata, preview: files.preview, image: files.image };
}
