// Local mirrors of the host's graph types (webview never imports extension src).
export type EdgeKind = "Imports" | "Calls" | "Inherits" | "References";

export interface StarRecord {
  id: string;
  pkg: string;
  dir: string;
  symbolCount: number;
  inDeg: number;
  outDeg: number;
  kindMix: Partial<Record<EdgeKind, number>>;
  isEntry: boolean;
  isHub: boolean;
}
export interface PackageInfo {
  id: string;
  fileCount: number;
  dirs: string[];
}
export interface FileLink {
  a: string;
  b: string;
  count: number;
}
export interface Bundle {
  fromPkg: string;
  toPkg: string;
  count: number;
  kindMix: Partial<Record<EdgeKind, number>>;
}
export interface IntraBundle {
  pkg: string;
  fromDir: string;
  toDir: string;
  count: number;
}
export interface SpaceModel {
  workspaceRoot: string;
  generatedAtMs: number;
  packages: PackageInfo[];
  stars: StarRecord[];
  bundles: Bundle[];
  intraBundles: IntraBundle[];
  links: FileLink[];
}
export interface SpaceDiff {
  added: StarRecord[];
  removed: string[];
  changed: StarRecord[];
  packages: PackageInfo[];
  bundles: Bundle[];
  intraBundles: IntraBundle[];
  links: FileLink[];
}
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
  symbolName?: string;
  line?: number;
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
  line?: number;
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

export type GraphToWebview =
  | { type: "space"; model: SpaceModel; staleAgeSec: number | null }
  | { type: "spaceDiff"; diff: SpaceDiff }
  | { type: "fileDetail"; detail: FileDetail }
  | { type: "symbolDetail"; detail: SymbolDetail }
  | { type: "symbolHits"; query: string; hits: SymbolHit[] }
  | { type: "noSnapshot"; reason: "missing" | "malformed"; message: string; building: boolean }
  | { type: "error"; message: string };
export type GraphToHost =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "fileDetail"; fileId: string }
  | { type: "symbolDetail"; symbolId: string }
  | { type: "searchSymbols"; query: string }
  | { type: "openFile"; path: string; line?: number }
  | { type: "buildIndex" };

export type FocusState =
  | { level: 0 }
  | { level: 1; pkg: string }
  | { level: 2; pkg: string; fileId: string }
  | { level: 3; pkg: string; fileId: string; symbolId: string | null };

export interface LayoutResult {
  ids: string[];
  positions: Float32Array;
}

export interface SceneCallbacks {
  onPickStar(id: string): void;
  onPickPackage(pkg: string): void;
  onPickSatellite(symbolId: string, line?: number): void;
  onBackgroundClick(): void;
}
/** Implemented by scene/graph-scene.ts. GraphApp only talks through this. */
export interface SceneHandle {
  setSpace(model: SpaceModel, layout: LayoutResult): void;
  morph(model: SpaceModel, layout: LayoutResult, removed: string[]): void;
  setFocus(focus: FocusState): void;
  showFileTrace(detail: FileDetail): void;
  showSatellites(detail: FileDetail): void;
  showSymbolTrace(detail: SymbolDetail): void;
  clearOverlays(): void;
  setLayers(layers: Record<EdgeKind, boolean>): void;
  flyToStar(id: string, radius?: number): void;
  /** Ride a lit trace thread to a target star; falls back to flyTo when no thread
   * is lit for that id. onArrive always fires (immediately on the fallback). */
  rideToStar(id: string, onArrive?: () => void): boolean;
  /** Ride a package-to-package beam; false (and immediate onArrive) when absent. */
  rideBeam(fromPkg: string, toPkg: string, onArrive?: () => void): boolean;
  framePackage(pkg: string): void;
  resetCamera(): void;
  dispose(): void;
}
