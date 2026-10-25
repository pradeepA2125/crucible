import { GitReplyTooLarge, isWorkspacePath, parseGitReply, type GitOperation } from "../../../../desktop/src/git-protocol";
import type { GitDiff, GitScope } from "../../../../desktop/src/workspace-git";
export class ChangesInspectionError extends Error {
  constructor(readonly reason: string, message: string) { super(message); }
}
export interface ChangesPort {
  scope(): Promise<GitScope>; diff(generation: string, path: string): Promise<GitDiff>;
  project(): string | null; onProjectChange(callback: (projectId: string | null) => void): () => void; dispose(): void;
}
type Job = {
  id: string; operation: GitOperation; projectId: string; generation?: string; path?: string;
  promise: Promise<GitScope | GitDiff>; resolve(value: GitScope | GitDiff): void; reject(error: Error): void;
  expired: boolean; timer?: ReturnType<typeof setTimeout>; recovery?: ReturnType<typeof setTimeout>;
};
/** No background scans: the host supplies project identity, and callers explicitly inspect. */
export function createChangesClient(send: (message: unknown) => void, target: Window = window): ChangesPort {
  const active = new Map<GitOperation, Job>(), generations = new Map<string, Set<string>>();
  const listeners = new Set<(projectId: string | null) => void>();
  let projectId: string | null = null, pendingDiff: Job | undefined, stopped: Error | undefined;
  const error = (reason: string, message: string) => new ChangesInspectionError(reason, message);
  const clear = (job: Job) => { clearTimeout(job.timer); clearTimeout(job.recovery); };
  const reset = (cause: Error) => {
    for (const job of [...active.values(), ...(pendingDiff ? [pendingDiff] : [])]) { clear(job); job.reject(cause); }
    active.clear(); pendingDiff = undefined; generations.clear();
  };
  const fail = (cause: Error) => { stopped = cause; reset(cause); target.removeEventListener("message", receive); listeners.clear(); };
  const start = (job: Job) => {
    active.set(job.operation, job);
    job.timer = setTimeout(() => {
      job.expired = true; job.reject(error("timeout", "Git did not respond. Waiting for its last inspection to finish."));
      // Hold this lane until an acknowledgement: pumping now could cascade busy failures.
      job.recovery = setTimeout(() => fail(error("unavailable", "Git did not reconnect. Reload this view to inspect again.")), 15000);
    }, 45000);
    try { send({ type: job.operation === "scope" ? "desktop/gitScope" : "desktop/gitDiff", requestId: job.id,
      ...(job.operation === "diff" ? { generation: job.generation, path: job.path } : {}) }); }
    catch { fail(error("unavailable", "Git could not reconnect. Reload this view to inspect again.")); }
  };
  const release = (job: Job) => {
    clear(job); if (active.get(job.operation) !== job) return;
    active.delete(job.operation);
    if (job.operation === "diff" && pendingDiff) {
      const next = pendingDiff; pendingDiff = undefined;
      if (!next.generation || !next.path || !generations.get(next.generation)?.has(next.path)) next.reject(error("stale", "This comparison is out of date. Refresh Changes."));
      else start(next);
    }
  };
  const receive = (event: MessageEvent) => {
    if (event.source !== target || event.origin !== "app://app") return;
    const raw = event.data;
    if (raw?.type === "desktop/project" && typeof raw.projectId === "string" && raw.projectId.length > 0 && raw.projectId.length <= 1024) {
      if (projectId !== raw.projectId) {
        reset(error("cancelled", "The project changed. Open a new comparison."));
        projectId = raw.projectId; for (const callback of listeners) callback(raw.projectId);
      }
      return;
    }
    if (raw?.type !== "desktop/gitResult" || typeof raw.requestId !== "string") return;
    const job = [...active.values()].find(value => value.id === raw.requestId); if (!job) return;
    let reply;
    try { reply = parseGitReply(raw, job.operation); }
    catch (reason) { fail(reason instanceof GitReplyTooLarge ? error("too-large", reason.message) : error("invalid", "Git returned an invalid inspection. Reload this view to retry.")); return; }
    if (reply.projectId !== projectId || job.projectId !== projectId) {
      reset(error("cancelled", "The project changed. Open a new comparison."));
      projectId = null; for (const callback of listeners) callback(null); return;
    }
    if (job.expired) { release(job); return; } // Late bytes acknowledge occupancy only.
    if (reply.outcome.status !== "ok") job.reject(error(reply.outcome.status, reply.outcome.message));
    else if (reply.operation === "scope" && "state" in reply.outcome.result) {
      const result = reply.outcome.result;
      if (result.state === "ready") {
        generations.set(result.generation, new Set(result.changes.map(change => change.path)));
        while (generations.size > 2) generations.delete(generations.keys().next().value!);
      }
      job.resolve(result);
    } else {
      if (!job.generation || !generations.has(job.generation)) job.reject(error("stale", "This comparison is out of date. Refresh Changes."));
      else job.resolve(reply.outcome.result);
    }
    release(job);
  };
  const make = (operation: GitOperation, generation?: string, path?: string): Job => {
    let resolve!: Job["resolve"], reject!: Job["reject"];
    const promise = new Promise<GitScope | GitDiff>((done, refused) => { resolve = done; reject = refused; });
    return { id: crypto.randomUUID(), operation, projectId: projectId!, promise, resolve, reject, expired: false,
      ...(generation === undefined ? {} : { generation }), ...(path === undefined ? {} : { path }) };
  };
  const unavailable = () => stopped ?? (!projectId ? error("project", "Wait for the project to connect before inspecting Git.") : undefined);
  target.addEventListener("message", receive);
  return {
    project: () => projectId, onProjectChange: callback => { if (!stopped) listeners.add(callback); return () => { listeners.delete(callback); }; },
    scope: () => {
      const cause = unavailable(); if (cause) return Promise.reject(cause);
      const job = active.get("scope") ?? make("scope"); if (!active.has("scope")) start(job);
      return job.promise as Promise<GitScope>;
    },
    diff: (generation, path) => {
      const cause = unavailable(); if (cause) return Promise.reject(cause);
      if (!isWorkspacePath(path, false)) return Promise.reject(error("unsupported", "This filename cannot be shown in Desktop."));
      if (!generations.get(generation)?.has(path)) return Promise.reject(error("stale", "This file is not in a retained comparison. Refresh Changes."));
      const running = active.get("diff");
      if (running?.generation === generation && running.path === path) {
        if (pendingDiff) { pendingDiff.reject(error("superseded", "A newer file selection replaced this inspection.")); pendingDiff = undefined; }
        return running.promise as Promise<GitDiff>;
      }
      if (pendingDiff?.generation === generation && pendingDiff.path === path) return pendingDiff.promise as Promise<GitDiff>;
      if (pendingDiff) pendingDiff.reject(error("superseded", "A newer file selection replaced this inspection."));
      const job = make("diff", generation, path);
      if (running) pendingDiff = job; else start(job);
      return job.promise as Promise<GitDiff>;
    },
    dispose: () => fail(error("cancelled", "Git inspection cancelled.")),
  };
}
