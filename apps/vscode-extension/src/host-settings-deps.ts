
import type { BackendClientFactory } from "./controller.js";
import { PROVIDER_KEY_ENV } from "./provider-credentials.js";
import type { RuntimeCore } from "./runtime/runtime-core.js";
import type { SettingsDeps } from "./settings-data.js";
import { createClaudeDeps } from "./claude-host-deps.js";
import { createChatGPTDeps } from "./chatgpt-host-deps.js";
import { buildModelOptions } from "./composer-models.js";
import { PROVIDERS } from "./setup-data.js";

export interface HostSettingsOptions {
  runtimeManager: RuntimeCore;
  native: Pick<SettingsDeps, "readEnvFlags" | "updateSetting" | "readAppearance" | "setAppearance" | "readInstructions" | "writeInstructions" | "openFile">;
  openExternal(url: string): Promise<void>;
  openClaudeExternal?(url: string): Promise<void>;
  workspacePath: string;
  clientFactory: BackendClientFactory;
  // Resolves the backend URL the same way the chat controller does — honors an
  // explicit `crucible.backendBaseUrl` (dev-attach), else the managed backend.
  resolveBackendUrl: () => string;
}

/** Common settings wiring; only OS/editor effects are host ports. */
export function createHostSettingsDeps(opts: HostSettingsOptions): SettingsDeps {
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
      setMcpServerTools: (name, enabledTools, disabledTools, disabled) =>
        client().setMcpServerTools(name, enabledTools, disabledTools, disabled),
      testContextWindow: (req) => client().testContextWindow(req),
      listAgentDefinitions: () => client().listAgentDefinitions(),
      saveAgentDefinition: (name, input) => client().saveAgentDefinition(name, input),
      deleteAgentDefinition: (name) => client().deleteAgentDefinition(name),
      trustAgentDefinition: (path, sha256) => client().trustAgentDefinition(path, sha256),
    },
    workspace: workspacePath,
    readRuntimeJson: () => runtimeManager.installedRuntime(),
    mcpDisabled: () => runtimeManager.mcpDisabled(),
    setMcpDisabled: (names) => runtimeManager.setMcpDisabled(names),
    skillsDisabled: () => runtimeManager.skillsDisabled(),
    setSkillsDisabled: (names) => runtimeManager.setSkillsDisabled(names),
    storeSecret: (backend, key) => runtimeManager.storeProviderKey(backend, key),
    deleteSecret: (backend) => runtimeManager.deleteProviderKey(backend),
    storeExtraCredentials: (backend, extraCredentials) =>
      runtimeManager.storeProviderExtraCredentials(backend, extraCredentials),
    keyEnvVar: (backend) => PROVIDER_KEY_ENV[backend],
    ...opts.native,
    restartBackend: () => runtimeManager.restart(workspacePath),
    chatgpt: createChatGPTDeps(runtimeManager, client, opts.openExternal),
    claude: createClaudeDeps(runtimeManager, client, opts.openClaudeExternal ?? opts.openExternal),
    saveContextWindow: (tokens) => runtimeManager.saveContextWindow(tokens),
    // The same option list as the composer's model menu (spec §10.2): providers with a
    // stored key, plus the active one.
    listModels: async () => {
      const config = await client().getConfig();
      const keyed: string[] = [];
      for (const p of PROVIDERS) {
        if (p.keyEnvVar && (await runtimeManager.getProviderKey(p.id)) !== undefined) keyed.push(p.id);
      }
      const options = buildModelOptions(config.provider ?? null, keyed, PROVIDERS);
      return [...new Set(options.map((o) => o.model).filter((m) => m))];
    },
  };
}
