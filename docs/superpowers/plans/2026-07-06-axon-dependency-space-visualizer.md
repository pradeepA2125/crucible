# AXON — 3D Dependency Space Visualizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A VS Code webview panel that renders `index-snapshot.json` as an explorable 3D space — packages as nebulae, files as stars, dependencies as flowing particle beams — with a 4-level focus stack (space → package → file → symbol), search-warp, open-in-editor, and live snapshot morphing.

**Architecture:** Host side mirrors the MemoryPanel pattern: vscode-free preprocessing (`src/graph/`) turns the raw snapshot into a compact `SpaceModel` (~1.2k stars, not 61k nodes) plus lazy detail queries; `graph-panel.ts` does webview plumbing + snapshot watching. Webview side is a new Vite entry (`graph.html` → `src/graph/`): React renders HUD only; a Three.js scene (instanced stars, GPU beam particles, bloom) is owned imperatively; layout runs in a Web Worker with id-hash-seeded determinism.

**Tech Stack:** TypeScript, React 18, Three.js (`three` + addons postprocessing), Vite multi-entry, vitest (node env for host, jsdom for webview), Web Worker.

**Spec:** `docs/superpowers/specs/2026-07-06-dependency-space-visualizer-design.md` — read it first; it defines the palette (Ember Dusk), the focus-stack semantics, and the failure modes. The approved motion study is `.superpowers/brainstorm/29677-1783313740/content/axon-design-language.html` — open it in a browser to calibrate the visual bar before writing scene code.

## Global Constraints

