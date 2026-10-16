# Axon and repository intelligence: review-only addition

Promotion status (2026-10-16): this demonstration now ships in the production landing page from `landing/src/experience/`. The prototype URL loads the same component. The review-only scope below describes the earlier implementation stage.

Owner explicitly wants the Rust indexer and Axon celebrated, preserving the accepted original-inspired page and its motion. Axon is an established capability, not relabeled a post-v0.5.4 addition.

## Verified product truth
- Rust IndexerService bootstraps Tree-sitter parsing, optionally opens files through LSP, waits for indexing, resolves placeholders and persists a graph snapshot (services/indexer-rs/src/service.rs:77–109). Watch mode handles changes; resolution depends on available language servers and supported languages.
- Snapshot captures symbols and relationship edges. Parser imports were read directly; query API supports Calls, Imports, References, Inherits, Implements. Graph evidence is navigation, not a guarantee of complete semantic understanding.
- Backend RetrievalContext.as_prompt_payload includes repository structure, file outlines, evidence excerpts, actionable diagnostics and graph-neighbor files (artifact_client.py:76–113). Retrieval can be unavailable/stale; source reads remain authoritative.
- ChatController._retrieval_seed computes the initial goal-informed context once per thread, persists it and reuses the same bytes, including across restart (controller.py:695–731). It is NOT freshly recomputed for every query. Current exploration uses tools; workspace-change notes do not mutate the pinned seed. Marketing: 'Start with a map. Follow the evidence.' Supporting copy explains the exact lifecycle.
- Actual Axon renderer is apps/vscode-extension/webview-ui/src/graph/scene/graph-scene.ts with starfield, bloom, camera rides, file traces and symbol satellites. GraphApp and its focus reducer implement overview → package → file → symbols, layers and connection traversal. This addition uses those real modules, not the landing hero's illustrative graph.

## Composition and interaction
Midnight #03020A, Axon violet #A78BFA, call cyan #67E8F9, inbound rose #F0ABFC, star #EFEDFF; existing locally served type. Large 'Meet Axon. Your repository, in another dimension.' heading; a wide actual renderer viewport occupies the section, not a tiny equal card. Plain-language controls: activate 3D, trace a file, enter its symbols, follow a connection, overview, layers. Selected file context is readable HTML, with keyboard-operable file choices. A pipeline below ties Rust indexing → initial repository seed → continued exploration to the rendered evidence.

Scene loads only on activation and disposes when offscreen, hidden, paused or reduced motion; no background GPU loop for the entire marketing page. Canvas wheel handlers must not trap document scrolling: overlay intercepts wheel only to stop propagation, never prevent page scrolling. Drag orbit is opt-in 3D interaction. Reduced motion uses a static actual-render capture and source relationship details rather than camera rides. The poster and optional WebM clip are full-color captures of this renderer demo using the repository subset. They are explicitly labeled as a renderer demonstration, not a recording of the editor UI. The older GIF assets are retained for provenance but are no longer served by the page.

Capture data comes from the existing local index snapshot, restricted to tracked apps/services source metadata; no source bodies, diagnostics, absolute paths, memory/settings, untracked paths or remote requests. It is a captured subset of this repository, not a live production index or benchmark. Export reads but does not rewrite the index.

Only new/modified landing/.impeccable prototype artifacts change. Production landing, unrelated dirty README/UI/backend work and memory data/settings remain untouched. No extension reinstall, backend restart, dependency upgrade, commit or deployment.

## October 16 takeover

The embedded scene now retains explicit width/height when the product renderer sets its parent to relative positioning for labels. Recording mode hides the interactive canvas so the disposed last frame cannot cover the video. Package focus is held in React state, and file focus uses a wider camera distance with a softer nebula palette for the dense captured graph. The poster and 9-second WebM replace the old GIF-derived preview, whose 128-color palette baked in the conspicuous gradient bands.

Verified: prototype TypeScript check and standalone Vite production build; desktop/mobile nonzero canvas dimensions and no horizontal overflow; hub trace, symbols and connection following; full-color video playback; offscreen scene disposal; reduced-motion and explicit pause/resume. Browser checks found no page JavaScript errors. The prototype remains local for review; the shipping landing source and shared Axon renderer were not changed by this takeover.
