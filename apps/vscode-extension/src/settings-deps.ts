import * as vscode from "vscode";

import type { BackendClientFactory } from "./controller.js";
import { PROVIDER_KEY_ENV } from "./runtime/vscode-runtime.js";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";
import type { SettingsDeps } from "./settings-data.js";
import { loadInstructions, saveInstructions } from "./instructions-file.js";

const ENV_FLAG_KEYS = [
  "crucible.policy.shell",
  "crucible.policy.scope",
  "crucible.memory.enabled",
  "crucible.memory.reranker",
];

export interface SettingsDepsOptions {
  runtimeManager: RuntimeManager;
  workspacePath: string;
  clientFactory: BackendClientFactory;
  // Resolves the backend URL the same way the chat controller does — honors an
  // explicit `crucible.backendBaseUrl` (dev-attach), else the managed backend.
  resolveBackendUrl: () => string;
}

/**
 * Builds the vscode-wired {@link SettingsDeps} consumed by `createSettingsHandler`.
 * Extracted from SettingsPanel so the chat webview's floating settings overlay can
 * share the exact same host wiring (provider hot-swap, MCP admin, skills/policy
 * flags, runtime restart) — one settings backend, two mount points.
 */
export function buildSettingsDeps(opts: SettingsDepsOptions): SettingsDeps {
  const { runtimeManager, workspacePath } = opts;
  const client = () => {
    const url = opts.resolveBackendUrl().trim();
    if (!url) {
      throw new Error(
        'Backend not started yet — run "Crucible: Run Setup" or restart the backend.',
      );
    }
    return opts.clientFactory(url);
  };

  return {
    client: {
      getConfig: () => client().getConfig(),
      listMcpServers: () => client().listMcpServers(),
      listSkills: (workspace) => client().listSkills(workspace),
      validateProvider: (req) => client().validateProvider(req),
      setProvider: async (req) => {
        const result = await client().setProvider(req);
        // Persist the choice for the next managed restart/relaunch — the hot-swap
        // route only changes the in-process engine. The secret itself is stored
        // separately via storeSecret (SecretStorage), so no apiKey here.
        await runtimeManager.saveProvider(result.backend, result.model);
        return result;
      },
      upsertMcpServer: (name, entry, disabled) => client().upsertMcpServer(name, entry, disabled),
      deleteMcpServer: (name, disabled) => client().deleteMcpServer(name, disabled),
      reconnectMcpServer: (name, disabled) => client().reconnectMcpServer(name, disabled),
    },
    workspace: workspacePath,
    readRuntimeJson: () => runtimeManager.installedRuntime(),
    mcpDisabled: () => runtimeManager.mcpDisabled(),
    setMcpDisabled: (names) => runtimeManager.setMcpDisabled(names),
    skillsDisabled: () => runtimeManager.skillsDisabled(),
    setSkillsDisabled: (names) => runtimeManager.setSkillsDisabled(names),
    storeSecret: (backend, key) => runtimeManager.storeProviderKey(backend, key),
    storeExtraCredentials: (backend, extraCredentials) =>
      runtimeManager.storeProviderExtraCredentials(backend, extraCredentials),
    keyEnvVar: (backend) => PROVIDER_KEY_ENV[backend],
    readEnvFlags: () => {
      const cfg = vscode.workspace.getConfiguration();
      const out: Record<string, string> = {};
      for (const key of ENV_FLAG_KEYS) {
        const value = cfg.get<string | boolean>(key);
        if (value !== undefined) out[key] = String(value);
      }
      return out;
    },
    updateSetting: async (key, value) => {
      await vscode.workspace
        .getConfiguration()
        .update(key, value, vscode.ConfigurationTarget.Global);
    },
    readInstructions: () => loadInstructions(workspacePath),
    writeInstructions: (content) => saveInstructions(workspacePath, content),
    restartBackend: () => runtimeManager.restart(workspacePath),
  };
}
