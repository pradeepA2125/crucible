import type { McpServerList, McpServerView } from "@ai-editor/editor-client";

import type { SettingsSectionId } from "./settings-sections.js";

// vscode-free message handler for the settings panel (settings-panel.ts wires it to
// RuntimeManager + HttpBackendClient). Mirrors setup-data.ts/memory-data.ts's split.

export interface McpServerRow extends McpServerView {
  userEnabled: boolean;
}

export interface SettingsState {
  provider: { backend: string; model: string } | null;
  runtime: { releaseTag: string; components: Record<string, string> } | null;
  mcp: { enabled: boolean; servers: McpServerRow[] };
  skills: { name: string; description: string; enabled: boolean }[];
  envFlags: Record<string, string>;
  restartRequired: boolean;
}

// webview → host
export type SettingsInMsg =
  | { type: "settings/load" }
  | { type: "settings/setProvider"; backend: string; model: string; apiKey?: string }
  | { type: "settings/mcpUpsert"; name: string; entry: Record<string, unknown> }
  | { type: "settings/mcpDelete"; name: string }
  | { type: "settings/mcpToggle"; name: string; enabled: boolean }
  | { type: "settings/mcpReconnect"; name: string }
  | { type: "settings/skillToggle"; name: string; enabled: boolean }
  | { type: "settings/setEnvFlag"; key: string; value: string }
  | { type: "settings/loadInstructions" }
  | { type: "settings/saveInstructions"; content: string }
  | { type: "settings/restartBackend" };

// host → webview
export type SettingsOutMsg =
  | { type: "settings/state"; state: SettingsState }
  | { type: "settings/instructions"; content: string; exists: boolean }
  | { type: "settings/error"; message: string }
  | { type: "settings/navigate"; section: SettingsSectionId };

export interface SettingsDeps {
  client: {
    getConfig(): Promise<{ provider?: { backend: string; model: string } | null | undefined }>;
    listMcpServers(): Promise<McpServerList>;
    listSkills(workspace: string): Promise<{ name: string; description: string }[]>;
    validateProvider(req: {
      backend: string;
      model?: string;
      credentials?: Record<string, string>;
    }): Promise<{ ok: boolean; error?: string | undefined }>;
    setProvider(req: {
      backend: string;
      model?: string;
      credentials?: Record<string, string>;
    }): Promise<{ backend: string; model: string }>;
    upsertMcpServer(
      name: string,
      entry: Record<string, unknown>,
      disabled: string[],
    ): Promise<McpServerList>;
    deleteMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
    reconnectMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
  };
  workspace: string;
  readRuntimeJson(): { releaseTag: string; components: Record<string, string> } | null;
  mcpDisabled(): string[];
  setMcpDisabled(names: string[]): Promise<void>;
  skillsDisabled(): string[];
  setSkillsDisabled(names: string[]): Promise<void>;
  storeSecret(backend: string, key: string): Promise<void>;
  keyEnvVar(backend: string): string | undefined;
  readEnvFlags(): Record<string, string>;
  updateSetting(key: string, value: string): Promise<void>;
  readInstructions(): { content: string; exists: boolean };
  writeInstructions(content: string): void;
  restartBackend(): Promise<void>;
}

async function buildState(deps: SettingsDeps, restartRequired: boolean): Promise<SettingsState> {
  const [config, mcpList, skillSummaries] = await Promise.all([
    deps.client.getConfig(),
    deps.client.listMcpServers(),
    deps.client.listSkills(deps.workspace),
  ]);
  const disabledMcp = new Set(deps.mcpDisabled());
  const disabledSkills = new Set(deps.skillsDisabled());
  return {
    provider: config.provider ?? null,
    runtime: deps.readRuntimeJson(),
    mcp: {
      enabled: mcpList.enabled,
      servers: mcpList.servers.map((s) => ({ ...s, userEnabled: !disabledMcp.has(s.name) })),
    },
    skills: skillSummaries.map((s) => ({ ...s, enabled: !disabledSkills.has(s.name) })),
    envFlags: deps.readEnvFlags(),
    restartRequired,
  };
}

export function createSettingsHandler(
  deps: SettingsDeps,
  post: (msg: SettingsOutMsg) => void,
): (msg: SettingsInMsg) => Promise<void> {
  let restartRequired = false;

  const postState = async (): Promise<void> => {
    post({ type: "settings/state", state: await buildState(deps, restartRequired) });
  };

  return async (msg: SettingsInMsg): Promise<void> => {
    try {
      switch (msg.type) {
        case "settings/load": {
          await postState();
          return;
        }
        case "settings/setProvider": {
          const envVar = deps.keyEnvVar(msg.backend);
          const credentials =
            envVar && msg.apiKey ? { [envVar]: msg.apiKey } : undefined;
          const result = await deps.client.validateProvider({
            backend: msg.backend,
            model: msg.model,
            ...(credentials ? { credentials } : {}),
          });
          if (!result.ok) {
            post({ type: "settings/error", message: result.error ?? "validation failed" });
            return;
          }
          if (envVar && msg.apiKey) {
            await deps.storeSecret(msg.backend, msg.apiKey);
          }
          await deps.client.setProvider({
            backend: msg.backend,
            model: msg.model,
            ...(credentials ? { credentials } : {}),
          });
          await postState();
          return;
        }
        case "settings/mcpUpsert": {
          await deps.client.upsertMcpServer(msg.name, msg.entry, deps.mcpDisabled());
          await postState();
          return;
        }
        case "settings/mcpDelete": {
          await deps.client.deleteMcpServer(msg.name, deps.mcpDisabled());
          await postState();
          return;
        }
        case "settings/mcpToggle": {
          const current = new Set(deps.mcpDisabled());
          if (msg.enabled) current.delete(msg.name);
          else current.add(msg.name);
          const nextDisabled = Array.from(current);
          await deps.setMcpDisabled(nextDisabled);
          await deps.client.reconnectMcpServer(msg.name, nextDisabled);
          await postState();
          return;
        }
        case "settings/mcpReconnect": {
          await deps.client.reconnectMcpServer(msg.name, deps.mcpDisabled());
          await postState();
          return;
        }
        case "settings/skillToggle": {
          const current = new Set(deps.skillsDisabled());
          if (msg.enabled) current.delete(msg.name);
          else current.add(msg.name);
          await deps.setSkillsDisabled(Array.from(current));
          restartRequired = true;
          await postState();
          return;
        }
        case "settings/setEnvFlag": {
          await deps.updateSetting(msg.key, msg.value);
          restartRequired = true;
          await postState();
          return;
        }
        case "settings/loadInstructions": {
          post({ type: "settings/instructions", ...deps.readInstructions() });
          return;
        }
        case "settings/saveInstructions": {
          deps.writeInstructions(msg.content);
          post({ type: "settings/instructions", content: msg.content, exists: true });
          return;
        }
        case "settings/restartBackend": {
          await deps.restartBackend();
          restartRequired = false;
          await postState();
          return;
        }
      }
    } catch (err) {
      post({
        type: "settings/error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };
}
