import type { MemoryToHost } from "./types";

interface VscodeApi {
  postMessage(msg: MemoryToHost): void;
}

declare function acquireVsCodeApi(): VscodeApi;

// acquireVsCodeApi() may only be called once per webview lifetime. In tests it's mocked.
const _api: VscodeApi =
  typeof acquireVsCodeApi === "function" ? acquireVsCodeApi() : { postMessage: () => {} };

export const vscode: VscodeApi = _api;
