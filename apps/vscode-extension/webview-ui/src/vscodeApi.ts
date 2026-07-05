import type { WebviewMessage } from "./types";
import { acquireVsCodeApiSingleton } from "./sharedVscodeApi";

interface VscodeApi {
  postMessage(msg: WebviewMessage): void;
}

// Shared, window-cached handle: the chat bundle also embeds the settings UI
// (src/settings/vscodeApi.ts), and acquireVsCodeApi() may be called only once per
// webview. Both go through the singleton so they share the single acquired handle.
export const vscode: VscodeApi = acquireVsCodeApiSingleton<VscodeApi>();
