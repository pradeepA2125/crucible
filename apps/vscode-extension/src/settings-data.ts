import type { McpServerList, McpServerView } from "@crucible/editor-client";

import type { SettingsSectionId } from "./settings-sections.js";

// vscode-free message handler for the settings panel (settings-panel.ts wires it to
// RuntimeManager + HttpBackendClient). Mirrors setup-data.ts/memory-data.ts's split.

export interface McpServerRow extends McpServerView {
  userEnabled: boolean;
}

export interface SettingsState {
  // contextWindow is the window compaction is using right now, read back from
  // GET /v1/config so the field shows what the process actually has, not what the
  // panel last sent.
  provider: { backend: string; model: string; contextWindow?: number | null } | null;
  // Non-fatal note from the last successful provider validate (e.g. an
  // openai_compatible endpoint that only supports json_object, not strict JSON
  // schema). null once no validate has produced one yet.
  providerWarning?: string | null;
  runtime: { releaseTag: string; components: Record<string, string> } | null;
  mcp: { enabled: boolean; servers: McpServerRow[] };
  skills: { name: string; description: string; enabled: boolean }[];
  envFlags: Record<string, string>;
  restartRequired: boolean;
}

// webview → host
export type SettingsInMsg =
  | { type: "settings/load" }
  | { type: "settings/setProvider"; backend: string; model: string; apiKey?: string; extraCredentials?: Record<string, string>; contextWindow?: number }
  // Explicit delete of a backend's stored API key. A blank API-key field means
  // "keep the stored key" (setProvider only writes a non-empty one), so this is
  // the ONLY way to get rid of it — see the openai_compatible note in SettingsDeps.
  | { type: "settings/clearProviderKey"; backend: string }
  // Opt-in, expensive (~one full window of input tokens per call), never part of
  // a save. Carries the same credentials as a save so the user can test an
  // endpoint before committing to it.
  | { type: "settings/testContextWindow"; backend: string; model: string; contextWindow: number; apiKey?: string; extraCredentials?: Record<string, string> }
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
  | { type: "settings/navigate"; section: SettingsSectionId }
  // Deliberately NOT folded into settings/state: a verdict about a value is not a
  // change to one, and it must not survive the next snapshot rebuild.
  | { type: "settings/contextTestResult"; result: { ok: boolean; recalled: boolean; promptTokens?: number | undefined; exact?: boolean | undefined; error?: string | undefined } };

export interface SettingsDeps {
  client: {
    getConfig(): Promise<{ provider?: { backend: string; model: string } | null | undefined }>;
    listMcpServers(): Promise<McpServerList>;
    listSkills(workspace: string): Promise<{ name: string; description: string }[]>;
    validateProvider(req: {
      backend: string;
      model?: string;
      credentials?: Record<string, string>;
    }): Promise<{ ok: boolean; error?: string | undefined; jsonMode?: string | undefined; warning?: string | undefined }>;
    setProvider(req: {
      backend: string;
      model?: string;
      credentials?: Record<string, string>;
      contextWindow?: number;
    }): Promise<{ backend: string; model: string }>;
    upsertMcpServer(
      name: string,
      entry: Record<string, unknown>,
      disabled: string[],
    ): Promise<McpServerList>;
    deleteMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
    reconnectMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
    testContextWindow(req: {
      backend: string;
      model?: string;
      credentials?: Record<string, string>;
      contextWindow: number;
    }): Promise<{ ok: boolean; recalled: boolean; promptTokens?: number | undefined; exact?: boolean | undefined; error?: string | undefined }>;
  };
  workspace: string;
  readRuntimeJson(): { releaseTag: string; components: Record<string, string> } | null;
  mcpDisabled(): string[];
  setMcpDisabled(names: string[]): Promise<void>;
  skillsDisabled(): string[];
  setSkillsDisabled(names: string[]): Promise<void>;
  storeSecret(backend: string, key: string): Promise<void>;
  /**
   * Delete a backend's stored API key. Needed because the stored key follows the
   * PROVIDER SLOT, not the endpoint: for `openai_compatible` a user can retarget
   * the Base URL at a different host while the previous host's bearer token stays
   * on file and keeps being sent. Every other provider is safe by construction
   * (its key env var is 1:1 with its endpoint), but the delete is offered for all
   * of them since the mechanism is identical.
   */
  deleteSecret(backend: string): Promise<void>;
  storeExtraCredentials(backend: string, extraCredentials: Record<string, string>): Promise<void>;
  keyEnvVar(backend: string): string | undefined;
  readEnvFlags(): Record<string, string>;
  updateSetting(key: string, value: string): Promise<void>;
  readInstructions(): { content: string; exists: boolean };
  writeInstructions(content: string): void;
  restartBackend(): Promise<void>;
  /** Persist the declared context window for the next managed spawn. Separate from
   * saveProvider for the same reason storeSecret is: a composer model hot-swap
   * writes backend/model and must not disturb the window. */
  saveContextWindow(tokens: number): Promise<void>;
}