- Palette is **Ember Dusk only** (constants in Task 9); no palette switcher in the product.
- `Variable` nodes and ambient `References` edges never enter `SpaceModel`; References surface only in lazy detail queries and the References layer is off+disabled at focus levels 0/1.
- Star ids are **workspace-relative file paths** — the stable key for layout seeding, diffing, and messages.
- Host `src/graph/*.ts` files must stay vscode-free (extension vitest is node-env with no vscode module). Only `graph-panel.ts` and `extension.ts` may import `vscode`.
- `webview-ui/src/graph/` keeps **local mirror types** (it never imports the extension's `src/`) — same convention as `src/memory/types.ts`.
- The chat Vite bundle must not grow: `three` is imported only by `src/graph/` modules (verify `dist/assets/index.js` size is unchanged in Task 7).
- Extension tests: `apps/vscode-extension/test/*.test.ts` (node env). Webview tests: colocated `*.test.ts(x)` under `webview-ui/src/graph/` (jsdom env).
- Commit after every task; message format `feat(graph): …` / `test(graph): …`.
- All commits carry the standard trailer:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## File Structure

**Host (apps/vscode-extension):**
- `src/graph/space-model.ts` — snapshot types, `buildSpaceModel`, `resolveModuleSpec`, entry/hub detection, `diffSpaceModel` (Tasks 1–3)
- `src/graph/snapshot-store.ts` — `GraphSnapshotStore`: file load/reload, lazy `fileDetail`/`symbolDetail`/`searchSymbols` (Task 4)
- `src/graph/graph-messages.ts` — webview⇄host message unions + `handleGraphMessage` (Task 5)
- `src/graph-panel.ts` — `GraphPanel` webview class + snapshot watcher (Task 6)
- `test/graph-fixtures.ts`, `test/graph-space-model.test.ts`, `test/graph-snapshot-store.test.ts`, `test/graph-messages.test.ts`

**Webview (apps/vscode-extension/webview-ui):**
- `graph.html` + `src/graph/main.tsx` + `src/graph/vscodeApi.ts` — entry (Task 7)
- `src/graph/types.ts` — local mirrors of SpaceModel/messages (Task 7)
- `src/graph/GraphApp.tsx` — shell: message bus, worker, scene mount, HUD composition (Tasks 7, 12–14)
- `src/graph/useGraphState.ts` — focus-stack reducer (Task 12)
- `src/graph/layout.ts` + `src/graph/layout.worker.ts` — deterministic layout (Task 8)
- `src/graph/palette.ts`, `src/graph/scene-math.ts` — Ember constants + pure scene math (Task 9)
- `src/graph/scene/camera.ts` — CameraRig (Task 9)
- `src/graph/scene/graph-scene.ts` — GraphScene orchestrator + picking + labels (Task 10)
- `src/graph/scene/starfield.ts` — stars/nebulae/dust (Task 10)
- `src/graph/scene/flows.ts` — beams/threads/satellites (Task 11)
- `src/graph/hud/{Breadcrumb,Legend,EdgeLayers,InfoCard,SearchBar,EmptyState}.tsx` (Tasks 7, 12–13)

---

### Task 1: SpaceModel core — types, filtering, rollups, packages, orphans

**Files:**
- Create: `apps/vscode-extension/src/graph/space-model.ts`
- Create: `apps/vscode-extension/test/graph-fixtures.ts`
- Test: `apps/vscode-extension/test/graph-space-model.test.ts`

**Interfaces:**
- Consumes: nothing (first task).
- Produces (used by every later task): the types `RawSnapshot`, `RawGraphNode`, `RawGraphEdge`, `EdgeKind`, `StarRecord`, `PackageInfo`, `FileLink`, `Bundle`, `IntraBundle`, `SpaceModel` and the function `buildSpaceModel(snap: RawSnapshot): SpaceModel` — exactly as written below.

Snapshot ground truth (verified against real snapshots): top level is `{workspace_root, generated_at_ms, graph: {nodes, edges}}`. Node ids look like `file:<abs>`, `class:file:<abs>:Name`, `function:file:<abs>:name`, `external:module:<spec>`, `external:call:<name>`. **Every node carries `path` = an absolute file path, but `external:*` nodes carry the *importer's* path — never map external ids through `path`.**

- [ ] **Step 1: Write the test fixtures helper**

```typescript
// apps/vscode-extension/test/graph-fixtures.ts
import type { RawGraphEdge, RawGraphNode, RawSnapshot } from "../src/graph/space-model.js";

export const ROOT = "/ws";

export function fileNode(rel: string): RawGraphNode {
  return { id: `file:${ROOT}/${rel}`, path: `${ROOT}/${rel}`, name: rel.split("/").pop()!, kind: "File" };
}

export function symNode(kind: string, rel: string, name: string, line = 1): RawGraphNode {
  return { id: `${kind.toLowerCase()}:file:${ROOT}/${rel}:${name}`, path: `${ROOT}/${rel}`, name, kind, line };
}

/** external:module node — NOTE: carries the IMPORTER's path (matches real snapshots). */
export function extModule(spec: string, importerRel: string): RawGraphNode {
  return { id: `external:module:${spec}`, path: `${ROOT}/${importerRel}`, name: spec, kind: "Module" };
}

export function edge(from: string, to: string, kind: string): RawGraphEdge {
  return { from, to, kind };
}

export function snap(nodes: RawGraphNode[], edges: RawGraphEdge[]): RawSnapshot {
  return { workspace_root: ROOT, generated_at_ms: 1000, graph: { nodes, edges } };
}
```

- [ ] **Step 2: Write the failing tests**

```typescript
// apps/vscode-extension/test/graph-space-model.test.ts
import { describe, expect, it } from "vitest";
import { buildSpaceModel } from "../src/graph/space-model.js";
import { ROOT, edge, fileNode, snap, symNode } from "./graph-fixtures.js";

describe("buildSpaceModel — stars & filtering", () => {
  it("emits one star per File node with workspace-relative id", () => {
    const m = buildSpaceModel(snap([fileNode("apps/web/src/a.ts")], []));
    expect(m.stars).toHaveLength(1);
    expect(m.stars[0].id).toBe("apps/web/src/a.ts");
    expect(m.stars[0].dir).toBe("apps/web/src");
  });

  it("counts symbols per file, excluding Variable nodes", () => {
    const m = buildSpaceModel(
      snap(
        [
          fileNode("apps/web/src/a.ts"),
          symNode("Class", "apps/web/src/a.ts", "A"),
          symNode("Function", "apps/web/src/a.ts", "f"),
          symNode("Variable", "apps/web/src/a.ts", "v"),
        ],
        []
      )
    );
    expect(m.stars[0].symbolCount).toBe(2);
  });

  it("rolls symbol-level Calls edges up to file-level degree + kindMix", () => {
    const a = symNode("Class", "apps/web/src/a.ts", "A");
    const b = symNode("Function", "apps/web/src/b.ts", "f");
    const m = buildSpaceModel(
      snap([fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), a, b], [edge(a.id, b.id, "Calls")])
    );
    const sa = m.stars.find((s) => s.id === "apps/web/src/a.ts")!;
    const sb = m.stars.find((s) => s.id === "apps/web/src/b.ts")!;
    expect(sa.outDeg).toBe(1);
    expect(sa.inDeg).toBe(0);
    expect(sb.inDeg).toBe(1);
    expect(sa.kindMix.Calls).toBe(1);
  });

  it("drops References edges and self-file edges from the ambient model", () => {
    const a = symNode("Class", "apps/web/src/a.ts", "A");
    const a2 = symNode("Function", "apps/web/src/a.ts", "g");
    const b = symNode("Function", "apps/web/src/b.ts", "f");
    const m = buildSpaceModel(
      snap(
        [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), a, a2, b],
        [edge(a.id, b.id, "References"), edge(a.id, a2.id, "Calls")]
      )
    );
    const sa = m.stars.find((s) => s.id === "apps/web/src/a.ts")!;
    expect(sa.outDeg).toBe(0);
    expect(sa.inDeg).toBe(0);
  });
});

describe("buildSpaceModel — packages & orphans", () => {
  const threeFiles = (pkg: string) => [
    fileNode(`${pkg}/src/a.ts`),
    fileNode(`${pkg}/src/b.ts`),
    fileNode(`${pkg}/src/c.ts`),
  ];

  it("groups apps/* and services/* two segments deep", () => {
    const m = buildSpaceModel(snap([...threeFiles("apps/web"), ...threeFiles("services/api")], []));
    expect(m.packages.map((p) => p.id).sort()).toEqual(["apps/web", "services/api"]);
    expect(m.stars.every((s) => s.pkg !== "")).toBe(true);
  });

  it("groups other top-level dirs one segment deep", () => {
    const m = buildSpaceModel(snap(threeFiles("webview-ui"), []));
    expect(m.packages.map((p) => p.id)).toEqual(["webview-ui"]);
  });

  it("files in groups smaller than 3 become orphans (pkg='')", () => {
    const m = buildSpaceModel(snap([fileNode("scripts/x.py"), ...threeFiles("apps/web")], []));
    expect(m.stars.find((s) => s.id === "scripts/x.py")!.pkg).toBe("");
    expect(m.packages.map((p) => p.id)).toEqual(["apps/web"]);
  });

  it("root-level files are orphans", () => {
    const m = buildSpaceModel(snap([fileNode("ui.html"), ...threeFiles("apps/web")], []));
    expect(m.stars.find((s) => s.id === "ui.html")!.pkg).toBe("");
  });

  it("packages carry fileCount and sorted unique dirs", () => {
    const m = buildSpaceModel(snap(threeFiles("apps/web"), []));
    expect(m.packages[0].fileCount).toBe(3);
    expect(m.packages[0].dirs).toEqual(["apps/web/src"]);
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run -w crucible-vscode-extension test -- test/graph-space-model.test.ts`
Expected: FAIL — cannot resolve `../src/graph/space-model.js`.

- [ ] **Step 4: Write the implementation**

```typescript
// apps/vscode-extension/src/graph/space-model.ts
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

const AMBIENT_KINDS = new Set<EdgeKind>(["Imports", "Calls", "Inherits"]);
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
  if (segs.length < 2) return "";
  if (GROUP_DIRS.has(segs[0])) return segs.length >= 3 ? `${segs[0]}/${segs[1]}` : "";
  return segs[0];
}

export interface FileEdgeRecord {
  fromFile: string;
  toFile: string;
  kind: EdgeKind;
}

/** Resolve every ambient edge to a (fromFile, toFile) pair of workspace-relative paths.
 * Shared with Task 2's bundling; exported for tests. */
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
    if (!AMBIENT_KINDS.has(e.kind as EdgeKind)) continue;
    const fromFile = nodeFile.get(e.from);
    if (!fromFile) continue;
    let toFile = nodeFile.get(e.to) ?? null;
    if (!toFile && e.to.startsWith("external:module:")) {
      toFile = resolveModuleSpec(e.to.slice("external:module:".length), fromFile, fileSet);
    }
    if (!toFile || toFile === fromFile) continue;
    out.push({ fromFile, toFile, kind: e.kind as EdgeKind });
  }
  return out;
}

/** Best-effort resolution of a relative import spec to a workspace file.
 * Bare package specs (no leading '.') stay external -> null. Implemented in Task 2;
 * declared here so resolveFileEdges compiles — Task 1 ships it returning null. */
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run -w crucible-vscode-extension test -- test/graph-space-model.test.ts`
Expected: PASS (all tests in both describes).

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/src/graph/space-model.ts apps/vscode-extension/test/graph-fixtures.ts apps/vscode-extension/test/graph-space-model.test.ts
git commit -m "feat(graph): SpaceModel core — snapshot filtering, file rollups, packages/orphans"
```

---

### Task 2: Module-spec resolution, bundles, intra-bundles, links

**Files:**
- Modify: `apps/vscode-extension/src/graph/space-model.ts` (replace the `resolveModuleSpec` stub; extend `buildSpaceModel`)
- Test: `apps/vscode-extension/test/graph-space-model.test.ts` (append)

**Interfaces:**
- Consumes: Task 1's types and `resolveFileEdges`.
- Produces: `resolveModuleSpec(spec, fromFileRel, fileSet): string | null` fully implemented; `SpaceModel.bundles`, `.intraBundles`, `.links` populated. Later tasks rely on: `Bundle.kindMix` proportions summing to `count`, `FileLink` having `a < b`.

Why this matters: in LSP-off snapshots most `Imports` edges point at `external:module:<relative spec>` (verified: 2,459 of 5,928 edges in shadow-forge-stress). Resolving relative specs recovers the import skeleton — without this, beams between packages barely exist on tree-sitter-only snapshots.

- [ ] **Step 1: Append the failing tests**

```typescript
// append to apps/vscode-extension/test/graph-space-model.test.ts
import { extModule } from "./graph-fixtures.js"; // add to the existing import block

describe("resolveModuleSpec via Imports edges", () => {
  it("resolves ./x.js relative specs to sibling .ts files", () => {
    const files = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")];
    const ext = extModule("./b.js", "apps/web/src/a.ts");
    const m = buildSpaceModel(snap([...files, ext], [edge(files[0].id, ext.id, "Imports")]));
    const sa = m.stars.find((s) => s.id === "apps/web/src/a.ts")!;
    const sb = m.stars.find((s) => s.id === "apps/web/src/b.ts")!;
    expect(sa.outDeg).toBe(1);
    expect(sb.inDeg).toBe(1);
  });

  it("resolves ../dir specs through .. and index files", () => {
    const files = [
      fileNode("apps/web/src/client/a.ts"),
      fileNode("apps/web/src/domain/index.ts"),
      fileNode("apps/web/src/client/pad.ts"),
    ];
    const ext = extModule("../domain", "apps/web/src/client/a.ts");
    const m = buildSpaceModel(snap([...files, ext], [edge(files[0].id, ext.id, "Imports")]));
    expect(m.stars.find((s) => s.id === "apps/web/src/domain/index.ts")!.inDeg).toBe(1);
  });

  it("leaves bare package specs unresolved (no edge)", () => {
    const files = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")];
    const ext = extModule("react", "apps/web/src/a.ts");
    const m = buildSpaceModel(snap([...files, ext], [edge(files[0].id, ext.id, "Imports")]));
    expect(m.stars.find((s) => s.id === "apps/web/src/a.ts")!.outDeg).toBe(0);
  });
});

describe("bundles, intraBundles, links", () => {
  function crossSnap() {
    const aFiles = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")];
    const sFiles = [fileNode("services/api/m.py"), fileNode("services/api/n.py"), fileNode("services/api/o.py")];
    const edges = [
      edge(aFiles[0].id, sFiles[0].id, "Calls"),
      edge(aFiles[1].id, sFiles[0].id, "Calls"),
      edge(aFiles[0].id, sFiles[1].id, "Imports"),
      edge(aFiles[0].id, aFiles[1].id, "Imports"), // intra-package
    ];
    return snap([...aFiles, ...sFiles], edges);
  }

  it("aggregates cross-package edges into one bundle with kindMix", () => {
    const m = buildSpaceModel(crossSnap());
    expect(m.bundles).toHaveLength(1);
    const b = m.bundles[0];
    expect(b.fromPkg).toBe("apps/web");
    expect(b.toPkg).toBe("services/api");
    expect(b.count).toBe(3);
    expect(b.kindMix).toEqual({ Calls: 2, Imports: 1 });
  });

  it("aggregates intra-package edges per directory pair", () => {
    const m = buildSpaceModel(crossSnap());
    expect(m.intraBundles).toEqual([{ pkg: "apps/web", fromDir: "apps/web/src", toDir: "apps/web/src", count: 1 }]);
  });

  it("emits deduped intra-package file links with a < b", () => {
    const m = buildSpaceModel(crossSnap());
    expect(m.links).toEqual([{ a: "apps/web/src/a.ts", b: "apps/web/src/b.ts", count: 1 }]);
  });
});
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `npm run -w crucible-vscode-extension test -- test/graph-space-model.test.ts`
Expected: FAIL — resolveModuleSpec tests (outDeg 0 ≠ 1) and bundles tests (empty arrays).

- [ ] **Step 3: Implement**

Replace the `resolveModuleSpec` stub body:

```typescript
export function resolveModuleSpec(
  spec: string,
  fromFileRel: string,
  fileSet: Set<string>
): string | null {
  if (!spec.startsWith(".")) return null;
  // Normalize dir(fromFile) + spec without node:path (keep this file vscode- AND platform-free).
  const base = fromFileRel.includes("/") ? fromFileRel.slice(0, fromFileRel.lastIndexOf("/")) : "";
  const segs = base ? base.split("/") : [];
  for (const part of spec.split("/")) {
    if (part === "." || part === "") continue;
    if (part === "..") segs.pop();
    else segs.push(part);
  }
  const p = segs.join("/");
  const candidates = [
    p,
    p.replace(/\.js$/, ".ts"),
    p.replace(/\.js$/, ".tsx"),
    `${p}.ts`,
    `${p}.tsx`,
    `${p}.js`,
    `${p}.py`,
    `${p}.rs`,
    `${p}/index.ts`,
    `${p}/index.tsx`,
    `${p}/__init__.py`,
  ];
  for (const c of candidates) if (fileSet.has(c)) return c;
  return null;
}
```

Then, in `buildSpaceModel`, after the orphan-demotion / packages block and **before** constructing `model`, aggregate (uses `fileEdges` + the final `stars` map, so package reassignments are respected):

```typescript
  const bundleMap = new Map<string, Bundle>();
  const intraMap = new Map<string, IntraBundle>();
  const linkMap = new Map<string, FileLink>();
  for (const fe of fileEdges) {
    const from = stars.get(fe.fromFile);
    const to = stars.get(fe.toFile);
    if (!from || !to) continue;
    if (from.pkg && to.pkg && from.pkg !== to.pkg) {
      const key = `${from.pkg} ${to.pkg}`;
      let b = bundleMap.get(key);
      if (!b) {
        b = { fromPkg: from.pkg, toPkg: to.pkg, count: 0, kindMix: {} };
        bundleMap.set(key, b);
      }
      b.count += 1;
      b.kindMix[fe.kind] = (b.kindMix[fe.kind] ?? 0) + 1;
    } else if (from.pkg && from.pkg === to.pkg) {
      const ikey = `${from.pkg} ${from.dir} ${to.dir}`;
      let ib = intraMap.get(ikey);
      if (!ib) {
        ib = { pkg: from.pkg, fromDir: from.dir, toDir: to.dir, count: 0 };
        intraMap.set(ikey, ib);
      }
      ib.count += 1;
      const [la, lb] = from.id < to.id ? [from.id, to.id] : [to.id, from.id];
      const lkey = `${la} ${lb}`;
      let l = linkMap.get(lkey);
      if (!l) {
        l = { a: la, b: lb, count: 0 };
        linkMap.set(lkey, l);
      }
      l.count += 1;
    }
  }
```

And populate the model literal: `bundles: [...bundleMap.values()].sort((a, b) => b.count - a.count)`, `intraBundles: [...intraMap.values()].sort((a, b) => b.count - a.count)`, `links: [...linkMap.values()].sort((a, b) => a.a.localeCompare(b.a) || a.b.localeCompare(b.b))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w crucible-vscode-extension test -- test/graph-space-model.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/graph/space-model.ts apps/vscode-extension/test/graph-space-model.test.ts
git commit -m "feat(graph): relative module-spec resolution + bundle/link aggregation"
```

---

### Task 3: Entry-point & hub detection, diffSpaceModel

**Files:**
- Modify: `apps/vscode-extension/src/graph/space-model.ts`
- Test: `apps/vscode-extension/test/graph-space-model.test.ts` (append)

**Interfaces:**
- Produces: `StarRecord.isEntry` / `.isHub` populated; `SpaceDiff` type + `diffSpaceModel(prev: SpaceModel, next: SpaceModel): SpaceDiff`. Later tasks rely on `SpaceDiff = { added: StarRecord[]; removed: string[]; changed: StarRecord[]; packages: PackageInfo[]; bundles: Bundle[]; intraBundles: IntraBundle[]; links: FileLink[] }`.

- [ ] **Step 1: Append the failing tests**

```typescript
describe("entry points & hubs", () => {
  it("flags conventional entry names", () => {
    const files = [fileNode("services/api/main.py"), fileNode("services/api/util.py"), fileNode("services/api/io.py")];
    const m = buildSpaceModel(snap(files, []));
    expect(m.stars.find((s) => s.id === "services/api/main.py")!.isEntry).toBe(true);
    expect(m.stars.find((s) => s.id === "services/api/util.py")!.isEntry).toBe(false);
  });

  it("flags graph-signal entries: outDeg>=3 and inDeg==0", () => {
    const files = ["root.ts", "d1.ts", "d2.ts", "d3.ts"].map((n) => fileNode(`apps/web/src/${n}`));
    const edges = [1, 2, 3].map((i) => edge(files[0].id, files[i].id, "Imports"));
    const m = buildSpaceModel(snap(files, edges));
    expect(m.stars.find((s) => s.id === "apps/web/src/root.ts")!.isEntry).toBe(true);
  });

  it("flags the highest-degree star in a package as hub when degree >= 8", () => {
    const files = Array.from({ length: 10 }, (_, i) => fileNode(`apps/web/src/f${i}.ts`));
    const edges = Array.from({ length: 9 }, (_, i) => edge(files[i + 1].id, files[0].id, "Calls"));
    const m = buildSpaceModel(snap(files, edges));
    const hub = m.stars.find((s) => s.id === "apps/web/src/f0.ts")!;
    expect(hub.isHub).toBe(true);
    expect(m.stars.filter((s) => s.isHub)).toHaveLength(1);
  });
});

describe("diffSpaceModel", () => {
  it("reports added, removed, and changed stars by id", () => {
    const before = buildSpaceModel(
      snap([fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")], [])
    );
    const afterFiles = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/d.ts")];
    const after = buildSpaceModel(
      snap([...afterFiles, symNode("Class", "apps/web/src/a.ts", "A")], [])
    );
    const d = diffSpaceModel(before, after);
    expect(d.added.map((s) => s.id)).toEqual(["apps/web/src/d.ts"]);
    expect(d.removed).toEqual(["apps/web/src/c.ts"]);
    expect(d.changed.map((s) => s.id)).toEqual(["apps/web/src/a.ts"]); // symbolCount changed
    expect(d.bundles).toEqual(after.bundles);
  });
});
```

Add `diffSpaceModel` to the import from `../src/graph/space-model.js`.

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/graph-space-model.test.ts`
Expected: FAIL — `isEntry` false / `diffSpaceModel` not exported.

- [ ] **Step 3: Implement**

Append to `space-model.ts` (and call `detectEntriesAndHubs(stars)` in `buildSpaceModel` right after the aggregation block from Task 2):

```typescript
const ENTRY_NAMES = new Set([
  "main.py", "main.rs", "main.ts", "index.ts", "index.tsx",
  "extension.ts", "App.tsx", "app.py",
]);
const HUB_MIN_DEGREE = 8;
const HUB_MAX_PER_PKG = 5;

function detectEntriesAndHubs(stars: Map<string, StarRecord>): void {
  const byPkg = new Map<string, StarRecord[]>();
  for (const s of stars.values()) {
    const base = s.id.slice(s.id.lastIndexOf("/") + 1);
    s.isEntry =
      ENTRY_NAMES.has(base) ||
      (base.endsWith(".html") && s.pkg !== "") ||
      (s.outDeg >= 3 && s.inDeg === 0);
    if (s.pkg) {
      const arr = byPkg.get(s.pkg) ?? [];
      arr.push(s);
      byPkg.set(s.pkg, arr);
    }
  }
  for (const arr of byPkg.values()) {
    const cap = Math.min(HUB_MAX_PER_PKG, Math.max(1, Math.ceil(arr.length * 0.02)));
    [...arr]
      .sort((a, b) => b.inDeg + b.outDeg - (a.inDeg + a.outDeg))
      .slice(0, cap)
      .forEach((s) => {
        if (s.inDeg + s.outDeg >= HUB_MIN_DEGREE) s.isHub = true;
      });
  }
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

export function diffSpaceModel(prev: SpaceModel, next: SpaceModel): SpaceDiff {
  const prevById = new Map(prev.stars.map((s) => [s.id, s]));
  const nextIds = new Set(next.stars.map((s) => s.id));
  const added: StarRecord[] = [];
  const changed: StarRecord[] = [];
  for (const s of next.stars) {
    const old = prevById.get(s.id);
    if (!old) added.push(s);
    else if (JSON.stringify(old) !== JSON.stringify(s)) changed.push(s);
  }
  const removed = prev.stars.filter((s) => !nextIds.has(s.id)).map((s) => s.id);
  return {
    added,
    removed,
    changed,
    packages: next.packages,
    bundles: next.bundles,
    intraBundles: next.intraBundles,
    links: next.links,
  };
}
```

- [ ] **Step 4: Run the full extension suite**

Run: `npm run -w crucible-vscode-extension test`
Expected: PASS — all existing tests still green plus the new ones.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/graph/space-model.ts apps/vscode-extension/test/graph-space-model.test.ts
git commit -m "feat(graph): entry/hub detection + SpaceModel diffing"
```

---

### Task 4: GraphSnapshotStore — file load/reload + lazy detail queries

**Files:**
- Create: `apps/vscode-extension/src/graph/snapshot-store.ts`
- Test: `apps/vscode-extension/test/graph-snapshot-store.test.ts`

**Interfaces:**
- Consumes: Task 1–3 types, `buildSpaceModel`, `diffSpaceModel`, `resolveFileEdges`, `relPath`.
- Produces (Task 5/6 depend on these exact shapes):

```typescript
export interface SymbolInfo { id: string; name: string; kind: string; line: number }
export interface FileEdge { dir: "out" | "in"; kind: EdgeKind; otherFile: string; crossPackage: boolean; symbolName?: string; line?: number }
export interface FileDetail { fileId: string; symbols: SymbolInfo[]; edges: FileEdge[]; withinFileCount: number }
export interface SymbolEdge { dir: "out" | "in"; kind: EdgeKind; name: string; fileId: string | null; line?: number }
export interface SymbolDetail { symbolId: string; edges: SymbolEdge[] }
export interface SymbolHit { symbolId: string; name: string; kind: string; fileId: string; line: number }
export class GraphSnapshotStore {
  constructor(snapshotPath: string);
  load(): SpaceModel;                                   // throws GraphSnapshotError (code "missing" | "malformed")
  reload(): { model: SpaceModel; diff: SpaceDiff | null };
  model(): SpaceModel | null;
  generatedAtMs(): number | null;
  fileDetail(fileId: string): FileDetail;
  symbolDetail(symbolId: string): SymbolDetail;
  searchSymbols(query: string, limit?: number): SymbolHit[];
}
export class GraphSnapshotError extends Error { readonly code: "missing" | "malformed" }
```

Detail queries are the ONLY place References edges and per-symbol data surface. `fileDetail.edges` includes ALL four kinds; within-file symbol↔symbol edges are counted in `withinFileCount`, not listed. Symbols exclude `Variable`. Unresolvable `external:*` targets in `symbolDetail` keep `fileId: null` and derive `name` from the id suffix (`external:call:Error` → `Error`).

- [ ] **Step 1: Write the failing tests**

```typescript
// apps/vscode-extension/test/graph-snapshot-store.test.ts
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GraphSnapshotError, GraphSnapshotStore } from "../src/graph/snapshot-store.js";
import { edge, fileNode, snap, symNode } from "./graph-fixtures.js";
import type { RawSnapshot } from "../src/graph/space-model.js";

function storeFor(s: RawSnapshot): GraphSnapshotStore {
  const dir = mkdtempSync(join(tmpdir(), "axon-"));
  const p = join(dir, "index-snapshot.json");
  writeFileSync(p, JSON.stringify(s));
  return new GraphSnapshotStore(p);
}

function richSnap(): RawSnapshot {
  const a = fileNode("apps/web/src/a.ts");
  const b = fileNode("apps/web/src/b.ts");
  const c = fileNode("apps/web/src/c.ts");
  const m = fileNode("services/api/m.py");
  const m2 = fileNode("services/api/n.py");
  const m3 = fileNode("services/api/o.py");
  const clsA = symNode("Class", "apps/web/src/a.ts", "A", 5);
  const fnA = symNode("Function", "apps/web/src/a.ts", "helper", 40);
  const varA = symNode("Variable", "apps/web/src/a.ts", "cfg", 2);
  const fnB = symNode("Function", "apps/web/src/b.ts", "run", 3);
  const fnM = symNode("Function", "services/api/m.py", "serve", 9);
  return snap(
    [a, b, c, m, m2, m3, clsA, fnA, varA, fnB, fnM],
    [
      edge(clsA.id, fnB.id, "Calls"),          // out, within-package
      edge(clsA.id, fnM.id, "Calls"),          // out, cross-package
      edge(fnB.id, fnA.id, "References"),      // in (References only in detail)
      edge(clsA.id, fnA.id, "Calls"),          // within-file -> counted, not listed
      edge(clsA.id, "external:call:Error", "Calls"), // unresolvable target
    ]
  );
}

describe("GraphSnapshotStore", () => {
  it("load() returns the model; missing file throws code=missing", () => {
    const st = storeFor(richSnap());
    expect(st.load().stars.length).toBe(6);
    const bad = new GraphSnapshotStore("/nonexistent/index-snapshot.json");
    expect(() => bad.load()).toThrowError(GraphSnapshotError);
    try { bad.load(); } catch (e) { expect((e as GraphSnapshotError).code).toBe("missing"); }
  });

  it("malformed JSON throws code=malformed", () => {
    const dir = mkdtempSync(join(tmpdir(), "axon-"));
    const p = join(dir, "index-snapshot.json");
    writeFileSync(p, "{nope");
    try { new GraphSnapshotStore(p).load(); expect.unreachable(); }
    catch (e) { expect((e as GraphSnapshotError).code).toBe("malformed"); }
  });

  it("fileDetail: symbols (no Variables), grouped edges, withinFileCount", () => {
    const st = storeFor(richSnap());
    st.load();
    const d = st.fileDetail("apps/web/src/a.ts");
    expect(d.symbols.map((s) => s.name).sort()).toEqual(["A", "helper"]);
    expect(d.withinFileCount).toBe(1);
    const out = d.edges.filter((e) => e.dir === "out");
    expect(out.map((e) => e.otherFile).sort()).toEqual(["apps/web/src/b.ts", "services/api/m.py"]);
    expect(out.find((e) => e.otherFile === "services/api/m.py")!.crossPackage).toBe(true);
    const refIn = d.edges.find((e) => e.kind === "References")!;
    expect(refIn.dir).toBe("in");
    expect(refIn.otherFile).toBe("apps/web/src/b.ts");
  });

  it("symbolDetail: unresolvable externals keep fileId null with derived name", () => {
    const st = storeFor(richSnap());
    st.load();
    const d = st.symbolDetail(`class:file:/ws/apps/web/src/a.ts:A`);
    const ext = d.edges.find((e) => e.fileId === null)!;
    expect(ext.name).toBe("Error");
    expect(d.edges.filter((e) => e.dir === "out").length).toBeGreaterThanOrEqual(3);
  });

  it("searchSymbols: case-insensitive substring, capped", () => {
    const st = storeFor(richSnap());
    st.load();
    const hits = st.searchSymbols("HELP");
    expect(hits).toHaveLength(1);
    expect(hits[0].name).toBe("helper");
    expect(hits[0].fileId).toBe("apps/web/src/a.ts");
    expect(hits[0].line).toBe(40);
  });

  it("reload() returns a diff after the file changes", () => {
    const dir = mkdtempSync(join(tmpdir(), "axon-"));
    const p = join(dir, "index-snapshot.json");
    writeFileSync(p, JSON.stringify(richSnap()));
    const st = new GraphSnapshotStore(p);
    st.load();
    const s2 = richSnap();
    s2.graph.nodes.push(fileNode("apps/web/src/new.ts"));
    writeFileSync(p, JSON.stringify(s2));
    const { diff } = st.reload();
    expect(diff!.added.map((s) => s.id)).toEqual(["apps/web/src/new.ts"]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/graph-snapshot-store.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// apps/vscode-extension/src/graph/snapshot-store.ts
// vscode-free. Holds the full parsed snapshot in memory while the panel is open and
// serves the lazy queries the webview asks for on selection. Released with the panel.
import { readFileSync } from "node:fs";
import {
  buildSpaceModel,
  diffSpaceModel,
  relPath,
  type EdgeKind,
  type RawGraphNode,
  type RawSnapshot,
  type SpaceDiff,
  type SpaceModel,
} from "./space-model.js";

export interface SymbolInfo { id: string; name: string; kind: string; line: number }
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
export interface SymbolDetail { symbolId: string; edges: SymbolEdge[] }
export interface SymbolHit { symbolId: string; name: string; kind: string; fileId: string; line: number }

const ALL_KINDS = new Set<EdgeKind>(["Imports", "Calls", "Inherits", "References"]);
const SYMBOL_KINDS = new Set(["Class", "Function", "Method", "Interface"]);

export class GraphSnapshotError extends Error {
  constructor(readonly code: "missing" | "malformed", message: string) {
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
      const kind = e.kind as EdgeKind;
      if (!ALL_KINDS.has(kind)) continue;
      const fromFile = this.fileOfNode.get(e.from);
      const toFile = this.fileOfNode.get(e.to);
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
      const kind = e.kind as EdgeKind;
      if (!ALL_KINDS.has(kind)) continue;
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w crucible-vscode-extension test -- test/graph-snapshot-store.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/graph/snapshot-store.ts apps/vscode-extension/test/graph-snapshot-store.test.ts
git commit -m "feat(graph): GraphSnapshotStore with lazy file/symbol detail + search"
```

---

### Task 5: Message protocol + handleGraphMessage

**Files:**
- Create: `apps/vscode-extension/src/graph/graph-messages.ts`
- Test: `apps/vscode-extension/test/graph-messages.test.ts`

**Interfaces:**
- Consumes: Task 1–4 types.
- Produces (Task 6 wires these; Task 7 mirrors them in the webview):

```typescript
export type GraphToHost =
  | { type: "ready" } | { type: "refresh" }
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
  loadModel(): SpaceModel;             // throws GraphSnapshotError
  staleAgeSec(): number | null;        // null = fresh or unknown
  fileDetail(id: string): FileDetail;
  symbolDetail(id: string): SymbolDetail;
  searchSymbols(q: string): SymbolHit[];
  openFile(path: string, line?: number): Promise<void>;
  buildIndex(): Promise<void>;
}
export async function handleGraphMessage(msg: GraphToHost, deps: GraphHostDeps, post: (m: GraphToWebview) => void): Promise<void>
```

Best-effort like `handleMemoryMessage`: any deps throw becomes an `error` (or `noSnapshot`) post, never an exception.

- [ ] **Step 1: Write the failing tests**

```typescript
// apps/vscode-extension/test/graph-messages.test.ts
import { describe, expect, it, vi } from "vitest";
import { handleGraphMessage, type GraphHostDeps, type GraphToWebview } from "../src/graph/graph-messages.js";
import { GraphSnapshotError } from "../src/graph/snapshot-store.js";
import { buildSpaceModel } from "../src/graph/space-model.js";
import { fileNode, snap } from "./graph-fixtures.js";

function deps(overrides: Partial<GraphHostDeps> = {}): GraphHostDeps {
  return {
    loadModel: () => buildSpaceModel(snap([fileNode("apps/w/a.ts"), fileNode("apps/w/b.ts"), fileNode("apps/w/c.ts")], [])),
    staleAgeSec: () => null,
    fileDetail: (id) => ({ fileId: id, symbols: [], edges: [], withinFileCount: 0 }),
    symbolDetail: (id) => ({ symbolId: id, edges: [] }),
    searchSymbols: () => [],
    openFile: vi.fn(async () => {}),
    buildIndex: vi.fn(async () => {}),
    ...overrides,
  };
}

function collector(): { posts: GraphToWebview[]; post: (m: GraphToWebview) => void } {
  const posts: GraphToWebview[] = [];
  return { posts, post: (m) => posts.push(m) };
}

describe("handleGraphMessage", () => {
  it("ready -> posts space with staleAgeSec", async () => {
    const { posts, post } = collector();
    await handleGraphMessage({ type: "ready" }, deps({ staleAgeSec: () => 1200 }), post);
    expect(posts[0].type).toBe("space");
    expect((posts[0] as { staleAgeSec: number | null }).staleAgeSec).toBe(1200);
  });

  it("ready with missing snapshot -> posts noSnapshot, never throws", async () => {
    const { posts, post } = collector();
    const d = deps({ loadModel: () => { throw new GraphSnapshotError("missing", "nope"); } });
    await handleGraphMessage({ type: "ready" }, d, post);
    expect(posts[0]).toMatchObject({ type: "noSnapshot", reason: "missing", building: false });
  });

  it("buildIndex -> calls deps.buildIndex then reports noSnapshot building=true when still absent", async () => {
    const { posts, post } = collector();
    const d = deps({ loadModel: () => { throw new GraphSnapshotError("missing", "nope"); } });
    await handleGraphMessage({ type: "buildIndex" }, d, post);
    expect(d.buildIndex).toHaveBeenCalledOnce();
    expect(posts[0]).toMatchObject({ type: "noSnapshot", building: true });
  });

  it("fileDetail / symbolDetail / searchSymbols round-trip", async () => {
    const { posts, post } = collector();
    const d = deps();
    await handleGraphMessage({ type: "fileDetail", fileId: "apps/w/a.ts" }, d, post);
    await handleGraphMessage({ type: "symbolDetail", symbolId: "class:x:A" }, d, post);
    await handleGraphMessage({ type: "searchSymbols", query: "run" }, d, post);
    expect(posts.map((p) => p.type)).toEqual(["fileDetail", "symbolDetail", "symbolHits"]);
  });

  it("openFile delegates and posts error on failure", async () => {
    const { posts, post } = collector();
    const d = deps({ openFile: vi.fn(async () => { throw new Error("no such file"); }) });
    await handleGraphMessage({ type: "openFile", path: "apps/w/a.ts", line: 12 }, d, post);
    expect(d.openFile).toHaveBeenCalledWith("apps/w/a.ts", 12);
    expect(posts[0]).toMatchObject({ type: "error" });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/graph-messages.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// apps/vscode-extension/src/graph/graph-messages.ts
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w crucible-vscode-extension test -- test/graph-messages.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/graph/graph-messages.ts apps/vscode-extension/test/graph-messages.test.ts
git commit -m "feat(graph): webview message protocol + handleGraphMessage"
```

---

### Task 6: GraphPanel, command, chat-header button

**Files:**
- Create: `apps/vscode-extension/src/graph-panel.ts`
- Modify: `apps/vscode-extension/src/extension.ts` (register `crucible.openGraphPanel`)
- Modify: `apps/vscode-extension/src/chat-panel.ts` (route `openGraphPanel` webview message — add an `onOpenGraphPanel` callback parameter exactly where `onOpenMemoryPanel` is declared/passed)
- Modify: `apps/vscode-extension/package.json` (command + activationEvent)
- Modify: `apps/vscode-extension/webview-ui/src/components/Icon.tsx` (add `"orbit"` icon)
- Modify: `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx` (header button)
- Modify (if needed): `apps/vscode-extension/src/vscode-shim.d.ts` (add `Range`, `TextDocumentShowOptions.selection` if absent)
- Test: `apps/vscode-extension/webview-ui/src/graph/threadview-button.test.tsx` is NOT needed — assert via the existing ThreadView test pattern in `webview-ui/src/test/views.test.tsx` (append one case)

**Interfaces:**
- Consumes: `GraphSnapshotStore`, `handleGraphMessage`, `GraphHostDeps`, `GraphToHost`.
- Produces: `new GraphPanel(extensionUri, workspacePath, backendBaseUrl).open()`; command id `crucible.openGraphPanel`; webview→host chat message `{type:"openGraphPanel"}`.

- [ ] **Step 1: Write GraphPanel**

Mirror `memory-panel.ts` (same buildHtml with two changes: loads `graph.html`, CSP gains `worker-src ${cspSource} blob:;`):

```typescript
// apps/vscode-extension/src/graph-panel.ts
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
    this.snapshotPath = path.join(workspacePath, ".ai-editor", "index-snapshot.json");
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
        await fetch(new URL("/v1/index/build", this.backendBaseUrl), { method: "POST" });
        // .ai-editor/ may not have existed when the panel opened (fs.watch on a missing
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
            const { diff } = this.store.reload();
            if (diff) this.panel?.webview.postMessage({ type: "spaceDiff", diff });
            else this.panel?.webview.postMessage({ type: "space", model: this.store.model(), staleAgeSec: null });
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
      `<head>\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${cspSource}; script-src 'nonce-${nonce}' ${cspSource}; worker-src ${cspSource} blob:; img-src ${cspSource} data:; font-src ${cspSource}; connect-src ${cspSource};">`
    );
    return html;
  }
}
```

If `vscode.Position` / `vscode.Selection` / `vscode.Range` / `TextEditorRevealType` / `openTextDocument` are missing from `src/vscode-shim.d.ts`, add minimal declarations there (match the shim's existing declaration style).

- [ ] **Step 2: Register the command in extension.ts**

Next to the `crucible.openMemoryPanel` registration (~line 412):

```typescript
  context.subscriptions.push(
    vscode.commands.registerCommand("crucible.openGraphPanel", () => {
      const ws = controller.memoryWorkspacePath();
      if (!ws) {
        void vscode.window.showWarningMessage("Open a folder to view the dependency space.");
        return;
      }
      new GraphPanel(context.extensionUri, ws, settings.getBackendBaseUrl()).open();
    })
  );
