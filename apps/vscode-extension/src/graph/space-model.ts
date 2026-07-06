// vscode-free. Turns a raw index-snapshot into the compact SpaceModel the webview renders.
// The webview never sees the raw graph: Variable nodes and ambient References edges are
// dropped here; per-node detail is served lazily by snapshot-store.ts.

export interface RawGraphNode {
  id: string;
  path: string;
  name: string;
  kind: string;
  line?: number;
}
export interface RawGraphEdge {
  from: string;
  to: string;
  kind: string;
}
export interface RawSnapshot {
  workspace_root: string;
  generated_at_ms?: number;
  graph: { nodes: RawGraphNode[]; edges: RawGraphEdge[] };
}

export type EdgeKind = "Imports" | "Calls" | "Inherits" | "References";

export interface StarRecord {
  /** Workspace-relative file path — THE stable key across layout/diff/messages. */
  id: string;
  /** Package id ("" = orphan drifting between nebulae). */
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
/** Intra-package file↔file link (a < b lexically); layout springs + L1 detail. */
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

const AMBIENT_KINDS = new Set<string>(["Imports", "Calls", "Inherits"]);
const SYMBOL_KINDS = new Set(["Class", "Function", "Method", "Interface"]);
const GROUP_DIRS = new Set(["apps", "services", "packages", "libs", "crates"]);
const MIN_PKG_FILES = 3;

export function relPath(abs: string, root: string): string {
  const prefix = root.endsWith("/") ? root : root + "/";
  return abs.startsWith(prefix) ? abs.slice(prefix.length) : abs;
}

function dirOf(rel: string): string {
  const i = rel.lastIndexOf("/");
  return i === -1 ? "" : rel.slice(0, i);
}

/** Candidate package for a path — before the MIN_PKG_FILES orphan demotion pass. */
export function packageCandidate(rel: string): string {
  const segs = rel.split("/");
  const first = segs[0];
  const second = segs[1];
  if (!first || segs.length < 2) return "";
  if (GROUP_DIRS.has(first)) return segs.length >= 3 && second ? `${first}/${second}` : "";
  return first;
}

export interface FileEdgeRecord {
  fromFile: string;
  toFile: string;
  kind: EdgeKind;
}

/** Resolve every ambient edge to a (fromFile, toFile) pair of workspace-relative paths.
 * Shared with bundling; exported for tests. */
export function resolveFileEdges(snapData: RawSnapshot): FileEdgeRecord[] {
  const root = snapData.workspace_root;
  const fileSet = new Set<string>();
  const nodeFile = new Map<string, string>(); // node id -> rel file path
  for (const n of snapData.graph.nodes) {
    if (n.id.startsWith("external:")) continue; // external nodes carry the importer's path — never map them
    const rel = relPath(n.path, root);
    nodeFile.set(n.id, rel);
    if (n.kind === "File") fileSet.add(rel);
  }
  const out: FileEdgeRecord[] = [];
  for (const e of snapData.graph.edges) {
    if (!AMBIENT_KINDS.has(e.kind)) continue;
    const fromFile = nodeFile.get(e.from);
    if (!fromFile) continue;
    let toFile: string | null = nodeFile.get(e.to) ?? null;
    if (!toFile && e.to.startsWith("external:module:")) {
      toFile = resolveModuleSpec(e.to.slice("external:module:".length), fromFile, fileSet);
    }
    if (!toFile || toFile === fromFile) continue;
    out.push({ fromFile, toFile, kind: e.kind as EdgeKind });
  }
  return out;
}

/** Best-effort resolution of a relative import spec to a workspace file.
 * Bare package specs (no leading '.') stay external -> null.
 * NOTE: stub for now — implemented in the bundling task. */
export function resolveModuleSpec(
  spec: string,
  fromFileRel: string,
  fileSet: Set<string>
): string | null {
  void spec;
  void fromFileRel;
  void fileSet;
  return null;
}

export function buildSpaceModel(snapData: RawSnapshot): SpaceModel {
  const root = snapData.workspace_root;
  const stars = new Map<string, StarRecord>();
  for (const n of snapData.graph.nodes) {
    if (n.kind !== "File" || n.id.startsWith("external:")) continue;
    const rel = relPath(n.path, root);
    if (stars.has(rel)) continue;
    stars.set(rel, {
      id: rel,
      pkg: packageCandidate(rel),
      dir: dirOf(rel),
      symbolCount: 0,
      inDeg: 0,
      outDeg: 0,
      kindMix: {},
      isEntry: false,
      isHub: false,
    });
  }

  for (const n of snapData.graph.nodes) {
    if (!SYMBOL_KINDS.has(n.kind) || n.id.startsWith("external:")) continue;
    const star = stars.get(relPath(n.path, root));
    if (star) star.symbolCount += 1;
  }

  const fileEdges = resolveFileEdges(snapData);
  for (const fe of fileEdges) {
    const from = stars.get(fe.fromFile);
    const to = stars.get(fe.toFile);
    if (!from || !to) continue;
    from.outDeg += 1;
    to.inDeg += 1;
    from.kindMix[fe.kind] = (from.kindMix[fe.kind] ?? 0) + 1;
    to.kindMix[fe.kind] = (to.kindMix[fe.kind] ?? 0) + 1;
  }

  // Orphan demotion: candidate packages with < MIN_PKG_FILES files dissolve.
  const pkgCounts = new Map<string, number>();
  for (const s of stars.values()) {
    if (s.pkg) pkgCounts.set(s.pkg, (pkgCounts.get(s.pkg) ?? 0) + 1);
  }
  for (const s of stars.values()) {
    if (s.pkg && (pkgCounts.get(s.pkg) ?? 0) < MIN_PKG_FILES) s.pkg = "";
  }

  const packages = new Map<string, PackageInfo>();
  for (const s of stars.values()) {
    if (!s.pkg) continue;
    let p = packages.get(s.pkg);
    if (!p) {
      p = { id: s.pkg, fileCount: 0, dirs: [] };
      packages.set(s.pkg, p);
    }
    p.fileCount += 1;
    if (!p.dirs.includes(s.dir)) p.dirs.push(s.dir);
  }
  for (const p of packages.values()) p.dirs.sort();

  const model: SpaceModel = {
    workspaceRoot: root,
    generatedAtMs: snapData.generated_at_ms ?? 0,
    packages: [...packages.values()].sort((a, b) => a.id.localeCompare(b.id)),
    stars: [...stars.values()].sort((a, b) => a.id.localeCompare(b.id)),
    bundles: [],
    intraBundles: [],
    links: [],
  };
  return model;
}
