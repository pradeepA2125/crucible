import * as vscode from "vscode";
import { describe, expect, it, vi } from "vitest";
import { RuntimeManager } from "../src/runtime/vscode-runtime.js";
import { buildBackendEnv } from "../src/runtime/backend-process.js";

const native = vi.hoisted(() => ({ configuration: { get: vi.fn(), inspect: vi.fn(), update: vi.fn(async () => {}) }, statuses: [] as { text: string; command?: string; tooltip?: unknown; show: ReturnType<typeof vi.fn>; hide: ReturnType<typeof vi.fn> }[] }));
vi.mock("vscode", () => ({
  workspace: { getConfiguration: () => native.configuration },
  window: { createStatusBarItem: () => { const status = { text: "", show: vi.fn(), hide: vi.fn() }; native.statuses.push(status); return status; }, showErrorMessage: vi.fn(async () => undefined) },
  ExtensionMode: { Production: 1, Development: 2, Test: 3 },
  StatusBarAlignment: { Left: 1 }, ConfigurationTarget: { Global: 1 },
}));
vi.mock("../src/runtime/legacy-migration.js", () => ({ migrateLegacyRuntimeRoot: () => "/runtime-fixture" }));

function setup() {
  native.statuses.length = 0; native.configuration.update.mockClear();
  const preferences = new Map<string, unknown>([
    ["crucible.provider.backend", "watsonx"], ["crucible.provider.model", "model"],
    ["crucible.provider.contextWindow", 200000], ["crucible.reasoningEffort", "high"],
    ["crucible.provider.extraEnvVars.watsonx", ["WATSONX_URL"]],
    ["crucible.skillsDisabled", ["disabled-skill"]], ["crucible.localModels", true],
  ]);
  const settings: Record<string, unknown> = { "crucible.policy.shell": "ask", "crucible.performance.mcpOnDemand": false,
    "crucible.memory.experimental.recall": true, "crucible.memory.experimental.storePath": "/workspace/memory.sqlite3" };
  native.configuration.get.mockImplementation((key: string, fallback?: unknown) => settings[key] ?? fallback);
  native.configuration.inspect.mockImplementation((key: string) => key in settings ? { workspaceValue: settings[key] } : undefined);
  const state = { get: <T>(key: string, fallback?: T): T | undefined => preferences.has(key) ? preferences.get(key) as T : fallback,
    update: vi.fn(async (key: string, value: unknown) => { preferences.set(key, value); }) };
  // Public endpoint metadata, not a generated credential. The absent API key must stay absent.
  const secrets = { get: vi.fn(async (key: string) => key === "crucible.providerExtra.watsonx.WATSONX_URL" ? "https://example.invalid/service" : undefined),
    store: vi.fn(async () => {}), delete: vi.fn(async () => {}) };
  const context = { globalState: state, secrets, extensionUri: { fsPath: "/extension" }, subscriptions: [] as unknown[] };
  const output = { append: vi.fn(), appendLine: vi.fn(), show: vi.fn() };
  const runtime = new RuntimeManager(context as unknown as vscode.ExtensionContext, output as unknown as vscode.OutputChannel);
  return { runtime, context, output, preferences };
}