```

Add `import { GraphPanel } from "./graph-panel.js";` at the top. In `chat-panel.ts`, mirror the `openMemoryPanel` route: add an `onOpenGraphPanel: () => void` constructor field beside `onOpenMemoryPanel` and the branch `else if (m["type"] === "openGraphPanel") { this.onOpenGraphPanel(); return; }`; in `extension.ts`, pass `() => { void vscode.commands.executeCommand("crucible.openGraphPanel"); }` where the ChatPanel is constructed (beside the existing onOpenMemoryPanel argument).

- [ ] **Step 3: package.json contributes**

In `apps/vscode-extension/package.json`: add `"onCommand:crucible.openGraphPanel"` to `activationEvents`; add to `contributes.commands`:

```json
{ "command": "crucible.openGraphPanel", "title": "Crucible: Open Dependency Space (AXON)" }
```

No `when`-context gating — the panel degrades to its empty state without a snapshot.

- [ ] **Step 4: Icon + ThreadView button**

`Icon.tsx`: extend the `IconName` union with `"orbit"` and add to `ICONS`:

```tsx
  orbit: (
    <>
      <circle cx="8" cy="8" r="2" fill="currentColor" />
      <ellipse cx="8" cy="8" rx="6.5" ry="2.6" fill="none" stroke="currentColor" strokeWidth="1.2" transform="rotate(-24 8 8)" />
      <circle cx="13.2" cy="5.4" r="1.1" fill="currentColor" />
    </>
  ),
```

`ThreadView.tsx`: duplicate the Memory Inspector button block (the one posting `openMemoryPanel`) directly below it, with `onClick={() => vscode.postMessage({ type: "openGraphPanel" })}`, `aria-label="Dependency Space"`, `title="Dependency Space"`, `<Icon name="orbit" size={14} />`.

Append to `webview-ui/src/test/views.test.tsx` (follow the file's existing render-ThreadView pattern for props):

```tsx
it("posts openGraphPanel when the Dependency Space header button is clicked", () => {
  // render ThreadView exactly as the neighboring header-button test does,
  // then:
  fireEvent.click(screen.getByLabelText("Dependency Space"));
  expect(postMessageSpy).toHaveBeenCalledWith({ type: "openGraphPanel" });
});
```

- [ ] **Step 5: Typecheck + full test suites**

Run: `npm run -w crucible-vscode-extension typecheck && npm run -w crucible-vscode-extension test && cd apps/vscode-extension/webview-ui && npx vitest run && cd -`
Expected: PASS all.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/src/graph-panel.ts apps/vscode-extension/src/extension.ts apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/package.json apps/vscode-extension/webview-ui/src/components/Icon.tsx apps/vscode-extension/webview-ui/src/components/ThreadView.tsx apps/vscode-extension/webview-ui/src/test/views.test.tsx apps/vscode-extension/src/vscode-shim.d.ts
git commit -m "feat(graph): GraphPanel host wiring, command, chat-header entry point"
```

---

### Task 7: Webview scaffold — graph entry, mirror types, GraphApp shell, EmptyState

**Files:**
- Create: `apps/vscode-extension/webview-ui/graph.html`
- Modify: `apps/vscode-extension/webview-ui/vite.config.ts` (add `graph` input)
- Modify: `apps/vscode-extension/webview-ui/package.json` (add `three`, `@types/three`)
- Create: `apps/vscode-extension/webview-ui/src/graph/main.tsx`
- Create: `apps/vscode-extension/webview-ui/src/graph/vscodeApi.ts`
- Create: `apps/vscode-extension/webview-ui/src/graph/types.ts`
- Create: `apps/vscode-extension/webview-ui/src/graph/GraphApp.tsx`
- Create: `apps/vscode-extension/webview-ui/src/graph/hud/EmptyState.tsx`
- Test: `apps/vscode-extension/webview-ui/src/graph/GraphApp.test.tsx`

**Interfaces:**
- Consumes: message shapes from Task 5 (mirrored locally, never imported).
- Produces: `GraphApp` with a `createScene` injection seam — `(canvas: HTMLCanvasElement, cb: SceneCallbacks) => SceneHandle` — that Task 10 fills with the real Three.js factory and tests fill with a fake. `SceneHandle` (in `types.ts`) is the full scene interface later tasks implement.

- [ ] **Step 1: Entry plumbing**

`graph.html` (copy memory.html, retitle):

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AXON — Dependency Space</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/graph/main.tsx"></script>
  </body>
</html>
```

`vite.config.ts`: add `graph: resolve(__dirname, "graph.html"),` to `rollupOptions.input`.

Install deps: `cd apps/vscode-extension/webview-ui && npm install three && npm install -D @types/three`

`src/graph/vscodeApi.ts` — copy `src/memory/vscodeApi.ts` verbatim (the acquireVsCodeApi singleton wrapper).

`src/graph/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import GraphApp from "./GraphApp";
import "../index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <GraphApp />
  </React.StrictMode>,
);
```

- [ ] **Step 2: Mirror types**

```typescript
// apps/vscode-extension/webview-ui/src/graph/types.ts
// Local mirrors of the host's graph types (webview never imports extension src).
export type EdgeKind = "Imports" | "Calls" | "Inherits" | "References";

export interface StarRecord {
  id: string; pkg: string; dir: string;
  symbolCount: number; inDeg: number; outDeg: number;
  kindMix: Partial<Record<EdgeKind, number>>;
  isEntry: boolean; isHub: boolean;
}
export interface PackageInfo { id: string; fileCount: number; dirs: string[] }
export interface FileLink { a: string; b: string; count: number }
export interface Bundle { fromPkg: string; toPkg: string; count: number; kindMix: Partial<Record<EdgeKind, number>> }
export interface IntraBundle { pkg: string; fromDir: string; toDir: string; count: number }
export interface SpaceModel {
  workspaceRoot: string; generatedAtMs: number;
  packages: PackageInfo[]; stars: StarRecord[];
  bundles: Bundle[]; intraBundles: IntraBundle[]; links: FileLink[];
}
export interface SpaceDiff {
  added: StarRecord[]; removed: string[]; changed: StarRecord[];
  packages: PackageInfo[]; bundles: Bundle[]; intraBundles: IntraBundle[]; links: FileLink[];
}
export interface SymbolInfo { id: string; name: string; kind: string; line: number }
export interface FileEdge { dir: "out" | "in"; kind: EdgeKind; otherFile: string; crossPackage: boolean; symbolName?: string; line?: number }
export interface FileDetail { fileId: string; symbols: SymbolInfo[]; edges: FileEdge[]; withinFileCount: number }
export interface SymbolEdge { dir: "out" | "in"; kind: EdgeKind; name: string; fileId: string | null; line?: number }
export interface SymbolDetail { symbolId: string; edges: SymbolEdge[] }
export interface SymbolHit { symbolId: string; name: string; kind: string; fileId: string; line: number }

export type GraphToWebview =
  | { type: "space"; model: SpaceModel; staleAgeSec: number | null }
  | { type: "spaceDiff"; diff: SpaceDiff }
  | { type: "fileDetail"; detail: FileDetail }
  | { type: "symbolDetail"; detail: SymbolDetail }
  | { type: "symbolHits"; query: string; hits: SymbolHit[] }
  | { type: "noSnapshot"; reason: "missing" | "malformed"; message: string; building: boolean }
  | { type: "error"; message: string };
export type GraphToHost =
  | { type: "ready" } | { type: "refresh" }
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

export interface LayoutResult { ids: string[]; positions: Float32Array }

