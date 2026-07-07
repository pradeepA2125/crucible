// vscode-free. Holds the full parsed snapshot in memory while the panel is open and
// serves the lazy queries the webview asks for on selection. Released with the panel.
import { readFileSync } from "node:fs";
import {
  buildSpaceModel,
  buildSpecResolver,
  diffSpaceModel,
  relPath,
  type EdgeKind,
  type RawGraphNode,
  type RawSnapshot,
  type SpaceDiff,
  type SpaceModel,
} from "./space-model.js";

export interface SymbolInfo {
  id: string;
  name: string;
  kind: string;
  line: number;
}
export interface FileEdge {
  dir: "out" | "in";
  kind: EdgeKind;
  otherFile: string;
  crossPackage: boolean;
  symbolName?: string | undefined;
  line?: number | undefined;
}
export interface FileDetail {
  fileId: string;
  symbols: SymbolInfo[];
  edges: FileEdge[];
  withinFileCount: number;
}
export interface SymbolEdge {
  dir: "out" | "in";
  kind: EdgeKind;
  name: string;
  fileId: string | null;
  line?: number | undefined;
}
export interface SymbolDetail {
  symbolId: string;
  edges: SymbolEdge[];
}
export interface SymbolHit {
  symbolId: string;
  name: string;
  kind: string;
  fileId: string;
  line: number;
}

const ALL_KINDS = new Set<string>(["Imports", "Calls", "Inherits", "References"]);
const SYMBOL_KINDS = new Set(["Class", "Function", "Method", "Interface"]);

export class GraphSnapshotError extends Error {
  constructor(
    readonly code: "missing" | "malformed",
    message: string
  ) {
    super(message);
    this.name = "GraphSnapshotError";
  }
}

export class GraphSnapshotStore {
  private raw: RawSnapshot | null = null;
  private _model: SpaceModel | null = null;
  private nodeById = new Map<string, RawGraphNode>();
  private fileOfNode = new Map<string, string>();
  private pkgOfFile = new Map<string, string>();
  private resolveSpec: ((spec: string, fromFileRel: string) => string | null) | null = null;

  constructor(private readonly snapshotPath: string) {}

  load(): SpaceModel {
    let text: string;
    try {
      text = readFileSync(this.snapshotPath, "utf8");
    } catch (err) {
      throw new GraphSnapshotError("missing", `snapshot not found at ${this.snapshotPath}: ${String(err)}`);
    }
    let parsed: RawSnapshot;
    try {
      parsed = JSON.parse(text) as RawSnapshot;
      if (!parsed?.graph?.nodes || !parsed?.graph?.edges) throw new Error("missing graph.nodes/edges");
    } catch (err) {
      throw new GraphSnapshotError("malformed", `snapshot unreadable at ${this.snapshotPath}: ${String(err)}`);
    }
    this.raw = parsed;
    this._model = buildSpaceModel(parsed);
    this.nodeById.clear();
    this.fileOfNode.clear();
    for (const n of parsed.graph.nodes) {
      this.nodeById.set(n.id, n);
      if (!n.id.startsWith("external:")) this.fileOfNode.set(n.id, relPath(n.path, parsed.workspace_root));
    }
    this.pkgOfFile = new Map(this._model.stars.map((s) => [s.id, s.pkg]));
    this.resolveSpec = buildSpecResolver(new Set(this._model.stars.map((s) => s.id)));
    return this._model;
  }

  reload(): { model: SpaceModel; diff: SpaceDiff | null } {
    const prev = this._model;
    const model = this.load();
    return { model, diff: prev ? diffSpaceModel(prev, model) : null };
  }

  model(): SpaceModel | null {
    return this._model;
  }

  generatedAtMs(): number | null {
    return this.raw?.generated_at_ms ?? null;
  }

