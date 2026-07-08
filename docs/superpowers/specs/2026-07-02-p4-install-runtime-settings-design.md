# P4 — Installation, Managed Runtime & Settings UI (Design)

**Date:** 2026-07-02 · **Status:** Approved design, pre-plan
**Roadmap phase:** P4 of `docs/superpowers/2026-06-29-feature-roadmap-copilot-parity.md`
**Inputs:** parity roadmap P4 section · `docs/superpowers/2026-07-02-mcp-settings-ui-research.md` · `scripts/stress/start-backend.sh` (the manual flow being replaced)

---

## 1. Goal

Turn the capability stack (P1–P3 + memory + controller) into a **shippable, installable, configurable product**: a new user goes from zero → working chat turn via a VSIX install and a first-run wizard — no cloned repo, no terminal, no `.env`, no `start-backend.sh`.

### Decomposition (decided)

- **A — Install & run** and **B — Settings UI** are designed and built **together** (this spec).
- **C — UI polish & composer UX** (`@`-file mentions, unified `/`-autocomplete for prompts+skills, design-system pass, onboarding/empty states/error surfaces) is **deferred** to its own brainstorm → spec → plan cycle after A&B land. C's item list is durably indexed in the parity roadmap P4 scope. Where A&B makes a UI choice C may want to refine (panel layout, theming), we build plainly and mark it C-refinable — no gold-plating.

## 2. Decisions log (from brainstorm)

| # | Decision |
|---|----------|
| 1 | A&B together first; C later. |
| 2 | The extension **provisions** agentd (via uv-managed Python), the per-OS prebuilt indexer binary, ripgrep, and the Node LSP servers (pyright + typescript-language-server, npm-installed into the private runtime dir). **rust-analyzer stays detect-only** (degrade gracefully). Local LLMs (ollama/turboquant): **detect-and-guide**. Cloud providers: key only. |
| 3 | Every backend-supported provider gets equal, polished onboarding in the picker: openai, anthropic, gemini, groq, ollama, watsonx, openrouter, huggingface, turboquant (`scripted` stays dev-only, hidden). |
| 4 | Settings UI = **custom webview panel** (second-Vite-entry pattern, like `MemoryPanel`). |
| 5 | Distribution: **runtime assets on GitHub Releases** (indexer binaries, agentd package, manifest) + **VSIX published to the VS Code Marketplace** — the roadmap's decided channel; Releases alone can't host discoverability. |
| 6 | Architecture: **Approach 1 — Runtime Manager inside the extension** (see §4). |
| 7 | Provider/model changes apply via a **hot-swap backend route** (no restart). Other env-backed settings (ports, DB paths, policies, feature flags) take a **managed restart** (~5s, extension-orchestrated). |
| 8 | MCP config changes go through read-modify-write routes + the existing `McpConnectionManager.reconcile()` seam — **no restart** (per the MCP settings-UI research). |

## 3. The real provisioning surface

`start-backend.sh` is the ground truth for what "running the product" means today. The managed runtime must cover every load-bearing thing it does:

| Concern | Today (script) | Managed runtime |
|---|---|---|
| agentd + venv | pre-existing `.venv`, bootstrap script | uv-installed from pinned release (uv brings its own Python) |
| Env assembly | ~30 exports (workspace, DB paths, flags, model vars) | `BackendProcess` builds the env from settings + SecretStorage at spawn |
| Health wait | curl poll loop, 60s | health-poll with progress UI |
| Semantic index pre-warm | `POST /v1/index/build` + status poll before "ready" | same calls, surfaced in wizard/status UI; first build downloads the embedding model (~130MB) — shown as an explicit progress step, not a silent hang |
| Indexer watcher | sibling process, LSP env, single-writer reap guard | child of `BackendProcess`; single-writer via lockfile discipline |
| LSP servers | assumed on PATH | pyright + typescript-language-server npm-installed into the runtime dir; absolute paths passed via `CRUCIBLE_LSP_*_CMD`; rust-analyzer detected on PATH or skipped |
| ripgrep | PATH-fix hack for Homebrew | per-OS static binary provisioned into the runtime dir; passed via `CRUCIBLE_RIPGREP_CMD` |
| Provider key checks | per-backend fail-fast in bash | wizard "Test connection" via `POST /v1/providers/validate` |
| Local LLM reachability | curl to ollama/TQP | detect-and-guide in wizard (link to install docs; re-check button) |

`start-backend.sh` and the `.env` dev flow remain untouched for development.

## 4. Approaches considered

1. **Runtime Manager inside the extension** (chosen) — extension owns download/install/spawn/upgrade. One-click install (VSIX only); wizard gets synchronous progress/errors; everything pinned as one unit. Cost: download/verify/retry logic + per-OS testing lives in TS.
2. **Installer CLI, extension just spawns** — provisioning testable headlessly, but a terminal step gates onboarding (the thing being killed) and the CLI needs its own distribution story.
3. **Backend-managed provisioning** — agentd provisions indexer/LSPs itself. But the extension needs uv/download logic to provision agentd anyway, so nothing is saved; failure UX is murkier.

