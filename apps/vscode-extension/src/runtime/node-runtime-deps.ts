// Trusted Node host adapters shared by VS Code and desktop. No IDE API.
import { execFile, spawn } from "node:child_process";
import { StdoutLines, type ExecOutcome, type ProcessDeps } from "./backend-process.js";
import type { ExecResult } from "./installer.js";
import { ownerFor, verifyOwnerEndpoint, assertOwnerSession } from "./project-owner.js";
import { readBackendToken } from "./backend-token.js";
import { readProcessInfo } from "./process-info.js";
import type { RuntimeOutput } from "./runtime-core.js";

function execWithTimeout(cmd: string, args: string[], timeoutMs: number): Promise<ExecOutcome> {
  return new Promise((resolve) => {
    // execFile's `timeout` sends SIGTERM at expiry; killSignal makes it SIGKILL, since an
    // old indexer treats --version as "start watching" and may ignore SIGTERM.
    execFile(cmd, args, { timeout: timeoutMs, killSignal: "SIGKILL", maxBuffer: 1024 * 1024 },
      (err, stdout, stderr) => {
        const e = err as (NodeJS.ErrnoException & { killed?: boolean; code?: number | string }) | null;
        resolve({
          code: e ? (typeof e.code === "number" ? e.code : null) : 0,
          stdout: String(stdout ?? ""),
          stderr: String(stderr ?? ""),
          timedOut: Boolean(e?.killed),
        });
      });
  });
}

export function execCollecting(cmd: string, args: string[], cwd?: string): Promise<ExecResult> {
  return new Promise((resolve) => {
    execFile(
      cmd,
      args,
      { maxBuffer: 64 * 1024 * 1024, ...(cwd ? { cwd } : {}) },
      (err, stdout, stderr) => {
        const code = err
          ? (err as NodeJS.ErrnoException & { code?: number | string }).code
          : 0;
        resolve({
          code: typeof code === "number" ? code : err ? 1 : 0,
          stdout: String(stdout ?? ""),
          stderr: String(stderr ?? (err ? err.message : "")),
        });
      },
    );
  });
}

export function createNodeProcessDeps(runtimeDir: string, output: RuntimeOutput): ProcessDeps {
  return {
    runtimeDir: runtimeDir,
    spawn: (cmd, args, opts) => {
      const child = spawn(cmd, args, { env: opts.env, cwd: opts.cwd, stdio: ["ignore", "pipe", "pipe"] });
      const lines = new StdoutLines();
      const exited = new Promise<number | null>((resolve) => {
        child.once("exit", (code) => resolve(code));
        child.once("error", () => resolve(null)); // spawn failure (ENOENT…)
      });
      child.stdout?.on("data", (chunk: Buffer) => {
        const text = chunk.toString();
        output.append(text);
        lines.push(text);
      });
      child.stderr?.on("data", (chunk: Buffer) => output.append(chunk.toString()));
      return {
        pid: child.pid ?? -1,
        kill: (sig) => { child.kill(sig); },
        onExit: (cb) => { void exited.then(cb); },
        onStdoutLine: (cb) => lines.subscribe(cb),
        exited,
      };
    },
    fetchJson: async (url, init) => {
      const base = new URL(url).origin, owner = ownerFor(base);
      const mutation = !["GET", "HEAD"].includes((init?.method ?? "GET").toUpperCase());
      if (mutation && !owner) throw new Error("This client is an observer; mutation requires its controlling host.");
      if (owner) await verifyOwnerEndpoint(base, owner, fetch, readBackendToken);
      if (owner && ownerFor(base) !== owner) throw new Error("Project ownership changed while the proof was waiting.");
      if (owner && init?.method && !["GET", "HEAD"].includes(init.method.toUpperCase())) {
        const token = readBackendToken(Number(new URL(base).port));
        const session = await fetch(`${base}/v1/project/session`, { headers: { "x-crucible-owner": owner.key, ...(token ? { authorization: `Bearer ${token}` } : {}) }, redirect: "manual", signal: AbortSignal.timeout(3000) });
        if (!session.ok) throw new Error("Project ownership could not be verified.");
        assertOwnerSession(await session.json(), owner);
      }
      if (owner && ownerFor(base) !== owner) throw new Error("Project ownership changed while waiting.");
      const token = readBackendToken(Number(new URL(url).port));
      const headers: Record<string, string> = owner ? { "x-crucible-owner": owner.key } : {};
      if (init?.body) headers["content-type"] = "application/json";
      if (token) headers.authorization = `Bearer ${token}`;
      const res = await fetch(url, { ...(init?.method ? { method: init.method } : {}),
        ...(init?.body ? { body: init.body } : {}), headers, redirect: "manual" });
      if (!res.ok) throw new Error(`request failed (${res.status}) for ${url}`);
      return res.json();
    },
    fetchRaw: async (url, init) => {
      const res = await fetch(url, { signal: init.signal, redirect: "manual" });
      return { status: res.status, body: await res.text() };
    },
    readToken: (port) => readBackendToken(port),
    processInfo: (pid) => readProcessInfo(pid, execWithTimeout),
    signal: (pid, sig) => { try { process.kill(pid, sig); } catch { /* gone */ } },
    exec: (cmd, args, timeoutMs) => execWithTimeout(cmd, args, timeoutMs),
    now: () => Date.now(),
    // exactOptionalPropertyTypes: omit the key on Windows rather than pass undefined.
    ...(process.getuid ? { uid: process.getuid() } : {}),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    isPidAlive: (pid) => {
      try {
        process.kill(pid, 0);
        return true;
      } catch (err) {
        return (err as NodeJS.ErrnoException).code === "EPERM";
      }
    },
    log: (line) => output.appendLine(line),
  };
}
