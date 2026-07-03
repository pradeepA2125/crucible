// Thin vscode layer over the vscode-free runtime core (installer + backend-process).
// Owns: install root, SecretStorage/globalState config, per-workspace BackendProcess
// instances, the status bar item, and crash backoff. Everything unit-testable lives
// in installer.ts / backend-process.ts — this file is wiring.
import { execFile, spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createServer } from "node:net";
import { homedir } from "node:os";
import { join } from "node:path";
import * as vscode from "vscode";

import {
  BackendProcess,
  type BackendSettings,
  type ProcessDeps,
} from "./backend-process.js";
import {
  RuntimeInstaller,
  type ComponentProgress,
  type ExecResult,
  type InstallerDeps,
  type InstallResult,
} from "./installer.js";
import type { RuntimeManifest } from "./manifest.js";

// Same table as agentd/providers/factory.py::PROVIDER_KEY_ENV (local providers absent).
export const PROVIDER_KEY_ENV: Record<string, string> = {
  openai: "OPENAI_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  gemini: "GEMINI_API_KEY",
  groq: "GROQ_API_KEY",
  openrouter: "OPENROUTER_API_KEY",
  watsonx: "WATSONX_API_KEY",
  huggingface: "HF_TOKEN",
};

// VS Code settings that become spawn env (only when the user explicitly set them —
// otherwise buildBackendEnv's defaults stand).
const SETTING_ENV_MAP: Record<string, string> = {
  "aiEditor.policy.shell": "AI_EDITOR_SHELL_POLICY",
  "aiEditor.policy.scope": "AI_EDITOR_SCOPE_POLICY",
  "aiEditor.memory.enabled": "AI_EDITOR_MEMORY_ENABLED",
  "aiEditor.memory.reranker": "AI_EDITOR_MEMORY_RERANKER",
};

const MAX_RESTART_ATTEMPTS = 3;
const RESTART_RESET_MS = 5 * 60_000;

function pickFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => (port ? resolve(port) : reject(new Error("no free port"))));
    });
  });
}

