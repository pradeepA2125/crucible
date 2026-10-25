import { ownerFor,parseProjectSession } from "./project-owner.js";
// Shared runtime orchestration. State, secrets, configuration and presentation
// are host ports; installer/process behavior is the same in both applications.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { extractArchive } from "./archive.js";
import { BackendProcess, RuntimeUpdateRequiredError, type BackendSettings, type ProcessDeps } from "./backend-process.js";
import { isEditableInstall, RuntimeInstaller, type ComponentProgress, type InstallerDeps, type InstallResult } from "./installer.js";
import { platformKey, type RuntimeManifest } from "./manifest.js";
import { createNodeProcessDeps, execCollecting } from "./node-runtime-deps.js";
import { PROVIDER_KEY_ENV } from "../provider-credentials.js";

export interface RuntimeState {
  get<T>(key: string): T | undefined;
  get<T>(key: string, fallback: T): T;
  update(key: string, value: unknown): PromiseLike<void>;
}
export interface RuntimeSecrets {
  get(key: string): PromiseLike<string | undefined>;
  store(key: string, value: string): PromiseLike<void>;
  delete(key: string): PromiseLike<void>;
}
export interface RuntimeConfiguration {
  get<T>(key: string): T | undefined;
  get<T>(key: string, fallback: T): T;
  inspect<T = unknown>(key: string): {
    globalValue?: T | undefined; workspaceValue?: T | undefined; workspaceFolderValue?: T | undefined;
  } | undefined;
}
export interface RuntimeStatus { text: string; tooltip?: unknown; show(): void; hide(): void; }
export interface RuntimeOutput { append(text: string): void; appendLine(text: string): void; show(): void; }
export type ManagedProcess = Pick<BackendProcess, "start" | "stop" | "backendHandle">;
export interface RuntimePorts {
  runtimeDir: string;
  resourceDir: string;
  clientName?: string;
  isTrusted?(): boolean;
  state: RuntimeState;
  secrets: RuntimeSecrets;
  configuration(): RuntimeConfiguration;
  updateGlobalSetting(key: string, value: unknown): PromiseLike<void>;
  output: RuntimeOutput;
  statusBar: RuntimeStatus;
  authStatus: RuntimeStatus;
  showError(message: string, modal: boolean, actions?: string[]): Promise<string | undefined>;
  now?: () => number;
  schedule?: (callback: () => void, delayMs: number) => unknown;
  createProcess?: (deps: ProcessDeps) => ManagedProcess;
  createInstaller?: (deps: InstallerDeps) => Pick<RuntimeInstaller, "installAll">;
}

// Host setting keys that become spawn env (only when the user explicitly set them —
// otherwise buildBackendEnv's defaults stand).
const SETTING_ENV_MAP: Record<string, string> = {
  "crucible.policy.shell": "CRUCIBLE_SHELL_POLICY",
  "crucible.policy.scope": "CRUCIBLE_SCOPE_POLICY",
  "crucible.performance.toolBatching": "CRUCIBLE_TOOL_BATCHING_ENABLED",
  "crucible.performance.adaptiveReads": "CRUCIBLE_ADAPTIVE_READS_ENABLED",
  "crucible.performance.compactContext": "CRUCIBLE_COMPACT_CONTEXT_ENABLED",
  "crucible.performance.mcpOnDemand": "CRUCIBLE_CAPABILITY_LOADING_ENABLED",
  "crucible.webSearch.enabled": "CRUCIBLE_WEB_SEARCH_ENABLED",
  "crucible.webFetch.enabled": "CRUCIBLE_WEB_FETCH_ENABLED",
  "crucible.memory.enabled": "CRUCIBLE_MEMORY_ENABLED",
  "crucible.memory.experimental.recall": "CRUCIBLE_MEMORY_RECALL_V2",
  "crucible.memory.experimental.curator": "CRUCIBLE_MEMORY_CURATOR_V2",
  "crucible.memory.experimental.compaction": "CRUCIBLE_MEMORY_COMPACT_EVIDENCE",
  "crucible.localModels": "CRUCIBLE_LOCAL_MODELS",
};

