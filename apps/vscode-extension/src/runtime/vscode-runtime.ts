// VS Code owns native presentation and storage adapters; runtime orchestration
// is shared with desktop in RuntimeCore.
import { homedir } from "node:os";
import { isAbsolute } from "node:path";
import * as vscode from "vscode";
import { RuntimeCore } from "./runtime-core.js";
import { migrateLegacyRuntimeRoot } from "./legacy-migration.js";
export { PROVIDER_KEY_ENV } from "../provider-credentials.js";

export class RuntimeManager extends RuntimeCore {
  constructor(context: vscode.ExtensionContext, output: vscode.OutputChannel) {
    const developmentRuntime = process.env.CRUCIBLE_DEV_RUNTIME;
    const runtimeDir = context.extensionMode === vscode.ExtensionMode.Development && developmentRuntime && isAbsolute(developmentRuntime)
      ? developmentRuntime : migrateLegacyRuntimeRoot(homedir());
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    statusBar.command = "crucible.openSettingsPanel";
    const authStatus = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 49);
    authStatus.text = "$(error) Crucible: not authorized";
    authStatus.command = "crucible.openSettingsPanel";
    super({
      clientName:"VS Code",isTrusted:()=>vscode.workspace.isTrusted,
      runtimeDir, resourceDir: context.extensionUri.fsPath,
      state: context.globalState, secrets: context.secrets, output, statusBar, authStatus,
      configuration: () => vscode.workspace.getConfiguration(),
      updateGlobalSetting: (key, value) => vscode.workspace.getConfiguration().update(key, value, vscode.ConfigurationTarget.Global),
      showError: async (message, modal, actions = []) => modal
        ? vscode.window.showErrorMessage(message, { modal: true }, ...actions)
        : vscode.window.showErrorMessage(message, ...actions),
    });
    context.subscriptions.push(statusBar, authStatus);
  }
}
