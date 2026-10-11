import { parseContextUsage, type ContextUsage } from "@crucible/editor-client";
/** Local connection/view state only: all measurements and capacity come from agentd. */
export class ContextUsageState {
  usage: ContextUsage | null = null;
  stale = false;
  epoch = 0;
  thread: string | null = null;
  url = "";
  private process: string | null = null;
  private readonly retired = new Set<string>();
  navigate(thread: string | null, url: string): number {
    this.thread = thread; this.url = url;
    this.usage = null; this.stale = false;
    this.process = null;
    return ++this.epoch;
  }
  current(epoch: number, url: string): boolean { return epoch === this.epoch && url === this.url; }
  accept(raw: unknown, epoch: number, url: string, source: "live" | "sse", process?: string | null): "accepted" | "ignored" | "hydrate" {
    if (!this.current(epoch, url)) return "ignored";
    const usage = parseContextUsage(raw);
    if (source === "live") {
      if (!process || this.retired.has(process)) return "ignored";
      if (process !== this.process) {
        if (this.process) this.retired.add(this.process);
        this.process = process; this.usage = null; this.stale = false;
      }
    }
    // A healthy /live envelope proves connectivity, not telemetry freshness.
    // Keep the last measurement visibly stale until matching valid data arrives.
    if (!usage) {
      if (source === "live") this.stale = this.usage !== null;
      return source === "live" ? "accepted" : "ignored";
    }
    if (usage.thread_id !== this.thread || this.retired.has(usage.process_id)) {
      if (source === "live") this.stale = this.usage !== null;
      return "ignored";
    }
    if (usage.process_id !== this.process) {
      if (source === "live") this.stale = this.usage !== null;
      return source === "sse" ? "hydrate" : "ignored";
    }
    const previous = this.usage;
    if (previous && (usage.generation < previous.generation || usage.revision < previous.revision)) return "ignored";
    if (!previous || usage.revision > previous.revision || usage.generation > previous.generation) this.usage = usage;
    if (source === "live") this.stale = false;
    return "accepted";
  }
  fail(epoch: number, url: string): void { if (this.current(epoch, url)) this.stale = true; }
}
