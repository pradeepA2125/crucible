import type { BackendTaskClient, ProjectChatActivity, ThreadLiveState } from "@crucible/editor-client";
/** One physical activity read per project host, with a visible deadline. */
export class WorkAuthority {
  private slot: { key: string; abort: AbortController; result: Promise<ProjectChatActivity> } | undefined;
  private closed = false;
  private gate: { abort: AbortController; result: Promise<ThreadLiveState> } | undefined;
  readGate(client: BackendTaskClient, thread: string): Promise<ThreadLiveState> {
    if (this.closed || this.gate) return Promise.reject(new Error("Waiting for the previous approval status read."));
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const actual = Promise.resolve().then(() => client.getThreadLiveState(thread, abort.signal));
    const deadline = new Promise<never>((_resolve, reject) => { timer = setTimeout(() => { abort.abort(); reject(new Error("Approval status timed out.")); }, 4000); });
    const slot = { abort, result: Promise.race([actual, deadline]).finally(() => clearTimeout(timer)) };
    this.gate = slot; void actual.finally(() => { if (this.gate === slot) this.gate = undefined; }).catch(() => {}); return slot.result;
  }
  read(key: string, client: BackendTaskClient, workspace: string): Promise<ProjectChatActivity> {
    if (this.closed || !client.getChatActivity) return Promise.reject(new Error("Project activity is unavailable. Reconnect before starting work."));
    if (this.slot) return this.slot.key === key && !this.slot.abort.signal.aborted ? this.slot.result : Promise.reject(new Error("Waiting for the previous project status read."));
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const deadline = new Promise<never>((_resolve, reject) => { timer = setTimeout(() => { abort.abort(); reject(new Error("Project activity timed out.")); }, 4000); });
    const actual = Promise.resolve().then(() => client.getChatActivity!(workspace, abort.signal));
    const slot = { key, abort, result: Promise.race([actual, deadline]).finally(() => clearTimeout(timer)) };
    this.slot = slot; void actual.finally(() => { if (this.slot === slot) this.slot = undefined; }).catch(() => {}); return slot.result;
  }
  dispose(): void { this.closed = true; this.slot?.abort.abort(); this.gate?.abort.abort(); }
}
