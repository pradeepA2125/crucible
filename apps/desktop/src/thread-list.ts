import type { ChatThreadSummary } from "@crucible/editor-client";
interface Ports {
  list(): Promise<ChatThreadSummary[]>; activeThread(): string; focused(): boolean; identity(): unknown;
  render(threads: ChatThreadSummary[], active: string): void; log(message: string): void;
}
/** Sidebar metadata only; never attaches/stops a turn or owns gate decisions. */
export class ThreadListPoller {
  private timer: ReturnType<typeof setInterval> | undefined;
  private disposed = false;
  private pending = false;
  private queued = false;
  private generation = 0;
  private publishing = false;
  private signature = "";
  private failed = false;
  constructor(private readonly ports: Ports) {}
  start(): void { if (!this.timer) this.timer = setInterval(() => { void this.refresh(); }, 5000); void this.refresh(true); }
  async refresh(force = false): Promise<void> {
    if (this.disposed) return;
    if (this.pending) { this.queued ||= force; return; }
    if (!force && !this.ports.focused()) return;
    const identity = this.ports.identity(), generation = this.generation; this.pending = true;
    try {
      const threads = await this.ports.list();
      if (this.disposed || identity !== this.ports.identity() || generation !== this.generation) return;
      const active = this.ports.activeThread();
      const signature = JSON.stringify([threads, active, identity]);
      if (signature !== this.signature) { this.signature = signature; this.publishing = true;
        try { this.ports.render(threads, active); } finally { this.publishing = false; } }
      this.failed = false;
    } catch {
      if (!this.disposed && !this.failed) this.ports.log("Conversation list refresh failed; it will retry.\n");
      this.failed = true;
    } finally { this.pending = false; if (this.queued) { this.queued = false; void this.refresh(true); } }
  }
  invalidate(): void { if (!this.publishing) { ++this.generation; this.signature = ""; } }
  dispose(): void { this.disposed = true; clearInterval(this.timer); }
}