Approach 1 wins because the provisioning complexity exists in all three; only Approach 1 makes the VSIX the entire install. It also answers the parity doc's open packaging question: the backend is a **managed local process**, not bundled inside the VSIX.

## 5. Architecture — A (install & run)

New extension module `src/runtime/` — vscode-free core (unit-testable) + thin vscode wrapper, mirroring the `controller.ts` / `memory-data.ts` discipline.

### 5.1 RuntimeInstaller

Provisions everything into `~/.ai-editor/runtime/`:

- **uv** — static binary downloaded if absent (or reused from PATH when version-compatible).
- **agentd** — `uv tool install` (or `uv venv` + `uv pip install`) from the pinned GitHub Release artifact; uv supplies the managed Python — no system Python required.
- **indexer** — per-OS prebuilt binary (`macos-arm64`, `macos-x64`, `linux-x64`, `win-x64`) from the same release.
- **ripgrep** — per-OS static binary (same channel).
- **LSP servers** — `npm install` of pyright + typescript-language-server into `runtime/node_modules` (requires node on PATH; absent → skipped with a visible "graph edges degraded — install Node to enable" notice, detect-only thereafter).
- Every download verified against **`manifest.json`** (versions + URLs + sha256), which is pinned per extension version and shipped in the release.
- **Resumable**: per-component install state; a failed component retries without redoing the rest. Checksum mismatch = hard fail with retry. Offline detected up front with a clear message.

### 5.2 BackendProcess

One per workspace folder:

- Spawns agentd on a **dynamic port**; env assembled from settings + SecretStorage (keys injected at spawn, never written to disk/logs).
- Health-polled; crash → restart with backoff; logs captured to an output channel + file.
- Runs the **semantic index pre-warm** and launches the **indexer watcher** (LSP env pointing at the runtime-dir servers; `RUST_LOG` defaults as in the script).
- **Lockfile:** agentd writes `<workspace>/.agentd/agentd.lock` (pid + port + started_at). The extension reuses a live backend, reaps stale locks, and refuses to double-spawn — killing the three-backends-one-DB split-brain class by construction. The same discipline covers the watcher (single writer per snapshot).
- Disposed on deactivate (backend + watcher; no orphans).

### 5.3 RuntimeState

`runtime.json` in the runtime dir records installed component versions. Extension + agentd + indexer move as one pinned unit; **upgrade = re-run installer against a newer manifest** (prompted when the extension updates and the pinned manifest changed).

### 5.4 Backend additions (agentd)

- **`POST /v1/providers/validate`** — `{backend, credentials, model?}` → one cheap provider ping → ok / actionable error. Powers "Test connection". Never logs credentials.
- **`PUT /v1/config/provider`** — hot-swap: `{backend, model, credentials?}`. The extension (which owns SecretStorage) sends the target provider's key in the request body when it isn't already in the spawn env; the backend holds it in memory only — never persisted, never logged. Validates via the same ping, then re-initializes the reasoning engine in-process. Applies **from the next turn** — an in-flight turn finishes on the old engine (guard: swap is a pending-engine pointer read at turn start, no mid-turn mutation). Invalid target → 4xx, engine untouched.
- **Lockfile write** at startup (§5.2).
- **MCP management routes** (per the research doc, §6.2 below).

Everything else stays env-at-spawn; the extension owns restarts.

## 6. Architecture — B (settings UI)

### 6.1 First-run wizard

Webview flow, auto-opens on activation when `~/.ai-editor/runtime/` is absent; re-runnable via `AI Editor: Run Setup`.

1. **Welcome** — what will be installed, where, disk estimate.
2. **Install** — live per-component checklist (uv → agentd → indexer → ripgrep → LSPs): spinner → ✓/✗, per-component retry, "Open logs" on failure. Node-absent shows LSP row as skipped-with-consequence, not error.
3. **Providers** — all-provider picker; key field → **SecretStorage**; model dropdown (curated defaults per provider, free-text override); **Test connection** (`/v1/providers/validate`). Local providers (ollama/turboquant) get reachability detect + guidance instead of a key field.
4. **Finish** — backend spawned + healthy, index pre-warm kicked off (with the embedding-model download surfaced), chat panel opened.

### 6.2 Settings panel

Custom webview, second Vite entry in `webview-ui` (the proven `MemoryPanel` pattern: panel class + vscode-free data source + `when`-context off `/v1/config`). Sections:

