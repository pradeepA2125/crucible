import * as vscode from "vscode";
import type { BackendTaskClient } from "@crucible/editor-client";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";
import { createChatGPTDeps } from "./chatgpt-host-deps.js";
export function buildChatGPTDeps(runtime: RuntimeManager, client: () => BackendTaskClient) {
  return createChatGPTDeps(runtime, client, async url => {
    await vscode.env.openExternal(vscode.Uri.parse(url));
  });
}
