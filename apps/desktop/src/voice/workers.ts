import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { access, lstat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { StringDecoder } from "node:string_decoder";
import { z } from "zod";

const maxLineBytes = 1_400_000;
const workerError = "The local voice engine stopped. Start voice again to reconnect.";

/** A helper's file name on this platform (Windows executables end in `.exe`). */
export function helperPath(helpers: string, name: string, platform: string): string {
  return join(helpers, platform === "win32" ? `${name}.exe` : name);
}

/** The minimal environment a speech helper runs with: nothing from the user's shell, but what
 *  each system needs to load a program (Windows looks up DLLs through SystemRoot and PATH). */
export function helperEnvironment(platform: string, env: NodeJS.ProcessEnv, helpers: string): Record<string, string> {
  if (platform === "win32") {
    const systemRoot = env.SystemRoot ?? env.windir ?? "C:\\Windows";
    const temp = env.TEMP ?? env.TMP;
    return {
      SystemRoot: systemRoot, windir: systemRoot,
      ...(temp ? { TEMP: temp, TMP: temp } : {}),
      PATH: `${helpers};${systemRoot}\\System32`,
    };
  }
  return { PATH: "/usr/bin:/bin", LANG: "en_US.UTF-8", ...(env.TMPDIR ? { TMPDIR: env.TMPDIR } : {}) };
}

/** Whether a helper can be started: a regular file, executable on POSIX (Windows has no execute bit). */
export async function helperUsable(path: string, platform: string): Promise<boolean> {
  try {
    if (!(await lstat(path)).isFile()) return false;
    if (platform !== "win32") await access(path, constants.X_OK);
    return true;
  } catch { return false; }
}

interface Job<T> {
  id: string;
  resolve(value: T): void;
  reject(reason: Error): void;
}

export class LineWorker {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly completion: Promise<void>;
  private readonly decoder = new StringDecoder();
  private tail = "";
  private closed = false;

  constructor(path: string, args: string[], onLine: (line: string) => void, onFailure: (error: Error) => void) {
    this.child = spawn(path, args, {
      env: helperEnvironment(process.platform, process.env, dirname(path)),
      stdio: ["pipe", "pipe", "pipe"],
      shell: false,
    });
    this.completion = new Promise<void>(resolve => this.child.once("close", () => resolve()));
    // Native diagnostics can include recognized text. Never forward them to app logs.
    this.child.stderr.resume();
    const failed = () => {
      if (this.closed) return;
      this.close();
      onFailure(new Error(workerError));
    };
    this.child.once("error", failed);
    this.child.once("exit", failed);
    this.child.stdin.on("error", failed);
    this.child.stdout.on("data", (chunk: Buffer) => {
      if (this.closed) return;
      this.tail += this.decoder.write(chunk);
      let boundary = this.tail.indexOf("\n");
      while (boundary >= 0) {
        if (boundary > maxLineBytes) return failed();
        const line = this.tail.slice(0, boundary).trimEnd();
        this.tail = this.tail.slice(boundary + 1);
        if (line) onLine(line);
        if (this.closed) return;
        boundary = this.tail.indexOf("\n");
      }
      if (this.tail.length > maxLineBytes) failed();
    });
  }

  send(line: string): void {
    if (this.closed) throw new Error(workerError);
    this.child.stdin.write(line + "\n");
  }

  close(): Promise<void> {
    if (this.closed) return this.completion;
    this.closed = true;
    this.tail = "";
    this.child.stdin.destroy();
    if (this.child.exitCode !== null || this.child.signalCode !== null) return this.completion;
    this.child.kill("SIGTERM");
    const timer = setTimeout(() => {
      if (this.child.exitCode === null && this.child.signalCode === null) this.child.kill("SIGKILL");
    }, 1200);
    timer.unref();
    this.child.once("exit", () => clearTimeout(timer));
    return this.completion;
  }
}

class Readiness {
  private resolve!: () => void;
  private reject!: (reason: Error) => void;
  private readonly promise = new Promise<void>((resolve, reject) => {
    this.resolve = resolve;
    this.reject = reject;
  });
  constructor() { void this.promise.catch(() => {}); }
  ready(): void { this.resolve(); }
  fail(error: Error): void { this.reject(error); }
  wait(signal: AbortSignal): Promise<void> { return withTimeout(this.promise, signal, 30_000); }
}

function newJob<T>(id: string): Job<T> & { result: Promise<T> } {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const result = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { id, resolve, reject, result };
}

function withTimeout<T>(promise: Promise<T>, signal: AbortSignal, milliseconds: number): Promise<T> {
  const deadline = AbortSignal.any([signal, AbortSignal.timeout(milliseconds)]);
  if (deadline.aborted) return Promise.reject(deadline.reason);
  return new Promise<T>((resolve, reject) => {
    const aborted = () => reject(deadline.reason);
    deadline.addEventListener("abort", aborted, { once: true });
    void promise.then(resolve, reject).finally(() => deadline.removeEventListener("abort", aborted));
  });
}

export class Recognizer {
  private readonly ready = new Readiness();
  private readonly worker: LineWorker;
  private job: Job<string> | undefined;

  /** prompt: the project's own names, added to Whisper's coding glossary. */
  constructor(path: string, model: string, private readonly failure: (error: Error) => void, prompt?: string) {
    this.worker = new LineWorker(path, ["--model", model, ...(prompt ? ["--prompt", prompt] : [])], line => {
      if (line === "ready") { this.ready.ready(); return; }
      const [id, status, ...content] = line.split("\t");
      const job = this.job;
      if (!job || job.id !== id) return;
      this.job = undefined;
      if (status === "result") job.resolve(content.join("\t").trim().slice(0, 4000));
      else job.reject(new Error("The voice engine could not transcribe this audio."));
    }, error => {
      this.ready.fail(error);
      this.job?.reject(error);
      this.job = undefined;
      failure(error);
    });
  }

  wait(signal: AbortSignal): Promise<void> { return this.ready.wait(signal); }

  async decode(pcm: ArrayBuffer, signal: AbortSignal): Promise<string> {
    if (this.job) throw new Error("A transcript is already being decoded.");
    const id = randomUUID();
    const job = newJob<string>(id);
    this.job = job;
    try {
      this.worker.send(`${id}\t${Buffer.from(pcm).toString("base64")}`);
      return await withTimeout(job.result, signal, 15_000);
    } catch (error) {
      if (this.job === job) {
        this.job = undefined;
        this.worker.close();
        if (!signal.aborted) this.failure(new Error(workerError));
      }
      throw error;
    }
  }

  close(): Promise<void> {
    const error = new Error("The voice session ended.");
    this.ready.fail(error);
    this.job?.reject(error);
    this.job = undefined;
    return this.worker.close();
  }
}

/**
 * Silero voice activity detection (crucible-whisper --vad): one speech probability per 32 ms.
 * Optional: a failure marks it unusable and the session falls back to loudness.
 */
export class Detector {
  private readonly ready = new Readiness();
  private readonly worker: LineWorker;
  private readonly jobs = new Map<string, Job<number[]>>();
  private failed = false;

  constructor(path: string, model: string, private readonly failure: (error: Error) => void) {
    this.worker = new LineWorker(path, ["--vad", model], line => {
      if (line === "ready") { this.ready.ready(); return; }
      const [id = "", status, content = ""] = line.split("\t");
      const job = this.jobs.get(id);
      if (!job) return;
      this.jobs.delete(id);
      const probs = content ? content.split(",").map(Number) : [];
      if (status !== "result" || probs.some(value => !Number.isFinite(value) || value < 0 || value > 1)) {
        job.reject(new Error("The voice activity detector rejected this audio."));
      } else {
        job.resolve(probs);
      }
    }, error => this.stop(error));
  }

  get usable(): boolean { return !this.failed; }

  wait(signal: AbortSignal): Promise<void> { return this.ready.wait(signal); }

  async detect(pcm: ArrayBuffer, signal: AbortSignal): Promise<number[]> {
    if (this.failed) throw new Error(workerError);
    if (this.jobs.size >= 8) throw new Error("The voice activity detector is behind.");
    const id = randomUUID();
    const job = newJob<number[]>(id);
    this.jobs.set(id, job);
    try {
      this.worker.send(`${id}\t${Buffer.from(pcm).toString("base64")}`);
      return await withTimeout(job.result, signal, 2_000);
    } finally {
      this.jobs.delete(id);
    }
  }

  close(): Promise<void> {
    this.failed = true;
    const error = new Error("The voice session ended.");
    this.ready.fail(error);
    for (const job of this.jobs.values()) job.reject(error);
    this.jobs.clear();
    return this.worker.close();
  }

  private stop(error: Error): void {
    if (this.failed) return;
    this.failed = true;
    this.ready.fail(error);
    for (const job of this.jobs.values()) job.reject(error);
    this.jobs.clear();
    this.failure(error);
  }
}

const ttsMessage = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ready") }),
  z.object({ type: z.literal("audio"), id: z.string(), pcm: z.string().max(350_000), sampleRate: z.number().int().min(8000).max(96000) }),
  z.object({ type: z.literal("done"), id: z.string() }),
  z.object({ type: z.literal("error"), id: z.string(), error: z.string().optional() }),
]);

