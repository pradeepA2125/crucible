import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { handleGraphMessage, type GraphHostDeps, type GraphToHost } from "./graph/graph-messages.js";
import { GraphSnapshotStore } from "./graph/snapshot-store.js";

const STALE_AFTER_SEC = 900; // mirrors CRUCIBLE_RETRIEVAL_MAX_AGE_SEC default
const WATCH_DEBOUNCE_MS = 400;

/** AXON dependency-space panel. Loads the webview-ui `graph` Vite entry and serves
 * SpaceModel + lazy detail from the workspace snapshot. Mirrors memory-panel.ts. */
export class GraphPanel {
  private panel: vscode.WebviewPanel | null = null;
  private watcher: fs.FSWatcher | null = null;
  private debounce: ReturnType<typeof setTimeout> | null = null;
  private readonly store: GraphSnapshotStore;
  private readonly snapshotPath: string;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly workspacePath: string,
    private readonly backendBaseUrl: string
  ) {
    this.snapshotPath = path.join(workspacePath, ".crucible", "index-snapshot.json");
    this.store = new GraphSnapshotStore(this.snapshotPath);
  }

  open(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Two);
      return;
    }
    this.panel = vscode.window.createWebviewPanel("crucibleGraph", "AXON: Dependency Space", vscode.ViewColumn.Two, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist")],
    });
    this.panel.webview.html = this.buildHtml();
    const deps = this.buildDeps();
    this.panel.webview.onDidReceiveMessage((msg: unknown) =>
      handleGraphMessage(msg as GraphToHost, deps, (m) => this.panel?.webview.postMessage(m))
    );
    this.startWatcher();
    this.panel.onDidDispose(() => {
      this.stopWatcher();
      this.panel = null;
    });
  }

  private buildDeps(): GraphHostDeps {
    return {
      loadModel: () => this.store.load(),
      staleAgeSec: () => {
        const gen = this.store.generatedAtMs();
        if (!gen) return null;
        const age = Math.floor((Date.now() - gen) / 1000);
        return age > STALE_AFTER_SEC ? age : null;
      },
      fileDetail: (id) => this.store.fileDetail(id),
      symbolDetail: (id) => this.store.symbolDetail(id),
      searchSymbols: (q) => this.store.searchSymbols(q),
      openFile: async (rel, line) => {
        const uri = vscode.Uri.file(path.join(this.workspacePath, rel));
        const doc = await vscode.workspace.openTextDocument(uri);
        const editor = await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.One });
        if (line && line > 0) {
          const pos = new vscode.Position(line - 1, 0);
          editor.selection = new vscode.Selection(pos, pos);
          editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
        }
      },
      buildIndex: async () => {
        // The route 422s without a JSON body — workspace_path is required.
        await fetch(new URL("/v1/index/build", this.backendBaseUrl), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace_path: this.workspacePath }),
        });
        // .crucible/ may not have existed when the panel opened (fs.watch on a missing
        // dir throws) — re-arm so the snapshot ignites the space when the build lands.
        this.stopWatcher();
        this.startWatcher();
      },
    };
  }

  private startWatcher(): void {
    const dir = path.dirname(this.snapshotPath);
    try {
      this.watcher = fs.watch(dir, (_event, name) => {
        if (name !== "index-snapshot.json") return;
        if (this.debounce) clearTimeout(this.debounce);
        this.debounce = setTimeout(() => {
          try {
            const { model, diff } = this.store.reload();
            if (diff) this.panel?.webview.postMessage({ type: "spaceDiff", diff });
            else this.panel?.webview.postMessage({ type: "space", model, staleAgeSec: null });
          } catch {
            // snapshot mid-rewrite or gone — the next ready/refresh will surface state
          }
        }, WATCH_DEBOUNCE_MS);
      });
    } catch {
      this.watcher = null; // watch failure degrades to manual refresh, never breaks the panel
    }
  }

  private stopWatcher(): void {
    if (this.debounce) clearTimeout(this.debounce);
    this.watcher?.close();
    this.watcher = null;
  }

  private buildHtml(): string {
    const distPath = vscode.Uri.joinPath(this.extensionUri, "webview-ui", "dist");
    let rawHtml: string;
    try {
      rawHtml = fs.readFileSync(vscode.Uri.joinPath(distPath, "graph.html").fsPath, "utf8");
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      return `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>AXON</title></head>
<body style="background:#070203;color:#ccc;font-family:sans-serif;padding:2em">
  <p>Graph webview build is missing.</p>
  <pre>Run: npm run -w crucible-vscode-extension build</pre>
  <p style="color:#888">${errMsg}</p>
</body></html>`;
    }
    const nonce = Array.from({ length: 16 }, () =>
      Math.floor(Math.random() * 256)
        .toString(16)
        .padStart(2, "0")
    ).join("");
    const cspSource = this.panel!.webview.cspSource;
    let html = rawHtml.replace(/(src|href)="\.\/(assets\/[^"]+)"/g, (_m, attr: string, assetPath: string) => {
      const uri = this.panel!.webview.asWebviewUri(vscode.Uri.joinPath(distPath, assetPath));
      return `${attr}="${uri}"`;
    });
    html = html.replace(/<script(?=[\s>])/g, `<script nonce="${nonce}"`);
    html = html.replace(
      "<head>",
      `<head>\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${cspSource}; script-src 'nonce-${nonce}' ${cspSource}; worker-src ${cspSource} blob:; img-src ${cspSource} data:; font-src ${cspSource}; connect-src ${cspSource};">`
    );
    return html;
  }
}