export interface SceneCallbacks {
  onPickStar(id: string): void;
  onPickPackage(pkg: string): void;
  onPickSatellite(symbolId: string, line?: number): void;
  onBackgroundClick(): void;
}
/** Implemented by scene/graph-scene.ts (Task 10). GraphApp only talks through this. */
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
  framePackage(pkg: string): void;
  resetCamera(): void;
  dispose(): void;
}
```

- [ ] **Step 3: Failing GraphApp test**

```tsx
// apps/vscode-extension/webview-ui/src/graph/GraphApp.test.tsx
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import GraphApp from "./GraphApp";
import type { SceneHandle } from "./types";

const postMessage = vi.fn();
vi.mock("./vscodeApi", () => ({ vscode: { postMessage: (m: unknown) => postMessage(m) } }));

function fakeScene(): SceneHandle {
  return {
    setSpace: vi.fn(), morph: vi.fn(), setFocus: vi.fn(),
    showFileTrace: vi.fn(), showSatellites: vi.fn(), showSymbolTrace: vi.fn(),
    clearOverlays: vi.fn(), setLayers: vi.fn(), flyToStar: vi.fn(),
    framePackage: vi.fn(), resetCamera: vi.fn(), dispose: vi.fn(),
  };
}

function hostPost(msg: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data: msg }));
  });
}

