import type { BackendTaskClient } from "@crucible/editor-client";
type Backend = Pick<BackendTaskClient,"listCheckpoints"|"getCheckpointBaseline"|"getCheckpointBaselineFile">;
/** Resolve the gated client for every HTTP call; runtime restart may change its URL. */
export function currentBaselineBackend(current: () => Backend): Backend {
  return {
    listCheckpoints: (thread,signal) => { const client=current();if(!client.listCheckpoints)throw new Error("Retained checkpoints unavailable.");return client.listCheckpoints(thread,signal); },
    getCheckpointBaseline: (thread,seq,signal) => { const client=current();if(!client.getCheckpointBaseline)throw new Error("Retained checkpoints unavailable.");return client.getCheckpointBaseline(thread,seq,signal); },
    getCheckpointBaselineFile: (thread,seq,path,signal) => { const client=current();if(!client.getCheckpointBaselineFile)throw new Error("Retained checkpoints unavailable.");return client.getCheckpointBaselineFile(thread,seq,path,signal); },
  };
}
/** Only host-emitted thread state owns authority; switch intents do not. */
export function syncBaselineThread(context: {threadId:string|null}, payload: Record<string,unknown>, cancel: () => void): void {
  const next = payload.type === "clearThread" ? payload.threadId : payload.type === "renderThreadList" ? payload.activeThreadId : undefined;
  if (next !== null && typeof next !== "string") return;
  const threadId = next || null;
  if (context.threadId === threadId) return;
  context.threadId=threadId;cancel();
}
