import * as fs from "node:fs";
import * as vscode from "vscode";

import type { BackendClientFactory } from "./controller.js";
import type { RuntimeManager } from "./runtime/vscode-runtime.js";
import { createHostSetupDeps } from "./host-setup-deps.js";
import { createSetupHandler, type SetupDeps, type SetupInMsg } from "./setup-data.js";
import { injectThemeAttributes, themeHub, themeReadyReply } from "./theme-settings.js";

/** First-run setup wizard: install runtime, pick a provider, start the backend.
 * Loads the webview-ui `setup` Vite entry (dist/setup.html). Mirrors memory-panel.ts. */
export class SetupPanel {
  private panel: vscode.WebviewPanel | null = null;
  private themeSubscription: (() => void) | null = null;

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
    this.themeSubscription?.();
    this.themeSubscription = themeHub.subscribe((message) => {
      void this.panel?.webview.postMessage(message);
    });
    const handle = createSetupHandler(this.buildDeps(), (msg) => this.panel?.webview.postMessage(msg));
    this.panel.webview.onDidReceiveMessage((msg: unknown) => {
      const themeReply = themeReadyReply(msg, themeHub);
      if (themeReply) {
        void this.panel?.webview.postMessage(themeReply);
        return;
      }
      void handle(msg as SetupInMsg);
    });
    this.panel.onDidDispose(() => {
      this.themeSubscription?.();
      this.themeSubscription = null;
      this.panel = null;
    });
  }

  private buildDeps(): SetupDeps {
    return createHostSetupDeps({ runtime: this.runtimeManager, workspace: this.workspacePath,
      clientFactory: this.clientFactory, openChat: this.openChatCommand,
      onBackendUrl: this.setManagedBackendUrl, openExternal: async url => {
        await vscode.env.openExternal(vscode.Uri.parse(url));
      }, openClaudeExternal: async url => {
        if (!await vscode.env.openExternal(vscode.Uri.parse(url))) throw new Error("The browser could not be opened. Try connecting Claude again.");
      } });
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
    return injectThemeAttributes(html, themeHub.settings);
  }
}
