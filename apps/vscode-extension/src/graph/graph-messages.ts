// vscode-free message handler — GraphPanel (graph-panel.ts) routes webview messages here.
import { GraphSnapshotError, type FileDetail, type SymbolDetail, type SymbolHit } from "./snapshot-store.js";
import type { SpaceDiff, SpaceModel } from "./space-model.js";

export type GraphToHost =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "fileDetail"; fileId: string }
  | { type: "symbolDetail"; symbolId: string }
  | { type: "searchSymbols"; query: string }
  | { type: "openFile"; path: string; line?: number }
  | { type: "buildIndex" };

export type GraphToWebview =
  | { type: "space"; model: SpaceModel; staleAgeSec: number | null }
  | { type: "spaceDiff"; diff: SpaceDiff }
  | { type: "fileDetail"; detail: FileDetail }
  | { type: "symbolDetail"; detail: SymbolDetail }
  | { type: "symbolHits"; query: string; hits: SymbolHit[] }
  | { type: "noSnapshot"; reason: "missing" | "malformed"; message: string; building: boolean }
  | { type: "error"; message: string };

export interface GraphHostDeps {
  loadModel(): SpaceModel;
  staleAgeSec(): number | null;
  fileDetail(id: string): FileDetail;
  symbolDetail(id: string): SymbolDetail;
  searchSymbols(q: string): SymbolHit[];
  openFile(path: string, line?: number): Promise<void>;
  buildIndex(): Promise<void>;
}

function postSpaceOrEmpty(deps: GraphHostDeps, post: (m: GraphToWebview) => void, building: boolean): void {
  try {
    post({ type: "space", model: deps.loadModel(), staleAgeSec: deps.staleAgeSec() });
  } catch (err) {
    if (err instanceof GraphSnapshotError) {
      post({ type: "noSnapshot", reason: err.code, message: err.message, building });
    } else {
      post({ type: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }
}

export async function handleGraphMessage(
  msg: GraphToHost,
  deps: GraphHostDeps,
  post: (m: GraphToWebview) => void
): Promise<void> {
  try {
    if (msg.type === "ready" || msg.type === "refresh") {
      postSpaceOrEmpty(deps, post, false);
    } else if (msg.type === "buildIndex") {
      await deps.buildIndex();
      postSpaceOrEmpty(deps, post, true);
    } else if (msg.type === "fileDetail") {
      post({ type: "fileDetail", detail: deps.fileDetail(msg.fileId) });
    } else if (msg.type === "symbolDetail") {
      post({ type: "symbolDetail", detail: deps.symbolDetail(msg.symbolId) });
    } else if (msg.type === "searchSymbols") {
      post({ type: "symbolHits", query: msg.query, hits: deps.searchSymbols(msg.query) });
    } else if (msg.type === "openFile") {
      await deps.openFile(msg.path, msg.line);
    }
  } catch (err) {
    post({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
}
