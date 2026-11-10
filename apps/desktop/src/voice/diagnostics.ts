import { appendFile, chmod, mkdir, rename, stat } from "node:fs/promises";
import { dirname } from "node:path";

export type DiagnosticSource = "app" | "host";
export type DiagnosticValue = string | number | boolean | null;

/**
 * A local JSON-lines record of a voice session: what the microphone heard, when a pause or
 * resumed speech was detected, what was decided and what was said. Best-effort: a write
 * failure never reaches the voice session. One rotated copy is kept.
 */
export class VoiceDiagnostics {
  private queue: Promise<void> = Promise.resolve();

  constructor(private readonly path: string, private readonly maxBytes = 5_000_000,
    private readonly now: () => Date = () => new Date()) {}

  write(source: DiagnosticSource, event: string, data: Record<string, DiagnosticValue>): void {
    const line = `${JSON.stringify({ at: this.now().toISOString(), source, event, ...data })}\n`;
    this.queue = this.queue.then(() => this.append(line)).catch(() => {});
  }

  flush(): Promise<void> {
    return this.queue;
  }

  private async append(line: string): Promise<void> {
    // Transcripts of what was said live here: private to the user, also for a folder that
    // an earlier version created with the default mode.
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    await chmod(dirname(this.path), 0o700);
    const size = await stat(this.path).then(info => info.size, () => 0);
    if (size > this.maxBytes) await rename(this.path, `${this.path}.1`);
    await appendFile(this.path, line, { mode: 0o600 });
  }
}
