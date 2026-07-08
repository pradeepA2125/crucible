# AXON — 3D Dependency Space Visualizer (design)

**Date:** 2026-07-06
**Status:** approved design, pre-implementation
**Codename:** AXON (working name; shown in the panel header)

## Purpose

A dedicated VS Code webview panel that renders the code knowledge graph produced by
`indexer-rs` (`index-snapshot.json`) as an explorable, animated 3D space: packages as
glowing nebulae, files as stars sized by coupling, dependencies as flowing particle
beams, entry points as pulsing beacons. Semantic zoom reveals successive layers —
package → file → symbol — and every element is selectable and traceable down to the
real edge list.

**Phased intent:** v1 is a showcase-grade *navigation* instrument (fly, search, select,
trace, open-in-editor). Later phases can grow analysis features (metrics overlays,
history playback). This spec covers v1 only.

**Test workspace:** `workspaces/shadow-forge-stress` (4,328 indexed nodes). The AI
editor monorepo itself (61,730 nodes / 94,697 edges) is the stress reference.

## Design language

Palette: **Ember Dusk** — deep maroon-black void (`#160709 → #070203`), cluster tints
`#fb923c / #f472b6 / #fbbf24 / #e879f9 / #93c5fd`, star core `#fff4ea`, beacon
`#fde047`. Edge kinds: Imports `#fb923c`, Calls `#fbbf24`, Inherits `#f472b6`,
References `#a58d92`. Outgoing threads = ember amber, incoming = rose.

The approved motion study (hand-rolled canvas prototype, faithful to the intended
feel) lives at `.superpowers/brainstorm/29677-1783313740/content/axon-design-language.html`.
It demonstrates: nebula glow, star twinkle, beam particle flow, selection dimming,
trace threads with directional particles, entry-beacon pulse rings, hub gravity rings,
the dive-inside satellite orbit study, search-warp, and the HUD glass chrome. The
production renderer must match or exceed this bar. HUD panels reuse the existing
webview design tokens (glass surfaces, uppercase letterspaced labels, monospace paths).

## Architecture

Follows the MemoryPanel pattern exactly:

- **Webview:** new Vite entry in `webview-ui`: `graph.html` → `src/graph/`
  (`main.tsx`, `GraphApp.tsx`, scene modules, worker). React renders HUD only; the
  Three.js scene is owned imperatively by one canvas component.
- **Host, vscode-free:** `apps/vscode-extension/src/graph-data.ts` — snapshot load,
  preprocessing, lazy detail queries, diffing. Unit-testable in the node-env vitest.
- **Host, vscode wiring:** `apps/vscode-extension/src/graph-panel.ts` — panel class
  (CSP/asset rewrite mirroring `chat-panel.ts`), message routing, snapshot mtime
  watcher, `showTextDocument` for open-file.
- **Entry points:** command `aiEditor.openGraphPanel` + an icon button in the chat
  header (`ThreadView.tsx`, alongside the memory `db` icon).
- **New dependency:** `three` (+ its postprocessing pass) in `webview-ui` (~650KB
  added to this panel's bundle only — separate Vite entry, chat bundle unaffected).
- **`extension.ts` owns panel construction** (needs `context.extensionUri`);
  `controller.ts` stays vscode-API-free.

## Data pipeline

The webview never receives the raw snapshot. The host holds the full graph in memory
while the panel is open (released on dispose) and sends a compact **SpaceModel**:

1. **Filter:** drop `Variable` nodes (33k in this repo) and ambient `References`
   edges (51k). They surface only via lazy selection queries.
2. **File rollups:** every `File` node becomes a star record:
   `{id, path, pkg, dir, symbolCount, inDeg, outDeg, kindMix, isEntry, isHub}`.
   Degree/kindMix computed from the retained edge set, rolled up from the file's
   symbols.
3. **Cluster tree:** package → directory → file hierarchy derived from paths.
   Packages = top-level workspace dirs (`apps/*`, `services/*`, …); files outside
   any package are orphans (rendered adrift between nebulae).
4. **Bundles:** cross-package edges aggregated per package pair:
   `{fromPkg, toPkg, count, kindMix}` — one energy beam regardless of edge count;
   particle intensity scales with `log(count)`.
5. **Intra-package bundles:** file↔file edge counts aggregated per directory pair
   inside each package (powers the L1 package-focus intensity view).
6. **Entry-point detection (heuristics, in order):** conventional names
   (`main.py`, `main.rs`, `index.ts`, `extension.ts`, `App.tsx`, `*.html` Vite
   entries), FastAPI/uvicorn app modules, package manifest entries when present,
   plus graph signal fallback (high out-degree, zero workspace in-degree).
7. **Lazy detail queries** (webview → host request/response):
   - `fileDetail(id)` → the file's symbols + its real individual edge list
     (including References), grouped within-package / cross-package, each with
     direction and target star id.
   - `symbolDetail(id)` → a symbol's edges (Calls/Inherits/References) with target
     symbol + file + line.
   - `searchSymbols(query)` → lazy symbol name search over the full snapshot.

SpaceModel for this repo: ~1.2k stars + ~20 beams + tree — small JSON, fast to send.

## Layout (Web Worker)

Runs inside the webview in a dedicated worker; UI never blocks.

- **Deterministic seeding:** package anchor positions on a loose ring (angle ordered
  by package size), directory cores placed by seeded hash within the package volume,
  files gaussian-scattered around their directory core. All randomness keyed by
  stable id hashes → same snapshot ⇒ same layout; a changed snapshot morphs
  minimally instead of reshuffling.
- **Force refinement:** limited-iteration springs over Imports+Calls edges within
  each package (files that couple tightly drift together), mild repulsion for
  overlap relief. Packages stay anchored — forces only refine local structure.
- Worker emits positions once at load and again per refresh diff; the scene tweens
  stars to new positions.

## Renderer (Three.js)

- **Stars:** one instanced points/sprite system with a custom ShaderMaterial —
  per-instance size (degree), tint (package), twinkle phase, beacon/hub flags.
  Entry beacons pulse (expanding ring shader); hubs get a gravity-ring halo.
- **Nebulae:** additive billboard sprites at cluster centers, tinted per package.
- **Beams:** bezier curves between package surfaces; GPU particle system flowing
  along them (direction = edge direction; mixed-kind bundles carry particles
  colored per kind proportional to kindMix).
- **Trace threads:** on selection, per-edge curves with faster directional
  particles; the rest of the scene dims (uniform, not per-object relight).
- **Post:** UnrealBloomPass for glow; depth fog; vignette in CSS.
- **Satellites (L3):** on dive, the star's symbols orbit on tilted ellipses,
  colored by symbol kind, labeled when close.
- **Labels:** screen-space (CSS2D or canvas overlay): package labels always (fade by
  distance), file labels on hover/selection/proximity, symbol labels at L3.

## Interaction model

**Focus is a 4-level stack.** Esc pops one level. HUD breadcrumb:
`workspace ▸ package ▸ file ▸ symbol`.

- **L0 · Space** — ambient: nebulae, flowing beams, beacons. No selection.
- **L1 · Package** (click nebula label or beam endpoint): camera frames the package;
  other packages dim to ~20%. Intra-package directory-pair bundles light up with
  intensity = edge count. The package's external beams stay lit with visible flow
  direction — everything entering/leaving the boundary is readable.
- **L2 · File** (click a star): real edges materialize in visually distinct groups:
  within-package threads; leaving-package threads that merge into the beam path and
  peel off at the destination; within-file shown as a count badge. Outgoing = ember,
  incoming = rose. Info card: path, in/out degree, symbol count, **Open in editor**,
  **Dive inside**.
- **L3 · Symbol** (dive: satellites orbit): click a satellite → its own edge threads,
  including cross-file ones leaving to other stars. Double-click a satellite → open
  the file at that symbol's line.

**Navigation:** drag = orbit, scroll = dolly, double-click = fly-to + focus, inertia
everywhere. Click a thread's destination ring → **ride the thread** (camera travels
the curve, refocuses on arrival). Idle drift at L0.

