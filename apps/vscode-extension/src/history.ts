import type { ChatMessage } from "@crucible/editor-client";
export type HistoryView = { threadId: string; phase: "loading" | "current" | "unavailable";
  cached: boolean; notice?: string; navigationSafe?: boolean; workBlocked?: string };
/** Extra saved copies only; the visible transcript is not silently truncated. */
export class HistoryCache {
  private readonly entries = new Map<string, { messages: ChatMessage[]; bytes: number }>();
  private bytes = 0;
  constructor(private readonly limit = 32 * 1024 * 1024, private readonly count = 8) {}
  static key(url: string, project: string, threadId: string): string { return JSON.stringify([url, project, threadId]); }
  get(key: string): ChatMessage[] | undefined {
    const entry = this.entries.get(key); if (!entry) return undefined;
    this.entries.delete(key); this.entries.set(key, entry);
    return structuredClone(entry.messages);
  }
  put(key: string, messages: ChatMessage[]): boolean {
    const prior = this.entries.get(key);
    if (prior) { this.bytes -= prior.bytes; this.entries.delete(key); }
    const bytes = Buffer.byteLength(JSON.stringify(messages), "utf8") + Buffer.byteLength(key, "utf8");
    if (bytes > this.limit) return false;
    this.entries.set(key, { messages: structuredClone(messages), bytes }); this.bytes += bytes;
    while (this.bytes > this.limit || this.entries.size > this.count) {
      const oldest = this.entries.keys().next().value!;
      this.bytes -= this.entries.get(oldest)!.bytes; this.entries.delete(oldest);
    }
    return true;
  }
  clear(): void { this.entries.clear(); this.bytes = 0; }
}
/** One actual read across attachments; a cancelled-but-unsettled fetch still owns its slot. */
export class HistoryReader {
  private slot: { key: string; abort: AbortController; visible: Promise<ChatMessage[]> } | undefined;
  private disposed = false;
  constructor(private readonly deadline = 4000) {}
  cancel(): void { this.slot?.abort.abort(); }
  dispose(): void { this.disposed = true; this.cancel(); }
  read(key: string, read: (signal: AbortSignal) => Promise<ChatMessage[]>): Promise<ChatMessage[]> {
    if (this.disposed) return Promise.reject(new Error("History reader is closed"));
    if (this.slot) return this.slot.key === key && !this.slot.abort.signal.aborted
      ? this.slot.visible : Promise.reject(new Error("Waiting for the previous history read to close"));
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = () => {};
    const stop = new Promise<never>((_resolve, reject) => {
      cancelled = () => reject(new Error("History read interrupted"));
      abort.signal.addEventListener("abort", cancelled, { once: true });
      timer = setTimeout(() => { abort.abort(); reject(new Error("History read timed out")); }, this.deadline);
    });
    // Reserve before the fetch starts, even when it throws synchronously.
    const actual = Promise.resolve().then(() => read(abort.signal));
    const slot = { key, abort, visible: Promise.race([actual, stop]).finally(() => {
      clearTimeout(timer); abort.signal.removeEventListener("abort", cancelled);
    }) };
    this.slot = slot;
    void actual.finally(() => { if (this.slot === slot) this.slot = undefined; }).catch(() => {});
    return slot.visible;
  }
}
