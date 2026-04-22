# Architecture (Polyglot v1)

## Services
- `apps/editor-client` (TypeScript)
  - Shared JSON schemas and task contracts for editor UI
  - Typed HTTP client for backend task APIs
- `apps/vscode-extension` (TypeScript)
  - VS Code command surface for task lifecycle actions
  - Review panel UI for status, diagnostics, plan/patch payload, and diff actions
  - Real-vs-shadow diff opening for modified files during review
- `services/agentd-py` (Python)
  - Stateful task orchestration and deterministic control loop
  - Budget enforcement, lifecycle transitions, repair loop policy
  - Provider adapters (OpenAI and future providers)
- `services/indexer-rs` (Rust)
  - Incremental parse/index pipeline
  - Symbol graph materialization
  - LSP diagnostics enrichment
  - Snapshot artifact + deterministic graph query CLI

## Runtime layering
- Generic runtime core is the default:
  - retrieval scoring based on lexical, symbol, graph, and diagnostics relevance
  - plan/patch contracts
  - orchestrator grounding and deterministic validation
- Optional repo/domain adapters may augment evidence labeling or critique rules:
  - `AI_EDITOR_EVIDENCE_ADAPTER=generic|legacy_repo`
  - `AI_EDITOR_PLANNING_ADAPTER=generic|legacy_repo`
- Language-specific logic is isolated to explicit language adapters:
  - Python execution/parsing via `libcst`
  - TypeScript/Rust execution/parsing via tree-sitter
- Artifact/debug output uses a configurable root:
  - `AI_EDITOR_ARTIFACTS_ROOT`
  - default `<workspace>/.agentd/artifacts`

## Deterministic boundaries
- Model output is never executed directly.
- `agentd-py` validates plan/patch payloads into typed models.
- `agentd-py` validates plan step targets against the real workspace file index and performs one-shot replan on unresolved paths.
- Patch application and validation gates remain deterministic.
- Retrieval context is artifact-backed and adapter-neutral by default (no repo-shape assumptions in core scoring).

## API contracts
- `POST /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `GET /v1/tasks/{task_id}/result`
- `GET /v1/tasks/{task_id}/artifacts`
- `POST /v1/tasks/{task_id}/cancel`
- `POST /v1/tasks/{task_id}/accept`
- `POST /v1/tasks/{task_id}/reject`

## Orchestration lifecycle
`QUEUED -> CONTEXT_READY -> AWAITING_PLAN_APPROVAL -> PLANNED -> EXECUTING -> VALIDATING -> VALIDATED -> READY_FOR_REVIEW -> PROMOTING -> SUCCEEDED|FAILED|ABORTED`

## Retrieval artifact flow
1. `indexer-rs index` writes `<workspace>/.ai-editor/index-snapshot.json` with schema/version metadata, full graph, diagnostics, and stats.
2. `agentd-py` loads snapshot artifact once after shadow workspace preparation.
3. If artifact is missing, `agentd-py` tries a single auto-index command and retries artifact load once.
4. Stale/corrupt/missing artifacts emit warning diagnostics; task execution continues with empty retrieval context when needed.
5. Plan/patch prompts receive compact retrieval context (`related_files`, `related_symbols`, neighbors, diagnostics excerpt, snapshot age/stats).
6. Planner evidence packs remain generic by default; any repo-specific labeling comes from an explicit evidence adapter, not the retrieval core.

## Implementation Status

### ✅ Phase 0: Evaluation Harness (Completed)
- Unified evaluation/replay harness with benchmark scoring and failure clustering
- Benchmark corpus freeze (100 internal + 50 OSS tasks)
- Deterministic replay bundle and loader
- Baseline metrics dashboard and weekly scorecard

### ✅ Phase 1: Patch Engine v2 (Completed)
**Hybrid CST/AST + Text-Based Patching:**
- SearchReplaceOpV2 (Fast Apply): O(N) text search/replace for large files
- ApplyDiffOpV2 (Unified Diff): Multi-hunk diff with context validation
- Codex-style diff format support (`*** Begin/End Patch` markers)
- Enhanced preflight validation for all operation types
- Newline normalization for robust diff matching
- Comprehensive test suite (12/12 tests passing)

**Previously Completed:**
- Simulated apply preflight (dependency, anchor stability, parse checks)
- Plan-target grounding and one-shot replan feedback loop
- Step-attempt checkpoint transactions with deterministic rollback
- Candidate patch ranking and best-candidate selection
- Artifacts API (`GET /v1/tasks/{task_id}/artifacts`)
- Phase 1 failure corpus + gate report command

**Pending:** Benchmark validation of 70% failure reduction target

### 🔄 Phase 2: Planner/Executor/Critic v2 (Next)
- Plan graph v2 with preconditions/postconditions/verification
- Typed critic taxonomy and targeted repair prompts
- Rules/memory precedence engine (global → workspace → repo → task)
- Add explicit `REGENERATING_PLAN` lifecycle state for feedback-driven plan regeneration windows.

### 📋 Phase 3: Core Parity Surface (Planned)
- Timeline/event UX in VS Code (attempt traces, preflight/validation deltas)
- Background task mode with resume and checkpoint restore
- Code review assistant mode (file/PR findings and suggestions)
- MCP policy controls (allowlist, scope, audit)

### 📋 Phase 4: Workflow Layer (Planned)
- Issue-driven flow (issue → plan → patch → review artifacts)
- Knowledge spaces and memory ingestion pipeline
- Collaboration metadata (provenance, approvals, shareable run context)

### 📋 Phase 5: Differentiation (Planned)
- Multi-agent orchestrator (Planner/Retriever/Patcher/Verifier)
- Retrieval v2 (symbolic + semantic + freshness-aware ranking)
- Autonomous long-running refactor mode with staged promotion

## Planned additive contracts (program-level)
- Task APIs:
  `GET /v1/tasks/{task_id}/events`,
  `GET /v1/tasks/{task_id}/timeline`,
  `GET /v1/tasks/{task_id}/artifacts`,
  `POST /v1/tasks/{task_id}/resume`,
  `POST /v1/tasks/{task_id}/rollback`,
  optional SSE stream endpoint.
- Planner/Patcher contracts:
  `PlanStepV2`, `PatchDocumentV2`, `FailureTaxonomyV2`.
- Policy contracts:
  `RuleSet` scope precedence and `ToolPolicy` allowlist/scope/audit metadata.

## Program references
- `docs/program/parity-plus-6-month-plan.md`
- `docs/program/linear-execution-model.md`
- `docs/program/notion-structure.md`