describe("VS Code runtime port adapter", () => {
  it("wires native status/disposal, state, secrets and global configuration writes", async () => {
    const h = setup();
    expect(h.context.subscriptions).toEqual(native.statuses);
    expect(native.statuses.map(status => status.command)).toEqual(["crucible.openSettingsPanel", "crucible.openSettingsPanel"]);
    await h.runtime.saveProvider("chatgpt", "chosen"); await h.runtime.saveReasoningEffort("medium");
    await h.runtime.saveChatGPTRegistration("account"); await h.runtime.setLocalModels(false);
    expect(h.context.globalState.update).toHaveBeenCalledWith("crucible.provider.backend", "chatgpt");
    expect(h.context.globalState.update).toHaveBeenCalledWith("crucible.provider.model", "chosen");
    expect(h.context.globalState.update).toHaveBeenCalledWith("crucible.reasoningEffort", "medium");
    expect(h.context.globalState.update).toHaveBeenCalledWith("crucible.chatgpt.registration", "account");
    expect(native.configuration.update).toHaveBeenCalledWith("crucible.localModels", false, vscode.ConfigurationTarget.Global);
    await h.runtime.getProviderKey("watsonx"); await h.runtime.deleteProviderKey("watsonx");
    expect(h.context.secrets.get).toHaveBeenCalledWith("crucible.providerKey.watsonx");
    expect(h.context.secrets.delete).toHaveBeenCalledWith("crucible.providerKey.watsonx");
    h.runtime.setAuthError("sign in again");
    expect(native.statuses[1]?.tooltip).toBe("sign in again"); expect(native.statuses[1]?.show).toHaveBeenCalledOnce();
    h.runtime.setAuthError(null); expect(native.statuses[1]?.hide).toHaveBeenCalledOnce();
  });

  it("keeps the pre-extraction settings and complete spawn-env golden", async () => {
    const h = setup();
    // Golden verified against base 6bd7dc65 before extraction. No git/history
    // dependency: this regression must keep working after committing and in tarballs.
    const settings = await h.runtime.getProviderSettings();
    expect(settings).toEqual({ backend: "watsonx", model: "model", contextWindow: 200000,
      reasoningEffort: "high", skillsDisabled: ["disabled-skill"], localModels: true,
      extraEnv: { CRUCIBLE_SHELL_POLICY: "ask", CRUCIBLE_CAPABILITY_LOADING_ENABLED: "false",
        CRUCIBLE_MEMORY_RECALL_V2: "true", CRUCIBLE_MEMORY_STORE_PATH: "/workspace/memory.sqlite3",
        WATSONX_URL: "https://example.invalid/service" } });
    expect(buildBackendEnv("/workspace", settings!, "/runtime", "darwin-arm64")).toEqual({
      CRUCIBLE_REASONING_BACKEND: "watsonx", CRUCIBLE_WATSONX_MODEL: "model",
      CRUCIBLE_WORKSPACE_PATH: "/workspace", CRUCIBLE_DB_PATH: "/workspace/.crucible/state/agentd.sqlite3",
      CRUCIBLE_CHAT_DB_PATH: "/workspace/.crucible/state/chat.sqlite3",
      CRUCIBLE_SHADOW_ROOT: "/workspace/.crucible/state/shadows", CRUCIBLE_LOG_FILE: "/workspace/.crucible/state/agentd.log",
      CRUCIBLE_ARTIFACTS_ROOT: "/workspace/.crucible/state/artifacts",
      CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH: "/workspace/.crucible/index-snapshot.json",
      CRUCIBLE_MEMORY_DB_PATH: "/workspace/.crucible/state/memory.sqlite3",
      CRUCIBLE_VECTOR_INDEX_PATH: "/workspace/.crucible/vector-index", CRUCIBLE_LOG_DIR: "/workspace/.tmp/reasoning",
      CRUCIBLE_RIPGREP_CMD: "/runtime/bin/rg", CRUCIBLE_CHAT_CONTROLLER: "1", CRUCIBLE_TASK_SUBSYSTEM: "0",
      CRUCIBLE_TOOL_BATCHING_ENABLED: "1", CRUCIBLE_ADAPTIVE_READS_ENABLED: "1", CRUCIBLE_COMPACT_CONTEXT_ENABLED: "1",
      CRUCIBLE_MEMORY_RECALL_V2: "true", CRUCIBLE_MEMORY_CURATOR_V2: "1", CRUCIBLE_MEMORY_COMPACT_EVIDENCE: "1",
      CRUCIBLE_SKILLS_ENABLED: "1", CRUCIBLE_SKILLS_DISABLED: "disabled-skill", CRUCIBLE_MCP_ENABLED: "1",
      CRUCIBLE_WEB_SEARCH_ENABLED: "1", CRUCIBLE_WEB_FETCH_ENABLED: "1", CRUCIBLE_PROVIDER_MAX_RPM: "35",
      CRUCIBLE_EXEC_SESSIONS_ENABLED: "1", CRUCIBLE_LOCAL_MODELS: "1", CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT: "false",
      CRUCIBLE_SHELL_POLICY: "ask", CRUCIBLE_SCOPE_POLICY: "ask", CRUCIBLE_SCOPE_TRIGGER: "any",
      CRUCIBLE_MEMORY_WINDOW_TOKENS: "200000", CRUCIBLE_REASONING_EFFORT: "high",
      CRUCIBLE_CAPABILITY_LOADING_ENABLED: "false", CRUCIBLE_MEMORY_STORE_PATH: "/workspace/memory.sqlite3",
      WATSONX_URL: "https://example.invalid/service",
    });
  });
});
