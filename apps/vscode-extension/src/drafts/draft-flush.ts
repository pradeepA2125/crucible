import { randomUUID } from "node:crypto";
export class DraftFlusher {
  private pending = new Map<string, { view: unknown; finish(error?: string): void }>();
  constructor(private readonly timeout = 2000) {}
  request(view: unknown, post: (payload: Record<string, unknown>) => void): Promise<void> {
    const requestId = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(requestId); reject(new Error("Draft could not be saved. Navigation was cancelled; retry after the conversation reconnects.")); }, this.timeout);
      this.pending.set(requestId, { view, finish: error => { clearTimeout(timer); this.pending.delete(requestId); if (error) reject(new Error(error)); else resolve(); } });
      post({ type: "draft/flush", requestId });
    });
  }
  async beforeQuit(view: unknown, post: (payload: Record<string, unknown>) => void, canFlush: boolean): Promise<void> {
    if (!canFlush) return;
    try { await this.request(view, post); }
    catch { throw new Error("Draft could not be saved. Retry saving, or explicitly discard the latest unsaved edits before quitting."); }
  }
  acknowledge(view: unknown, requestId: string, error?: string): void {
    const pending = this.pending.get(requestId);
    if (pending && pending.view === view) pending.finish(error);
  }
}
