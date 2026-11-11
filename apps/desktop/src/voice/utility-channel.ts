import type { WorkerChannel } from "./workers.js";

/** The parts of Electron's UtilityProcess this channel uses (injected, so this module never
 *  imports Electron and can be tested in plain Node). */
export interface UtilityChild {
  postMessage(message: unknown): void;
  kill(): boolean;
  on(event: "message", listener: (message: unknown) => void): unknown;
  on(event: "exit", listener: (code: number) => void): unknown;
}

export type ForkUtility = (script: string, args: string[]) => UtilityChild;

const workerError = "The local voice engine stopped. Start voice again to reconnect.";

/** A line-protocol worker running as an Electron utility process: each line travels as one
 *  string message, in both directions. */
export class UtilityChannel implements WorkerChannel {
  private readonly child: UtilityChild;
  private readonly exited: Promise<void>;
  private closed = false;

  constructor(fork: ForkUtility, script: string, args: string[],
    onLine: (line: string) => void, onFailure: (error: Error) => void) {
    this.child = fork(script, args);
    this.exited = new Promise(resolve => {
      this.child.on("exit", () => {
        resolve();
        if (this.closed) return;
        this.closed = true;
        onFailure(new Error(workerError));
      });
    });
    this.child.on("message", message => {
      if (!this.closed && typeof message === "string" && message) onLine(message);
    });
  }

  send(line: string): void {
    if (this.closed) throw new Error(workerError);
    this.child.postMessage(line);
  }

  close(): Promise<void> {
    if (this.closed) return this.exited;
    this.closed = true;
    this.child.kill();
    return this.exited;
  }
}
