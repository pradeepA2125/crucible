import { GitReadStopped, type createWorkspaceGit } from "./workspace-git.js";
import { GitReplyTooLarge, parseGitReply, type GitOperation, type GitRequest, type GitReply } from "./git-protocol.js";
type Reader = ReturnType<typeof createWorkspaceGit>;
type ReadResult = Awaited<ReturnType<Reader["scope"]>> | Awaited<ReturnType<Reader["diff"]>>;
/** Project-scoped Git reads only. One scope and one diff slot; no host queue. */
export function createWorkspaceGitRequests(reader: Reader, identity: () => { viewId: string; projectId: string | null }, post: (reply: GitReply) => void) {
  const active = new Map<GitOperation, AbortController>(); let stopped = false;
  const handle = async (request: GitRequest): Promise<void> => {
    if (stopped) return;
    const captured = { ...identity() }, operation: GitOperation = request.type === "desktop/gitScope" ? "scope" : "diff";
    let replied = false;
    const reply = (outcome: unknown) => {
      if (replied) return;
      const current = identity();
      if (!stopped && captured.viewId === current.viewId) {
        const value = parseGitReply({ type: "desktop/gitResult", requestId: request.requestId, projectId: current.projectId, operation,
          outcome: current.projectId === captured.projectId ? outcome : { status: "cancelled", message: "The project changed. Reopen Changes." } }, operation);
        replied = true; post(value);
      } else replied = true;
    };
    if (active.has(operation)) { reply({ status: "busy", message: "A Git " + (operation === "scope" ? "comparison" : "diff") + " is already running. Retry when it finishes." }); return; }
    const controller = new AbortController(); active.set(operation, controller);
    const milliseconds = operation === "scope" ? 30000 : 15000, deadline = Date.now() + milliseconds;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, milliseconds);
    try {
      // Also bounds injected readers that ignore abort. The real reader separately
      // stops subprocesses and bounds outstanding filesystem I/O.
      const result = await new Promise<ReadResult>((resolve, reject) => {
        const aborted = () => reject(new GitReadStopped(timedOut ? "timeout" : "cancelled"));
        controller.signal.addEventListener("abort", aborted, { once: true });
        Promise.resolve().then<ReadResult>(() => {
          if (controller.signal.aborted) throw new GitReadStopped(timedOut ? "timeout" : "cancelled");
          const options = { signal: controller.signal, deadline };
          return request.type === "desktop/gitScope" ? reader.scope(options) : reader.diff(request.generation, request.path, options);
        }).then(resolve, reject).finally(() => controller.signal.removeEventListener("abort", aborted));
      });
      reply({ status: "ok", result });
    } catch (error) {
      reply(error instanceof GitReplyTooLarge ? { status: "too-large", message: operation === "scope"
        ? "This Git comparison exceeds the 16 MiB reply limit. A complete file list could not be returned."
        : "This Git diff exceeds the 16 MiB reply limit. This file could not be compared." }
        : error instanceof GitReadStopped ? { status: timedOut ? "timeout" : error.reason, message: error.message }
        : { status: "error", message: "Git returned an invalid or failed inspection. Refresh Changes to retry." });
    } finally {
      clearTimeout(timer); if (active.get(operation) === controller) active.delete(operation);
    }
  };
  const cancel = () => { for (const controller of active.values()) controller.abort(); };
  return { handle, cancel, dispose: () => { stopped = true; cancel(); } };
}
