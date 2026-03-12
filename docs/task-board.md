# Task Board

## Program Track: Parity+ 24-Week Plan

### Completed Foundation
- [x] Polyglot split (`apps/editor-client`, `services/agentd-py`, `services/indexer-rs`)
- [x] Shadow workspace + forbidden-path policy
- [x] SQLite persistence + lifecycle states + review/promote flow
- [x] LSP diagnostics + parser registry + artifact-first retrieval
- [x] VS Code MVP review loop
- [x] Step-scoped patch execution + deterministic preflight guardrails

### Completed Sprint (Phase 0: Eval Harness)
- [x] Define benchmark corpus manifest (`100 internal + 50 OSS` with language mix and task labels).
- [x] Add replay bundle schema and deterministic replay runner command.
- [x] Add baseline scorer (success rate, retry count, unsafe mutation rate, human accept rate).
- [x] Add weekly benchmark summary artifact (`.tmp/benchmarks/YYYY-MM-DD-report.json`).
- [x] Add failure cluster report command (top 5 failure modes by taxonomy).

### Completed Sprint (Phase 1: Enhanced Patch Operations) ✅
- [x] Patch preflight dependency graph checks (op ordering and symbol/anchor stability).
- [x] Parse-before-apply simulation for TS/Py/Rs touched files.
- [x] Plan-target grounding gate: validate step targets against workspace files and trigger one-shot replan with missing-target feedback.
- [x] Step-attempt transactional checkpoint manager with rollback replay metadata.
- [x] Candidate patch ranking and best-candidate application flow.
- [x] Additive artifacts API (`GET /v1/tasks/{task_id}/artifacts`) and V2 candidate result wiring.
- [x] Regression suite updates for known syntax/indent/anchor drift failures.
- [x] Phase 1 failure corpus seed + benchmark gate report command.
- [x] **NEW**: SearchReplaceOpV2 (Fast Apply) - O(N) text search/replace for large files.
- [x] **NEW**: ApplyDiffOpV2 (Unified Diff) - Multi-hunk diff application with context validation.
- [x] **NEW**: Codex-style diff format support (`*** Begin/End Patch` markers).
- [x] **NEW**: Enhanced LLM prompts with strategy-based operation selection.
- [x] **NEW**: Comprehensive test suite (12/12 tests passing).
- [x] **NEW**: Newline normalization for robust diff validation.

### Current Sprint (Phase 2 Planning)
- [ ] Benchmark Phase 1 implementation against failure corpus.
- [ ] Validate 70% reduction in syntax/indent/anchor-drift failures.
- [ ] Plan Phase 2: Planner/Executor/Critic v2 implementation.

### Upcoming (Phases 2-3)
- [ ] Plan graph v2 (`preconditions`, `postconditions`, `verification`) and critic taxonomy.
- [ ] Rules/memory precedence engine and scoped policy evaluation.
- [ ] Task timeline and artifacts APIs (`/events`, `/timeline`, `/artifacts`).
- [ ] VS Code timeline UI + background task resume/rollback controls.
- [ ] Code review assistant mode and MCP policy audit controls.
