import { WorkspaceFileError } from "./workspace-files.js";
import type { createWorkspaceBrowser } from "./workspace-browser.js";
export type WorkspaceFileRequest = { requestId: string } & (
  { type: "desktop/filesList"; path: string; cursor?: string } |
  { type: "desktop/filesPreview" | "desktop/filesImage"; path: string } |
  { type: "desktop/filesMetadata"; paths: string[] } |
  { type: "desktop/filesSearch"; query: string; includeIgnored: boolean } |
  { type: "desktop/filesCancelSearch" }
);
type Browser = ReturnType<typeof createWorkspaceBrowser>;
export function createWorkspaceFileRequests(browser: Browser, identity: () => { viewId: string; projectId: string | null }, post: (reply: Record<string, unknown>) => void) {
  let active = 0, stopped = false, search: AbortController | undefined;
  const handle = async (request: WorkspaceFileRequest): Promise<void> => {
    const captured = identity(); let settled = false;
    const reply = (value: Record<string, unknown>) => {
      if (settled) return; settled = true;
      const current = identity();
      if (!stopped && current.viewId === captured.viewId) post({ type: "desktop/filesResult", requestId: request.requestId,
        ...(current.projectId === captured.projectId ? value : { cancelled: true }) });
    };
    if (stopped) return;
    if (request.type === "desktop/filesCancelSearch") { search?.abort(); reply({ cancelled: true }); return; }
    if (request.type === "desktop/filesSearch") search?.abort();
    if (active >= 4) { reply({ error: "Files is busy. Retry after the current inspection finishes.", reason: "busy" }); return; }
    active++;
    const controller = new AbortController();
    controller.signal.addEventListener("abort", () => reply({ cancelled: true }), { once: true });
    if (request.type === "desktop/filesSearch") search = controller;
    try {
      const result = request.type === "desktop/filesSearch" ? await browser.search(request.query, request.includeIgnored, controller.signal)
        : request.type === "desktop/filesMetadata" ? await browser.metadata(request.paths) : request.type === "desktop/filesList" ? await browser.list(request.path, request.cursor) : request.type === "desktop/filesImage" ? await browser.image(request.path) : await browser.preview(request.path);
      if (!controller.signal.aborted) reply({ result });
    } catch (error) {
      if (!controller.signal.aborted) reply({ error: error instanceof WorkspaceFileError ? error.message : "Files could not be loaded. Refresh and retry.", reason: error instanceof WorkspaceFileError ? error.reason : "unavailable" });
    } finally { active--; if (search === controller) search = undefined; }
  };
  return { handle, cancelSearch: () => search?.abort(), dispose: () => { stopped = true; search?.abort(); } };
}