  fileDetail(fileId: string): FileDetail {
    const raw = this.mustRaw();
    const symbols: SymbolInfo[] = [];
    for (const n of raw.graph.nodes) {
      if (!SYMBOL_KINDS.has(n.kind) || n.id.startsWith("external:")) continue;
      if (this.fileOfNode.get(n.id) === fileId) {
        symbols.push({ id: n.id, name: n.name, kind: n.kind, line: n.line ?? 1 });
      }
    }
    symbols.sort((a, b) => a.line - b.line);
    const edges: FileEdge[] = [];
    let withinFileCount = 0;
    const myPkg = this.pkgOfFile.get(fileId) ?? "";
    for (const e of raw.graph.edges) {
      if (!ALL_KINDS.has(e.kind)) continue;
      const kind = e.kind as EdgeKind;
      const fromFile = this.fileOfNode.get(e.from);
      let toFile = this.fileOfNode.get(e.to);
      // Unresolved import targets (external:module:<spec>) resolve exactly like the
      // model build does — otherwise a hub's info card shows zero edges on LSP-off snapshots.
      if (!toFile && fromFile && this.resolveSpec && e.to.startsWith("external:module:")) {
        toFile = this.resolveSpec(e.to.slice("external:module:".length), fromFile) ?? undefined;
      }
      if (fromFile === fileId && toFile === fileId) {
        withinFileCount += 1;
      } else if (fromFile === fileId && toFile) {
        edges.push(this.mkFileEdge("out", kind, toFile, myPkg, e.from));
      } else if (toFile === fileId && fromFile) {
        edges.push(this.mkFileEdge("in", kind, fromFile, myPkg, e.to));
      }
    }
    return { fileId, symbols, edges, withinFileCount };
  }

  private mkFileEdge(
    dir: "out" | "in",
    kind: EdgeKind,
    otherFile: string,
    myPkg: string,
    mySymbolNodeId: string
  ): FileEdge {
    const otherPkg = this.pkgOfFile.get(otherFile) ?? "";
    const symNodeRaw = this.nodeById.get(mySymbolNodeId);
    return {
      dir,
      kind,
      otherFile,
      crossPackage: myPkg !== otherPkg,
      symbolName: symNodeRaw && symNodeRaw.kind !== "File" ? symNodeRaw.name : undefined,
      line: symNodeRaw?.line,
    };
  }

  symbolDetail(symbolId: string): SymbolDetail {
    const raw = this.mustRaw();
    const edges: SymbolEdge[] = [];
    for (const e of raw.graph.edges) {
      if (!ALL_KINDS.has(e.kind)) continue;
      const kind = e.kind as EdgeKind;
      if (e.from !== symbolId && e.to !== symbolId) continue;
      const dir: "out" | "in" = e.from === symbolId ? "out" : "in";
      const otherId = dir === "out" ? e.to : e.from;
      const other = this.nodeById.get(otherId);
      const fileId = this.fileOfNode.get(otherId) ?? null;
      const name = other?.name ?? otherId.slice(otherId.lastIndexOf(":") + 1);
      edges.push({ dir, kind, name, fileId, line: other?.line });
    }
    return { symbolId, edges };
  }

  searchSymbols(query: string, limit = 20): SymbolHit[] {
    const raw = this.mustRaw();
    const q = query.toLowerCase();
    const hits: SymbolHit[] = [];
    if (!q) return hits;
    for (const n of raw.graph.nodes) {
      if (!SYMBOL_KINDS.has(n.kind) || n.id.startsWith("external:")) continue;
      if (!n.name.toLowerCase().includes(q)) continue;
      const fileId = this.fileOfNode.get(n.id);
      if (!fileId) continue;
      hits.push({ symbolId: n.id, name: n.name, kind: n.kind, fileId, line: n.line ?? 1 });
      if (hits.length >= limit) break;
    }
    return hits;
  }

  private mustRaw(): RawSnapshot {
    if (!this.raw) throw new GraphSnapshotError("missing", "snapshot not loaded — call load() first");
    return this.raw;
  }
}
