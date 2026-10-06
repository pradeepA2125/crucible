import type { SetupInMsg } from "./types";
import { acquireVsCodeApiSingleton } from "../sharedVscodeApi";

interface VscodeApi {
  postMessage(msg: SetupInMsg): void;
}

// Shared, window-cached handle: the wizard embeds the ChatGPT plan panel, which posts
// through src/settings/vscodeApi.ts, and acquireVsCodeApi() may be called only once
// per webview — a second direct call throws.
export const vscode: VscodeApi = acquireVsCodeApiSingleton<VscodeApi>();