const MAX_RESTART_ATTEMPTS = 3;
const RESTART_RESET_MS = 5 * 60_000;

export class RuntimeCore {
  readonly runtimeDir: string;

  private readonly statusBar: RuntimeStatus;
  private readonly authStatus: RuntimeStatus;
  private readonly processes = new Map<string, ManagedProcess>();
  private readonly ports = new Map<string, number>();
  private readonly restartAttempts = new Map<string, number>();
  private readonly lastStartedAt = new Map<string, number>();
  private readonly intentionalStops = new Set<string>();
  private disposed = false;
  private readonly output: RuntimeOutput;
  private readonly readyListeners: Array<(workspace: string) => void> = [];

  constructor(private readonly host: RuntimePorts) {
    this.runtimeDir = host.runtimeDir;
    this.statusBar = host.statusBar;
    this.authStatus = host.authStatus;
    this.output = host.output;
  }

  /** Spec §3.8: one status item for auth failures, shared by the managed and the
   * explicit-URL flows. Cleared by the next ok report. */
  setAuthError(reason: string | null): void {
    if (reason === null) {
      this.authStatus.hide();
      return;
    }
    this.authStatus.tooltip = reason;
    this.authStatus.show();
  }

  // Fires every time a backend for a workspace becomes healthy — the initial managed
  // start, a crash-triggered auto-respawn (watchCrash), and an explicit restart() all
  // route through startForWorkspace, so one registration covers all three. Callers use
  // this to re-fetch /v1/config and refresh capability when-contexts (memoryEnabled etc)
  // instead of reading them once at activation, which goes stale across a crash/restart.
  private now(): number { return (this.host.now ?? Date.now)(); }

  onBackendReady(listener: (workspace: string) => void): void {
    this.readyListeners.push(listener);
  }

  isInstalled(): boolean {
    return existsSync(join(this.runtimeDir, "runtime.json"));
  }

  bundledManifest(): RuntimeManifest | null {
    try {
      const path = join(
        this.host.resourceDir, "resources", "runtime-manifest.json");
      return JSON.parse(readFileSync(path, "utf8")) as RuntimeManifest;
    } catch {
      return null;
    }
  }

