// vscode-free. One instance per workspace folder; owns agentd + watcher children.
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import {
  buildJdtlsCommand,
  configDirForPlatform,
  findEquinoxLauncher,
  findJavaExecutable,
  jdtlsDataDir,
} from "./jdtls.js";
import { binPath, venvPython } from "./installer.js";
import { platformKey, type PlatformKey } from "./manifest.js";
import { probeHealth, type ProbeDeps } from "./probe-health.js";

export interface BackendSettings {
  backend: string;                     // "gemini" | "openai" | ... (never "scripted")
  model: string;
  apiKey?: { envVar: string; value: string };   // from SecretStorage, spawn-env only
  extraEnv?: Record<string, string>;   // policies/flags from VS Code settings
  skillsDisabled?: string[];           // → CRUCIBLE_SKILLS_DISABLED (comma-joined)
  contextWindow?: number; // → CRUCIBLE_MEMORY_WINDOW_TOKENS
  reasoningEffort?: string; // → CRUCIBLE_REASONING_EFFORT
}
export interface ChildHandle {
  pid: number;
  kill(signal?: NodeJS.Signals): void;
  onExit(cb: (code: number | null) => void): void;
  /** Complete stdout lines; lines printed before the first subscriber are replayed. */
  onStdoutLine(cb: (line: string) => void): void;
  /** Resolves when the child exits — even if it exited before anyone asked. */
  exited: Promise<number | null>;
}
export interface ExecOutcome { code: number | null; stdout: string; stderr: string; timedOut: boolean }
export interface ProcessInfo { uid: number; command: string; startedAtSec: number }
export interface ProcessDeps {
  runtimeDir: string;
  spawn(cmd: string, args: string[], opts: { env: Record<string, string>; cwd?: string }): ChildHandle;
  /** Throws on non-2xx; attaches the bearer token for the URL's port. */
  fetchJson(url: string, init?: { method?: string; body?: string }): Promise<unknown>;
  fetchRaw(url: string, init: { signal: AbortSignal }): Promise<{ status: number; body: string }>;
  readToken(port: number): string | undefined;
  processInfo(pid: number): Promise<ProcessInfo | null>;
  signal(pid: number, sig: NodeJS.Signals): void;
  /** Mandatory timeout: the process is killed when it expires. */
  exec(cmd: string, args: string[], timeoutMs: number): Promise<ExecOutcome>;
  now(): number;
  sleep(ms: number): Promise<void>;
  isPidAlive(pid: number): boolean;
  log(line: string): void;
  platform?: PlatformKey;
  uid?: number;
}

// Same table as agentd/providers/factory.py::MODEL_ENV_VAR.
export const MODEL_ENV_VAR: Record<string, string> = {
  anthropic: "CRUCIBLE_ANTHROPIC_MODEL", gemini: "CRUCIBLE_GEMINI_MODEL",
  huggingface: "CRUCIBLE_HUGGINGFACE_MODEL", groq: "CRUCIBLE_GROQ_MODEL",
  openrouter: "CRUCIBLE_OPENROUTER_MODEL", watsonx: "CRUCIBLE_WATSONX_MODEL",
  ollama: "CRUCIBLE_OLLAMA_MODEL", turboquant: "CRUCIBLE_TURBOQUANT_MODEL",
  openai: "CRUCIBLE_OPENAI_MODEL", openai_compatible: "CRUCIBLE_OPENAI_COMPAT_MODEL",
};

const HEALTH_ATTEMPTS = 60;
export const HANDSHAKE_TIMEOUT_MS = 10_000;
const HANDSHAKE_RE = /^CRUCIBLE_SERVE (\{.*\})\s*$/;

export class RuntimeUpdateRequiredError extends Error {
  constructor(readonly component: "agentd" | "indexer" | "backend", detail: string) {
    super(`Crucible runtime update required (${component}): ${detail}`);
    this.name = "RuntimeUpdateRequiredError";
  }
}

export class StdoutLines {
  private partial = "";
  private readonly buffered: string[] = [];
  private subscriber: ((line: string) => void) | null = null;

  push(chunk: string): void {
    const parts = (this.partial + chunk).split("\n");
    this.partial = parts.pop() ?? "";
    for (const line of parts) this.emit(line.replace(/\r$/, ""));
  }

  subscribe(cb: (line: string) => void): void {
    this.subscriber = cb;
    for (const line of this.buffered.splice(0)) cb(line);
  }

  private emit(line: string): void {
    if (this.subscriber) this.subscriber(line);
    else this.buffered.push(line);
  }
}

