import * as vscode from "vscode";
import { realpathSync } from "node:fs";
import { ownerFor } from "./runtime/project-owner.js";
import type { BackendClientFactory } from "./controller.js";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";
import { createHostSettingsDeps } from "./host-settings-deps.js";
import { loadInstructions, saveInstructions } from "./instructions-file.js";
import { readThemeConfig } from "./settings.js";
const ENV_FLAG_KEYS = [
  "crucible.policy.shell",
  "crucible.policy.scope",
  "crucible.webSearch.enabled",
  "crucible.webFetch.enabled",
  "crucible.memory.enabled",
  "crucible.localModels",
];

export interface SettingsDepsOptions {
  runtimeManager: RuntimeManager;
  workspacePath: string;
  clientFactory: BackendClientFactory;
  // Resolves the backend URL the same way the chat controller does — honors an
  // explicit `crucible.backendBaseUrl` (dev-attach), else the managed backend.
  resolveBackendUrl: () => string;
  setSandboxEnabled: (enabled: boolean) => Promise<void>;
}

export function buildSettingsDeps(opts: SettingsDepsOptions) {
  const { runtimeManager, workspacePath } = opts;
  return createHostSettingsDeps({ ...opts, openExternal: async url => {
    await vscode.env.openExternal(vscode.Uri.parse(url));
  }, openClaudeExternal: async url => {
    if (!await vscode.env.openExternal(vscode.Uri.parse(url))) throw new Error("The browser could not be opened. Try connecting Claude again.");
  }, native: {
    readEnvFlags: () => {
      const cfg = vscode.workspace.getConfiguration();
      const out: Record<string, string> = {};
      for (const key of ENV_FLAG_KEYS) {
        const value = cfg.get<string | boolean>(key);
        if (value !== undefined) out[key] = String(value);
      }
      out["crucible.localModels"] = String(runtimeManager.localModels());
      return out;
    },
    updateSetting: async (key, value) => {
      if (key === "crucible.localModels") {
        await runtimeManager.setLocalModels(value === true || value === "true");
        return;
      }
      await vscode.workspace
        .getConfiguration()
        .update(key, value, vscode.ConfigurationTarget.Global);
    },
    readAppearance: () => readThemeConfig(),
    setAppearance: async ({ appearance, accent }) => {
      const cfg = vscode.workspace.getConfiguration("crucible");
      await cfg.update("appearance", appearance, vscode.ConfigurationTarget.Global);
      await cfg.update("accent", accent, vscode.ConfigurationTarget.Global);
    },
    readInstructions: () => loadInstructions(workspacePath),
    writeInstructions: async (content) => {
      const owner = ownerFor(opts.resolveBackendUrl());
      if (!owner || owner.workspace !== realpathSync(workspacePath)) throw new Error("Read only. Save project instructions in the controlling client.");
      return saveInstructions(workspacePath, content);
    },
    openFile: async path => { await vscode.window.showTextDocument(vscode.Uri.file(path)); },
  } });
}