async function buildState(
  deps: SettingsDeps,
  restartRequired: boolean,
  providerWarning: string | null,
): Promise<SettingsState> {
  const [config, mcpList, skillSummaries] = await Promise.all([
    deps.client.getConfig(),
    deps.client.listMcpServers(),
    deps.client.listSkills(deps.workspace),
  ]);
  const disabledMcp = new Set(deps.mcpDisabled());
  const disabledSkills = new Set(deps.skillsDisabled());
  return {
    provider: config.provider ?? null,
    providerWarning,
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
  let providerWarning: string | null = null;

  const postState = async (): Promise<void> => {
    post({ type: "settings/state", state: await buildState(deps, restartRequired, providerWarning) });
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
          const primaryCred = envVar && msg.apiKey ? { [envVar]: msg.apiKey } : undefined;
          const credentials = (primaryCred || msg.extraCredentials)
            ? { ...primaryCred, ...msg.extraCredentials }
            : undefined;
          const result = await deps.client.validateProvider({
            backend: msg.backend,
            model: msg.model,
            ...(credentials ? { credentials } : {}),
          });
          if (!result.ok) {
            // A stale warning describes a validate that's no longer in effect —
            // it must not survive a subsequent failed attempt (mirrors the setup
            // wizard's SetupApp.tsx, which clears validateWarning on every new
            // attempt). A successful validate already unconditionally overwrites
            // providerWarning below regardless of backend, so a provider switch
            // that succeeds is already covered; this is the one remaining path.
            providerWarning = null;
            // Push the cleared state BEFORE the error. The webview sends no
            // follow-up `settings/load` here, so without this the stale amber
            // warning stays rendered next to the new red error. Order matters:
            // SettingsApp clears its error banner on every `settings/state`.
            try {
              await postState();
            } catch {
              // A state-rebuild failure must never swallow the validation error.
            }
            post({ type: "settings/error", message: result.error ?? "validation failed" });
            return;
          }
          providerWarning = result.warning ?? null;
          if (envVar && msg.apiKey) {
            await deps.storeSecret(msg.backend, msg.apiKey);
          }
          if (msg.extraCredentials) {
            await deps.storeExtraCredentials(msg.backend, msg.extraCredentials);
          }
          await deps.client.setProvider({
            backend: msg.backend,
            model: msg.model,
            ...(credentials ? { credentials } : {}),
            ...(msg.contextWindow !== undefined ? { contextWindow: msg.contextWindow } : {}),
          });
          // After the hot-swap succeeds, so a rejected provider never leaves a
          // stale window persisted for the next managed spawn.
          if (msg.contextWindow !== undefined) {
            await deps.saveContextWindow(msg.contextWindow);
          }
          await postState();
          return;
        }
        case "settings/clearProviderKey": {
          await deps.deleteSecret(msg.backend);
          // The running managed backend still holds the old key in its spawn env
          // (buildBackendEnv injects it at spawn), so deleting the secret alone
          // does not stop it being sent. Surface the existing restart banner.
          restartRequired = true;
          await postState();
          return;
        }
        case "settings/testContextWindow": {
          const envVar = deps.keyEnvVar(msg.backend);
          const primaryCred = envVar && msg.apiKey ? { [envVar]: msg.apiKey } : undefined;
          const credentials = (primaryCred || msg.extraCredentials)
            ? { ...primaryCred, ...msg.extraCredentials }
            : undefined;
          const result = await deps.client.testContextWindow({
            backend: msg.backend,
            model: msg.model,
            contextWindow: msg.contextWindow,
            ...(credentials ? { credentials } : {}),
          });
          // No postState(): the verdict is not part of the settings snapshot, and
          // a failed test (ok:false) is a 200 from the route — it belongs beside
          // the field, not in the panel-wide error banner.
          post({ type: "settings/contextTestResult", result });
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
