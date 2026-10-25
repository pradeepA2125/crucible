import { admittedFileRead, WorkspaceReadBusy } from "./workspace-read-budget.js";
export interface BaselineReadOptions { signal?: AbortSignal; deadline?: number; }
export class BaselineReadStopped extends Error {
  constructor(readonly reason: "timeout" | "cancelled" | "busy") {
    super(reason === "busy" ? "Earlier checkpoint inspections are still finishing. Retry shortly." : reason === "timeout" ? "Checkpoint inspection timed out. Refresh to retry." : "Checkpoint inspection cancelled.");
  }
}
export interface BaselineReadOperation {
  signal: AbortSignal;
  check(): void;
  file<T>(work: () => Promise<T>): Promise<T>;
  http<T>(work: (signal: AbortSignal) => Promise<T>): Promise<T>;
}
// Independent of the shared filesystem cap. A replacement project can't reset physical HTTP occupancy.
let pendingHttp = 0;
export function createBaselineReadOperations() {
  return async <T>(milliseconds: number, options: BaselineReadOptions, task: (operation: BaselineReadOperation) => Promise<T>): Promise<T> => {
    if (!Number.isFinite(milliseconds) || milliseconds < 0 || options.deadline !== undefined && !Number.isFinite(options.deadline)) throw new RangeError("Checkpoint read deadlines must be finite.");
    const controller = new AbortController();
    // Convert an external wall-clock deadline once; later wall-clock jumps cannot extend work.
    const duration = Math.max(0, Math.min(30000, milliseconds, (options.deadline ?? Infinity) - Date.now()));
    const deadline = performance.now() + duration;
    let stopped: BaselineReadStopped | undefined;
    const stop = (reason: BaselineReadStopped["reason"]) => { stopped ??= new BaselineReadStopped(reason); controller.abort(stopped); };
    const check = () => { if (!stopped && performance.now() >= deadline) stop("timeout"); if (stopped) throw stopped; };
    const cancelled = () => stop("cancelled");
    options.signal?.addEventListener("abort", cancelled, { once: true }); if (options.signal?.aborted) cancelled();
    const timer = setTimeout(() => stop("timeout"), duration);
    const wait = <V>(work: () => Promise<V>): Promise<V> => new Promise((resolve, reject) => {
      const aborted = () => reject(stopped!);
      try { check(); } catch (error) { reject(error); return; }
      controller.signal.addEventListener("abort", aborted, { once: true });
      Promise.resolve().then(() => { check(); return work(); }).then(value => { check(); resolve(value); }, reject)
        .catch(reject).finally(() => controller.signal.removeEventListener("abort", aborted));
    });
    const file: BaselineReadOperation["file"] = work => wait(async () => {
      try { return await admittedFileRead(work); }
      catch (error) { if (error instanceof WorkspaceReadBusy) { stop("busy"); check(); } throw error; }
    });
    const http: BaselineReadOperation["http"] = work => wait(async () => {
      if (pendingHttp >= 4) { stop("busy"); check(); }
      pendingHttp++;
      try { return await work(controller.signal); } finally { pendingHttp--; }
    });
    try { check(); const result = await wait(() => task({ check, file, http, signal: controller.signal })); check(); return result; }
    finally { clearTimeout(timer); options.signal?.removeEventListener("abort", cancelled); }
  };
}
