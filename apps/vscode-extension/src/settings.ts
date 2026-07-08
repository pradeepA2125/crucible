import * as vscode from "vscode";

import type { SettingsProvider } from "./controller.js";
import type { TaskMode } from "./types.js";

const DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8000";
const DEFAULT_MODE: TaskMode = "project_edit";
const DEFAULT_POLL_INTERVAL_MS = 1000;

export class VscodeSettingsProvider implements SettingsProvider {
  // Set by the managed runtime once its backend is up. An explicitly user-set
  // crucible.backendBaseUrl (the dev flow) always wins over this.
  private managedBackendUrl: string | null = null;

  setManagedBackendUrl(url: string | null): void {
    this.managedBackendUrl = url;
  }

  getBackendBaseUrl(): string {
    if (this.managedBackendUrl !== null && !isBackendBaseUrlUserSet()) {
      return this.managedBackendUrl;
    }
    return vscode.workspace
      .getConfiguration("crucible")
      .get<string>("backendBaseUrl", DEFAULT_BACKEND_BASE_URL)
      .trim();
  }

  getDefaultMode(): TaskMode {
    const configured = vscode.workspace
      .getConfiguration("crucible")
      .get<TaskMode>("defaultMode", DEFAULT_MODE);

    if (configured === "inline" || configured === "file_edit" || configured === "project_edit" || configured === "autonomous") {
      return configured;
    }

    return DEFAULT_MODE;
  }

  getPollIntervalMs(): number {
    const configured = vscode.workspace
      .getConfiguration("crucible")
      .get<number>("pollIntervalMs", DEFAULT_POLL_INTERVAL_MS);

    if (!Number.isFinite(configured)) {
      return DEFAULT_POLL_INTERVAL_MS;
    }

    return Math.max(250, Math.floor(configured));
  }
}

export function isBackendBaseUrlUserSet(): boolean {
  const info = vscode.workspace.getConfiguration("crucible").inspect<string>("backendBaseUrl");
  return (
    info !== undefined &&
    (info.globalValue !== undefined ||
      info.workspaceValue !== undefined ||
      info.workspaceFolderValue !== undefined)
  );
}

export async function checkBackendHealth(baseUrl: string): Promise<boolean> {
  const fetchFn = (globalThis as { fetch?: (input: string, init?: RequestInit) => Promise<Response> }).fetch;
  if (typeof fetchFn !== "function") {
    return true;
  }

  try {
    const response = await fetchFn(`${baseUrl}/health`, { method: "GET" });
    return response.ok;
  } catch {
    return false;
  }
}
