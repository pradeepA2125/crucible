import type { WorkspaceFileVersion } from "../../../../desktop/src/workspace-revision";
import type { BrowserDirectory, BrowserSearch } from "../../../../desktop/src/workspace-browser";
import type { WorkspaceImage, WorkspacePreview } from "../../../../desktop/src/workspace-file-types";
export type FileAction = "desktop/filesReveal" | "desktop/filesCopyPath" | "desktop/filesOpenEditor" | "desktop/filesChooseEditor";
export interface WorkspacePort {
  list(path: string, cursor?: string): Promise<BrowserDirectory>;
  preview(path: string): Promise<WorkspacePreview>;
  search(query: string, includeIgnored: boolean): Promise<BrowserSearch>;
  metadata?(paths: string[]): Promise<{ entries: WorkspaceFileVersion[] }>;
  image?(path: string): Promise<WorkspaceImage>;
  action?(type: FileAction, path?: string): Promise<{ message: string }>;
  cancelSearch(): void;
}
export class FileInspectionError extends Error { constructor(message: string, readonly reason?: string) { super(message); } }
export class FileQueueFull extends Error { constructor() { super("Too many Files inspections. Retry after loading finishes."); } }
export class FileRequestCancelled extends Error { constructor() { super("Files inspection cancelled."); } }
export function createWorkspaceClient(send: (message: unknown) => void, target: Window = window): WorkspacePort & { dispose(): void } {
  type Job = { id: string; payload: Record<string, unknown>; resolve(value: unknown): void; reject(error: Error): void; retries: number; timer?: ReturnType<typeof setTimeout> };
  const queue: Job[] = [], running = new Map<string, Job>(), delayed = new Set<Job>(); let disposed = false;
  let editorChoice: Promise<{ message: string }> | undefined;
  const pump = () => {
    while (!disposed && running.size < 3 && queue.length) {
      const job = queue.shift()!; running.set(job.id, job);
      // A native chooser waits for the user; its lifetime ends on reply or client disposal.
      if (job.payload.type !== "desktop/filesChooseEditor") job.timer = setTimeout(() => { running.delete(job.id); job.reject(new Error("Files did not respond. Refresh and retry.")); pump(); }, 8000);
      try { send({ ...job.payload, requestId: job.id }); }
      catch { clearTimeout(job.timer); running.delete(job.id); job.reject(new Error("Files could not reconnect. Retry.")); }
    }
  };
  const request = <T,>(payload: Record<string, unknown>): Promise<T> => new Promise((resolve, reject) => {
    if (disposed || queue.length + running.size + delayed.size >= 64) { reject(new FileQueueFull()); return; }
    queue.push({ id: crypto.randomUUID(), payload, resolve: value => resolve(value as T), reject, retries: 0 }); pump();
  });
  const receive = (event: MessageEvent) => {
    if (event.source !== target || event.origin !== "app://app") return;
    const value = event.data;
    if (value?.type !== "desktop/filesResult" || typeof value.requestId !== "string") return;
    const job = running.get(value.requestId); if (!job) return;
    clearTimeout(job.timer); running.delete(job.id);
    if (value.reason === "busy" && job.retries++ < 3) {
      delayed.add(job); job.timer = setTimeout(() => { delayed.delete(job); if (!disposed) { queue.push(job); pump(); } }, 100 * 2 ** job.retries);
    } else if (value.cancelled) job.reject(new FileRequestCancelled());
    else if (typeof value.error === "string") job.reject(new FileInspectionError(value.error, typeof value.reason === "string" ? value.reason : undefined));
    else if (!value.result || typeof value.result !== "object") job.reject(new Error("Files returned an invalid inspection. Refresh and retry."));
    else job.resolve(value.result);
    pump();
  };
  const cancelSearch = () => {
    for (const job of [...queue, ...running.values(), ...delayed]) {
      if (job.payload.type !== "desktop/filesSearch") continue;
      clearTimeout(job.timer); running.delete(job.id); delayed.delete(job);
      const index = queue.indexOf(job); if (index >= 0) queue.splice(index, 1);
      job.reject(new FileRequestCancelled());
    }
    if (!disposed) { send({ type: "desktop/filesCancelSearch", requestId: crypto.randomUUID() }); pump(); }
  };
  const action = (type: FileAction, path?: string): Promise<{ message: string }> => {
    if (type !== "desktop/filesChooseEditor") return request({ type, ...(path ? { path } : {}) });
    if (!editorChoice) editorChoice = request<{ message: string }>({ type }).finally(() => { editorChoice = undefined; });
    return editorChoice;
  };
  target.addEventListener("message", receive);
  return { metadata: paths => request({ type: "desktop/filesMetadata", paths }), image: path => request({ type: "desktop/filesImage", path }), action, list: (path, cursor) => request({ type: "desktop/filesList", path, ...(cursor ? { cursor } : {}) }), preview: path => request({ type: "desktop/filesPreview", path }),
    search: (query, includeIgnored) => { cancelSearch(); return request({ type: "desktop/filesSearch", query, includeIgnored }); }, cancelSearch,
    dispose: () => { disposed = true; target.removeEventListener("message", receive);
      for (const job of [...queue, ...running.values(), ...delayed]) { clearTimeout(job.timer); job.reject(new FileRequestCancelled()); }
      queue.length = 0; running.clear(); delayed.clear(); } };
}
