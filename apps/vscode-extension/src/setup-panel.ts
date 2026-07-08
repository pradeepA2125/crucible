import * as fs from "node:fs";
import * as vscode from "vscode";

import type { BackendClientFactory } from "./controller.js";
import { PROVIDER_KEY_ENV } from "./runtime/vscode-runtime.js";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";
import { createSetupHandler, type SetupDeps, type SetupInMsg } from "./setup-data.js";

/** First-run setup wizard: install runtime, pick a provider, start the backend.
 * Loads the webview-ui `setup` Vite entry (dist/setup.html). Mirrors memory-panel.ts. */
export class SetupPanel {
  private panel: vscode.WebviewPanel | null = null;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly runtimeManager: RuntimeManager,
    private readonly workspacePath: string,
    private readonly clientFactory: BackendClientFactory,
    private readonly openChatCommand: () => void,
    private readonly setManagedBackendUrl: (url: string | null) => void
  ) {}

  open(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.One);
      return;
    }
    this.panel = vscode.window.createWebviewPanel(
      "crucibleSetup",
      "Crucible Setup",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist")],
      }
    );
    this.panel.webview.html = this.buildHtml();
    const handle = createSetupHandler(this.buildDeps(), (msg) => this.panel?.webview.postMessage(msg));
    this.panel.webview.onDidReceiveMessage((msg: unknown) => {
      void handle(msg as SetupInMsg);
    });
    this.panel.onDidDispose(() => {
      this.panel = null;
    });
  }

  private buildDeps(): SetupDeps {
    return {
      install: (onProgress) => this.runtimeManager.install(onProgress),
      validate: async (req) => {
        const url = this.runtimeManager.backendUrl(this.workspacePath);
        if (!url) {
          return { ok: false, error: "Backend not started yet — click Save & Start." };
        }
        const result = await this.clientFactory(url).validateProvider(req);
        return {
          ok: result.ok,
          ...(result.model !== undefined ? { model: result.model } : {}),
          ...(result.error !== undefined ? { error: result.error } : {}),
        };
      },
      saveAndStart: async (backend, model, apiKey) => {
        await this.runtimeManager.saveProvider(backend, model, apiKey);
        const { port } = await this.runtimeManager.startForWorkspace(this.workspacePath);
        const url = this.runtimeManager.backendUrl(this.workspacePath) ?? `http://localhost:${port}`;
        const result = await this.clientFactory(url).validateProvider({ backend, model });
        if (!result.ok) {
          throw new Error(result.error ?? "Provider validation failed after starting the backend.");
        }
        // The chat controller/settings provider must learn about this URL too — otherwise
        // a manually-run setup (e.g. the wizard didn't auto-open, or a retry after a failed
        // first attempt) leaves the controller pointed at the stale default backendBaseUrl
        // and every chat call fails with "fetch failed" even though the backend is healthy.
        this.setManagedBackendUrl(url);
        return { port };
      },
      openChat: () => this.openChatCommand(),
      keyEnvVar: (backend) => PROVIDER_KEY_ENV[backend],
    };
  }

  private buildHtml(): string {
    const distPath = vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist");
    let rawHtml: string;
    try {
      rawHtml = fs.readFileSync(vscode.Uri.joinPath(distPath, "setup.html").fsPath, "utf8");
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      return `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Setup</title></head>
<body style="background:#1e1e1e;color:#ccc;font-family:sans-serif;padding:2em">
  <p>Setup webview build is missing.</p>
  <pre>Run: npm run -w crucible-vscode-extension build</pre>
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
