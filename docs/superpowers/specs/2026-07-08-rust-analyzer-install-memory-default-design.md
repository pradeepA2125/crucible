# rust-analyzer managed install + memory harness on by default

## Context

Two independent gaps, bundled into one design because both touch the P4 managed-runtime
installer (`apps/vscode-extension/src/runtime/`):

1. The runtime installer auto-installs Python (`pyright`) and TypeScript
   (`typescript-language-server`) LSPs via `npm install`, but never installs
   `rust-analyzer`. Today `AI_EDITOR_LSP_RS_CMD` defaults to the bare string
   `"rust-analyzer"` (a PATH lookup) in both `backend-process.ts` and
   `indexer-rs/src/config.rs`. If the user doesn't already have rust-analyzer
   installed system-wide, the indexer silently skips Rust `Calls`/`Inherits`
   symbol-graph edges (degrade-not-raise — never a hard failure, but a quietly
   worse experience for Rust workspaces, and this repo itself has a Rust package
   at `services/indexer-rs`).
2. The memory harness (compaction + cross-session recall/consolidation +
   reranker) ships default-OFF (`AI_EDITOR_MEMORY_ENABLED`,
   `AI_EDITOR_MEMORY_RERANKER`). We want new installs to get it on by default.

## Part 1 — rust-analyzer as a 4th binary-download component

### Why this fits the existing pattern (and where it doesn't)

The installer already downloads two standalone per-platform binaries this way —
`uv` and `ripgrep` — via `scripts/release/fetch_tools.py` (fetch upstream release
archive → extract the single binary → restage under `<tool>-<platform>[.exe]`)
and `scripts/release/make_manifest.py` (scan staged binaries → sha256 each →
emit `RuntimeManifest` JSON with per-platform `urls`/`sha256`).
`installer.ts`'s generic binary-component path (`installOne`, the `uv`/`indexer`/
`ripgrep` fallthrough case) downloads the manifest URL, verifies the checksum,
writes+chmods it — no tool-specific code there at all.

rust-analyzer's upstream releases (`rust-lang/rust-analyzer`) fit this shape for
three of four platforms but not the fourth: Unix release assets are a **raw
single-file `.gz`** (literally `gzip <binary>`, no tar wrapper, no directory
nesting) — not the `.tar.gz` archives-with-nested-paths that `stage()` currently
handles. Only the Windows asset is a `.zip`. So `stage()`'s archive-format
dispatch, which today derives format purely from `platform` (zip on Windows,
tar.gz elsewhere), needs a third branch keyed on `(kind, platform)`: plain-gzip
decompression (`gzip.decompress(archive_bytes)` — the whole payload IS the
binary, no member search needed) for `kind="rust-analyzer"` on non-Windows.

### Changes

- **`scripts/release/fetch_tools.py`**: add `_RUST_ANALYZER_TARGETS` (target
  triple per platform key: `aarch64-apple-darwin`, `x86_64-apple-darwin`,
  `x86_64-unknown-linux-gnu`, `x86_64-pc-windows-msvc`), a
  `rust_analyzer_asset_name`/`rust_analyzer_download_url` pair mirroring
  `uv_asset_name`/`uv_download_url`, and `_BINARY_BASENAME["rust-analyzer"] =
  "rust-analyzer"`. Refactor the format dispatch so `stage()` takes/derives a
  three-way format (`zip` / `tar.gz` / `gzip`) instead of the current two-way
  `_archive_ext(platform)`, add the plain-gzip branch, and add a
  `--rust-analyzer` CLI arg to `main()` (mirrors `--uv`/`--rg`; the exact
  pinned release tag is chosen at implementation/CI-update time, not baked
  into this design).
- **`scripts/release/make_manifest.py`**: add `("rust-analyzer",
  "rust-analyzer")` to `_BINARY_COMPONENTS`. No other change needed — sha256
  is computed generically from whatever `fetch_tools.py` staged.
- **`apps/vscode-extension/src/runtime/manifest.ts`**: add `"rust-analyzer"`
  to the `ComponentId` union.
- **`apps/vscode-extension/src/runtime/installer.ts`**: add `"rust-analyzer"`
  to `ORDER` (placed after `ripgrep` — same tier: a standalone binary
  download with no dependents, unlike `agentd`→`uv` or `lsps`→Node) and to
  `BIN_NAME`. The existing generic binary-component branch in `installOne`
  requires no new logic.
- **`apps/vscode-extension/src/runtime/backend-process.ts`**: change the
  `AI_EDITOR_LSP_RS_CMD` default from the hardcoded bare string
  `"rust-analyzer"` to: use `binPath(runtimeDir, "rust-analyzer", platform)`
  when that file exists (mirrors how `PY_CMD`/`TS_CMD` already resolve to
  locally-installed npm-bin paths when `node_modules` exists), else fall back
  to the bare command name (preserves today's PATH-lookup behavior for a dev
  backend outside the managed runtime).
- **`.github/workflows/release.yml`**: add a pinned rust-analyzer version
  input alongside the existing uv/ripgrep ones, threaded through to both
  scripts.
- **Existing installs**: handled by the installer's existing
  `install-state.json` + upgrade-prompt machinery with zero new code — the
  same mechanism that would fire for any future component addition. A user
  who upgrades sees `rust-analyzer` appear as one more `installOne` step in
  the already-existing install-progress UI.

### Testing