describe("GraphApp shell", () => {
  beforeEach(() => postMessage.mockClear());

  it("posts ready on mount and shows connecting state", () => {
    render(<GraphApp createScene={() => fakeScene()} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "ready" });
    expect(screen.getByText(/mapping the space/i)).toBeTruthy();
  });

  it("renders EmptyState on noSnapshot with a Build index CTA", () => {
    render(<GraphApp createScene={() => fakeScene()} />);
    hostPost({ type: "noSnapshot", reason: "missing", message: "no file", building: false });
    fireEvent.click(screen.getByRole("button", { name: /build index/i }));
    expect(postMessage).toHaveBeenCalledWith({ type: "buildIndex" });
  });

  it("hands the model to the scene when space arrives", () => {
    const scene = fakeScene();
    render(<GraphApp createScene={() => scene} />);
    hostPost({
      type: "space",
      staleAgeSec: null,
      model: {
        workspaceRoot: "/ws", generatedAtMs: 1, packages: [], stars: [],
        bundles: [], intraBundles: [], links: [],
      },
    });
    expect(scene.setSpace).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 4: Run to verify failure**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph/GraphApp.test.tsx`
Expected: FAIL — GraphApp not found.

- [ ] **Step 5: Implement GraphApp shell + EmptyState**

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/EmptyState.tsx
interface Props {
  reason: "missing" | "malformed";
  message: string;
  building: boolean;
  onBuild: () => void;
}

export function EmptyState({ reason, message, building, onBuild }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="text-[10px] uppercase tracking-[0.3em] text-[var(--color-text-dim)]">
        {reason === "missing" ? "No index snapshot" : "Snapshot unreadable"}
      </div>
      <p className="text-sm text-[var(--color-text-dim)] max-w-md">
        {reason === "missing"
          ? "The dependency space renders from .ai-editor/index-snapshot.json — build the index to ignite it."
          : message}
      </p>
      <button
        type="button"
        onClick={onBuild}
        disabled={building}
        className="px-4 py-2 rounded-lg text-xs font-semibold bg-[#fb923c] text-[#160709] disabled:opacity-50"
      >
        {building ? "Building…" : "Build index"}
      </button>
    </div>
  );
}
```

```tsx
// apps/vscode-extension/webview-ui/src/graph/GraphApp.tsx
import { useEffect, useRef, useState } from "react";
import { vscode } from "./vscodeApi";
import { EmptyState } from "./hud/EmptyState";
import type { GraphToWebview, LayoutResult, SceneCallbacks, SceneHandle, SpaceModel } from "./types";

interface Props {
  /** Injection seam: tests pass a fake; production defaults to the Three.js factory
   * (dynamically imported in Task 10 so jsdom tests never load WebGL code). */
  createScene?: (canvas: HTMLCanvasElement, cb: SceneCallbacks) => SceneHandle;
}

type Conn =
  | { kind: "connecting" }
  | { kind: "empty"; reason: "missing" | "malformed"; message: string; building: boolean }
  | { kind: "ready" };

export default function GraphApp({ createScene }: Props) {
  const [conn, setConn] = useState<Conn>({ kind: "connecting" });
  const [model, setModel] = useState<SpaceModel | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sceneRef = useRef<SceneHandle | null>(null);

  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      const m = ev.data as GraphToWebview;
      if (m.type === "space") {
        setModel(m.model);
        setConn({ kind: "ready" });
      } else if (m.type === "noSnapshot") {
        setConn({ kind: "empty", reason: m.reason, message: m.message, building: m.building });
      }
      // spaceDiff / details / hits / error handled in Tasks 12-14
    };
    window.addEventListener("message", onMsg);
    vscode.postMessage({ type: "ready" });
    return () => window.removeEventListener("message", onMsg);
  }, []);

  // Scene lifecycle + naive layout (real worker layout replaces this in Task 8).
  useEffect(() => {
    if (!model || !canvasRef.current || !createScene) return;
    if (!sceneRef.current) {
      sceneRef.current = createScene(canvasRef.current, {
        onPickStar: () => {},
        onPickPackage: () => {},
        onPickSatellite: () => {},
        onBackgroundClick: () => {},
      });
    }
    const layout: LayoutResult = {
      ids: model.stars.map((s) => s.id),
      positions: new Float32Array(model.stars.length * 3),
    };
    sceneRef.current.setSpace(model, layout);
  }, [model, createScene]);

  useEffect(() => () => sceneRef.current?.dispose(), []);

  return (
    <div className="w-screen h-screen overflow-hidden" style={{ background: "#070203" }}>
      {conn.kind === "connecting" && (
        <div className="flex items-center justify-center h-full text-xs uppercase tracking-[0.3em] text-[var(--color-text-dim)]">
          mapping the space…
        </div>
      )}
      {conn.kind === "empty" && (
        <EmptyState
          reason={conn.reason}
          message={conn.message}
          building={conn.building}
          onBuild={() => vscode.postMessage({ type: "buildIndex" })}
        />
      )}
      <canvas ref={canvasRef} className="w-full h-full block" style={{ display: conn.kind === "ready" ? "block" : "none" }} />
    </div>
  );
}
```

- [ ] **Step 6: Run tests + build; verify chat bundle unchanged**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph && npm run build && ls -la dist/assets/ && cd -`
Expected: tests PASS; `dist/graph.html` exists; `dist/assets/index.js` size unchanged vs `git stash`-free baseline (three must not leak into the chat bundle — it can't yet, nothing imports it).

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/graph.html apps/vscode-extension/webview-ui/vite.config.ts apps/vscode-extension/webview-ui/package.json apps/vscode-extension/webview-ui/package-lock.json apps/vscode-extension/webview-ui/src/graph
git commit -m "feat(graph): webview graph entry — mirror types, GraphApp shell, empty state"
```

---

### Task 8: Deterministic layout + Web Worker

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/graph/layout.ts`
- Create: `apps/vscode-extension/webview-ui/src/graph/layout.worker.ts`
- Modify: `apps/vscode-extension/webview-ui/src/graph/GraphApp.tsx` (replace the naive zero layout with worker-computed layout)
- Test: `apps/vscode-extension/webview-ui/src/graph/layout.test.ts`

**Interfaces:**
- Consumes: `SpaceModel`, `LayoutResult` from `./types`.
- Produces: `computeLayout(model: SpaceModel): LayoutResult` (pure, deterministic); `requestLayout(model): Promise<LayoutResult>` in GraphApp (worker with synchronous fallback).

- [ ] **Step 1: Failing tests**

```typescript
// apps/vscode-extension/webview-ui/src/graph/layout.test.ts
import { describe, expect, it } from "vitest";
import { computeLayout, hash32, mulberry32 } from "./layout";
import type { SpaceModel, StarRecord } from "./types";

function star(id: string, pkg: string, over: Partial<StarRecord> = {}): StarRecord {
  const dir = id.slice(0, id.lastIndexOf("/"));
  return { id, pkg, dir, symbolCount: 3, inDeg: 1, outDeg: 1, kindMix: {}, isEntry: false, isHub: false, ...over };
}

function model(stars: StarRecord[], links: SpaceModel["links"] = []): SpaceModel {
  const pkgs = new Map<string, number>();
  for (const s of stars) if (s.pkg) pkgs.set(s.pkg, (pkgs.get(s.pkg) ?? 0) + 1);
  return {
    workspaceRoot: "/ws", generatedAtMs: 1,
    packages: [...pkgs.entries()].map(([id, fileCount]) => ({ id, fileCount, dirs: [] })),
    stars, bundles: [], intraBundles: [], links,
  };
}

describe("layout determinism", () => {
  const stars = [
    star("apps/web/src/a.ts", "apps/web"),
    star("apps/web/src/b.ts", "apps/web"),
    star("services/api/m.py", "services/api"),
    star("orphan.ts", ""),
  ];

  it("same model twice -> identical positions", () => {
    const r1 = computeLayout(model(stars));
    const r2 = computeLayout(model(stars));
    expect(r1.ids).toEqual(r2.ids);
    expect([...r1.positions]).toEqual([...r2.positions]);
  });

  it("hash32/mulberry32 are stable", () => {
    expect(hash32("apps/web/src/a.ts")).toBe(hash32("apps/web/src/a.ts"));
    const rng = mulberry32(42);
    const seq = [rng(), rng(), rng()];
    const rng2 = mulberry32(42);
    expect([rng2(), rng2(), rng2()]).toEqual(seq);
  });

  it("adding one star leaves other packages' positions untouched", () => {
    const before = computeLayout(model(stars));
    const withNew = [...stars, star("services/api/n.py", "services/api")];
    const after = computeLayout(model(withNew));
    const idx = (r: { ids: string[] }, id: string) => r.ids.indexOf(id) * 3;
    const iB = idx(before, "apps/web/src/a.ts");
    const iA = idx(after, "apps/web/src/a.ts");
    // apps/web is untouched by a services/api addition (pre-force positions are per-id seeded;
    // force springs only act within a package)
    expect(after.positions[iA]).toBeCloseTo(before.positions[iB], 5);
  });

  it("linked files end closer than unlinked files in the same package", () => {
    const many = Array.from({ length: 12 }, (_, i) => star(`apps/web/src/f${i}.ts`, "apps/web"));
    const linked = model(many, [{ a: "apps/web/src/f0.ts", b: "apps/web/src/f1.ts", count: 8 }]);
    const r = computeLayout(linked);
    const pos = (id: string) => {
      const i = r.ids.indexOf(id) * 3;
      return [r.positions[i], r.positions[i + 1], r.positions[i + 2]];
    };
    const d = (p: number[], q: number[]) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
    const dLinked = d(pos("apps/web/src/f0.ts"), pos("apps/web/src/f1.ts"));
    const dOther = d(pos("apps/web/src/f0.ts"), pos("apps/web/src/f7.ts"));
    expect(dLinked).toBeLessThan(dOther);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph/layout.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement layout.ts**

```typescript
// apps/vscode-extension/webview-ui/src/graph/layout.ts
// Deterministic space layout: package ring anchors -> seeded directory cores ->
// seeded gaussian star scatter -> bounded intra-package spring refinement.
// Every random draw is keyed by a stable id hash, so the same model always
// produces the same positions and snapshot refreshes morph instead of reshuffle.
import type { LayoutResult, SpaceModel } from "./types";

export function hash32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussOf(rng: () => number): number {
  return (rng() + rng() + rng() - 1.5) / 1.5;
}

const FORCE_ITERATIONS = 60;
const SPRING_REST = 40;
const SPRING_K = 0.02;

export function computeLayout(model: SpaceModel): LayoutResult {
  const ids = model.stars.map((s) => s.id);
  const index = new Map(ids.map((id, i) => [id, i]));
  const positions = new Float32Array(ids.length * 3);

  // Package ring anchors — angle slot from the package id hash (stable regardless of
  // which other packages exist), radius from total scale.
  const totalFiles = model.stars.length || 1;
  const ringRadius = 420 * Math.max(1, Math.sqrt(totalFiles / 300));
  const anchors = new Map<string, [number, number, number]>();
  for (const p of model.packages) {
    const rng = mulberry32(hash32("pkg:" + p.id));
    const angle = rng() * Math.PI * 2;
    anchors.set(p.id, [Math.cos(angle) * ringRadius, (rng() - 0.5) * 160, Math.sin(angle) * ringRadius]);
  }

  for (const s of model.stars) {
    const i = index.get(s.id)! * 3;
    if (!s.pkg) {
      const rng = mulberry32(hash32("orphan:" + s.id));
      positions[i] = gaussOf(rng) * ringRadius * 0.35;
      positions[i + 1] = gaussOf(rng) * ringRadius * 0.2;
      positions[i + 2] = gaussOf(rng) * ringRadius * 0.35;
      continue;
    }
    const [ax, ay, az] = anchors.get(s.pkg) ?? [0, 0, 0];
    const fileCount = model.packages.find((p) => p.id === s.pkg)?.fileCount ?? 10;
    const spread = Math.min(260, 60 + fileCount * 0.85);
    const dirRng = mulberry32(hash32("dir:" + s.pkg + ":" + s.dir));
    const cx = ax + gaussOf(dirRng) * spread * 0.8;
    const cy = ay + gaussOf(dirRng) * spread * 0.3;
    const cz = az + gaussOf(dirRng) * spread * 0.8;
    const rng = mulberry32(hash32("star:" + s.id));
    positions[i] = cx + gaussOf(rng) * spread * 0.55;
    positions[i + 1] = cy + gaussOf(rng) * spread * 0.22;
    positions[i + 2] = cz + gaussOf(rng) * spread * 0.55;
  }

  // Spring refinement over intra-package links (deterministic iteration order).
  const springs = model.links
    .map((l) => ({ a: index.get(l.a), b: index.get(l.b), w: Math.min(4, Math.sqrt(l.count)) }))
    .filter((s): s is { a: number; b: number; w: number } => s.a !== undefined && s.b !== undefined);
  for (let iter = 0; iter < FORCE_ITERATIONS; iter++) {
    for (const sp of springs) {
      const ia = sp.a * 3;
      const ib = sp.b * 3;
      const dx = positions[ib] - positions[ia];
      const dy = positions[ib + 1] - positions[ia + 1];
      const dz = positions[ib + 2] - positions[ia + 2];
      const dist = Math.hypot(dx, dy, dz) || 1;
      const f = SPRING_K * sp.w * (dist - SPRING_REST) / dist / 2;
      positions[ia] += dx * f; positions[ia + 1] += dy * f; positions[ia + 2] += dz * f;
      positions[ib] -= dx * f; positions[ib + 1] -= dy * f; positions[ib + 2] -= dz * f;
    }
  }

  return { ids, positions };
}
```

- [ ] **Step 4: Worker + GraphApp wiring**

```typescript
// apps/vscode-extension/webview-ui/src/graph/layout.worker.ts
import { computeLayout } from "./layout";
import type { SpaceModel } from "./types";

self.onmessage = (ev: MessageEvent<SpaceModel>) => {
  const result = computeLayout(ev.data);
  (self as unknown as Worker).postMessage(result, [result.positions.buffer]);
};
```

In `GraphApp.tsx`, replace the naive zero-layout block with:

```typescript
async function requestLayout(model: SpaceModel): Promise<LayoutResult> {
  try {
    const worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    return await new Promise<LayoutResult>((resolve, reject) => {
      worker.onmessage = (ev: MessageEvent<LayoutResult>) => { worker.terminate(); resolve(ev.data); };
      worker.onerror = (e) => { worker.terminate(); reject(e); };
      worker.postMessage(model);
    });
  } catch {
    // Worker unavailable (restricted webview) — compute synchronously.
    const { computeLayout } = await import("./layout");
    return computeLayout(model);
  }
}
```

and call it from the model effect (`requestLayout(model).then((layout) => sceneRef.current?.setSpace(model, layout))`, guarding against a stale model with a request-id ref).

**Test update required:** layout is now asynchronous, so Task 7's "hands the model to the scene" assertion must become `await waitFor(() => expect(scene.setSpace).toHaveBeenCalledOnce())` (import `waitFor` from `@testing-library/react`). jsdom has no `Worker`, so the try/catch falls through to the synchronous `computeLayout` — still a microtask later.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph`
Expected: PASS (layout + GraphApp suites).

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/graph
git commit -m "feat(graph): deterministic seeded layout with worker + sync fallback"
```

---

### Task 9: Palette, scene math, CameraRig

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/graph/palette.ts`
- Create: `apps/vscode-extension/webview-ui/src/graph/scene-math.ts`
- Create: `apps/vscode-extension/webview-ui/src/graph/scene/camera.ts`
- Test: `apps/vscode-extension/webview-ui/src/graph/scene-math.test.ts`

**Interfaces:**
- Produces:
  - `EMBER` palette constant (Task 10/11 use `EMBER.clusterTints[i % 5]`, `EMBER.kinds[kind]`, `EMBER.star`, `EMBER.beacon`, `EMBER.out`, `EMBER.inn`).
  - `starSize(inDeg, outDeg, isHub): number`, `particleCount(edgeCount): number`, `mixToCounts(kindMix, total): Record<EdgeKind, number>`, `sphericalToPosition(yaw, pitch, radius, target): [number, number, number]`.
  - `class CameraRig { constructor(camera: THREE.PerspectiveCamera); yaw/pitch/radius/target goals; update(dt): void; attach(el: HTMLElement, onClick: (x: number, y: number) => void): () => void; flyTo(target: [number,number,number], radius: number): void; reset(): void }` — drag orbit, wheel dolly, inertia smoothing `k = 1 - exp(-4.2 * dt)`, idle drift toggle. (`onClick` fires only when total drag < 6px.)

- [ ] **Step 1: Failing scene-math tests**

```typescript
// apps/vscode-extension/webview-ui/src/graph/scene-math.test.ts
import { describe, expect, it } from "vitest";
import { mixToCounts, particleCount, sphericalToPosition, starSize } from "./scene-math";

describe("scene math", () => {
  it("starSize grows sub-linearly with degree and boosts hubs", () => {
    expect(starSize(0, 0, false)).toBeGreaterThan(0);
    expect(starSize(20, 20, false)).toBeGreaterThan(starSize(2, 2, false));
    expect(starSize(20, 20, false)).toBeLessThan(starSize(2, 2, false) * 5);
    expect(starSize(10, 10, true)).toBeGreaterThan(starSize(10, 10, false));
  });

  it("particleCount is log-scaled and capped at 48", () => {
    expect(particleCount(10)).toBeGreaterThanOrEqual(6);
    expect(particleCount(1840)).toBeGreaterThan(particleCount(100));
    expect(particleCount(1_000_000)).toBeLessThanOrEqual(48);
  });

  it("mixToCounts allocates exactly total via largest remainder", () => {
    const c = mixToCounts({ Imports: 62, Calls: 30, Inherits: 8 }, 20);
    expect(c.Imports + c.Calls + c.Inherits + c.References).toBe(20);
    expect(c.Imports).toBeGreaterThan(c.Calls);
    expect(mixToCounts({}, 10).Imports).toBe(10); // degenerate mix -> all Imports
  });

  it("sphericalToPosition matches hand-computed axes", () => {
    const [x, y, z] = sphericalToPosition(0, 0, 100, [0, 0, 0]);
    expect(x).toBeCloseTo(0);
    expect(y).toBeCloseTo(0);
    expect(z).toBeCloseTo(100);
    const [, y2] = sphericalToPosition(0, Math.PI / 2, 100, [0, 0, 0]);
    expect(y2).toBeCloseTo(100);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph/scene-math.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement palette + scene-math**

```typescript
// apps/vscode-extension/webview-ui/src/graph/palette.ts
// Ember Dusk — the approved AXON palette (spec: Design language). v1 ships only this.
export const EMBER = {
  bgTop: "#160709",
  bgBot: "#070203",
  clusterTints: ["#fb923c", "#f472b6", "#fbbf24", "#e879f9", "#93c5fd"],
  star: "#fff4ea",
  beacon: "#fde047",
  kinds: {
    Imports: "#fb923c",
    Calls: "#fbbf24",
    Inherits: "#f472b6",
    References: "#a58d92",
  } as const,
  out: "#fbbf24",
  inn: "#f472b6",
} as const;
```

```typescript
// apps/vscode-extension/webview-ui/src/graph/scene-math.ts
// Pure scene math — kept WebGL-free so it runs in jsdom tests.
import type { EdgeKind } from "./types";

export function starSize(inDeg: number, outDeg: number, isHub: boolean): number {
  return 1.6 + Math.sqrt(inDeg + outDeg) * 0.62 + (isHub ? 2.2 : 0);
}

export function particleCount(edgeCount: number): number {
  return Math.min(48, Math.round(6 + 14 * Math.log10(Math.max(10, edgeCount))));
}

const KINDS: EdgeKind[] = ["Imports", "Calls", "Inherits", "References"];

/** Largest-remainder allocation of `total` particles across a kind mix. */
export function mixToCounts(
  kindMix: Partial<Record<EdgeKind, number>>,
  total: number
): Record<EdgeKind, number> {
  const sum = KINDS.reduce((acc, k) => acc + (kindMix[k] ?? 0), 0);
  const out = { Imports: 0, Calls: 0, Inherits: 0, References: 0 } as Record<EdgeKind, number>;
  if (sum === 0) {
    out.Imports = total;
    return out;
  }
  const exact = KINDS.map((k) => ((kindMix[k] ?? 0) / sum) * total);
  let used = 0;
  exact.forEach((e, i) => {
    out[KINDS[i]] = Math.floor(e);
    used += Math.floor(e);
  });
  const remainders = exact
    .map((e, i) => ({ i, r: e - Math.floor(e) }))
    .sort((a, b) => b.r - a.r);
  for (let j = 0; j < total - used; j++) out[KINDS[remainders[j % 4].i]] += 1;
  return out;
}

export function sphericalToPosition(
  yaw: number,
  pitch: number,
  radius: number,
  target: [number, number, number]
): [number, number, number] {
  return [
    target[0] + radius * Math.cos(pitch) * Math.sin(yaw),
    target[1] + radius * Math.sin(pitch),
    target[2] + radius * Math.cos(pitch) * Math.cos(yaw),
  ];
}
```

- [ ] **Step 4: Implement CameraRig**

```typescript
// apps/vscode-extension/webview-ui/src/graph/scene/camera.ts
import type * as THREE from "three";
import { sphericalToPosition } from "../scene-math";

const SMOOTH = 4.2;
const MIN_RADIUS = 70;
const MAX_RADIUS = 4200;
const DRIFT_RATE = 0.038;

/** Orbit camera with smoothed goals: drag = yaw/pitch, wheel = dolly, flyTo = tween.
 * All motion converges via k = 1 - exp(-SMOOTH * dt) each frame. */
export class CameraRig {
  yaw = 0.8; pitch = 0.34; radius = 3400;
  target: [number, number, number] = [0, 0, 0];
  gYaw = 0.8; gPitch = 0.34; gRadius = 1350;
  gTarget: [number, number, number] = [0, 0, 0];
  drift = true;

  constructor(private readonly camera: THREE.PerspectiveCamera) {}

  update(dt: number): void {
    if (this.drift) this.gYaw += dt * DRIFT_RATE;
    const k = 1 - Math.exp(-SMOOTH * dt);
    this.yaw += (this.gYaw - this.yaw) * k;
    this.pitch += (this.gPitch - this.pitch) * k;
    this.radius += (this.gRadius - this.radius) * k;
    for (let i = 0; i < 3; i++) this.target[i] += (this.gTarget[i] - this.target[i]) * k;
    const [x, y, z] = sphericalToPosition(this.yaw, this.pitch, this.radius, this.target);
    this.camera.position.set(x, y, z);
    this.camera.lookAt(this.target[0], this.target[1], this.target[2]);
  }

  /** Pointer + wheel handling. onClick fires on pointerup with total drag < 6px,
   * passing client coords; returns a detach function. */
  attach(el: HTMLElement, onClick: (x: number, y: number) => void): () => void {
    let dragging = false;
    let moved = 0;
    let px = 0;
    let py = 0;
    const down = (e: PointerEvent) => {
      dragging = true; moved = 0; px = e.clientX; py = e.clientY;
      el.setPointerCapture(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (!dragging) return;
      const dx = e.clientX - px; const dy = e.clientY - py;
      px = e.clientX; py = e.clientY;
      moved += Math.abs(dx) + Math.abs(dy);
      this.gYaw += dx * 0.0042;
      this.gPitch = Math.max(-1.25, Math.min(1.25, this.gPitch + dy * 0.0032));
      this.drift = false;
    };
    const up = (e: PointerEvent) => {
      dragging = false;
      if (moved < 6) onClick(e.clientX, e.clientY);
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      this.gRadius = Math.max(MIN_RADIUS, Math.min(MAX_RADIUS, this.gRadius * Math.exp(e.deltaY * 0.00095)));
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("wheel", wheel, { passive: false });
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("wheel", wheel);
    };
  }

  flyTo(target: [number, number, number], radius: number): void {
    this.gTarget = [...target] as [number, number, number];
    this.gRadius = radius;
    this.drift = false;
  }

  reset(): void {
    this.gTarget = [0, 0, 0];
    this.gRadius = 1350;
    this.gPitch = 0.34;
    this.drift = true;
  }
}
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/graph
git commit -m "feat(graph): Ember palette, scene math, CameraRig"
```

---

### Task 10: GraphScene + Starfield (stars, nebulae, dust, bloom, labels, picking)

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/graph/scene/starfield.ts`
- Create: `apps/vscode-extension/webview-ui/src/graph/scene/graph-scene.ts`
- Modify: `apps/vscode-extension/webview-ui/src/graph/GraphApp.tsx` (default `createScene` via dynamic import)
- Test: none runnable in jsdom — **visual checkpoint in the dev host is the verification step.** All math that could be unit-tested already lives in scene-math.ts (Task 9).

**Interfaces:**
- Consumes: `SceneHandle`/`SceneCallbacks` (Task 7), `CameraRig` (Task 9), `computeLayout` output, `EMBER`, scene-math helpers.
- Produces: `createGraphScene(canvas, cb): SceneHandle` — the default factory GraphApp dynamic-imports; `class Starfield` used by Task 11/14 with methods `setStars(stars: StarRecord[], layout: LayoutResult)`, `positionOf(id): [number,number,number] | null`, `setDimSet(keep: Set<string> | null, pkgKeep: string | null)`, `morphTo(...)` (Task 14 adds), `update(t: number)`.

This is the heart of the showcase. Calibrate against the approved motion study before starting (open `.superpowers/brainstorm/29677-1783313740/content/axon-design-language.html`).

- [ ] **Step 1: Implement Starfield**

```typescript
// apps/vscode-extension/webview-ui/src/graph/scene/starfield.ts
// Instanced star sprites + package nebula billboards + background dust.
// One THREE.Points for all stars: per-vertex size/color/flags/phase attributes,
// shader-driven twinkle, beacon pulse, hub halo, and per-star dim factor.
import * as THREE from "three";
import { EMBER } from "../palette";
import { starSize } from "../scene-math";
import { hash32, mulberry32 } from "../layout";
import type { LayoutResult, StarRecord } from "../types";

const STAR_VERT = /* glsl */ `
  attribute float aSize;
  attribute vec3 aColor;
  attribute float aFlags;   // 1 = entry beacon, 2 = hub (bitwise via mod)
  attribute float aPhase;
  attribute float aDim;
  uniform float uTime;
  varying vec3 vColor;
  varying float vFlags;
  varying float vPulse;
  varying float vDim;
  void main() {
    vColor = aColor;
    vFlags = aFlags;
    vDim = aDim;
    float tw = 0.82 + 0.18 * sin(uTime * 1.3 + aPhase);
    vPulse = fract(uTime * 0.55 + aPhase);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * tw * (900.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;
const STAR_FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vFlags;
  varying float vPulse;
  varying float vDim;
  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv) * 2.0;
    if (d > 1.0) discard;
    float core = smoothstep(0.5, 0.0, d);
    float halo = smoothstep(1.0, 0.25, d) * 0.55;
    vec3 col = vColor * (core * 1.6 + halo);
    float isEntry = step(0.5, mod(vFlags, 2.0));
    // expanding beacon ring
    float ring = isEntry * smoothstep(0.06, 0.0, abs(d - vPulse)) * (1.0 - vPulse);
    col += vec3(0.99, 0.87, 0.28) * ring;
    float isHub = step(1.5, vFlags);
    col += vColor * isHub * smoothstep(0.05, 0.0, abs(d - 0.82)) * 0.5;
    gl_FragColor = vec4(col * vDim, (core + halo + ring) * vDim);
  }
`;

export class Starfield {
  readonly points: THREE.Points;
  private geo = new THREE.BufferGeometry();
  private mat: THREE.ShaderMaterial;
  private ids: string[] = [];
  private idIndex = new Map<string, number>();
  private stars: StarRecord[] = [];
  private nebulae: THREE.Sprite[] = [];
  private dust: THREE.Points | null = null;
  private pkgIndex = new Map<string, number>();

  constructor(private readonly scene: THREE.Scene) {
    this.mat = new THREE.ShaderMaterial({
      vertexShader: STAR_VERT,
      fragmentShader: STAR_FRAG,
      uniforms: { uTime: { value: 0 } },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    this.points = new THREE.Points(this.geo, this.mat);
    this.points.frustumCulled = false;
    scene.add(this.points);
    this.addDust();
  }

  setStars(stars: StarRecord[], layout: LayoutResult, pkgOrder: string[]): void {
    this.stars = stars;
    this.ids = layout.ids;
    this.idIndex = new Map(layout.ids.map((id, i) => [id, i]));
    this.pkgIndex = new Map(pkgOrder.map((p, i) => [p, i]));
    const n = stars.length;
    const sizes = new Float32Array(n);
    const colors = new Float32Array(n * 3);
    const flags = new Float32Array(n);
    const phases = new Float32Array(n);
    const dims = new Float32Array(n).fill(1);
    const byId = new Map(stars.map((s) => [s.id, s]));
    const c = new THREE.Color();
    layout.ids.forEach((id, i) => {
      const s = byId.get(id);
      if (!s) return;
      sizes[i] = starSize(s.inDeg, s.outDeg, s.isHub) * 3.4;
      const tint = s.isEntry
        ? EMBER.beacon
        : s.pkg
          ? EMBER.clusterTints[(this.pkgIndex.get(s.pkg) ?? 0) % EMBER.clusterTints.length]
          : EMBER.star;
      c.set(tint);
      colors.set([c.r, c.g, c.b], i * 3);
      flags[i] = (s.isEntry ? 1 : 0) + (s.isHub ? 2 : 0);
      phases[i] = (hash32(id) % 6283) / 1000;
    });
    this.geo.setAttribute("position", new THREE.BufferAttribute(layout.positions.slice(), 3));
    this.geo.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
    this.geo.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
    this.geo.setAttribute("aFlags", new THREE.BufferAttribute(flags, 1));
    this.geo.setAttribute("aPhase", new THREE.BufferAttribute(phases, 1));
    this.geo.setAttribute("aDim", new THREE.BufferAttribute(dims, 1));
    this.geo.computeBoundingSphere();
    this.rebuildNebulae(pkgOrder, layout);
  }

  /** Per-package nebula: additive radial-gradient sprite at the package centroid. */
  private rebuildNebulae(pkgOrder: string[], layout: LayoutResult): void {
    for (const n of this.nebulae) this.scene.remove(n);
    this.nebulae = [];
    const centroids = new Map<string, { x: number; y: number; z: number; n: number; spread: number }>();
    this.stars.forEach((s) => {
      if (!s.pkg) return;
      const i = this.idIndex.get(s.id);
      if (i === undefined) return;
      const c = centroids.get(s.pkg) ?? { x: 0, y: 0, z: 0, n: 0, spread: 0 };
      c.x += layout.positions[i * 3];
      c.y += layout.positions[i * 3 + 1];
      c.z += layout.positions[i * 3 + 2];
      c.n += 1;
      centroids.set(s.pkg, c);
    });
    for (const [pkg, c] of centroids) {
      if (!c.n) continue;
      const tint = EMBER.clusterTints[(this.pkgIndex.get(pkg) ?? 0) % EMBER.clusterTints.length];
      const sprite = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: nebulaTexture(tint),
          transparent: true,
          opacity: 0.16,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );
      sprite.position.set(c.x / c.n, c.y / c.n, c.z / c.n);
      const scale = 180 + Math.sqrt(c.n) * 60;
      sprite.scale.set(scale, scale, 1);
      sprite.userData.pkg = pkg;
      this.scene.add(sprite);
      this.nebulae.push(sprite);
    }
  }

  private addDust(): void {
    const rng = mulberry32(1337);
    const n = 420;
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 900 + rng() * 2200;
      const th = rng() * Math.PI * 2;
      const ph = (rng() - 0.5) * Math.PI;
      pos[i * 3] = Math.cos(th) * Math.cos(ph) * r;
      pos[i * 3 + 1] = Math.sin(ph) * r * 0.6;
      pos[i * 3 + 2] = Math.sin(th) * Math.cos(ph) * r;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    this.dust = new THREE.Points(
      g,
      new THREE.PointsMaterial({ color: EMBER.star, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0.28, depthWrite: false })
    );
    this.scene.add(this.dust);
  }

  positionOf(id: string): [number, number, number] | null {
    const i = this.idIndex.get(id);
    if (i === undefined) return null;
    const p = this.geo.getAttribute("position") as THREE.BufferAttribute;
    return [p.getX(i), p.getY(i), p.getZ(i)];
  }

  pkgCentroids(): Map<string, [number, number, number]> {
    const out = new Map<string, [number, number, number]>();
    for (const s of this.nebulae) out.set(s.userData.pkg as string, [s.position.x, s.position.y, s.position.z]);
    return out;
  }

  starAt(index: number): StarRecord | null {
    const id = this.ids[index];
    return this.stars.find((s) => s.id === id) ?? null;
  }

  /** Dim everything except `keep` ids (or a whole kept package). null = undim all. */
  setDimSet(keep: Set<string> | null, pkgKeep: string | null): void {
    const dims = this.geo.getAttribute("aDim") as THREE.BufferAttribute;
    const byId = new Map(this.stars.map((s) => [s.id, s]));
    this.ids.forEach((id, i) => {
      const s = byId.get(id);
      const kept = !keep && !pkgKeep ? true : (keep?.has(id) ?? false) || (pkgKeep !== null && s?.pkg === pkgKeep);
      dims.setX(i, kept ? 1 : 0.18);
    });
    dims.needsUpdate = true;
    for (const n of this.nebulae) {
      const mat = n.material as THREE.SpriteMaterial;
      mat.opacity = !pkgKeep && !keep ? 0.16 : n.userData.pkg === pkgKeep ? 0.22 : 0.06;
    }
  }

  update(t: number): void {
    this.mat.uniforms.uTime.value = t;
  }

  dispose(): void {
    this.geo.dispose();
    this.mat.dispose();
    for (const n of this.nebulae) (n.material as THREE.SpriteMaterial).dispose();
  }
}

const texCache = new Map<string, THREE.CanvasTexture>();
function nebulaTexture(color: string): THREE.CanvasTexture {
  let t = texCache.get(color);
  if (t) return t;
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, color + "cc");
  grad.addColorStop(0.4, color + "44");
  grad.addColorStop(1, "transparent");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  t = new THREE.CanvasTexture(c);
  texCache.set(color, t);
  return t;
}
```

- [ ] **Step 2: Implement GraphScene**

```typescript
// apps/vscode-extension/webview-ui/src/graph/scene/graph-scene.ts
// Owns renderer/composer/frame-loop/picking/labels. GraphApp only sees SceneHandle.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { EMBER } from "../palette";
import type {
  EdgeKind, FileDetail, FocusState, LayoutResult, SceneCallbacks, SceneHandle, SpaceModel, SymbolDetail,
} from "../types";
import { CameraRig } from "./camera";
import { Starfield } from "./starfield";
import { Flows } from "./flows";

export function createGraphScene(canvas: HTMLCanvasElement, cb: SceneCallbacks): SceneHandle {
  return new GraphScene(canvas, cb);
}

class GraphScene implements SceneHandle {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private rig: CameraRig;
  private composer: EffectComposer;
  private starfield: Starfield;
  private flows: Flows;
  private labelCanvas: HTMLCanvasElement;
  private labelCtx: CanvasRenderingContext2D;
  private raycaster = new THREE.Raycaster();
  private model: SpaceModel | null = null;
  private focus: FocusState = { level: 0 };
  private raf = 0;
  private clock = new THREE.Clock();
  private detachInput: () => void;
  private resizeObs: ResizeObserver;

  constructor(private readonly canvas: HTMLCanvasElement, private readonly cb: SceneCallbacks) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.scene.background = new THREE.Color(EMBER.bgBot);
    this.scene.fog = new THREE.FogExp2(EMBER.bgBot, 0.00045);
    this.camera = new THREE.PerspectiveCamera(55, 1, 1, 20000);
    this.rig = new CameraRig(this.camera);
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(1, 1), 0.9, 0.7, 0.12));
    this.starfield = new Starfield(this.scene);
    this.flows = new Flows(this.scene);
    this.raycaster.params.Points = { threshold: 14 };

    // Screen-space label overlay: a sibling 2D canvas the panel positions over the GL one.
    this.labelCanvas = document.createElement("canvas");
    this.labelCanvas.style.cssText = "position:absolute;inset:0;pointer-events:none;";
    canvas.parentElement!.style.position = "relative";
    canvas.parentElement!.appendChild(this.labelCanvas);
    this.labelCtx = this.labelCanvas.getContext("2d")!;

    this.detachInput = this.rig.attach(canvas, (x, y) => this.pick(x, y));
    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(canvas.parentElement!);
    this.resize();
    this.loop();
  }

  private resize(): void {
    const w = this.canvas.parentElement!.clientWidth || 1;
    const h = this.canvas.parentElement!.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.labelCanvas.width = w * window.devicePixelRatio;
    this.labelCanvas.height = h * window.devicePixelRatio;
    this.labelCanvas.style.width = `${w}px`;
    this.labelCanvas.style.height = `${h}px`;
    this.labelCtx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  private loop = (): void => {
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(0.05, this.clock.getDelta());
    const t = this.clock.elapsedTime;
    this.rig.update(dt);
    this.starfield.update(t);
    this.flows.update(t, dt);
    this.composer.render();
    this.drawLabels();
  };

  private pick(x: number, y: number): void {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((x - rect.left) / rect.width) * 2 - 1, -((y - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const sat = this.flows.pickSatellite(this.raycaster);
    if (sat) {
      this.cb.onPickSatellite(sat.symbolId, sat.line);
      return;
    }
    const hits = this.raycaster.intersectObject(this.starfield.points, false);
    if (hits.length && hits[0].index !== undefined) {
      const star = this.starfield.starAt(hits[0].index);
      if (star) {
        this.cb.onPickStar(star.id);
        return;
      }
    }
    // Nebula label pick: nearest package centroid within 60px screen distance
    const pkg = this.nearestPackage(x - rect.left, y - rect.top, 60);
    if (pkg) this.cb.onPickPackage(pkg);
    else this.cb.onBackgroundClick();
  }

  private nearestPackage(sx: number, sy: number, maxPx: number): string | null {
    let best: string | null = null;
    let bd = maxPx;
    const v = new THREE.Vector3();
    for (const [pkg, [x, y, z]] of this.starfield.pkgCentroids()) {
      v.set(x, y, z).project(this.camera);
      if (v.z > 1) continue;
      const px = ((v.x + 1) / 2) * this.canvas.clientWidth;
      const py = ((1 - v.y) / 2) * this.canvas.clientHeight;
      const d = Math.hypot(px - sx, py - sy);
      if (d < bd) { bd = d; best = pkg; }
    }
    return best;
  }

  private drawLabels(): void {
    const ctx = this.labelCtx;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    if (!this.model) return;
    const v = new THREE.Vector3();
    ctx.font = "600 11px ui-sans-serif, system-ui";
    ctx.textAlign = "center";
    for (const [pkg, [x, y, z]] of this.starfield.pkgCentroids()) {
      v.set(x, y, z).project(this.camera);
      if (v.z > 1) continue;
      const px = ((v.x + 1) / 2) * w;
      const py = ((1 - v.y) / 2) * h - 30;
      const dimmed = this.focus.level >= 1 && (this.focus as { pkg?: string }).pkg !== pkg;
      ctx.globalAlpha = dimmed ? 0.25 : 0.85;
      ctx.fillStyle = EMBER.clusterTints[0];
      ctx.fillText(pkg.toUpperCase(), px, py);
    }
    ctx.globalAlpha = 1;
    // Focused star + trace target labels
    ctx.font = "11px ui-monospace, Menlo, monospace";
    for (const { id, tint } of this.flows.labelAnchors()) {
      const p = this.starfield.positionOf(id);
      if (!p) continue;
      v.set(p[0], p[1], p[2]).project(this.camera);
      if (v.z > 1) continue;
      const px = ((v.x + 1) / 2) * w;
      const py = ((1 - v.y) / 2) * h + 18;
      const short = id.slice(id.lastIndexOf("/") + 1);
      ctx.fillStyle = "rgba(7,2,3,0.72)";
      const tw = ctx.measureText(short).width;
      ctx.fillRect(px - tw / 2 - 5, py - 11, tw + 10, 15);
      ctx.fillStyle = tint;
      ctx.fillText(short, px, py);
    }
    this.flows.drawSatelliteLabels(ctx, this.camera, w, h);
  }

  // ---- SceneHandle ----
  setSpace(model: SpaceModel, layout: LayoutResult): void {
    this.model = model;
    const pkgOrder = model.packages.map((p) => p.id);
    this.starfield.setStars(model.stars, layout, pkgOrder);
    this.flows.setBundles(model.bundles, this.starfield.pkgCentroids());
  }

  morph(model: SpaceModel, layout: LayoutResult, removed: string[]): void {
    void removed; // Task 14 replaces with tweened ignite/fade morph
    this.setSpace(model, layout);
  }

  setFocus(focus: FocusState): void {
    this.focus = focus;
    if (focus.level === 0) {
      this.starfield.setDimSet(null, null);
      this.flows.clearTrace();
      this.flows.clearSatellites();
    } else if (focus.level === 1) {
      this.starfield.setDimSet(null, focus.pkg);
      this.flows.clearTrace();
      this.flows.clearSatellites();
    }
    // level 2/3 dim sets are applied by showFileTrace/showSatellites (they know the edge targets)
  }

  showFileTrace(detail: FileDetail): void {
    const keep = new Set<string>([detail.fileId]);
    for (const e of detail.edges) keep.add(e.otherFile);
    this.starfield.setDimSet(keep, null);
    this.flows.traceFile(detail, (id) => this.starfield.positionOf(id));
  }

  showSatellites(detail: FileDetail): void {
    const center = this.starfield.positionOf(detail.fileId);
    if (center) this.flows.showSatellites(detail, center);
  }

  showSymbolTrace(detail: SymbolDetail): void {
    this.flows.traceSymbol(detail, (id) => this.starfield.positionOf(id));
  }

  clearOverlays(): void {
    this.flows.clearTrace();
    this.flows.clearSatellites();
  }

  setLayers(layers: Record<EdgeKind, boolean>): void {
    this.flows.setLayers(layers);
  }

  flyToStar(id: string, radius = 300): void {
    const p = this.starfield.positionOf(id);
    if (p) this.rig.flyTo(p, radius);
  }

  framePackage(pkg: string): void {
    const c = this.starfield.pkgCentroids().get(pkg);
    if (c) this.rig.flyTo(c, 520);
  }

  resetCamera(): void {
    this.rig.reset();
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.detachInput();
    this.resizeObs.disconnect();
    this.starfield.dispose();
    this.flows.dispose();
    this.composer.dispose();
    this.renderer.dispose();
    this.labelCanvas.remove();
  }
}
```

Note: this imports `Flows` from `./flows` which Task 11 creates. To keep Task 10 independently buildable, create `scene/flows.ts` now with the class skeleton whose methods are no-ops returning empty values (`setBundles() {}`, `traceFile() {}`, `traceSymbol() {}`, `showSatellites() {}`, `clearTrace() {}`, `clearSatellites() {}`, `setLayers() {}`, `update() {}`, `dispose() {}`, `pickSatellite(): null { return null; }`, `labelAnchors(): [] { return []; }`, `drawSatelliteLabels() {}`) — Task 11 fills them in.

- [ ] **Step 3: Default factory in GraphApp**

In `GraphApp.tsx`, when no `createScene` prop is passed, dynamic-import the real factory the first time a model arrives:

```typescript
const factoryRef = useRef<Props["createScene"] | null>(createScene ?? null);
// inside the model effect, before creating the scene:
if (!factoryRef.current) {
  const mod = await import("./scene/graph-scene");
  factoryRef.current = mod.createGraphScene;
}
```

(make the effect body an async function invoked immediately; jsdom tests always pass `createScene`, so the dynamic import never runs under vitest).

**WebGL-unavailable fallback (spec: Failure modes):** wrap the factory call in try/catch — `new THREE.WebGLRenderer` throws when no GL context is available. On catch, set a `glFailed` state and render a plain-text fallback instead of the canvas: package list with file counts + a notice ("WebGL unavailable — showing structure only"). Also, while `conn.kind === "empty"` with `building: true`, poll `vscode.postMessage({ type: "refresh" })` every 3s (clear the interval when `space` arrives) so the panel ignites when the index build lands even if the file watcher couldn't arm.

- [ ] **Step 4: Build + typecheck + existing tests**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph && npm run build && cd -`
Expected: PASS; build emits `dist/graph.html` + a graph asset chunk containing three.

- [ ] **Step 5: VISUAL CHECKPOINT (dev host)**

```bash
npm run build
code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/shadow-forge-stress"
# In the dev host: Cmd+Shift+P -> "Crucible: Open Dependency Space (AXON)"
```

Verify against the motion study: stars render with twinkle + package tints, entry beacons pulse, nebulae glow at package centroids, bloom on, drag orbits, scroll dollies, clicking a star logs the pick (info card comes in Task 12). Iterate shader constants until it matches the Ember Dusk prototype's feel. **This step is done only when the space is visibly beautiful, not merely functional.**

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/graph
git commit -m "feat(graph): Three.js scene — instanced starfield, nebulae, bloom, picking, labels"
```

---

### Task 11: Flows — energy beams, trace threads, satellites

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/graph/scene/flows.ts` (replace the Task 10 skeleton)
- Test: covered by the Task 9 `mixToCounts`/`particleCount` unit tests + Task 12's dev-host checkpoint; the curve/particle code itself is WebGL-only.

**Interfaces:**
- Consumes: `Bundle`, `FileDetail`, `SymbolDetail`, `EdgeKind`, `EMBER`, `particleCount`, `mixToCounts`, three.js.
- Produces (exact methods GraphScene already calls):
  - `setBundles(bundles: Bundle[], centroids: Map<string, [number,number,number]>): void`
  - `traceFile(detail: FileDetail, posOf: (id: string) => [number,number,number] | null): void`
  - `traceSymbol(detail: SymbolDetail, posOf: (id) => [number,number,number] | null): void`
  - `showSatellites(detail: FileDetail, center: [number,number,number]): void`
  - `pickSatellite(raycaster: THREE.Raycaster): { symbolId: string; line?: number } | null`
  - `labelAnchors(): { id: string; tint: string }[]` — the focused file + trace targets GraphScene labels
  - `drawSatelliteLabels(ctx, camera, w, h): void`
  - `clearTrace() / clearSatellites() / setLayers(layers) / update(t, dt) / dispose()`

Implementation notes (complete design, code follows the same structure as starfield.ts):

- **Beam** (one per bundle): `THREE.QuadraticBezierCurve3` from `centroids[fromPkg]` to `centroids[toPkg]`, control point = midpoint pushed radially outward from origin by `190 * (0.55 + rand(bundleKey))` and up by 90. Two objects:
  1. a faint path: `THREE.Line` over 26 curve samples, `LineBasicMaterial({ transparent: true, opacity: 0.10, blending: AdditiveBlending, color: dominant kind color })`;
  2. particles: one `THREE.Points` with `particleCount(bundle.count)` vertices; per-vertex attributes `aT0` (seeded random phase), `aSpeed` (0.05–0.125), `aColor` (from `mixToCounts` allocation — each particle is one kind), `aKindId` (0..3 for layer filtering). Vertex shader computes `t = fract(aT0 + uTime * aSpeed)` and evaluates the quadratic bezier from uniforms `uP0/uP1/uP2`, sets `gl_PointSize` with perspective attenuation; fragment shader = soft round sprite with additive glow (reuse the star fragment's core+halo pattern minus flags). Layer filtering: uniform `uKindOn[4]`; vertex shader collapses size to 0 when the particle's kind is off.
- **Trace threads**: on `traceFile`, for each of up to 40 edges build a thin bezier (control = midpoint + seeded jitter 45) colored `EMBER.out` (dir="out") or `EMBER.inn` (dir="in"), opacity 0.34, plus 3 fast particles per thread (same particle shader, speed 0.25–0.45; for `dir:"in"` reverse by negating speed and offsetting phase). Cross-package edges (`crossPackage: true`) route via the bundle control point (control = the matching bundle's `uP1` when one exists) so threads visibly merge into the beam. Store `labelAnchors` = fileId + all otherFiles (tint by direction color). `traceSymbol` identical but sources from `SymbolEdge.fileId` (skip nulls).
- **Satellites**: `showSatellites` builds one `THREE.Points` with ≤ 24 vertices (one per symbol, sorted by line; skip beyond 24) positioned CPU-side every `update`: orbit radius `16 + i * 4.6`, tilt seeded per symbol, angular speed `0.35 + rand*0.5` alternating sign; color by symbol kind — Class→`EMBER.kinds.Inherits`, Function→`EMBER.kinds.Calls`, Method→`EMBER.kinds.Imports`, Interface→`EMBER.beacon`. Keep a parallel `satMeta: { symbolId, name, line }[]`; `pickSatellite` raycasts this Points (threshold 10) and returns `satMeta[index]`. `drawSatelliteLabels` projects each satellite and draws `name` at 10px monospace when camera radius < 260.
- `update(t, dt)` sets `uTime` on all beam/thread materials and advances satellite positions.
- `dispose()` and the two `clear*()` methods remove objects from the scene and dispose geometries/materials — verify with `renderer.info` in the dev host that repeated select/clear cycles don't grow buffer counts.

- [ ] **Step 1: Implement flows.ts per the notes above** (replace every skeleton method; ~300 lines).
- [ ] **Step 2: Typecheck + tests**

Run: `cd apps/vscode-extension/webview-ui && npx tsc --noEmit -p tsconfig.json && npx vitest run src/graph && cd -`
Expected: PASS.

- [ ] **Step 3: VISUAL CHECKPOINT (dev host)**

Rebuild + reload the dev host (same commands as Task 10 Step 5). Verify: beams flow between package nebulae with kind-colored particles; particle density visibly differs between heavy and light bundles; toggling nothing yet (layers UI comes in Task 12) but `scene.setLayers({...References:false})` from the console path works.

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/graph/scene/flows.ts
git commit -m "feat(graph): energy beams, trace threads, orbiting satellites"
```

---

### Task 12: Focus stack, HUD, selection wiring

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/graph/useGraphState.ts`
- Create: `apps/vscode-extension/webview-ui/src/graph/hud/Breadcrumb.tsx`
- Create: `apps/vscode-extension/webview-ui/src/graph/hud/Legend.tsx`
- Create: `apps/vscode-extension/webview-ui/src/graph/hud/EdgeLayers.tsx`
- Create: `apps/vscode-extension/webview-ui/src/graph/hud/InfoCard.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/graph/GraphApp.tsx`
- Test: `apps/vscode-extension/webview-ui/src/graph/useGraphState.test.ts`, `apps/vscode-extension/webview-ui/src/graph/hud/hud.test.tsx`

**Interfaces:**
- Consumes: types from Task 7; scene handle.
- Produces: `graphReducer(state: GraphUiState, action: GraphAction): GraphUiState` + initial state factory `initialGraphState()`. Task 13/14 dispatch through the same reducer.

- [ ] **Step 1: Failing reducer tests**

```typescript
// apps/vscode-extension/webview-ui/src/graph/useGraphState.test.ts
import { describe, expect, it } from "vitest";
import { graphReducer, initialGraphState, type GraphAction, type GraphUiState } from "./useGraphState";

function seq(...actions: GraphAction[]): GraphUiState {
  return actions.reduce(graphReducer, initialGraphState());
}

describe("focus stack", () => {
  it("pickPackage -> L1; pickStar -> L2; dive -> L3; pop unwinds one level", () => {
    let s = seq({ type: "pickPackage", pkg: "apps/web" });
    expect(s.focus).toEqual({ level: 1, pkg: "apps/web" });
    s = graphReducer(s, { type: "pickStar", fileId: "apps/web/src/a.ts", pkg: "apps/web" });
    expect(s.focus).toEqual({ level: 2, pkg: "apps/web", fileId: "apps/web/src/a.ts" });
    s = graphReducer(s, { type: "dive" });
    expect(s.focus.level).toBe(3);
    s = graphReducer(s, { type: "pop" });
    expect(s.focus.level).toBe(2);
    s = graphReducer(s, { type: "pop" });
    expect(s.focus.level).toBe(1);
    s = graphReducer(s, { type: "pop" });
    expect(s.focus).toEqual({ level: 0 });
  });

  it("picking a star from L0 infers its package (L2 directly, breadcrumb intact)", () => {
    const s = seq({ type: "pickStar", fileId: "apps/web/src/a.ts", pkg: "apps/web" });
    expect(s.focus).toEqual({ level: 2, pkg: "apps/web", fileId: "apps/web/src/a.ts" });
  });

  it("pop from L3 with a selected symbol first clears the symbol", () => {
    let s = seq(
      { type: "pickStar", fileId: "a.ts", pkg: "" },
      { type: "dive" },
      { type: "pickSymbol", symbolId: "class:x:A" }
    );
    expect(s.focus).toEqual({ level: 3, pkg: "", fileId: "a.ts", symbolId: "class:x:A" });
    s = graphReducer(s, { type: "pop" });
    expect(s.focus).toEqual({ level: 3, pkg: "", fileId: "a.ts", symbolId: null });
  });
});

describe("edge layers", () => {
  it("References cannot be enabled below L2 and is force-disabled on pop below L2", () => {
    let s = seq({ type: "setLayer", kind: "References", on: true });
    expect(s.layers.References).toBe(false); // ignored at L0
    s = seq(
      { type: "pickStar", fileId: "a.ts", pkg: "" },
      { type: "setLayer", kind: "References", on: true }
    );
    expect(s.layers.References).toBe(true); // allowed at L2
    s = graphReducer(s, { type: "pop" });
    s = graphReducer(s, { type: "pop" });
    expect(s.focus.level).toBe(0);
    expect(s.layers.References).toBe(false); // forced off
  });
});

describe("detail plumbing", () => {
  it("fileDetail message is stored only when it matches the focused file", () => {
    let s = seq({ type: "pickStar", fileId: "a.ts", pkg: "" });
    s = graphReducer(s, {
      type: "hostFileDetail",
      detail: { fileId: "other.ts", symbols: [], edges: [], withinFileCount: 0 },
    });
    expect(s.fileDetail).toBeNull();
    s = graphReducer(s, {
      type: "hostFileDetail",
      detail: { fileId: "a.ts", symbols: [], edges: [], withinFileCount: 0 },
    });
    expect(s.fileDetail?.fileId).toBe("a.ts");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph/useGraphState.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the reducer**

```typescript
// apps/vscode-extension/webview-ui/src/graph/useGraphState.ts
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
```

- [ ] **Step 4: HUD components**

All HUD panels reuse the existing design tokens (`--color-*`) and the glass style used across the webview (`.surface-card` primitives from `index.css` where they fit). Complete components:

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/Breadcrumb.tsx
import type { FocusState } from "../types";

interface Props { focus: FocusState; onPop: () => void; onReset: () => void }

export function Breadcrumb({ focus, onPop, onReset }: Props) {
  const parts: string[] = ["workspace"];
  if (focus.level >= 1) parts.push(focus.pkg || "orphans");
  if (focus.level >= 2) parts.push(focus.fileId.slice(focus.fileId.lastIndexOf("/") + 1));
  if (focus.level === 3 && focus.symbolId) parts.push(focus.symbolId.slice(focus.symbolId.lastIndexOf(":") + 1));
  return (
    <div className="absolute top-4 left-4 flex items-center gap-1.5 px-3 py-2 rounded-xl text-[11px] font-mono
                    bg-[rgba(22,7,9,0.6)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md">
      {parts.map((p, i) => (
        <span key={i} className="flex items-center gap-1.5">
          {i > 0 && <span className="opacity-30">▸</span>}
          <button
            type="button"
            className={i === parts.length - 1 ? "text-[#fff4ea]" : "text-[#fb923c] hover:underline"}
            onClick={() => (i === 0 ? onReset() : i < parts.length - 1 ? onPop() : undefined)}
          >
            {p}
          </button>
        </span>
      ))}
    </div>
  );
}
```

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/Legend.tsx
import { EMBER } from "../palette";

export function Legend() {
  const rows: [string, string][] = [
    [EMBER.star, "file — mass = coupling"],
    [EMBER.beacon, "entry point beacon"],
    [EMBER.kinds.Calls, "energy = dependencies"],
  ];
  return (
    <div className="absolute top-4 right-4 px-4 py-3 rounded-xl text-[11px]
                    bg-[rgba(22,7,9,0.6)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md">
      <div className="text-[9px] uppercase tracking-[0.2em] opacity-50 mb-2">Reading the space</div>
      {rows.map(([c, label]) => (
        <div key={label} className="flex items-center gap-2 mt-1 opacity-80">
          <span className="w-2 h-2 rounded-full" style={{ background: c, boxShadow: `0 0 6px ${c}` }} />
          {label}
        </div>
      ))}
    </div>
  );
}
```

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/EdgeLayers.tsx
import { EMBER } from "../palette";
import type { EdgeKind } from "../types";

interface Props {
  layers: Record<EdgeKind, boolean>;
  focusLevel: number;
  onToggle: (kind: EdgeKind, on: boolean) => void;
}

const KINDS: EdgeKind[] = ["Imports", "Calls", "Inherits", "References"];

export function EdgeLayers({ layers, focusLevel, onToggle }: Props) {
  return (
    <div className="absolute top-36 right-4 px-3 py-3 rounded-xl w-40
                    bg-[rgba(22,7,9,0.6)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md">
      <div className="text-[9px] uppercase tracking-[0.2em] opacity-50 mb-1">Edge layers</div>
      {KINDS.map((k) => {
        const disabled = k === "References" && focusLevel < 2;
        return (
          <button
            key={k}
            type="button"
            disabled={disabled}
            aria-pressed={layers[k]}
            onClick={() => onToggle(k, !layers[k])}
            title={disabled ? "References edges are scoped to a focused file (select a star)" : undefined}
            className={`flex items-center gap-2 w-full px-2 py-1.5 mt-1 rounded-md text-[11px] text-left
                        ${layers[k] ? "bg-[rgba(251,146,60,0.12)]" : "opacity-45"}
                        ${disabled ? "opacity-25 cursor-not-allowed" : "hover:bg-[rgba(255,244,234,0.07)]"}`}
          >
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: EMBER.kinds[k], boxShadow: layers[k] ? `0 0 6px ${EMBER.kinds[k]}` : "none" }}
            />
            {k}
          </button>
        );
      })}
    </div>
  );
}
```

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/InfoCard.tsx
import type { FileDetail, StarRecord } from "../types";

interface Props {
  star: StarRecord;
  detail: FileDetail | null;
  onOpen: () => void;
  onDive: () => void;
}

export function InfoCard({ star, detail, onOpen, onDive }: Props) {
  const name = star.id.slice(star.id.lastIndexOf("/") + 1);
  return (
    <div className="absolute bottom-5 left-4 w-80 px-4 py-4 rounded-xl
                    bg-[rgba(22,7,9,0.65)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md">
      <div className="text-[10px] font-mono opacity-45 break-all">{star.id}</div>
      <div className="text-[15px] font-bold mt-1 text-[#fff4ea]">{name}</div>
      <div className="text-[9px] uppercase tracking-[0.2em] text-[#fbbf24] mt-0.5">
        {star.isEntry ? "entry point · " : star.isHub ? "hub · " : ""}file
      </div>
      <div className="flex gap-5 my-3">
        {[
          [star.outDeg, "outgoing"],
          [star.inDeg, "incoming"],
          [star.symbolCount, "symbols"],
          [detail?.withinFileCount ?? "…", "within-file"],
        ].map(([v, l]) => (
          <div key={String(l)}>
            <div className="text-[16px] font-bold text-[#fff4ea]">{v}</div>
            <div className="text-[8.5px] uppercase tracking-[0.15em] opacity-45">{l}</div>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <button type="button" onClick={onOpen}
          className="flex-1 py-2 rounded-lg text-[11px] font-semibold bg-[#fb923c] text-[#160709]">
          Open in editor
        </button>
        <button type="button" onClick={onDive}
          className="flex-1 py-2 rounded-lg text-[11px] font-semibold border border-[rgba(251,146,60,0.3)] text-[#fff4ea]">
          Dive inside
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: HUD tests**

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/hud.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Breadcrumb } from "./Breadcrumb";
import { EdgeLayers } from "./EdgeLayers";
import { InfoCard } from "./InfoCard";

describe("EdgeLayers", () => {
  const layers = { Imports: true, Calls: true, Inherits: true, References: false };

  it("disables References below focus level 2", () => {
    render(<EdgeLayers layers={layers} focusLevel={0} onToggle={vi.fn()} />);
    expect(screen.getByRole("button", { name: /references/i })).toHaveProperty("disabled", true);
  });

  it("enables References at level 2 and reports toggles", () => {
    const onToggle = vi.fn();
    render(<EdgeLayers layers={layers} focusLevel={2} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: /references/i }));
    expect(onToggle).toHaveBeenCalledWith("References", true);
  });
});

describe("Breadcrumb", () => {
  it("renders the focus path and pops on ancestor click", () => {
    const onPop = vi.fn();
    render(
      <Breadcrumb focus={{ level: 2, pkg: "apps/web", fileId: "apps/web/src/a.ts" }} onPop={onPop} onReset={vi.fn()} />
    );
    expect(screen.getByText("apps/web")).toBeTruthy();
    fireEvent.click(screen.getByText("apps/web"));
    expect(onPop).toHaveBeenCalled();
  });
});

describe("InfoCard", () => {
  it("shows rollups and fires open/dive", () => {
    const onOpen = vi.fn();
    const onDive = vi.fn();
    render(
      <InfoCard
        star={{ id: "apps/web/src/a.ts", pkg: "apps/web", dir: "apps/web/src", symbolCount: 7, inDeg: 3, outDeg: 5, kindMix: {}, isEntry: false, isHub: true }}
        detail={null}
        onOpen={onOpen}
        onDive={onDive}
      />
    );
    expect(screen.getByText("a.ts")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /open in editor/i }));
    fireEvent.click(screen.getByRole("button", { name: /dive inside/i }));
    expect(onOpen).toHaveBeenCalled();
    expect(onDive).toHaveBeenCalled();
  });
});
```

- [ ] **Step 6: Wire GraphApp**

Rework `GraphApp` to `useReducer(graphReducer, undefined, initialGraphState)`:
- Scene callbacks dispatch: `onPickStar` → look up the star's pkg from the model → `{type:"pickStar", fileId, pkg}` + `scene.flyToStar(id)`; `onPickPackage` → `{type:"pickPackage"}` + `scene.framePackage(pkg)`; `onBackgroundClick` → `{type:"pop"}`; `onPickSatellite(symbolId, line)` → if already at L3 pick the symbol, else openFile at the line.
- Effects keyed on `state.focus`: L2 entered → `vscode.postMessage({type:"fileDetail", fileId})`; `state.fileDetail` arrives → `scene.showFileTrace(detail)` (and `scene.showSatellites(detail)` when L3); L3 symbol picked → post `symbolDetail`; `state.symbolDetail` → `scene.showSymbolTrace`. Focus change always calls `scene.setFocus(focus)`; `pop` to L0/L1 calls `scene.clearOverlays()` + `resetCamera()` at L0.
- `Escape` keydown → dispatch pop.
- `state.layers` change → `scene.setLayers(layers)`.
- Render HUD: `<Breadcrumb>`, `<Legend>`, `<EdgeLayers>`, and `<InfoCard>` when L≥2 (star from model, `onOpen` posts `{type:"openFile", path: fileId}`, `onDive` dispatches dive).

Extend `GraphApp.test.tsx` with two integration cases (fake scene):

```tsx
it("clicking through star -> requests fileDetail and shows the info card", () => {
  const scene = fakeScene();
  let cb: SceneCallbacks | null = null;
  render(<GraphApp createScene={(_c, callbacks) => ((cb = callbacks), scene)} />);
  hostPost({ type: "space", staleAgeSec: null, model: modelWithOneStar });
  act(() => cb!.onPickStar("apps/web/src/a.ts"));
  expect(postMessage).toHaveBeenCalledWith({ type: "fileDetail", fileId: "apps/web/src/a.ts" });
  expect(screen.getByRole("button", { name: /open in editor/i })).toBeTruthy();
});

it("Escape pops focus", () => {
  const scene = fakeScene();
  let cb: SceneCallbacks | null = null;
  render(<GraphApp createScene={(_c, callbacks) => ((cb = callbacks), scene)} />);
  hostPost({ type: "space", staleAgeSec: null, model: modelWithOneStar });
  act(() => cb!.onPickStar("apps/web/src/a.ts"));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(scene.setFocus).toHaveBeenLastCalledWith({ level: 1, pkg: "apps/web" });
});
```

(`modelWithOneStar` = the Task 7 empty model with one `StarRecord` for `apps/web/src/a.ts`, pkg `apps/web`, and one `PackageInfo`.)

- [ ] **Step 7: Run all graph tests**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/graph && cd -`
Expected: PASS.

- [ ] **Step 8: VISUAL CHECKPOINT** — dev host: click a star → space dims, threads materialize with directional particles, info card shows real rollups, Open lands in the editor, Esc unwinds, breadcrumb tracks, References toggle greys out below L2.

- [ ] **Step 9: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/graph
git commit -m "feat(graph): focus stack, HUD chrome, selection tracing wired end to end"
```

---

### Task 13: Search bar (files + lazy symbols), dive polish, open-at-line

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/graph/hud/SearchBar.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/graph/GraphApp.tsx`
- Test: `apps/vscode-extension/webview-ui/src/graph/hud/SearchBar.test.tsx`

**Interfaces:**
- Consumes: `SymbolHit`, `StarRecord`.
- Produces: `<SearchBar stars={StarRecord[]} symbolHits={SymbolHit[]} onQuerySymbols={(q) => void} onGoFile={(fileId) => void} onGoSymbol={(hit) => void} />`. Merged result model: file-name substring matches (top 5, instant) above symbol hits (top 5, lazily fetched, 250ms debounce).

- [ ] **Step 1: Failing tests**

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/SearchBar.test.tsx
import { fireEvent, render, screen, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { SearchBar } from "./SearchBar";
import type { StarRecord } from "../types";

function star(id: string): StarRecord {
  return { id, pkg: "apps/web", dir: "", symbolCount: 0, inDeg: 0, outDeg: 0, kindMix: {}, isEntry: false, isHub: false };
}

describe("SearchBar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows instant file matches and debounces symbol queries", () => {
    const onQuerySymbols = vi.fn();
    render(
      <SearchBar stars={[star("apps/web/src/engine.ts"), star("apps/web/src/other.ts")]}
        symbolHits={[]} onQuerySymbols={onQuerySymbols} onGoFile={vi.fn()} onGoSymbol={vi.fn()} />
    );
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "engi" } });
    expect(screen.getByText("apps/web/src/engine.ts")).toBeTruthy();
    expect(onQuerySymbols).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(300));
    expect(onQuerySymbols).toHaveBeenCalledWith("engi");
  });

  it("Enter selects the first result", () => {
    const onGoFile = vi.fn();
    render(
      <SearchBar stars={[star("apps/web/src/engine.ts")]} symbolHits={[]}
        onQuerySymbols={vi.fn()} onGoFile={onGoFile} onGoSymbol={vi.fn()} />
    );
    const input = screen.getByPlaceholderText(/search/i);
    fireEvent.change(input, { target: { value: "engine" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onGoFile).toHaveBeenCalledWith("apps/web/src/engine.ts");
  });

  it("symbol hits render beneath file hits with kind badges", () => {
    render(
      <SearchBar stars={[]} symbolHits={[{ symbolId: "class:x:Engine", name: "Engine", kind: "Class", fileId: "a.ts", line: 4 }]}
        onQuerySymbols={vi.fn()} onGoFile={vi.fn()} onGoSymbol={vi.fn()} />
    );
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "eng" } });
    expect(screen.getByText("Engine")).toBeTruthy();
    expect(screen.getByText("Class")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/graph/hud/SearchBar.test.tsx` → FAIL (module not found).

- [ ] **Step 3: Implement SearchBar**

```tsx
// apps/vscode-extension/webview-ui/src/graph/hud/SearchBar.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import type { StarRecord, SymbolHit } from "../types";

interface Props {
  stars: StarRecord[];
  symbolHits: SymbolHit[];
  onQuerySymbols: (q: string) => void;
  onGoFile: (fileId: string) => void;
  onGoSymbol: (hit: SymbolHit) => void;
}

export function SearchBar({ stars, symbolHits, onQuerySymbols, onGoFile, onGoSymbol }: Props) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const fileHits = useMemo(() => {
    const lq = q.trim().toLowerCase();
    if (!lq) return [];
    return stars.filter((s) => s.id.toLowerCase().includes(lq)).slice(0, 5);
  }, [q, stars]);

  const symHits = q.trim() ? symbolHits.slice(0, 5) : [];
  const total = fileHits.length + symHits.length;

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const lq = q.trim();
    if (!lq) return;
    debounceRef.current = setTimeout(() => onQuerySymbols(lq), 250);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [q, onQuerySymbols]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function go(i: number): void {
    if (i < fileHits.length) onGoFile(fileHits[i].id);
    else if (symHits[i - fileHits.length]) onGoSymbol(symHits[i - fileHits.length]);
    setQ("");
    setSel(0);
  }

  return (
    <div className="absolute bottom-5 left-1/2 -translate-x-1/2 w-[420px]">
      {total > 0 && (
        <div className="mb-1.5 rounded-xl overflow-hidden bg-[rgba(22,7,9,0.85)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md">
          {fileHits.map((s, i) => (
            <button key={s.id} type="button" onClick={() => go(i)}
              className={`flex justify-between w-full px-3 py-2 text-[11px] font-mono text-left
                          ${i === sel ? "bg-[rgba(251,146,60,0.16)]" : ""}`}>
              <span className="text-[#fff4ea]">{s.id}</span>
              <span className="text-[9px] uppercase tracking-widest opacity-40">file</span>
            </button>
          ))}
          {symHits.map((hit, j) => (
            <button key={hit.symbolId} type="button" onClick={() => go(fileHits.length + j)}
              className={`flex justify-between w-full px-3 py-2 text-[11px] font-mono text-left
                          ${fileHits.length + j === sel ? "bg-[rgba(251,146,60,0.16)]" : ""}`}>
              <span className="text-[#fff4ea]">{hit.name}</span>
              <span className="text-[9px] uppercase tracking-widest opacity-40">{hit.kind}</span>
            </button>
          ))}
        </div>
      )}
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => { setQ(e.target.value); setSel(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { setSel((s) => Math.min(total - 1, s + 1)); e.preventDefault(); }
          if (e.key === "ArrowUp") { setSel((s) => Math.max(0, s - 1)); e.preventDefault(); }
          if (e.key === "Enter" && total) go(sel);
          if (e.key === "Escape") setQ("");
        }}
        placeholder="search files & symbols — fly to anything (⌘K)"
        className="w-full px-4 py-3 rounded-xl text-[12px] font-mono outline-none
                   bg-[rgba(22,7,9,0.6)] border border-[rgba(251,146,60,0.22)] backdrop-blur-md
                   text-[#fff4ea] placeholder:opacity-35 focus:border-[rgba(251,146,60,0.5)]"
      />
    </div>
  );
}
```

- [ ] **Step 4: Wire into GraphApp**

- `onQuerySymbols` → `vscode.postMessage({type:"searchSymbols", query})`; `hostSymbolHits` action already stores results.
- `onGoFile(fileId)` → look up star → dispatch `pickStar` + `scene.flyToStar(fileId, 260)`.
- `onGoSymbol(hit)` → dispatch `pickStar` for `hit.fileId`, then `dive`, then `pickSymbol(hit.symbolId)`; `scene.flyToStar(hit.fileId, 120)`.
- Satellite double-click open-at-line: GraphApp's `onPickSatellite(symbolId, line)` — when the same satellite is picked twice in <400ms, post `{type:"openFile", path: focus.fileId, line}`.

- [ ] **Step 5: Run tests** — `npx vitest run src/graph` → PASS.

- [ ] **Step 6: VISUAL CHECKPOINT** — dev host: `⌘K`, type `engine`, Enter warps + focuses; type a symbol name → badge rows appear after the debounce; satellite double-click opens the file at the right line.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/graph
git commit -m "feat(graph): search-warp for files and symbols, satellite open-at-line"
```

---

### Task 14: Live refresh morph + stale chip + monster-repo LOD

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/graph/GraphApp.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/graph/scene/starfield.ts` + `graph-scene.ts` (real morph)
- Create: `apps/vscode-extension/webview-ui/src/graph/lod.ts`
- Test: `apps/vscode-extension/webview-ui/src/graph/lod.test.ts`

**Interfaces:**
- Consumes: `SpaceDiff` (host already posts it — Task 6's watcher), `SpaceModel`.
- Produces: `applyDiff(model: SpaceModel, diff: SpaceDiff): SpaceModel` and `aggregateToDirs(model: SpaceModel): SpaceModel` in `lod.ts`.

- [ ] **Step 1: Failing tests**

```typescript
// apps/vscode-extension/webview-ui/src/graph/lod.test.ts
import { describe, expect, it } from "vitest";
import { aggregateToDirs, applyDiff, LOD_STAR_THRESHOLD } from "./lod";
import type { SpaceModel, StarRecord } from "./types";

function star(id: string, pkg: string): StarRecord {
  return { id, pkg, dir: id.slice(0, id.lastIndexOf("/")), symbolCount: 2, inDeg: 1, outDeg: 1, kindMix: {}, isEntry: false, isHub: false };
}
function model(stars: StarRecord[]): SpaceModel {
  return { workspaceRoot: "/ws", generatedAtMs: 1, packages: [{ id: "apps/web", fileCount: stars.length, dirs: [] }], stars, bundles: [], intraBundles: [], links: [] };
}

describe("applyDiff", () => {
  it("adds, removes, replaces changed stars, swaps aggregates", () => {
    const m = model([star("apps/web/a.ts", "apps/web"), star("apps/web/b.ts", "apps/web")]);
    const changed = { ...star("apps/web/a.ts", "apps/web"), symbolCount: 9 };
    const next = applyDiff(m, {
      added: [star("apps/web/c.ts", "apps/web")],
      removed: ["apps/web/b.ts"],
      changed: [changed],
      packages: m.packages, bundles: [], intraBundles: [], links: [],
    });
    expect(next.stars.map((s) => s.id).sort()).toEqual(["apps/web/a.ts", "apps/web/c.ts"]);
    expect(next.stars.find((s) => s.id === "apps/web/a.ts")!.symbolCount).toBe(9);
  });
});

describe("aggregateToDirs", () => {
  it("collapses files to one star per directory with summed rollups", () => {
    const m = model([star("apps/web/src/a.ts", "apps/web"), star("apps/web/src/b.ts", "apps/web"), star("apps/web/lib/c.ts", "apps/web")]);
    const agg = aggregateToDirs(m);
    expect(agg.stars).toHaveLength(2);
    const src = agg.stars.find((s) => s.id === "dir:apps/web/src")!;
    expect(src.symbolCount).toBe(4);
    expect(src.inDeg).toBe(2);
  });

  it("threshold constant is 5000", () => {
    expect(LOD_STAR_THRESHOLD).toBe(5000);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/graph/lod.test.ts` → FAIL.

- [ ] **Step 3: Implement lod.ts**

```typescript
// apps/vscode-extension/webview-ui/src/graph/lod.ts
import type { SpaceDiff, SpaceModel, StarRecord } from "./types";

/** Above this many files, L0 renders directory-level stars (spec: monster repos). */
export const LOD_STAR_THRESHOLD = 5000;

export function applyDiff(model: SpaceModel, diff: SpaceDiff): SpaceModel {
  const removed = new Set(diff.removed);
  const changedById = new Map(diff.changed.map((s) => [s.id, s]));
  const stars = model.stars
    .filter((s) => !removed.has(s.id))
    .map((s) => changedById.get(s.id) ?? s)
    .concat(diff.added);
  return {
    ...model,
    stars,
    packages: diff.packages,
    bundles: diff.bundles,
    intraBundles: diff.intraBundles,
    links: diff.links,
  };
}

/** Collapse each (pkg, dir) group into one aggregate star. Aggregate ids are
 * prefixed "dir:" so a pick handler can route them to package focus. */
export function aggregateToDirs(model: SpaceModel): SpaceModel {
  const groups = new Map<string, StarRecord>();
  for (const s of model.stars) {
    const key = `dir:${s.dir || s.pkg || "root"}`;
    let g = groups.get(key);
    if (!g) {
      g = { id: key, pkg: s.pkg, dir: s.dir, symbolCount: 0, inDeg: 0, outDeg: 0, kindMix: {}, isEntry: false, isHub: false };
      groups.set(key, g);
    }
    g.symbolCount += s.symbolCount;
    g.inDeg += s.inDeg;
    g.outDeg += s.outDeg;
    g.isEntry = g.isEntry || s.isEntry;
    g.isHub = g.isHub || s.isHub;
  }
  return { ...model, stars: [...groups.values()], links: [] };
}
```

- [ ] **Step 4: Wire refresh + LOD into GraphApp and the scene**

- `spaceDiff` host message → `setModel(applyDiff(model, diff))`; the layout effect recomputes via the worker and calls `scene.morph(model, layout, diff.removed)` instead of `setSpace` (track "was this a diff" with a ref).
- **Starfield.morphTo:** implement in `starfield.ts` — keep a `tweens: {i: number; from: [n,n,n]; to: [n,n,n]; t0: number}[]` list; on morph, for existing ids tween position over 900ms ease-in-out inside `update()`; new ids spawn at their target with `aSize` animated 0→final ("ignite" — 600ms overshoot 1.4×); removed ids animate `aDim`→0 over 400ms, then the geometry is rebuilt without them. `graph-scene.ts` `morph()` delegates to it (replacing the Task 10 stub) and refreshes nebulae + bundles after the rebuild.
- If the focused file id vanishes in a diff: dispatch pop to its package (reducer `pop` from L2/L3) — GraphApp compares `state.focus` fileId against `diff.removed`.
- **Stale chip:** GraphApp stores `staleAgeSec` from the `space` message; when non-null render a small chip next to the breadcrumb: `index stale · {Math.round(age/60)}m — watching for rebuild` in the same glass style.
- **LOD:** when `model.stars.length > LOD_STAR_THRESHOLD && focus.level === 0`, feed `aggregateToDirs(model)` to layout+scene; entering L1 (or any deeper focus) feeds the real model filtered implicitly by dimming. Picking a `dir:`-prefixed star routes to `pickPackage(star.pkg)`.

- [ ] **Step 5: Run all suites** — `npx vitest run src/graph` → PASS.

- [ ] **Step 6: VISUAL CHECKPOINT** — dev host on `shadow-forge-stress`: `touch` a tracked file to trigger the indexer watcher (or edit a file); when the snapshot rewrites, the space morphs — no camera jump, no focus loss; new file ignites.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/graph
git commit -m "feat(graph): live snapshot morph, stale chip, monster-repo dir LOD"
```

---

### Task 15: Full verification + live smoke

**Files:** none created — verification only.

- [ ] **Step 1: Full builds + suites**

```bash
npm run build && npm run typecheck && npm run test
npm run -w crucible-vscode-extension test
cd apps/vscode-extension/webview-ui && npx vitest run && npm run build && cd -
```
Expected: all PASS. Confirm `dist/assets/index.js` (chat bundle) byte size is within noise of main — three.js must only appear in the graph chunk.

- [ ] **Step 2: Real-snapshot spot check**

Write a throwaway script (run with `npx tsx` from `apps/vscode-extension/` to sidestep module-format friction):

```typescript
// /tmp/axon-spot-check.ts
import { GraphSnapshotStore } from "./src/graph/snapshot-store.js";
const st = new GraphSnapshotStore(
  `${process.env.HOME}/projects/AI editor/workspaces/shadow-forge-stress/.ai-editor/index-snapshot.json`
);
const m = st.load();
console.log("stars", m.stars.length, "packages", m.packages.map((p) => p.id), "bundles", m.bundles.length);
console.log("entries", m.stars.filter((s) => s.isEntry).length, "hubs", m.stars.filter((s) => s.isHub).length);
```
Expected: ~314 stars, packages resembling `apps/*`+`services/*`, ≥1 bundle, ≥1 entry, ≥1 hub.

- [ ] **Step 3: Live smoke (CDP recipe, per memory `smoke_controller_cdp_driving_recipe`)**

Dev host against `workspaces/shadow-forge-stress`:
1. Open panel via the chat-header orbit button AND via the command palette.
2. Space renders ≥300 stars at 60fps (check with the FPS meter in DevTools performance overlay); beams flow.
3. Click a star → dim + threads + info card; **Open in editor** lands on the exact file.
4. Dive → satellites orbit with labels; double-click a satellite → editor at that symbol's line.
5. `⌘K` search `client` → Enter → warp + focus.
6. Esc ×3 unwinds L2→L1→L0; breadcrumb tracks.
7. Edit a file in the workspace → snapshot rewrite → morph without camera/focus loss.
8. Delete `.ai-editor/index-snapshot.json` → reopen panel → EmptyState with working **Build index** CTA.

- [ ] **Step 4: Final commit + docs touch**

Add a one-paragraph AXON section to `CLAUDE.md` under the extension architecture notes (panel, command, data flow one-liner, LOD threshold, the "external nodes carry the importer's path" gotcha).

```bash
git add CLAUDE.md
git commit -m "docs: AXON dependency-space panel notes"
```

---

## Self-Review (completed)

- **Spec coverage:** surface/command/icon (T6-7), SpaceModel filtering+bundles+entries (T1-3), lazy detail (T4-5), layout worker determinism (T8), Ember renderer+bloom+labels (T9-10), beams/threads/satellites (T11), focus stack+layer gating+info card (T12), search+open-at-line (T13), live refresh+stale chip+LOD+WebGL-unavailable fallback — **gap found and noted:** the spec's WebGL-unavailable plain-text fallback is folded into Task 10 Step 3 (wrap renderer construction in try/catch → render the package/count list); implementer: do not skip it. Ride-the-thread camera travel is deliberately reduced to fly-to-on-target-click in v1 scope of Task 12 (full curve-following ride is a polish follow-up — noted as acceptable v1 deviation, the traversal affordance itself is present).
- **Type consistency:** `SceneHandle` (T7) matches GraphScene (T10) and GraphApp usage (T12-14); `GraphHostDeps` (T5) matches GraphPanel deps (T6); `SpaceDiff` fields consistent across T3/T6/T14.
- **Placeholders:** none — Task 11 carries a full behavioral contract instead of literal GLSL for the second particle system; every other code step is complete code.

