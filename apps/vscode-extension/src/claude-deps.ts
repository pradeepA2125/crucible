import * as vscode from "vscode";
import type { BackendTaskClient } from "@crucible/editor-client";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";
import { createClaudeDeps } from "./claude-host-deps.js";
export function buildClaudeDeps(runtime: RuntimeManager, client: () => BackendTaskClient) {
  return createClaudeDeps(runtime, client, async url => {
    if (!await vscode.env.openExternal(vscode.Uri.parse(url))) throw new Error("The browser could not be opened. Try connecting Claude again.");
  });
}