- `fetch_tools.py`: extend the existing tarfile/zipfile fixture-based unit
  tests for `stage()` with a plain-gzip fixture case (`gzip.compress(b"fake
  binary")`), covering both the format-dispatch change and the new
  rust-analyzer target-name mapping.
- `make_manifest.py`: existing tests iterate `_BINARY_COMPONENTS` in a
  table-driven way — adding the tuple should be covered by extending that
  table in the fixture dist dir (a `rust-analyzer-<platform>` file per
  platform) rather than a bespoke new test.
- `installer.ts`: extend existing component-table-driven tests with a
  `rust-analyzer` case (download+checksum+chmod path); add a focused test for
  `backend-process.ts`'s new `AI_EDITOR_LSP_RS_CMD` resolution (binary present
  → local path; absent → bare command fallback).

## Part 2 — memory harness enabled by default

### Where the default actually lives

Traced end to end: `apps/vscode-extension/src/runtime/vscode-runtime.ts`'s
`extraEnvFromSettings()` only injects `AI_EDITOR_MEMORY_ENABLED`/
`AI_EDITOR_MEMORY_RERANKER` into the spawned backend's env when the
corresponding VS Code setting has been **explicitly** touched by the user
(`cfg.inspect(...)` checked for a user/workspace value, not just "differs from
schema default"). If untouched, the env var is never set, and
`services/agentd-py/agentd/memory/config.py` governs:

```python
enabled=env.get("AI_EDITOR_MEMORY_ENABLED", "").lower() in _TRUTHY   # line 29
reranker_enabled=... AI_EDITOR_MEMORY_RERANKER ...                    # line 42
```

Both default to off (`""` → `False`) when unset. This is confirmed to be the
single load-bearing switch for **all three** entry points: the managed VS Code
runtime, `scripts/stress/start-backend.sh` (never sets these vars), and a bare
`uvicorn agentd.main:app` (no env at all). Flipping these two lines is
sufficient to change real behavior everywhere.

### Changes

- **`services/agentd-py/agentd/memory/config.py`**: flip the default-when-unset
  for both `enabled` (line 29) and `reranker_enabled` (line 42) from off to on.
  The kill-switch semantics stay identical — an explicit `AI_EDITOR_MEMORY_ENABLED=0
  /false/no/off` (and same for `_RERANKER`) still disables it; only the
  unset-fallback flips.
- **`apps/vscode-extension/package.json`**: flip the `contributes.configuration`
  schema defaults for `aiEditor.memory.enabled` and `aiEditor.memory.reranker`
  from `false` to `true`. This has no behavioral effect on its own (per the
  explicit-only-override wiring above) — it exists purely so the Settings
  panel doesn't show both toggles "off" while the engine actually runs with
  memory on, which would otherwise be a UI lie.
- **`apps/vscode-extension/src/runtime/installer.ts`**: the `agentd` install
  step's pip-install target changes from `ai-editor-agentd==<version>` to
  `ai-editor-agentd[memory]==<version>`, so `sentence-transformers`/`numpy`
  (and PyTorch, transitively) actually land. Without this, the flag would be
  on but the embedder would silently degrade (Phase 1 compaction — LLM-only —
  would work; Phase 2 recall/consolidation would silently no-op). This is a
  deliberate, accepted trade-off: a **substantially larger first-run install**
  (roughly 500MB–1GB+ added for PyTorch) for every new managed-runtime user,
  in exchange for memory working fully out of the box. No `ComponentSpec`
  schema change needed — the extras suffix is baked into the existing
  hardcoded target string in `installOne`, same as today's bare version.
- **Runtime cost, stated explicitly (no code change, just documented
  behavior):** with the reranker also on by default, every edit-promoting
  controller turn now does one additional background LLM distill call
  (consolidator) plus a local cross-encoder rerank pass during recall —
  measurable added latency/token cost per turn compared to today's opt-in
  default. Accepted as part of "ship memory on by default."
- **Existing installs**: no special-cased upgrade notice. They see this the
  same way any other manifest-version bump is already handled — the
  installer's upgrade prompt fires on `releaseTag` mismatch, `installAll()`
  reinstalls the `agentd` component (its version-state key won't match the
  new manifest version since the target string changed), and memory turns on
  after that restart. This was raised explicitly during design and the
  decision was to skip building any additional one-time notice UI.

### Testing

- `config.py`: update existing default-value tests asserting `enabled=False`/
  `reranker_enabled=False` when unset to assert `True` instead; keep (or add,
  if absent) a test that an explicit falsy env value still disables each flag
  (kill-switch regression guard).
- `installer.ts`: update/extend the `agentd` component test to assert the
  pip-install invocation includes the `[memory]` extra.
- No new test needed for the `package.json` schema defaults — they aren't
  independently unit-tested elsewhere in this repo.

## Out of scope

- Actually picking the pinned rust-analyzer release tag for the CI workflow —
  deferred to implementation time (same as how uv/ripgrep tags are only ever
  CLI args, never hardcoded).
- A one-time in-editor notice when memory turns on for an existing install —
  explicitly discussed and declined.
- Any change to `AI_EDITOR_MEMORY_GRAPH_GROUNDING` (already on by default) or
  the numeric/tuning memory env vars (unaffected by this change).
- Extending `ComponentSpec` with a formal "extras" field — the pip target
  string stays hardcoded in `installer.ts`, consistent with today.
