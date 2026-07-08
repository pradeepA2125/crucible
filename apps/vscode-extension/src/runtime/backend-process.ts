// vscode-free. One instance per workspace folder; owns agentd + watcher children.
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { binPath, venvPython } from "./installer.js";
import { platformKey, type PlatformKey } from "./manifest.js";

export interface BackendSettings {
  backend: string;                     // "gemini" | "openai" | ... (never "scripted")
  model: string;
  apiKey?: { envVar: string; value: string };   // from SecretStorage, spawn-env only
  extraEnv?: Record<string, string>;   // policies/flags from VS Code settings
  skillsDisabled?: string[];           // → CRUCIBLE_SKILLS_DISABLED (comma-joined)
}
export interface ChildHandle {
  pid: number;
  kill(): void;
  onExit(cb: (code: number | null) => void): void;
}
export interface ProcessDeps {
  runtimeDir: string;
  spawn(cmd: string, args: string[], opts: { env: Record<string, string> }): ChildHandle;
  fetchJson(url: string, init?: { method?: string; body?: string }): Promise<unknown>; // throws on non-2xx
  pickPort(): Promise<number>;
  sleep(ms: number): Promise<void>;
  isPidAlive(pid: number): boolean;
  log(line: string): void;
  platform?: PlatformKey;
}

// Same table as agentd/providers/factory.py::MODEL_ENV_VAR.
export const MODEL_ENV_VAR: Record<string, string> = {
  anthropic: "CRUCIBLE_ANTHROPIC_MODEL", gemini: "CRUCIBLE_GEMINI_MODEL",
  huggingface: "CRUCIBLE_HUGGINGFACE_MODEL", groq: "CRUCIBLE_GROQ_MODEL",
  openrouter: "CRUCIBLE_OPENROUTER_MODEL", watsonx: "CRUCIBLE_WATSONX_MODEL",
  ollama: "CRUCIBLE_OLLAMA_MODEL", turboquant: "CRUCIBLE_TURBOQUANT_MODEL",
  openai: "CRUCIBLE_OPENAI_MODEL",
};

const HEALTH_ATTEMPTS = 60;
const INDEX_WARM_ATTEMPTS = 120;

export function buildBackendEnv(
  workspace: string, settings: BackendSettings, runtimeDir: string, port: number,
  platform: PlatformKey = platformKey(),
): Record<string, string> {
  const agentdDir = join(workspace, ".agentd");
  const built: Record<string, string> = {
    CRUCIBLE_REASONING_BACKEND: settings.backend,
    CRUCIBLE_WORKSPACE_PATH: workspace,
    CRUCIBLE_PORT: String(port),
    CRUCIBLE_DB_PATH: join(agentdDir, "agentd.sqlite3"),
    CRUCIBLE_CHAT_DB_PATH: join(agentdDir, "chat.sqlite3"),
    CRUCIBLE_SHADOW_ROOT: join(agentdDir, "shadows"),
    CRUCIBLE_LOG_FILE: join(agentdDir, "agentd.log"),
    CRUCIBLE_ARTIFACTS_ROOT: join(agentdDir, "artifacts"),
    CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH: join(workspace, ".ai-editor", "index-snapshot.json"),
    CRUCIBLE_RIPGREP_CMD: binPath(runtimeDir, "rg", platform),
    CRUCIBLE_CHAT_CONTROLLER: "1",
    CRUCIBLE_SKILLS_ENABLED: "1",
    CRUCIBLE_MCP_ENABLED: "1",
    CRUCIBLE_DOC_WRITE_ENABLED: "1",
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
  return { ...built, ...settings.extraEnv };
}

interface LockInfo { pid: number; port: number; started_at: number }

function readLock(workspace: string): LockInfo | null {
  try {
    const raw = JSON.parse(
      readFileSync(join(workspace, ".agentd", "agentd.lock"), "utf8"));
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
    if (lock && this.deps.isPidAlive(lock.pid) && await this.healthy(lock.port)) {
      this._port = lock.port;
      this.deps.log(`[runtime] reusing live backend pid=${lock.pid} port=${lock.port}`);
      return { port: lock.port, reused: true };
    }
    if (lock) {
      try { unlinkSync(join(workspace, ".agentd", "agentd.lock")); } catch { /* gone already */ }
      this.deps.log(`[runtime] reaped stale lock (pid=${lock.pid})`);
    }

    // 2. Spawn agentd from the managed venv (no --reload — that's dev-script only).
    const port = await this.deps.pickPort();
    const env = {
      ...process.env,
      ...buildBackendEnv(workspace, settings, this.deps.runtimeDir, port, this.platform),
    } as Record<string, string>;
    this.backend = this.deps.spawn(
      venvPython(this.deps.runtimeDir, this.platform),
      ["-m", "uvicorn", "agentd.main:app", "--port", String(port)],
      { env },
    );
    this._port = port;

    // 3. Health poll.
    let up = false;
    for (let i = 0; i < HEALTH_ATTEMPTS; i++) {
      if (await this.healthy(port)) { up = true; break; }
      await this.deps.sleep(1000);
    }
    if (!up) {
      await this.stop();
      throw new Error("backend did not become healthy within 60s — see logs");
    }

    // 4. Pre-warm the index (non-fatal — the watcher keeps it fresh anyway).
    try {
      await this.deps.fetchJson(`http://localhost:${port}/v1/index/build`, {
        method: "POST",
        body: JSON.stringify({ workspace_path: workspace }),
      });
      for (let i = 0; i < INDEX_WARM_ATTEMPTS; i++) {
        const status = await this.deps.fetchJson(
          `http://localhost:${port}/v1/index/status`) as { building?: boolean };
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
    // Managed install lands at rustAnalyzerBin (installer.ts); fall back to a bare
    // PATH lookup for a dev backend running outside the managed runtime — the
    // indexer degrades gracefully either way if it's still not found.
    const rsCmd = existsSync(rustAnalyzerBin) ? rustAnalyzerBin : "rust-analyzer";
    const env = {
      ...process.env,
      CRUCIBLE_BACKEND_URL: `http://localhost:${port}`,
      CRUCIBLE_LSP_ENABLED: lspInstalled ? "true" : "false",
      CRUCIBLE_LSP_RS_CMD: rsCmd,
      ...(lspInstalled
        ? {
            CRUCIBLE_LSP_PY_CMD: `${lspBin("pyright-langserver")} --stdio`,
            CRUCIBLE_LSP_TS_CMD: `${lspBin("typescript-language-server")} --stdio`,
          }
        : {}),
    } as Record<string, string>;
    this.watcher = this.deps.spawn(indexer, [
      "index",
      "--workspace", workspace,
      "--snapshot-path", join(workspace, ".ai-editor", "index-snapshot.json"),
      "--watch", "true",
    ], { env });
  }

  async stop(): Promise<void> {
    // Watcher first so it doesn't observe the backend vanishing mid-write.
    try { this.watcher?.kill(); } catch { /* already dead */ }
    try { this.backend?.kill(); } catch { /* already dead */ }
    this.watcher = undefined;
    this.backend = undefined;
    this._port = undefined;
  }

  private async healthy(port: number): Promise<boolean> {
    try {
      await this.deps.fetchJson(`http://localhost:${port}/health`);
      return true;
    } catch {
      return false;
    }
  }
}