export class Synthesizer {
  private readonly ready = new Readiness();
  private readonly worker: LineWorker;
  private job: (Job<void> & { audio(pcm: ArrayBuffer, sampleRate: number): void }) | undefined;

  constructor(path: string, args: string[], failure: (error: Error) => void) {
    this.worker = new LineWorker(path, args, line => {
      let data: unknown;
      try { data = JSON.parse(line); } catch { return; }
      const parsed = ttsMessage.safeParse(data);
      if (!parsed.success) return;
      const message = parsed.data;
      if (message.type === "ready") { this.ready.ready(); return; }
      const job = this.job;
      if (!job || job.id !== message.id) return;
      if (message.type === "audio") {
        const bytes = Buffer.from(message.pcm, "base64");
        if (bytes.length && bytes.length % 2 === 0) job.audio(Uint8Array.from(bytes).buffer, message.sampleRate);
      } else {
        this.job = undefined;
        if (message.type === "done") job.resolve();
        else job.reject(new Error("The local voice could not read this reply."));
      }
    }, error => {
      this.ready.fail(error);
      this.job?.reject(error);
      this.job = undefined;
      failure(error);
    });
  }

  wait(signal: AbortSignal): Promise<void> { return this.ready.wait(signal); }

  async speak(text: string, audio: (pcm: ArrayBuffer, sampleRate: number) => void, signal: AbortSignal): Promise<void> {
    if (this.job) throw new Error("Another voice segment is still playing.");
    signal.throwIfAborted();
    const id = randomUUID();
    const job = { ...newJob<void>(id), audio };
    this.job = job;
    try {
      this.worker.send(JSON.stringify({ type: "speak", id, text }));
      await withTimeout(job.result, signal, 20_000);
    } finally {
      if (this.job === job) {
        this.job = undefined;
        this.worker.send(JSON.stringify({ type: "cancel", id }));
      }
    }
  }

  close(): Promise<void> {
    const error = new Error("The voice session ended.");
    this.ready.fail(error);
    this.job?.reject(error);
    this.job = undefined;
    return this.worker.close();
  }
}