**Search:** bottom bar (⌘K focuses): file names instantly from SpaceModel; symbol
names lazily via `searchSymbols`. Enter = warp + focus.

**Edge layers:** Imports/Calls/Inherits toggles active at every level.
**References** is off and disabled at L0/L1 (ambient density would white out the
space); enabled at L2/L3 where it's scoped to one node.

**Open in editor:** info-card button, or double-click a focused star (line 1) /
satellite (symbol line). Webview → host `openFile {path, line}` → `showTextDocument`.

## Live refresh

Host watches `index-snapshot.json` mtime (the indexer watcher rewrites it
continuously). On change: re-preprocess → diff SpaceModel by id → send delta.
Webview morphs: new stars ignite with a flare, deleted stars collapse and fade,
changed rollups tween size/intensity, layout re-seeds only affected nodes. A
refresh never resets camera or focus (if the focused node vanished, pop focus to
its package with a toast).

## Failure modes

- **No snapshot:** empty-state screen (Ember-styled) with a **Build index** CTA
  wired to the existing index-build path; polls until the snapshot appears.
- **Stale snapshot** (older than `CRUCIBLE_RETRIEVAL_MAX_AGE_SEC`): subtle
  "index stale" chip; space renders anyway.
- **Malformed snapshot:** host returns a typed error; webview shows the empty state
  with the parse error summary. Never a blank panel.
- **WebGL unavailable:** plain-text fallback listing packages/counts + a notice.

## Performance budgets & degradation

- 60fps target during orbit at L0 on the reference repo; initial render < 2s after
  SpaceModel arrives; layout worker < 2s for 5k files.
- Instancing everywhere; no per-node DOM; painter-free (GPU depth); particle counts
  capped per bundle (log scale).
- **Monster repos:** if file count > ~5k, L0 aggregates stars to directory level
  (a directory-star sized by file count); individual files resolve on L1 focus.
  Threshold constant, not env-configurable (UI-side concern).

## Testing

- **Unit (node-env vitest, `graph-data.ts`):** kind filtering, rollup math, bundle
  aggregation (incl. kindMix proportions), entry-point heuristics, orphan handling,
  cluster tree derivation, refresh diffing.
- **Unit (worker):** layout determinism — same snapshot ⇒ byte-identical positions;
  refresh with one added file moves only local neighborhood.
- **Component (webview vitest):** HUD components — breadcrumb stack, info card,
  search results, edge-layer toggles (References disabled at L0/L1).
- **Live smoke (CDP recipe):** against `shadow-forge-stress` — panel opens, stars
  render, click star → info card → Open in editor lands on the file, search warps,
  snapshot touch triggers morph.

## Out of scope (v1)

- Git-history playback / time scrubbing
- Metric overlays (churn heat, diagnostics density) — natural later phase
- Editing or task actions from the panel (view + navigate only)
- Cross-workspace or multi-root support
- Configurable palettes (Ember Dusk ships; the other two prototype palettes are
  kept in the prototype file only)