export function parseHandshake(line: string): { pid: number; port: number } | null {
  const json = HANDSHAKE_RE.exec(line)?.[1];
  if (json === undefined) return null;
  try {
    const raw = JSON.parse(json) as { pid?: unknown; port?: unknown };
    return Number.isInteger(raw.pid) && Number.isInteger(raw.port)
      ? { pid: raw.pid as number, port: raw.port as number }
      : null;
  } catch {
    return null;
  }
}

export function finalSpawnEnv(
  ...parts: Array<Record<string, string | undefined>>
): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const part of parts) {
    for (const [k, v] of Object.entries(part)) if (v !== undefined) merged[k] = v;
  }
  // Spec §3.6: a managed spawn never runs with the token check off, whoever set it.
  delete merged.CRUCIBLE_AUTH_DISABLED;
  return merged;
}
const INDEX_WARM_ATTEMPTS = 120;

export function buildBackendEnv(
  workspace: string, settings: BackendSettings, runtimeDir: string,
  platform: PlatformKey = platformKey(),
): Record<string, string> {
  const agentdDir = join(workspace, ".crucible/state");
  const built: Record<string, string> = {
    CRUCIBLE_REASONING_BACKEND: settings.backend,
    CRUCIBLE_WORKSPACE_PATH: workspace,
    CRUCIBLE_DB_PATH: join(agentdDir, "agentd.sqlite3"),
    CRUCIBLE_CHAT_DB_PATH: join(agentdDir, "chat.sqlite3"),
    CRUCIBLE_SHADOW_ROOT: join(agentdDir, "shadows"),
    CRUCIBLE_LOG_FILE: join(agentdDir, "agentd.log"),
    CRUCIBLE_ARTIFACTS_ROOT: join(agentdDir, "artifacts"),
    CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH: join(workspace, ".crucible", "index-snapshot.json"),
    CRUCIBLE_MEMORY_DB_PATH: join(agentdDir, "memory.sqlite3"),
    CRUCIBLE_VECTOR_INDEX_PATH: join(workspace, ".crucible", "vector-index"),
    CRUCIBLE_LOG_DIR: join(workspace, ".tmp", "reasoning"),
    CRUCIBLE_RIPGREP_CMD: binPath(runtimeDir, "rg", platform),
    CRUCIBLE_CHAT_CONTROLLER: "1",
    CRUCIBLE_SKILLS_ENABLED: "1",
    CRUCIBLE_MCP_ENABLED: "1",
    // One shared request budget; NIM free tier allows ~40/min per key (spec §3.11).
    CRUCIBLE_PROVIDER_MAX_RPM: "35",
    CRUCIBLE_EXEC_SESSIONS_ENABLED: "1",
    CRUCIBLE_SEMANTIC_RETRIEVAL: "true",
    CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT: "false",
    CRUCIBLE_SHELL_POLICY: "ask",
    CRUCIBLE_SCOPE_POLICY: "ask",
    CRUCIBLE_SCOPE_TRIGGER: "any",
  };
  const modelVar = MODEL_ENV_VAR[settings.backend];
  if (modelVar) built[modelVar] = settings.model;
  if (settings.apiKey) built[settings.apiKey.envVar] = settings.apiKey.value;
  if (settings.skillsDisabled?.length) {
    built.CRUCIBLE_SKILLS_DISABLED = settings.skillsDisabled.join(",");
  }
  // The declared context window from the settings panel. Written into the SAME env
  // var a hand-configured deployment uses, which is what collapses the spec's
  // "provider window > env var > 128000" order into one value the backend never
  // has to arbitrate. Absent means absent: a user who set only the env var and
  // never opened the panel keeps exactly today's behaviour.
  if (settings.contextWindow !== undefined) {
    built.CRUCIBLE_MEMORY_WINDOW_TOKENS = String(settings.contextWindow);
  }
  // Third of the three env sites a new backend flag needs (start-backend.sh and
  // the repo-root .env are the other two); the managed spawn reads neither of
  // those, so omitting this here is how a persisted dial silently does nothing.
  if (settings.reasoningEffort) {
    built.CRUCIBLE_REASONING_EFFORT = settings.reasoningEffort;
  }
  return { ...built, ...settings.extraEnv };
}

interface LockInfo { pid: number; port: number; started_at: number }

function readLock(workspace: string): LockInfo | null {
  try {
    const raw = JSON.parse(
      readFileSync(join(workspace, ".crucible/state", "agentd.lock"), "utf8"));
    if (typeof raw.pid !== "number" || typeof raw.port !== "number") return null;
    return raw as LockInfo;
  } catch {
    return null;
  }
}

