import * as fs from "node:fs";
import * as vscode from "vscode";
import { handleMemoryMessage, type MemoryDataSource, type MemoryToHost } from "./memory-data.js";

/** Dedicated read-only memory-inspector webview. Loads the webview-ui `memory` Vite entry
 * (dist/memory.html) and wires its messages to a MemoryDataSource. Mirrors chat-panel.ts. */
export class MemoryPanel {
  private panel: vscode.WebviewPanel | null = null;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly source: MemoryDataSource,
    private readonly threadId: string,
    private readonly workspacePath: string
  ) {}

  open(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Two);
      return;
    }
    this.panel = vscode.window.createWebviewPanel(
      "aiEditorMemory",
      "AI Editor: Memory",
      vscode.ViewColumn.Two,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist")],
      }
    );
    this.panel.webview.html = this.buildHtml();
    this.panel.webview.onDidReceiveMessage((msg: unknown) =>
      handleMemoryMessage(msg as MemoryToHost, this.source, this.threadId, this.workspacePath, (m) =>
        this.panel?.webview.postMessage(m)
      )
    );
    this.panel.onDidDispose(() => {
      this.panel = null;
    });
  }

  private buildHtml(): string {
    const distPath = vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist");
    let rawHtml: string;
    try {
      rawHtml = fs.readFileSync(vscode.Uri.joinPath(distPath, "memory.html").fsPath, "utf8");
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      return `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Memory</title></head>
<body style="background:#1e1e1e;color:#ccc;font-family:sans-serif;padding:2em">
  <p>Memory webview build is missing.</p>
  <pre>Run: npm run -w @ai-editor/vscode-extension build</pre>
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