  installedRuntime(): {
    releaseTag: string; components: Record<string, string>; localModels?: boolean;
  } | null {
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
      localModels: this.localModels(),
      download: async (url) => {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`download failed (${res.status}) for ${url}`);
        return Buffer.from(await res.arrayBuffer());
      },
      exec: (cmd, args, opts) => execCollecting(cmd, args, opts?.cwd),
      hasNode: async () => (await execCollecting("node", ["--version"])).code === 0,
      extract: extractArchive,
      // Development escape hatch: an editable agentd install from a local checkout.
      // Empty string (the default) means "unset" — a falsy value must not be passed
      // through, or every normal install would try to `pip install -e ""`.
      ...((): { devSourcePath?: string } => {
        const p = this.host.configuration()
          .get<string>("crucible.devSourcePath", "").trim();
        if (p) this.output.appendLine(`[install] dev source: editable agentd from ${p}`);
        return p ? { devSourcePath: p } : {};
      })(),
    };
    return (this.host.createInstaller ?? (options => new RuntimeInstaller(options)))(deps).installAll((p) => {
      this.output.appendLine(`[install] ${p.id}: ${p.status}${p.detail ? ` — ${p.detail}` : ""}`);
      onProgress(p);
    });
  }

  async getProviderSettings(): Promise<BackendSettings | undefined> {
    const backend = this.host.state.get<string>("crucible.provider.backend");
    const model = this.host.state.get<string>("crucible.provider.model");
    // A ChatGPT plan backend can start before a model is picked: the model list comes
    // from the account, which needs a running backend to sign in with.
    if (!backend || (!model && backend !== "chatgpt")) return undefined;
    const settings: BackendSettings = {
      backend,
      model: model ?? "",
      extraEnv: this.extraEnvFromSettings(),
      skillsDisabled: this.skillsDisabled(),
      localModels: this.localModels(),
    };
    const contextWindow = this.contextWindow();
    if (contextWindow !== undefined) settings.contextWindow = contextWindow;
    const reasoningEffort = this.getReasoningEffort();
    if (reasoningEffort !== undefined) settings.reasoningEffort = reasoningEffort;
    const envVar = PROVIDER_KEY_ENV[backend];
    if (envVar) {
      const value = await this.host.secrets.get(`crucible.providerKey.${backend}`);
      if (value) settings.apiKey = { envVar, value };
    }
    // Which ChatGPT sign-in the backend uses. An id, not a secret: the tokens live in
    // the backend's own credential store and never enter any environment.
    const registration = this.chatgptRegistration();
    if (backend === "chatgpt" && registration) {
      settings.extraEnv = { ...settings.extraEnv, CRUCIBLE_CHATGPT_REGISTRATION: registration };
    }
    const claude = this.claudeRegistration();
    if (backend === "claude_subscription" && claude) {
      settings.extraEnv = { ...settings.extraEnv, CRUCIBLE_CLAUDE_REGISTRATION: claude };
    }
    // Restore extra credentials (e.g. WATSONX_SPACE_ID, WATSONX_URL) into extraEnv.
    const extraEnvVars = this.host.state.get<string[]>(`crucible.provider.extraEnvVars.${backend}`, []);
    for (const ev of extraEnvVars) {
      const value = await this.host.secrets.get(`crucible.providerExtra.${backend}.${ev}`);
      if (value) settings.extraEnv = { ...settings.extraEnv, [ev]: value };
    }
    return settings;
  }

  async saveProvider(backend: string, model: string, apiKey?: string, extraCredentials?: Record<string, string>): Promise<void> {
    await this.host.state.update("crucible.provider.backend", backend);
    await this.host.state.update("crucible.provider.model", model);
    if (apiKey) {
      await this.storeProviderKey(backend, apiKey);
    }
    if (extraCredentials) {
      await this._persistExtraCredentials(backend, extraCredentials);
    }
  }

  claudeRegistration(): string | undefined {
    return this.host.state.get<string>("crucible.claude.registration");
  }

  async saveClaudeRegistration(id: string): Promise<void> {
    await this.host.state.update("crucible.claude.registration", id);
  }

  chatgptRegistration(): string | undefined {
    return this.host.state.get<string>("crucible.chatgpt.registration");
  }

  async saveChatGPTRegistration(registrationId: string): Promise<void> {
    await this.host.state.update("crucible.chatgpt.registration", registrationId);
  }

  /** Registrations already shown the one-time "You're using your ChatGPT plan" modal. */
  chatgptWelcomed(): string[] {
    return this.host.state.get<string[]>("crucible.chatgpt.welcomed", []);
  }

  async markChatGPTWelcomed(registrationId: string): Promise<void> {
    const seen = new Set(this.chatgptWelcomed());
    seen.add(registrationId);
    await this.host.state.update("crucible.chatgpt.welcomed", Array.from(seen));
  }

  /** The declared context window, in tokens. Stored under its own key rather than
   * inside the provider record so a model-only hot-swap (composer model menu)
   * cannot clear it. Read back into the spawn env by getProviderSettings. */
  contextWindow(): number | undefined {
    return this.host.state.get<number>("crucible.provider.contextWindow");
  }

  async saveContextWindow(tokens: number): Promise<void> {
    await this.host.state.update("crucible.provider.contextWindow", tokens);
  }

  /** The reasoning-effort rung, stored under its own key (mirrors contextWindow)
   * so a model-only hot-swap cannot clear it. Always the EFFECTIVE rung the
   * backend last reported — see the setReasoningEffort host handler in
   * extension.ts for why the requested rung is never what's persisted here.
   * Read back into the spawn env by getProviderSettings. */
  getReasoningEffort(): string | undefined {
    return this.host.state.get<string>("crucible.reasoningEffort");
  }

  async saveReasoningEffort(level: string | undefined): Promise<void> {
    await this.host.state.update("crucible.reasoningEffort", level);
  }

  /** Secret-only write (settings panel's hot-swap path stores the key separately
   * from the backend/model prefs — see SettingsPanel.buildDeps' setProvider wrapper). */
  async storeProviderKey(backend: string, key: string): Promise<void> {
    await this.host.secrets.store(`crucible.providerKey.${backend}`, key);
  }

  /** Delete a backend's stored API key (settings panel's "Clear key"). Uses the same
   * SecretStorage delete as _persistExtraCredentials' stale-secret pruning. Deleting a
   * key that was never stored is a no-op, so the caller needs no existence check. */
  async deleteProviderKey(backend: string): Promise<void> {
    await this.host.secrets.delete(`crucible.providerKey.${backend}`);
  }

  /** Store extra credentials (e.g. WATSONX_SPACE_ID, WATSONX_URL) without touching backend/model. */
  async storeProviderExtraCredentials(backend: string, extraCredentials: Record<string, string>): Promise<void> {
    await this._persistExtraCredentials(backend, extraCredentials);
  }

  /** Env-var names whose values are already in SecretStorage for this backend.
   * The setup wizard's required-field guard needs it to tell "the user left this
   * blank because it is already stored" from "this was never supplied". */
  storedExtraEnvVars(backend: string): string[] {
    return this.host.state.get<string[]>(`crucible.provider.extraEnvVars.${backend}`, []);
  }

  /** Write the extra-credential secrets and record their env-var names. Any secret
   * from a previous save whose env var is no longer present is deleted, so a removed
   * field (or a provider whose extra fields shrank) never leaves an orphaned secret. */
  private async _persistExtraCredentials(
    backend: string,
    extraCredentials: Record<string, string>,
  ): Promise<void> {
    const key = `crucible.provider.extraEnvVars.${backend}`;
    const prevEnvVars = this.host.state.get<string[]>(key, []);
    const nextEnvVars = Object.keys(extraCredentials);
    for (const stale of prevEnvVars.filter((ev) => !nextEnvVars.includes(ev))) {
      await this.host.secrets.delete(`crucible.providerExtra.${backend}.${stale}`);
    }
    await this.host.state.update(key, nextEnvVars);
    for (const [ev, value] of Object.entries(extraCredentials)) {
      await this.host.secrets.store(`crucible.providerExtra.${backend}.${ev}`, value);
    }
  }

  /** Stored API key for a backend, if the user ever validated one (composer model menu). */
  async getProviderKey(backend: string): Promise<string | undefined> {
    return (await this.host.secrets.get(`crucible.providerKey.${backend}`)) ?? undefined;
  }

  private extraEnvFromSettings(): Record<string, string> {
    const cfg = this.host.configuration();
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
    const storePath = cfg.get<string>("crucible.memory.experimental.storePath", "").trim();
    if (storePath) out.CRUCIBLE_MEMORY_STORE_PATH = storePath;
    return out;
  }

  async syncProjectTrust(workspace:string,trusted:boolean):Promise<void>{
    const base=this.backendUrl(workspace);if(!base || !ownerFor(base))return;
    const deps=createNodeProcessDeps(this.runtimeDir,this.output);
    const session=parseProjectSession(await deps.fetchJson(`${base}/v1/project/session`));
    await deps.fetchJson(`${base}/v1/project/trust`,{method:"PUT",body:JSON.stringify({process_id:session.process_id,trusted})});
  }

  async startForWorkspace(workspace: string): Promise<{ port: number; reused: boolean }> {
    if(this.disposed)throw new Error("Runtime has been disposed.");
    await this.reconcileLocalModelPackages(workspace);
    const settings = await this.getProviderSettings();
    if (!settings) {
      throw new Error("No provider configured — run \"Crucible: Run Setup\" first.");
    }
    if(this.disposed)throw new Error("Runtime has been disposed.");
    settings.clientName=this.host.clientName ?? "VS Code";
    this.statusBar.text = "$(rocket) Crucible: starting…";
    this.statusBar.show();
    const proc = this.processes.get(workspace) ?? (this.host.createProcess ?? (deps => new BackendProcess(deps)))(createNodeProcessDeps(this.runtimeDir, this.output));
    this.processes.set(workspace, proc);
    try {
      const result = await proc.start(workspace, settings);
      if(this.disposed){await proc.stop();throw new Error("Runtime was closed during startup.");}
      this.ports.set(workspace, result.port);
      this.lastStartedAt.set(workspace, this.now());
      this.statusBar.text = result.reused && !proc.backendHandle ? `$(eye) Crucible observer :${result.port}` : `$(check) Crucible owner :${result.port}`;
      if(this.host.isTrusted && ownerFor(`http://127.0.0.1:${result.port}`))await this.syncProjectTrust(workspace,this.host.isTrusted());
      this.watchCrash(workspace, proc);
      for (const listener of this.readyListeners) listener(workspace);
      return result;
    } catch (err) {
      if (err instanceof RuntimeUpdateRequiredError) {
        this.statusBar.text = "$(error) Crucible: runtime update required";
        this.statusBar.show();
        void this.promptRuntimeUpdate(workspace, err);
      } else {
        this.markFailed(err instanceof Error ? err.message : String(err));
      }
      throw err;
    }
  }

  private updatePromptOpen = false;

  private async promptRuntimeUpdate(workspace: string, err: RuntimeUpdateRequiredError): Promise<void> {
    if (this.updatePromptOpen) return; // one modal, however many start paths fail
    this.updatePromptOpen = true;
    try {
      if (isEditableInstall(this.runtimeDir, platformKey())) {
        await this.host.showError(
          `${err.message}. This is an editable development install: run scripts/dev/install-local.sh, then restart the backend.`,
          true);
        return;
      }
      const choice = await this.host.showError(
        `${err.message}.`, true, ["Update runtime"]);
      if (choice !== "Update runtime") return;
      this.intentionalStops.add(workspace);
      try {
        await this.processes.get(workspace)?.stop();
      } finally {
        this.intentionalStops.delete(workspace);
      }
      await this.install((p) => this.output.appendLine(`[install] ${p.id}: ${p.status}`));
      this.restartAttempts.delete(workspace);
      await this.startForWorkspace(workspace);
    } finally {
      this.updatePromptOpen = false;
    }
  }

  private watchCrash(workspace: string, proc: ManagedProcess): void {
    const handle = proc.backendHandle;
    if (!handle) return; // reused backend — not our child, nothing to watch
    handle.onExit((code) => {
      if (this.disposed || this.intentionalStops.has(workspace)) return;
      const healthyForMs = this.now() - (this.lastStartedAt.get(workspace) ?? 0);
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
      (this.host.schedule ?? setTimeout)(() => {
        if (this.disposed || this.intentionalStops.has(workspace) || this.processes.get(workspace)!==proc) return;
        void this.startForWorkspace(workspace).catch(() => {
          /* markFailed already ran inside startForWorkspace */
        });
      }, delayMs);
    });
  }

  private markFailed(detail: string): void {
    this.statusBar.text = "$(error) Crucible failed";
    this.statusBar.show();
    void this.host.showError(`Crucible backend failed: ${detail}`, false, ["Open logs"])
      .then((choice: string | undefined) => {
        if (choice === "Open logs") this.output.show();
      });
  }

  private localModelInstall: Promise<void> | undefined;

  private async reconcileLocalModelPackages(workspace: string): Promise<void> {
    if (this.localModelInstall) { await this.localModelInstall; return; }
    if ((this.installedRuntime()?.localModels ?? true) === this.localModels()) return;
    const workspaces = [...this.processes.keys()];
    this.localModelInstall = (async () => {
      for (const active of workspaces) this.intentionalStops.add(active);
      try {
        await Promise.all(workspaces.map(active => this.processes.get(active)?.stop()));
        const result = await this.install(p => this.output.appendLine(
          `[local models] ${p.id}: ${p.status}`));
        if (!result.ok) throw new Error("Local-model installation failed. Open runtime logs.");
      } finally {
        for (const active of workspaces) this.intentionalStops.delete(active);
      }
    })();
    try { await this.localModelInstall; } finally { this.localModelInstall = undefined; }
    for (const active of workspaces.filter(active => active !== workspace)) {
      this.restartAttempts.delete(active);
      await this.startForWorkspace(active);
    }
  }

  async restart(workspace: string): Promise<void> {
    await this.reconcileLocalModelPackages(workspace);
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
    return port ? `http://127.0.0.1:${port}` : undefined;
  }

  localModels(): boolean {
    const explicit = this.host.configuration().inspect<boolean>("crucible.localModels");
    return explicit?.globalValue
      ?? this.host.state.get<boolean>("crucible.localModels", true);
  }

  async setLocalModels(enabled: boolean): Promise<void> {
    await this.host.state.update("crucible.localModels", enabled);
    await this.host.updateGlobalSetting("crucible.localModels", enabled);
  }

  mcpDisabled(): string[] {
    return this.host.state.get<string[]>("crucible.mcpDisabledServers", []);
  }

  setMcpDisabled(names: string[]): Promise<void> {
    return Promise.resolve(
      this.host.state.update("crucible.mcpDisabledServers", names));
  }

  skillsDisabled(): string[] {
    return this.host.state.get<string[]>("crucible.skillsDisabled", []);
  }

  setSkillsDisabled(names: string[]): Promise<void> {
    return Promise.resolve(
      this.host.state.update("crucible.skillsDisabled", names));
  }

  /** "Review each step" — persisted for the same reason as planMode: it was local
   *  webview state defaulting to true, so every remount silently re-checked it and
   *  the next message gated its edits again. Default true (review on). */
  getStepReview(): boolean {
    return this.host.state.get<boolean>("crucible.chat.stepReview", true);
  }

  async setStepReview(enabled: boolean): Promise<void> {
    await this.host.state.update("crucible.chat.stepReview", enabled);
  }

  getPlanMode(): boolean {
    return this.host.state.get<boolean>("crucible.chat.planMode", false);
  }

  async setPlanMode(enabled: boolean): Promise<void> {
    await this.host.state.update("crucible.chat.planMode", enabled);
  }

  /** Pause only our child; borrowed observer runtimes are never signalled. */
  async pause(workspace: string): Promise<boolean> {
    const proc = this.processes.get(workspace);
    if (!proc?.backendHandle) return false;
    this.intentionalStops.add(workspace);
    try { await proc.stop(); this.ports.delete(workspace); this.processes.delete(workspace); return true; }
    finally { this.intentionalStops.delete(workspace); }
  }

  async dispose(): Promise<void> {
    this.disposed = true;
    const results = await Promise.allSettled([...this.processes].map(async ([workspace, proc]) => {
      this.intentionalStops.add(workspace);
      await proc.stop();
      this.processes.delete(workspace);
      this.ports.delete(workspace);
    }));
    const errors = results.flatMap(result => result.status === "rejected" ? [result.reason] : []);
    // Retain failed handles for an explicit retry; never report a live child gone.
    if (errors.length) throw new AggregateError(errors, "Some owned runtimes could not be stopped. Retry quitting.");
    this.ports.clear();
  }

}