export class BackendProcess {
  private readonly platform: PlatformKey;
  private backend: ChildHandle | undefined;
  private watcher: ChildHandle | undefined;
  private _port: number | undefined;
  private lastChildPid: number | undefined;

  constructor(private readonly deps: ProcessDeps) {
    this.platform = deps.platform ?? platformKey();
  }

  get port(): number | undefined {
    return this._port;
  }

  get backendHandle(): ChildHandle | undefined {
    return this.backend;
  }

  async start(
    workspace: string, settings: BackendSettings,
  ): Promise<{ port: number; reused: boolean }> {
    // 1. Reuse a live locked backend (a managed spawn already has a watcher).
    const lock = readLock(workspace);
    if (lock && (await probeHealth(
      lock.port, this.probeDeps(), { pid: lock.pid, workspace })) === "authed") {
      this._port = lock.port;
      this.deps.log(`[runtime] reusing live backend pid=${lock.pid} port=${lock.port}`);
      return { port: lock.port, reused: true };
    }
    if (lock) {
      try { unlinkSync(join(workspace, ".crucible/state", "agentd.lock")); } catch { /* gone already */ }
      this.deps.log(`[runtime] reaped stale lock (pid=${lock.pid})`);
    }

    // 2. Spawn agentd.serve on any free port; it binds first, then reports the port.
    const env = finalSpawnEnv(
      process.env as Record<string, string | undefined>,
      buildBackendEnv(workspace, settings, this.deps.runtimeDir, this.platform),
    );
    const child = this.deps.spawn(
      venvPython(this.deps.runtimeDir, this.platform),
      ["-m", "agentd.serve", "--port", "0", "--workspace-lock", workspace],
      { env, cwd: workspace },
    );
    this.backend = child;
    this.lastChildPid = child.pid;
    let handshake: { pid: number; port: number };
    try {
      handshake = await this.waitForHandshake(child);
    } catch (err) {
      await this.stop();
      throw err;
    }
    const port = handshake.port;
    this._port = port;

    // 3. Health: the proof must name this child's pid and this workspace.
    let up = false;
    for (let i = 0; i < HEALTH_ATTEMPTS; i++) {
      const result = await probeHealth(port, this.probeDeps(), { pid: handshake.pid, workspace });
      if (result === "authed") { up = true; break; }
      if (result === "preauth") {
        await this.stop();
        throw new RuntimeUpdateRequiredError("backend", "the backend answered without a token proof");
      }
      await this.deps.sleep(1000);
    }
    if (!up) {
      await this.stop();
      throw new Error("backend did not become healthy within 60s — see logs");
    }

    // 4. Pre-warm the index (non-fatal — the watcher keeps it fresh anyway).
    try {
      await this.deps.fetchJson(`http://127.0.0.1:${port}/v1/index/build`, {
        method: "POST",
        body: JSON.stringify({ workspace_path: workspace }),
      });
      for (let i = 0; i < INDEX_WARM_ATTEMPTS; i++) {
        const status = await this.deps.fetchJson(
          `http://127.0.0.1:${port}/v1/index/status`) as { building?: boolean };
        if (status.building === false) break;
        await this.deps.sleep(1000);
      }
    } catch (err) {
      this.deps.log(`[runtime] index pre-warm failed (non-fatal): ${String(err)}`);
    }

    // 5. Watcher (incremental re-index; LSP only when the LSP install landed).
    this.spawnWatcher(workspace, port);
    return { port, reused: false };
  }

