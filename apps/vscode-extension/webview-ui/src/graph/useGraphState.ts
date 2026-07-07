import type { EdgeKind, FileDetail, FocusState, SymbolDetail, SymbolHit } from "./types";

export interface GraphUiState {
  focus: FocusState;
  layers: Record<EdgeKind, boolean>;
  fileDetail: FileDetail | null;
  symbolDetail: SymbolDetail | null;
  searchHits: SymbolHit[];
  error: string | null;
}

export type GraphAction =
  | { type: "pickPackage"; pkg: string }
  | { type: "pickStar"; fileId: string; pkg: string }
  | { type: "dive" }
  | { type: "pickSymbol"; symbolId: string }
  | { type: "pop" }
  | { type: "reset" }
  | { type: "setLayer"; kind: EdgeKind; on: boolean }
  | { type: "hostFileDetail"; detail: FileDetail }
  | { type: "hostSymbolDetail"; detail: SymbolDetail }
  | { type: "hostSymbolHits"; hits: SymbolHit[] }
  | { type: "hostError"; message: string };

export function initialGraphState(): GraphUiState {
  return {
    focus: { level: 0 },
    layers: { Imports: true, Calls: true, Inherits: true, References: false },
    fileDetail: null,
    symbolDetail: null,
    searchHits: [],
    error: null,
  };
}

function popped(f: FocusState): FocusState {
  if (f.level === 3) {
    if (f.symbolId !== null) return { ...f, symbolId: null };
    return { level: 2, pkg: f.pkg, fileId: f.fileId };
  }
  if (f.level === 2) return { level: 1, pkg: f.pkg };
  return { level: 0 };
}

export function graphReducer(s: GraphUiState, a: GraphAction): GraphUiState {
  switch (a.type) {
    case "pickPackage":
      return { ...s, focus: { level: 1, pkg: a.pkg }, fileDetail: null, symbolDetail: null };
    case "pickStar":
      return { ...s, focus: { level: 2, pkg: a.pkg, fileId: a.fileId }, fileDetail: null, symbolDetail: null };
    case "dive": {
      if (s.focus.level !== 2) return s;
      return { ...s, focus: { level: 3, pkg: s.focus.pkg, fileId: s.focus.fileId, symbolId: null } };
    }
    case "pickSymbol": {
      if (s.focus.level !== 3) return s;
      return { ...s, focus: { ...s.focus, symbolId: a.symbolId }, symbolDetail: null };
    }
    case "pop": {
      const focus = popped(s.focus);
      const layers = focus.level < 2 ? { ...s.layers, References: false } : s.layers;
      return { ...s, focus, layers, ...(focus.level < 2 ? { fileDetail: null, symbolDetail: null } : {}) };
    }
    case "reset":
      return { ...initialGraphState() };
    case "setLayer": {
      if (a.kind === "References" && a.on && s.focus.level < 2) return s;
      return { ...s, layers: { ...s.layers, [a.kind]: a.on } };
    }
    case "hostFileDetail": {
      // NOTE: `level >= 2` would NOT narrow the discriminated union — use explicit discriminants.
      const want = s.focus.level === 2 || s.focus.level === 3 ? s.focus.fileId : null;
      return a.detail.fileId === want ? { ...s, fileDetail: a.detail } : s;
    }
    case "hostSymbolDetail":
      return { ...s, symbolDetail: a.detail };
    case "hostSymbolHits":
      return { ...s, searchHits: a.hits };
    case "hostError":
      return { ...s, error: a.message };
  }
}