- **Providers & models** — provider/model + keys + test-connection; applies via the hot-swap route. Key storage: SecretStorage only.
- **Runtime** — installed versions (`runtime.json`), per-workspace backend status (port, pid, health), Restart / Upgrade / Open logs.
- **MCP servers** — per the research doc: list with live status + tool counts (**`GET /v1/mcp/servers`** — merged config + `McpServerStatus`), guided add (**tier 1: QuickPick wizard** mirroring VS Code's "MCP: Add Server"; **tier 2: pane form**), remove, reconnect (**`POST /v1/mcp/servers/{name}/reconnect`**). Writes are **`POST/PATCH/DELETE /v1/mcp/servers/{name}`** doing read-modify-write on `.ai-editor/mcp.json` — preserve unknown keys, store `${VAR}` references verbatim (never resolved secrets) — then call `McpConnectionManager.reconcile(loader.load())`. **Enable/disable toggles write user-local state** (extension `globalState`), not the shareable file (presence-trust guard from the research doc); the extension passes the effective disabled set alongside reconcile-triggering calls — `reconcile(configs, disabled=…)` — so the backend never needs its own user-scoped store.
- **Skills** — list discovered skills (existing `GET /v1/skills`), enable/disable. Same user-local storage; v1 applies it via the managed-restart path (`CRUCIBLE_SKILLS_DISABLED=<names>` in the spawn env) rather than a new live route — skills toggling is rare enough that a 5s restart is acceptable.
- **Memory** — enabled / reranker / budget knobs. **Policies** — scope + shell policy. Env-backed ⇒ managed-restart path with an inline "applying — restarting backend" spinner (~5s).
- **Advanced** — port strategy, paths, feature flags (restart path).

Settings storage split: **secrets → SecretStorage**; **user prefs → VS Code settings/globalState**; **workspace-shared config → `.ai-editor/` files** (mcp.json, prompts, skills). The backend reads env at spawn + the two live routes (provider hot-swap, MCP reconcile).

## 7. Release pipeline

On tag, GitHub Actions (repo public):

1. Build + test all three stacks (npm workspaces, pytest, cargo).
2. Produce: 4 per-OS indexer binaries, ripgrep fetch manifest entries, agentd wheel/sdist, VSIX, `manifest.json` with sha256s.
3. Attach runtime assets + manifest to the **GitHub Release**.
4. `vsce publish` the VSIX to the **Marketplace** (manifest URL pinned inside).

Marketplace packaging (icon, README, listing copy, categories) is in scope for the first published version; deeper branding polish is C.

## 8. Error handling

- **Installer:** per-component retry + resume; checksum hard-fail; offline detection; every failure ends in an actionable message + "Open logs".
- **BackendProcess:** health-poll timeout → surfaced error with log excerpt; crash-loop backoff with a status-bar indicator; stale-lockfile reaping.
- **Hot-swap:** validate-before-swap; rejection leaves the engine untouched; UI shows the provider error verbatim (they're actionable — wrong key, bad model id).
- **MCP writes:** malformed edits rejected server-side; reconcile failures surface per-server status (`failed(reason)`) in the panel, mirroring the degrade-not-raise loader discipline.
- **LSP/node absent, rust-analyzer absent, local LLM unreachable:** degrade with visible consequence text, never block install.

## 9. Testing & exit criteria

- **Unit (TS):** `RuntimeInstaller` core with mocked downloads/checksums (happy path, resume, checksum-fail, node-absent); `BackendProcess` lifecycle with a fake process + lockfile scenarios (reuse live, reap stale, no double-spawn).
- **Unit (py):** route tests for `/v1/providers/validate` (per-provider stub transports), `PUT /v1/config/provider` (swap next-turn semantics, invalid target, in-flight guard), MCP write routes (RMW preserves unknown keys; never writes resolved secrets; reconcile invoked), lockfile write.
- **Live smoke (the roadmap's exit criterion):** a clean machine — fresh VS Code profile, no `~/.ai-editor` — goes zero → working chat turn via wizard alone; settings pane round-trips provider/model (hot-swap observed live), an MCP add via the pane connects without restart, a policy change applies via managed restart.
- CI runs the per-OS install path at least for the download/verify layer (matrix on the four targets).

## 10. Deferred to C (recorded, not designed here)

`@`-file mentions (clickable, feed turn context) · unified `/`-autocomplete dropdown (prompts + skills, badged) · design-system consistency pass (incl. memory inspector palette) · onboarding walkthrough polish, empty states, error-surface styling. Panel layout/theming choices in B are C-refinable.

## 11. Risks / open questions

- **Windows** is the least-exercised target (paths, process management, npm shims) — budget explicit smoke time; ship mac/linux first if it slips.
- **uv availability/behavior changes** — pin the uv version in the manifest like everything else.
- **Marketplace review constraints** on extensions that download+run binaries — mitigations: signed manifest, checksums, README disclosure. Verify policy before first publish.
- **Port strategy** — dynamic port per workspace is decided; the extension is the only client, so no fixed-port compat concern (dev flow keeps :8000 via the script).
