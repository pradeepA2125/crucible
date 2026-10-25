import { ThreadMetadataSchema, type ThreadPreference } from "../../../../desktop/src/thread-metadata";
export type Metadata = Record<string, ThreadPreference>;
export interface ThreadMetadataPort { read(): Promise<Metadata>; change(threadId: string, action: "pin" | "archive" | "name" | "viewed" | "unread", value?: boolean | string | null): Promise<Metadata>; }
export function createThreadMetadataClient(send: (message: unknown) => void, target: Window = window): ThreadMetadataPort {
  let queue: Promise<unknown> = Promise.resolve();
  const request = (fields: Record<string, unknown>) => new Promise<Metadata>((resolve, reject) => {
    const requestId = crypto.randomUUID();
    const done = () => { clearTimeout(timer); target.removeEventListener("message", receive); };
    const receive = (event: MessageEvent) => {
      if (event.source !== target || event.origin !== "app://app") return;
      const m = event.data;
      if (m?.type !== "desktop/threadsResult" || m.requestId !== requestId) return;
      done(); if (m.error) reject(new Error(String(m.error))); else { const parsed = ThreadMetadataSchema.safeParse(m.metadata); if (parsed.success) resolve(parsed.data); else reject(new Error("Invalid conversation preferences.")); }
    };
    const timer = setTimeout(() => { done(); reject(new Error("Conversation preferences did not respond. Retry after reconnecting.")); }, 4000);
    target.addEventListener("message", receive);
    try { send({ ...fields, requestId }); } catch (error) { done(); reject(error); }
  });
  const serial = (fields: Record<string, unknown>) => { const result = queue.catch(() => {}).then(() => request(fields)); queue = result; return result; };
  return { read: () => serial({ type: "desktop/threadsRead" }), change: (threadId, action, value) => serial({ type: "desktop/threadsUpdate", threadId, action, ...(value !== undefined ? { value } : {}) }) };
}