  private spawnWatcher(workspace: string, port: number): void {
    // Kill any previously-running watcher (e.g. after a crash-restart: the old
    // watcher is still alive but points at the now-dead port, so it will never
    // update the snapshot for new files).
    if (this.watcher) {
      try { this.watcher.kill(); } catch { /* already dead */ }
      this.watcher = undefined;
    }
    const indexer = binPath(this.deps.runtimeDir, "crucible-indexer", this.platform);
    if (!existsSync(indexer)) {
      this.deps.log("[runtime] indexer binary missing — watcher not started");
      return;
    }
    const lspBin = (name: string) => join(
      this.deps.runtimeDir, "node_modules", ".bin",
      this.platform === "win32-x64" ? `${name}.cmd` : name);
    const lspInstalled = existsSync(join(this.deps.runtimeDir, "node_modules"));
    const rustAnalyzerBin = binPath(this.deps.runtimeDir, "rust-analyzer", this.platform);
    const goplsBin = binPath(this.deps.runtimeDir, "gopls", this.platform);
    // Managed install lands at rustAnalyzerBin/goplsBin (installer.ts); fall
    // back to a bare PATH lookup for a dev backend running outside the
    // managed runtime — the indexer degrades gracefully either way if it's
    // still not found.
    const rsCmd = existsSync(rustAnalyzerBin) ? rustAnalyzerBin : "rust-analyzer";
    const goCmd = existsSync(goplsBin) ? goplsBin : "gopls";
    const javaCmd = this.buildJavaLspCommand(workspace);
    const env = finalSpawnEnv(process.env as Record<string, string | undefined>, {
      CRUCIBLE_BACKEND_URL: `http://127.0.0.1:${port}`,
      CRUCIBLE_LSP_ENABLED: lspInstalled ? "true" : "false",
      CRUCIBLE_LSP_RS_CMD: rsCmd,
      CRUCIBLE_LSP_GO_CMD: goCmd,
      // The indexer's own defaults (config.rs) are 3000ms for both — plenty
      // for an already-warm server answering a request, but too tight for a
      // cold *process spawn* + full initialize handshake (gopls in
      // particular: found live — a fresh gopls process on an end user's
      // first-ever workspace open timed out at the bare 3s default and fell
      // back to unresolved placeholders). start-backend.sh's dev flow
      // already overrides these to exactly these values; the managed
      // runtime path never did, so every non-dev install was exposed to the
      // 3s default. Mirroring start-backend.sh's values here closes that gap.
      CRUCIBLE_LSP_STARTUP_TIMEOUT_MS: "180000",
      CRUCIBLE_LSP_REQUEST_TIMEOUT_MS: "20000",
      ...(javaCmd ? { CRUCIBLE_LSP_JAVA_CMD: javaCmd } : {}),
      ...(lspInstalled
        ? {
            CRUCIBLE_LSP_PY_CMD: `${lspBin("pyright-langserver")} --stdio`,
            CRUCIBLE_LSP_TS_CMD: `${lspBin("typescript-language-server")} --stdio`,
          }
        : {}),
    });
    this.watcher = this.deps.spawn(indexer, [
      "index",
      "--workspace", workspace,
      "--snapshot-path", join(workspace, ".crucible", "index-snapshot.json"),
      "--watch", "true",
    ], { env, cwd: workspace });
  }

  /// Builds the managed CRUCIBLE_LSP_JAVA_CMD when both the bundled JRE and
  /// jdtls landed (installer.ts's "jre"/"jdtls" components); returns null
  /// otherwise so the caller falls back to a bare `jdtls` PATH lookup —
  /// same graceful-degradation pattern as rust-analyzer/gopls, except here
  /// the fallback is "nothing managed", not "one binary missing".
  private buildJavaLspCommand(workspace: string): string | null {
    const jreDir = join(this.deps.runtimeDir, "jre");
    const jdtlsDir = join(this.deps.runtimeDir, "jdtls");
    const javaExecutable = findJavaExecutable(jreDir, this.platform);
    const launcherJar = findEquinoxLauncher(jdtlsDir);
    const configDir = configDirForPlatform(jdtlsDir, this.platform);
    if (!javaExecutable || !launcherJar || !configDir) return null;

    return buildJdtlsCommand({
      javaExecutable,
      launcherJar,
      configDir,
      dataDir: jdtlsDataDir(this.deps.runtimeDir, workspace),
    });
  }

  async stop(): Promise<void> {
    // Watcher first so it doesn't observe the backend vanishing mid-write.
    try { this.watcher?.kill(); } catch { /* already dead */ }
    try { this.backend?.kill("SIGTERM"); } catch { /* already dead */ }
    this.watcher = undefined;
    this.backend = undefined;
    this._port = undefined;
  }

  private probeDeps(): ProbeDeps {
    return { fetchRaw: (url, init) => this.deps.fetchRaw(url, init),
             readToken: (port) => this.deps.readToken(port) };
  }

  private waitForHandshake(child: ChildHandle): Promise<{ pid: number; port: number }> {
    return new Promise((resolve, reject) => {
      let settled = false;
      const settle = (fn: () => void) => { if (!settled) { settled = true; fn(); } };
      child.onStdoutLine((line) => {
        const hs = parseHandshake(line);
        if (hs) settle(() => resolve(hs));
      });
      void child.exited.then((code) => settle(() => reject(new Error(
        `backend exited (code=${code}) before it started — see the Crucible output`))));
      void this.deps.sleep(HANDSHAKE_TIMEOUT_MS).then(() => settle(() => reject(new Error(
        "backend printed no CRUCIBLE_SERVE line within 10s — see the Crucible output"))));
    });
  }
}
