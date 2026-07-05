import * as fs from "node:fs";
import * as vscode from "vscode";

import type { BackendClientFactory } from "./controller.js";
import { PROVIDER_KEY_ENV } from "./runtime/vscode-runtime.js";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";
import {
  createSettingsHandler,
  type SettingsDeps,
  type SettingsInMsg,
} from "./settings-data.js";
import type { SettingsSectionId } from "./settings-sections.js";
import { loadInstructions, saveInstructions } from "./instructions-file.js";

const ENV_FLAG_KEYS = [
  "aiEditor.policy.shell",
  "aiEditor.policy.scope",
  "aiEditor.memory.enabled",
  "aiEditor.memory.reranker",
];

/** Settings panel: provider hot-swap, MCP server management, skills toggles, runtime
 * versions + restart. Loads the webview-ui `settings` Vite entry (dist/settings.html).
 * Mirrors setup-panel.ts/memory-panel.ts. */
export class SettingsPanel {
  private panel: vscode.WebviewPanel | null = null;
  // Section to deep-link to once the freshly-created webview signals readiness
  // (its `settings/load` mount message). Posting before mount would race the
  // webview's own message listener; deferring makes the jump reliable.
  private pendingSection: SettingsSectionId | null = null;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly runtimeManager: RuntimeManager,
    private readonly workspacePath: string,
    private readonly clientFactory: BackendClientFactory,
    // Resolves the backend URL the same way the chat controller does — honors an
    // explicit `aiEditor.backendBaseUrl` (dev-attach flow), falling back to the
    // managed backend. runtimeManager.backendUrl() alone only knows the managed
    // port, so the panel could never reach a dev backend without this.
    private readonly resolveBackendUrl: () => string,
  ) {}

  open(section?: SettingsSectionId): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.One);
      // Already mounted — its listener is live, so navigate immediately.
      if (section) this.panel.webview.postMessage({ type: "settings/navigate", section });
      return;
    }
    this.pendingSection = section ?? null;
    this.panel = vscode.window.createWebviewPanel(
      "aiEditorSettings",
      "AI Editor Settings",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist")],
      }
    );
    this.panel.webview.html = this.buildHtml();
    const handle = createSettingsHandler(this.buildDeps(), (msg) => this.panel?.webview.postMessage(msg));
    this.panel.webview.onDidReceiveMessage((msg: unknown) => {
      const inbound = msg as SettingsInMsg;
      // The webview posts `settings/load` on mount — the reliable "I'm ready"
      // signal. Fire the deferred deep-link navigate exactly once, then hand off.
      if (inbound?.type === "settings/load" && this.pendingSection) {
        this.panel?.webview.postMessage({ type: "settings/navigate", section: this.pendingSection });
        this.pendingSection = null;
      }
      void handle(inbound);
    });
    this.panel.onDidDispose(() => {
      this.panel = null;
      this.pendingSection = null;
    });
  }

  private client() {
    const url = this.resolveBackendUrl().trim();
    if (!url) {
      throw new Error("Backend not started yet — run \"AI Editor: Run Setup\" or restart the backend.");
    }
    return this.clientFactory(url);
  }

  private buildDeps(): SettingsDeps {
    return {
      client: {
        getConfig: () => this.client().getConfig(),
        listMcpServers: () => this.client().listMcpServers(),
        listSkills: (workspace) => this.client().listSkills(workspace),
        validateProvider: (req) => this.client().validateProvider(req),
        setProvider: async (req) => {
          const result = await this.client().setProvider(req);
          // Persist the choice for the next managed restart/relaunch — the hot-swap
          // route only changes the in-process engine. The secret itself is stored
          // separately via storeSecret (SecretStorage), so no apiKey here.
          await this.runtimeManager.saveProvider(result.backend, result.model);
          return result;
        },
        upsertMcpServer: (name, entry, disabled) => this.client().upsertMcpServer(name, entry, disabled),
        deleteMcpServer: (name, disabled) => this.client().deleteMcpServer(name, disabled),
        reconnectMcpServer: (name, disabled) => this.client().reconnectMcpServer(name, disabled),
      },
      workspace: this.workspacePath,
      readRuntimeJson: () => this.runtimeManager.installedRuntime(),
      mcpDisabled: () => this.runtimeManager.mcpDisabled(),
      setMcpDisabled: (names) => this.runtimeManager.setMcpDisabled(names),
      skillsDisabled: () => this.runtimeManager.skillsDisabled(),
      setSkillsDisabled: (names) => this.runtimeManager.setSkillsDisabled(names),
      storeSecret: (backend, key) => this.runtimeManager.storeProviderKey(backend, key),
      keyEnvVar: (backend) => PROVIDER_KEY_ENV[backend],
      readEnvFlags: () => {
        const cfg = vscode.workspace.getConfiguration();
        const out: Record<string, string> = {};
        for (const key of ENV_FLAG_KEYS) {
          const value = cfg.get<string | boolean>(key);
          if (value !== undefined) out[key] = String(value);
        }
        return out;
      },
      updateSetting: async (key, value) => {
        await vscode.workspace
          .getConfiguration()
          .update(key, value, vscode.ConfigurationTarget.Global);
      },
      readInstructions: () => loadInstructions(this.workspacePath),
      writeInstructions: (content) => saveInstructions(this.workspacePath, content),
      restartBackend: () => this.runtimeManager.restart(this.workspacePath),
    };
  }

  private buildHtml(): string {
    const distPath = vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist");
    let rawHtml: string;
    try {
      rawHtml = fs.readFileSync(vscode.Uri.joinPath(distPath, "settings.html").fsPath, "utf8");
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      return `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Settings</title></head>
<body style="background:#1e1e1e;color:#ccc;font-family:sans-serif;padding:2em">
  <p>Settings webview build is missing.</p>
  <pre>Run: npm run -w ai-editor-vscode-extension build</pre>
  <p style="color:#888">${errMsg}</p>
</body></html>`;
    }
    const nonce = Array.from({ length: 16 }, () =>
      Math.floor(Math.random() * 256).toString(16).padStart(2, "0")
    ).join("");
    const cspSource = this.panel!.webview.cspSource;
    let html = rawHtml.replace(/(src|href)="\.\/(assets\/[^"]+)"/g, (_m, attr: string, assetPath: string) => {
      const uri = this.panel!.webview.asWebviewUri(vscode.Uri.joinPath(distPath, assetPath));
      return `${attr}="${uri}"`;
    });
    html = html.replace(/<script(?=[\s>])/g, `<script nonce="${nonce}"`);
    html = html.replace(
      "<head>",
      `<head>\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${cspSource}; script-src 'nonce-${nonce}' ${cspSource}; img-src ${cspSource} data:; font-src ${cspSource};">`
    );
    return html;
  }
}