function execCollecting(cmd: string, args: string[], cwd?: string): Promise<ExecResult> {
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

export class RuntimeManager {
  readonly runtimeDir: string;

  private readonly statusBar: vscode.StatusBarItem;
  private readonly processes = new Map<string, BackendProcess>();
  private readonly ports = new Map<string, number>();
  private readonly restartAttempts = new Map<string, number>();
  private readonly lastStartedAt = new Map<string, number>();
  private readonly intentionalStops = new Set<string>();
  private disposed = false;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {
    this.runtimeDir = join(homedir(), ".ai-editor", "runtime");
    this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    this.statusBar.command = "aiEditor.openSettingsPanel";
    context.subscriptions.push(this.statusBar);
  }

  isInstalled(): boolean {
    return existsSync(join(this.runtimeDir, "runtime.json"));
  }

  bundledManifest(): RuntimeManifest | null {
    try {
      const path = join(
        this.context.extensionUri.fsPath, "resources", "runtime-manifest.json");
      return JSON.parse(readFileSync(path, "utf8")) as RuntimeManifest;
    } catch {
      return null;
    }
  }

  installedRuntime(): { releaseTag: string; components: Record<string, string> } | null {
    try {
      return JSON.parse(readFileSync(join(this.runtimeDir, "runtime.json"), "utf8"));
    } catch {
      return null;
    }
  }

  async install(onProgress: (p: ComponentProgress) => void): Promise<InstallResult> {
    const manifest = this.bundledManifest();
    if (!manifest) {
      throw new Error("bundled runtime-manifest.json is missing or unreadable");
    }
    const deps: InstallerDeps = {
      runtimeDir: this.runtimeDir,
      manifest,
      download: async (url) => {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`download failed (${res.status}) for ${url}`);
        return Buffer.from(await res.arrayBuffer());
      },
      exec: (cmd, args, opts) => execCollecting(cmd, args, opts?.cwd),
      hasNode: async () => (await execCollecting("node", ["--version"])).code === 0,
    };
    return new RuntimeInstaller(deps).installAll((p) => {
      this.output.appendLine(`[install] ${p.id}: ${p.status}${p.detail ? ` — ${p.detail}` : ""}`);
      onProgress(p);
    });
  }

  async getProviderSettings(): Promise<BackendSettings | undefined> {
    const backend = this.context.globalState.get<string>("aiEditor.provider.backend");
    const model = this.context.globalState.get<string>("aiEditor.provider.model");
    if (!backend || !model) return undefined;
    const settings: BackendSettings = {
      backend,
      model,
      extraEnv: this.extraEnvFromSettings(),
      skillsDisabled: this.skillsDisabled(),
    };
    const envVar = PROVIDER_KEY_ENV[backend];
    if (envVar) {
      const value = await this.context.secrets.get(`aiEditor.providerKey.${backend}`);
      if (value) settings.apiKey = { envVar, value };
    }
    return settings;
  }

  async saveProvider(backend: string, model: string, apiKey?: string): Promise<void> {
    await this.context.globalState.update("aiEditor.provider.backend", backend);
    await this.context.globalState.update("aiEditor.provider.model", model);
    if (apiKey) {
      await this.context.secrets.store(`aiEditor.providerKey.${backend}`, apiKey);
    }
  }

  private extraEnvFromSettings(): Record<string, string> {
    const cfg = vscode.workspace.getConfiguration();
    const out: Record<string, string> = {};
    for (const [setting, envVar] of Object.entries(SETTING_ENV_MAP)) {
      const info = cfg.inspect(setting);
      const isUserSet =
        info !== undefined &&
        (info.globalValue !== undefined ||
          info.workspaceValue !== undefined ||
          info.workspaceFolderValue !== undefined);
      if (isUserSet) out[envVar] = String(cfg.get(setting));
    }
    return out;
  }

  async startForWorkspace(workspace: string): Promise<{ port: number; reused: boolean }> {
    const settings = await this.getProviderSettings();
    if (!settings) {
      throw new Error("No provider configured — run \"AI Editor: Run Setup\" first.");
    }
    this.statusBar.text = "$(rocket) AI Editor: starting…";
    this.statusBar.show();
    const proc = this.processes.get(workspace) ?? new BackendProcess(this.processDeps());
    this.processes.set(workspace, proc);
    try {
      const result = await proc.start(workspace, settings);
      this.ports.set(workspace, result.port);
      this.lastStartedAt.set(workspace, Date.now());
      this.statusBar.text = `$(check) AI Editor :${result.port}`;
      this.watchCrash(workspace, proc);
      return result;
    } catch (err) {
      this.markFailed(err instanceof Error ? err.message : String(err));
      throw err;
    }
  }

  private watchCrash(workspace: string, proc: BackendProcess): void {
    const handle = proc.backendHandle;
    if (!handle) return; // reused backend — not our child, nothing to watch
    handle.onExit((code) => {
      if (this.disposed || this.intentionalStops.has(workspace)) return;
      const healthyForMs = Date.now() - (this.lastStartedAt.get(workspace) ?? 0);
      const attempts = healthyForMs > RESTART_RESET_MS
        ? 0
        : this.restartAttempts.get(workspace) ?? 0;
      this.output.appendLine(
        `[runtime] backend exited unexpectedly (code=${code}); restart attempt ${attempts + 1}/${MAX_RESTART_ATTEMPTS}`);
      if (attempts >= MAX_RESTART_ATTEMPTS) {
        this.markFailed("backend keeps crashing");
        return;
      }
      this.restartAttempts.set(workspace, attempts + 1);
      const delayMs = 2000 * 2 ** attempts;
      setTimeout(() => {
        if (this.disposed) return;
        void this.startForWorkspace(workspace).catch(() => {
          /* markFailed already ran inside startForWorkspace */
        });
      }, delayMs);
    });
  }

  private markFailed(detail: string): void {
    this.statusBar.text = "$(error) AI Editor failed";
    this.statusBar.show();
    void vscode.window
      .showErrorMessage(`AI Editor backend failed: ${detail}`, "Open logs")
      .then((choice: string | undefined) => {
        if (choice === "Open logs") this.output.show();
      });
  }

  async restart(workspace: string): Promise<void> {
    const proc = this.processes.get(workspace);
    this.intentionalStops.add(workspace);
    try {
      await proc?.stop();
    } finally {
      this.intentionalStops.delete(workspace);
    }
    this.restartAttempts.delete(workspace);
    await this.startForWorkspace(workspace);
  }

  backendUrl(workspace: string): string | undefined {
    const port = this.ports.get(workspace);
    return port ? `http://localhost:${port}` : undefined;
  }

  mcpDisabled(): string[] {
    return this.context.globalState.get<string[]>("aiEditor.mcpDisabledServers", []);
  }

  setMcpDisabled(names: string[]): Promise<void> {
    return Promise.resolve(
      this.context.globalState.update("aiEditor.mcpDisabledServers", names));
  }

  skillsDisabled(): string[] {
    return this.context.globalState.get<string[]>("aiEditor.skillsDisabled", []);
  }

  setSkillsDisabled(names: string[]): Promise<void> {
    return Promise.resolve(
      this.context.globalState.update("aiEditor.skillsDisabled", names));
  }

  async dispose(): Promise<void> {
    this.disposed = true;
    for (const [workspace, proc] of this.processes) {
      this.intentionalStops.add(workspace);
      await proc.stop();
    }
    this.processes.clear();
    this.ports.clear();
  }

  private processDeps(): ProcessDeps {
    return {
      runtimeDir: this.runtimeDir,
      spawn: (cmd, args, opts) => {
        const child = spawn(cmd, args, {
          env: opts.env,
          stdio: ["ignore", "pipe", "pipe"],
        });
        child.stdout?.on("data", (chunk: Buffer) =>
          this.output.append(chunk.toString()));
        child.stderr?.on("data", (chunk: Buffer) =>
          this.output.append(chunk.toString()));
        return {
          pid: child.pid ?? -1,
          kill: () => child.kill(),
          onExit: (cb) => child.on("exit", (code) => cb(code)),
        };
      },
      fetchJson: async (url, init) => {
        const res = await fetch(url, {
          ...(init?.method ? { method: init.method } : {}),
          ...(init?.body
            ? { body: init.body, headers: { "content-type": "application/json" } }
            : {}),
        });
        if (!res.ok) throw new Error(`request failed (${res.status}) for ${url}`);
        return res.json();
      },
      pickPort: pickFreePort,
      sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      isPidAlive: (pid) => {
        try {
          process.kill(pid, 0);
          return true;
        } catch (err) {
          return (err as NodeJS.ErrnoException).code === "EPERM";
        }
      },
      log: (line) => this.output.appendLine(line),
    };
  }
}
