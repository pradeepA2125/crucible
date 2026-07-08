# AI Editor

Production-grade AI editor foundation with a polyglot architecture.

## Program roadmap
- Active strategy: 24-week parity+ roadmap (reliability first, Cursor/Windsurf core parity baseline).
- Program plan: `docs/program/parity-plus-6-month-plan.md`
- Delivery model: `docs/program/linear-execution-model.md`
- Strategy/docs model: `docs/program/notion-structure.md`
- Copilot-agent parity tracker (P1–P6: instructions/prompt files, Agent Skills, MCP client, install/settings/composer UI, subagents): `docs/superpowers/2026-06-29-feature-roadmap-copilot-parity.md` — P1–P4 done and merged as of 2026-07-06.

## Service layout
- `apps/editor-client` (TypeScript): editor-facing contracts and HTTP client
- `apps/vscode-extension` (TypeScript): VS Code MVP command + review UI
- `services/agentd-py` (Python): deterministic orchestration backend
- `services/indexer-rs` (Rust): indexing and symbol graph service

## Why this split
- TypeScript fits VS Code/UI integration and schema sharing.
- Python fits agent orchestration and model/provider integrations.
- Rust fits high-throughput incremental indexing and graph updates.

## Install the VS Code extension

No Marketplace listing yet — install straight from the latest GitHub Release:

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/pradeepA2125/shadow-forge/main/install.sh | bash
```

**Windows (PowerShell)**
```powershell
iwr https://raw.githubusercontent.com/pradeepA2125/shadow-forge/main/install.ps1 -useb | iex
```

Both scripts download the `.vsix` attached to the latest release and run
`code --install-extension` for you (also detects `code-insiders`/`cursor`). Requires the
`code` CLI on `PATH` — in VS Code: Cmd/Ctrl+Shift+P → "Shell Command: Install 'code' command in
PATH". After install, open any folder and the setup wizard walks through provisioning the
managed backend runtime.

Prefer to inspect before running? Download the script and read it first, or grab the `.vsix`
directly from the [Releases page](https://github.com/pradeepA2125/shadow-forge/releases) and
run `code --install-extension path/to/file.vsix` yourself.

## Quick start

### TypeScript client package
```bash
npm install
npm run typecheck
npm run test
npm run build
```

### VS Code extension package
```bash
npm run -w crucible-vscode-extension typecheck
npm run -w crucible-vscode-extension test
npm run -w crucible-vscode-extension build
```

### Python backend
```bash
cd services/agentd-py
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn agentd.main:app --reload --port 8000
```

### Rust indexer
```bash
cd services/indexer-rs
cargo run -- index --workspace /path/to/repo --snapshot-path /path/to/repo/.crucible/index-snapshot.json --watch 0
cargo run -- query --snapshot-path /path/to/repo/.crucible/index-snapshot.json --mode symbol_name --value build --depth 2 --limit 200
```

## Retrieval core (artifact-first)
- `indexer-rs` persists full graph artifacts to JSON snapshots (`nodes`, `edges`, diagnostics, stats).
- `agentd-py` reads snapshot artifacts directly per task and passes retrieval context into planning/patch prompts.
- If a snapshot is missing, `agentd-py` performs at most one auto-index attempt for the task, then continues best-effort.
- Stale/corrupt/missing artifacts emit warning diagnostics and do not fail task orchestration.

Key env vars:
- `CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH` (default: `<workspace>/.crucible/index-snapshot.json`)
- `CRUCIBLE_RETRIEVAL_MAX_AGE_SEC` (default: `900`)
- `CRUCIBLE_INDEXER_INDEX_CMD` (optional command template with `{workspace}` and `{snapshot_path}`)

## Current focus
- [x] Pivot stack boundaries (TS client, Python orchestrator, Rust indexer)
- [x] Shadow workspace (`real_repo` + `shadow_repo`) in Python backend
- [x] Forbidden-path policy + patch preflight checks
- [x] SQLite task/event persistence for `agentd-py`
- [x] OpenAI reasoning provider integration (schema-constrained JSON outputs)
- [x] Deterministic validation command pipeline (configurable + auto-detected)
- [x] Patch review/promote lifecycle states (`READY_FOR_REVIEW` -> `PROMOTING` -> `SUCCEEDED`)
- [x] TaskResult parity on review endpoints (`/accept`, `/reject`)
- [x] TaskResult retrieval endpoint (`GET /v1/tasks/{task_id}/result`)
- [x] LSP session manager in Rust indexer (TS + Pyright + rust-analyzer, diagnostics-first, best-effort fallback)
- [x] Artifact-first retrieval core (parser registry, full graph snapshot, deterministic query CLI, orchestrator artifact integration)
- [x] VS Code MVP review loop (start task, poll status, review panel, real/shadow diff, accept/reject/refresh commands)
- [x] Step-scoped patch execution with bounded per-step patch calls
- [x] Deterministic patch preflight conflict detection for self-invalidating op order/anchors
- [x] Plan-target grounding with one-shot replan feedback for missing step paths

## Implementation Status

### Phase 1: Enhanced Patch Operations ✅ COMPLETE
**Goal**: Reduce Phase 1 failure corpus errors by 70% through enhanced patch operations

**Delivered**:
- ✅ **SearchReplaceOpV2**: Fast O(N) text search/replace with exact matching
- ✅ **ApplyDiffOpV2**: Unified diff format support with preflight validation
- ✅ **Codex Diff Parser**: Strips `*** Begin Patch` / `*** End Patch` markers
- ✅ **Hierarchical Strategy Selection**: ast_patch → fast_apply → diff_patch → file_ops
- ✅ **Enhanced Preflight Validation**: Simulates patch application before execution
- ✅ **Newline Normalization**: Handles inconsistent trailing newlines in diffs
- ✅ **Comprehensive Test Suite**: 29/29 tests passing with edge case coverage
- ✅ **Updated LLM Prompts**: Hybrid structured prompts for PLAN and PATCH operations

**Test Coverage**:
- Basic search/replace operations (exact match, case sensitivity)
- Unified diff application (single/multiple hunks, insertions, deletions)
- Codex-style diff parsing and application
- Edge cases: Unicode, special characters, complex indentation, CRLF line endings
- Error handling: Invalid operations, missing files, malformed diffs

**Next**: Run Phase 1 gate report (`crucible-eval phase1-gate-report`) to validate 70% failure reduction target

### Roadmap
- [x] **Phase 0**: Eval harness and deterministic replay bundles
- [x] **Phase 1**: Enhanced patch operations (SearchReplaceOpV2, ApplyDiffOpV2, Codex parser)
- [ ] **Future reliability**: Add explicit `REGENERATING_PLAN` lifecycle state during feedback-driven plan regeneration (instead of overloading `CONTEXT_READY`)
- [ ] **Phase 2**: Plan v2 with preconditions/postconditions/verification + two-stage retrieval
- [ ] **Phase 3**: Parity surfaces (timeline/background/code review + MCP policy controls)
- [ ] **Phase 4**: Workflow layer (issue-driven flows + knowledge spaces + collaboration metadata)
- [ ] **Phase 5**: Differentiation (multi-agent orchestrator + retrieval v2 + autonomous refactors)

See `docs/implementation-plan.md` for detailed phase breakdown and `docs/phase1-completion-summary.md` for Phase 1 details.
