import { usePreviewFreshness } from "./usePreviewFreshness";
import { useCallback, useEffect, useRef, useState } from "react";
import type { BrowserDirectory } from "../../../../desktop/src/workspace-browser";
import type { WorkspaceImage, WorkspacePreview } from "../../../../desktop/src/workspace-file-types";
import { FileInspectionError, FileQueueFull, type FileAction, type WorkspacePort } from "./workspaceClient";
export interface FileTab { path: string; preview?: WorkspacePreview | WorkspaceImage; loading: boolean; error?: string; stale?: boolean; released?: boolean; }
export interface DirectoryState { page?: BrowserDirectory; paged?: boolean; loading: boolean; error?: string; }
export function focusRefreshPaths(expanded: Set<string>, directories: Record<string, DirectoryState>) {
  return [...expanded].filter(path => {
    if (directories[path]?.loading || directories[path]?.paged) return false;
    const parts = path ? path.split("/") : [];
    return parts.every((_part, index) => {
      const parent = parts.slice(0, index).join("/"), child = parts.slice(0, index + 1).join("/");
      return expanded.has(parent) && directories[parent]?.page?.entries.some(entry => entry.path === child);
    });
  }).slice(0, 16);
}
export function useProjectFiles(port?: WorkspacePort, visible = true) {
  const [tabs, setTabs] = useState<FileTab[]>([]), [selection, select] = useState<string | null | undefined>(null);
  const [navigator, setNavigator] = useState(true), [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));
  const [directoryCache, setDirectoryCache] = useState<{ entries: Record<string, DirectoryState>; order: string[] }>({ entries: {}, order: [] }), [notice, setNotice] = useState("");
  const directories = directoryCache.entries;
  const [recent, setRecent] = useState<string[]>([]), [changed, setChanged] = useState<string[]>([]);
  const versions = useRef(new Map<string, number>()), directoryVersions = useRef(new Map<string, number>()), serial = useRef(0), live = useRef(true), currentTabs = useRef(tabs);
  const lastFocus = useRef(0), expandedNow = useRef(expanded), selectedPath = useRef(selection);
  currentTabs.current = tabs; expandedNow.current = expanded; selectedPath.current = selection;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const storeDirectory = useCallback((path: string, change: (state?: DirectoryState) => DirectoryState) => {
    setDirectoryCache(value => {
      const entries = { ...value.entries, [path]: change(value.entries[path]) }, order = value.order.filter(key => key !== path);
      order.push(path); while (order.length > 32) delete entries[order.shift()!];
      return { entries, order };
    });
  }, []);
  const loadDirectory = useCallback(async (path: string, cursor?: string, background = false) => {
    if (!port) return;
    const version = ++serial.current; directoryVersions.current.set(path, version);
    storeDirectory(path, value => ({ ...value, loading: true, error: undefined }));
    try {
      const page = await port.list(path, cursor);
      if (live.current && directoryVersions.current.get(path) === version) storeDirectory(path, () => ({ loading: false, page, paged: !!cursor }));
    } catch (error) {
      if (live.current && directoryVersions.current.get(path) === version) storeDirectory(path, value => ({ ...value, loading: false,
        error: background && error instanceof FileQueueFull ? undefined : error instanceof Error ? error.message : "Folder unavailable. Retry." }));
    } finally { if (directoryVersions.current.get(path) === version) directoryVersions.current.delete(path); }
  }, [port, storeDirectory]);
  const loadFile = useCallback(async (path: string) => {
    if (!port) return;
    const version = ++serial.current; versions.current.set(path, version);
    setTabs(value => value.map(tab => tab.path === path ? { path, loading: true } : tab));
    try {
      let preview: WorkspacePreview | WorkspaceImage;
      if (/\.(png|jpe?g|gif|webp|bmp)$/i.test(path) && port.image) preview = await port.image(path);
      else {
        preview = await port.preview(path);
        if (preview.kind === "binary" && port.image) { try { preview = await port.image(path); } catch (error) { if (!(error instanceof FileInspectionError) || error.reason !== "unsupported") throw error; } }
      }
      if (live.current && versions.current.get(path) === version) setTabs(value => {
        const next: FileTab[] = value.map(tab => tab.path === path ? { path, preview, loading: false } : tab);
        const resident = () => next.filter(tab => tab.preview?.kind === "image");
        while (resident().length > 2 || resident().reduce((sum, tab) => sum + (tab.preview?.kind === "image" ? tab.preview.dataUrl.length * 2 : 0), 0) > 32 * 1024 * 1024) {
          const candidate = next.findIndex(tab => tab.preview?.kind === "image" && tab.path !== currentTabs.current.find(item => item.path === selectedPath.current)?.path);
          if (candidate < 0) break; next[candidate] = { path: next[candidate]!.path, loading: false, released: true };
        }
        return next;
      });
    } catch (error) { if (live.current && versions.current.get(path) === version) setTabs(value => value.map(tab => tab.path === path ? { path, loading: false, error: error instanceof Error ? error.message : "Preview unavailable. Retry." } : tab)); }
    finally { if (versions.current.get(path) === version) versions.current.delete(path); }
  }, [port]);
  usePreviewFreshness(port, tabs.filter(tab => !tab.stale && !tab.loading && tab.preview).map(tab => ({ path: tab.path, revision: tab.preview?.revision })), visible,
    paths => {
      for (const path of paths) versions.current.delete(path);
      setTabs(value => value.map(tab => paths.includes(tab.path) ? { ...tab, loading: false, stale: true } : tab));
      
    }, () => setNotice("Preview freshness could not be checked. Reload a file to verify its contents."));
  const open = useCallback((path: string) => {
    const existing = currentTabs.current.find(tab => tab.path === path);
    if (!existing && currentTabs.current.length >= 12) { setNotice("Close a file tab before opening another (12 open tabs maximum)."); return; }
    setNotice(""); select(path); setRecent(value => [path, ...value.filter(item => item !== path)].slice(0, 20));
    if (!existing) { currentTabs.current = [...currentTabs.current, { path, loading: true }]; setTabs(value => value.some(tab => tab.path === path) ? value : [...value, { path, loading: true }]); void loadFile(path); }
  }, [loadFile]);
  useEffect(() => { if (typeof selection === "string" && currentTabs.current.find(tab => tab.path === selection)?.released) void loadFile(selection); }, [selection, loadFile]);
  const action = async (type: FileAction, path?: string) => {
    try { if (!port?.action) throw new Error("File actions are unavailable in this host."); setNotice((await port.action(type, path)).message); }
    catch (error) { setNotice(error instanceof Error ? error.message : "File action failed. Retry."); }
  };
  const close = (path: string) => { versions.current.delete(path); setTabs(value => value.filter(tab => tab.path !== path)); select(value => value === path ? null : value); };
  const toggleDirectory = (path: string) => {
    const next = new Set(expandedNow.current);
    if (next.has(path)) next.delete(path);
    else { if (next.size >= 128) { setNotice("Collapse a folder before expanding another (128 expanded folders maximum)."); return; } next.add(path); void loadDirectory(path); }
    expandedNow.current = next; setExpanded(next);
  };
  useEffect(() => { if (port) void loadDirectory(""); }, [port, loadDirectory]);
  useEffect(() => {
    const focused = () => {
      if (!visible || !navigator || Date.now() - lastFocus.current < 5000) return;
      lastFocus.current = Date.now();
      for (const path of focusRefreshPaths(expanded, directories)) void loadDirectory(path, undefined, true);
    };
    window.addEventListener("focus", focused); return () => window.removeEventListener("focus", focused);
  }, [visible, navigator, expanded, directories, loadDirectory]);
  useEffect(() => {
    const pending = new Map<string, string[]>();
    const paths = (entries: unknown): string[] => Array.isArray(entries) ? entries.flatMap(entry => entry && typeof entry.path === "string" ? [entry.path as string] : []) : [];
    const mark = (changedPaths: string[]) => {
      if (!changedPaths.length) return;
      for (const path of changedPaths) versions.current.delete(path);
      setTabs(value => value.map(tab => changedPaths.includes(tab.path) ? { ...tab, loading: false, stale: true } : tab));
      setChanged(value => [...new Set([...changedPaths, ...value])].slice(0, 500));

    };
    const receive = (event: MessageEvent) => {
      const message = event.data;
      if (message?.type === "clearThread") { pending.clear(); setChanged([]); }
      if (message?.type === "replaceMessages" && Array.isArray(message.messages)) {
        setChanged([...new Set<string>(message.messages.flatMap((item: { type?: string; metadata?: { resolved?: string; diff_entries?: unknown } }) => item.type === "diff_card" && item.metadata?.resolved === "applied" ? paths(item.metadata.diff_entries) : []))].slice(0, 500));
        for (const item of message.messages) {
        if (item.type === "diff_card" && !item.metadata?.resolved && typeof item.taskId === "string") pending.set(item.taskId, paths(item.metadata?.diff_entries));
      } }
      if (message?.type === "appendMessage" && message.message?.type === "diff_card") {
        const item = message.message, edited = paths(item.metadata?.diff_entries);
        if (item.metadata?.resolved === "applied") mark(edited);
        else if (typeof item.taskId === "string") { pending.set(item.taskId, edited); if (pending.size > 256) pending.delete(pending.keys().next().value!); }
      }
      if (message?.type === "resolveInlineChangeCard" && message.resolution === "applied") { mark(pending.get(message.taskId) ?? []); pending.delete(message.taskId); }
      if (message?.type === "agentEvent" && message.event?.type === "diff_ready" && message.event.payload?.resolved === "applied") mark(paths(message.event.payload.diff_entries));
    };
    window.addEventListener("message", receive); return () => window.removeEventListener("message", receive);
  }, []);
  return { port, tabs, selection, select, navigator, setNavigator, expanded, directories, notice, recent, changed, action, open, close, loadFile, loadDirectory, toggleDirectory };
}
export type ProjectFiles = ReturnType<typeof useProjectFiles>;
