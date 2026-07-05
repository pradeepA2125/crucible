import type { SettingsInMsg } from "./types";
import { acquireVsCodeApiSingleton } from "../sharedVscodeApi";

interface VscodeApi {
  postMessage(msg: SettingsInMsg): void;
}

// Shared, window-cached handle — see src/sharedVscodeApi.ts. When SettingsApp is
// embedded into the chat webview (floating overlay), this module and the chat's
// vscodeApi both resolve to the single acquireVsCodeApi() handle.
export const vscode: VscodeApi = acquireVsCodeApiSingleton<VscodeApi>();
