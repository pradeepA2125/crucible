# Roadmap

Program baseline: 24-week parity+ roadmap targeting Cursor/Windsurf core parity first, then differentiation toward a generic agentic code IDE on par with Claude Code and Cursor.

---

## Phase 0 (Weeks 1-2): Productization Baseline + Eval Harness ✅ COMPLETE
- [x] Unified evaluation harness (plan/patch/promotion/UX reliability).
- [x] Benchmark corpus freeze (100 internal + 50 OSS tasks).
- [x] Deterministic replay bundle and loader.
- [x] Baseline metrics dashboard and weekly scorecard.

---

## Phase 1 (Weeks 3-6): Enhanced Patch Operations ✅ COMPLETE
**Status**: Implementation complete, 12/12 tests passing, pending benchmark validation.

- [x] SearchReplaceOpV2 (Fast Apply): O(N) text search/replace for precise edits
- [x] ApplyDiffOpV2 (Unified Diff): Multi-hunk diff application with context validation
- [x] Codex-style diff format support (`*** Begin/End Patch` markers)
- [x] Preflight validation: simulated apply, anchor stability, context mismatch detection
- [x] Candidate patch ranking and best-candidate application flow
- [x] Step-attempt transactional checkpoint manager with rollback replay metadata
- [x] Plan-target grounding gate: validate step targets against workspace files
- [x] Artifacts API (`GET /v1/tasks/{task_id}/artifacts`)
- [x] Comprehensive test suite (12/12 tests passing)

**Remaining**
- [ ] Benchmark validation: confirm 70% reduction in syntax/indent/anchor-drift failures
- [ ] Run `ai-editor-eval phase1-gate-report`

---

## Phase 2 (Weeks 7-10): Advanced Retrieval Enhancement
**Status**: Not started.

### Two-Stage Retrieval Architecture
- [ ] **ANN Search**: Semantic search with local embeddings
  - Integrate embedding model in indexer-rs (CodeBERT/StarCoder)
  - Update snapshot schema with embeddings
  - Top 100 candidates via approximate nearest neighbor
- [ ] **Exact Match**: Ripgrep integration + graph-based symbol lookup
- [ ] **Hybrid scoring**: 0.6 × graph + 0.4 × semantic
- [ ] **Cross-Encoder Reranker**: `cross-encoder/ms-marco-MiniLM-L-6-v2`, rerank top 100 → top 20
- [ ] **LLM Reranker** (optional fallback for ambiguous queries)

### Lifecycle Cleanup
- [ ] Add explicit `REGENERATING_PLAN` status for plan-feedback regeneration windows (currently reuses `CONTEXT_READY` temporarily, which is misleading)

### Success Metrics
- 40%+ improvement in retrieval relevance (precision@20)
- Stage 1 recall: 90%+ relevant code in top 100
- Query latency: <500ms for two-stage retrieval

---

## Phase 3 (Weeks 11-14): Streaming & Real-Time Feedback ✅ SUBSTANTIALLY COMPLETE

### Streaming Patch API ✅ Done
- [x] SSE endpoint `/v1/tasks/{task_id}/stream-patch`
- [x] Event types: `operation_success`, `operation_error`, `done`
- [x] `PatchEventBroadcaster` with per-task asyncio queues and replay buffer
- [x] `on_patch_event` callback wired into `PatchEngine.apply_patch_candidate`
- [x] Client-side `streamPatch` with SSE line parsing
- [x] VS Code activity log with incremental `postMessage` rendering (no full-page reload)
- [x] Race condition fix: `clear_replay` at `AWAITING_PLAN_APPROVAL` pause; route pre-adds to `_running_tasks`

### Resume / Rollback ✅ Done (shipped ahead of schedule)
- [x] `POST /v1/tasks/{id}/resume` — child task pattern, parent immutable
- [x] Three stages: `plan` (full re-plan), `feedback` (snapshot rollback), `execute` (shadow clone)
- [x] `plan_approval_snapshot`: exact task state serialized at every `AWAITING_PLAN_APPROVAL` transition
- [x] Shadow workspace `clone()` for execute-stage resume
- [x] Budget override (`max_iterations`, `max_tokens`, etc.)
- [x] VS Code controller: `resumeTask`, stage picker, max-iterations prompt

### Remaining
- [x] Incremental per-operation validation (syntax check after each file write, collect all errors)
- [ ] Post-apply linter integration (ruff, eslint, clippy — configurable per workspace)
- [ ] Sandbox test execution in shadow workspace before promotion

---

## Phase 4 (Weeks 15-19): Agentic Tools & Shell Integration
**Status**: Planned. Reframed from "API Integration" to close the gap with Claude Code and Cursor agent modes.

### Shell / Terminal Tool
- [ ] Agent can run shell commands inside a sandboxed environment (workspace-scoped, no network by default)
- [ ] Command allowlist policy (`ToolPolicy` model): read-only vs mutating vs network
- [ ] Output captured and injected into agent context (stdout, stderr, exit code)
- [ ] Supports: test runners (`pytest`, `npm test`, `cargo test`), linters, build commands
- [ ] Audit log: every command invocation recorded with task ID + timestamp

### Web Search & Documentation Tool
- [ ] Agent can query web for error messages, API docs, library versions
- [ ] Results injected into retrieval context alongside codebase snippets
- [ ] Source citation preserved in plan and artifacts

### MCP Server Integration
- [ ] `agentd` exposes itself as an MCP server: `create_task`, `get_task`, `provide_feedback`, `accept_patch`
- [ ] Agent can consume external MCP servers (databases, APIs, internal tools) via tool calls
- [ ] MCP tool calls audited and policy-gated same as shell commands

### Context Attachments
- [ ] `@file` — attach specific file to agent context explicitly
- [ ] `@symbol` — attach symbol definition + usages
- [ ] `@url` — fetch and attach a web page
- [ ] `@diagnostics` — attach current workspace LSP diagnostics

### Agent Autonomy Modes (granular)
- [ ] `supervised` — every step requires approval (current default)
- [ ] `auto-plan` — approve plan only, execution runs unattended
- [ ] `autonomous` — full end-to-end with no gates (requires explicit opt-in)
- [ ] Per-task mode override in submission payload

### Success Metrics
- Shell tool: <2s latency overhead vs direct execution
- Zero unauthorized file writes outside workspace root
- MCP tool round-trip: <500ms

---

## Phase 5 (Weeks 20-22): Editor Parity — Chat, Inline Editing, GitHub
**Status**: Planned. Targets feature-level parity with Cursor Composer and Claude Code.

### Chat Interface
- [ ] Persistent chat thread per workspace alongside task submissions
- [ ] Chat messages can reference task history, files, symbols
- [ ] Agent responds inline (no task lifecycle overhead for Q&A)
- [ ] VS Code WebView chat panel (or sidebar tree view)

### Inline Editing Mode
- [ ] `Cmd+K`-style: select code range → describe change → agent patches in place (no shadow workspace for small edits)
- [ ] Inline diff decoration in the editor (accept/reject per hunk)
- [ ] Fallback to full task flow for multi-file or complex changes

### GitHub / VCS Integration
- [ ] `POST /v1/tasks/{id}/create-pr`: open a PR from the promoted shadow state
- [ ] PR body auto-populated from plan markdown + modified file list
- [ ] Issue-driven flow: attach a GitHub issue URL; agent reads it as goal context
- [ ] Git blame / log context injected into retrieval for files being modified

### Session Persistence
- [ ] Conversation / task history persisted across VS Code sessions
- [ ] Task timeline view: status transitions with timestamps, artifact links
- [ ] Shareable task snapshot (export to JSON for debugging or handoff)

### Cost & Token Tracking
- [ ] Per-task token usage recorded (prompt + completion per LLM call)
- [ ] Cumulative cost estimate shown in review panel
- [ ] Budget hard-limit enforcement (`max_tokens` in `TaskBudget` already exists — wire to UI)

---

## Phase 6 (Weeks 23-24): Autonomy, Memory & Differentiation
**Status**: Planned. Differentiation layer beyond Cursor/Claude Code parity.

### Rules & Memory
- [ ] Project-level rules file (`.ai-editor/rules.md`) — always injected into planning context
- [ ] User-level memory: agent learns and persists preferences, conventions, recurring patterns
- [ ] Memory scoped per workspace; exportable and auditable

### Multi-Agent Orchestration
- [ ] Specialized sub-agents: Planner, Retriever, Patcher, Verifier — each with its own LLM call and tool scope
- [ ] Agent communication via message passing; coordinator drives the pipeline
- [ ] Parallel step execution where dependency graph allows
- [ ] Per-agent model selection (cheap model for retrieval, strong model for planning)

### Autonomous Refactor Mode
- [ ] Long-running tasks with staged promotion (promote file-by-file, not all-or-nothing)
- [ ] Agent checkpoints after each file; user can review incrementally
- [ ] Automatic rollback on test failure in shadow

### Evaluation & Continuous Improvement
- [ ] Live benchmark runs on every significant model/prompt change
- [ ] A/B prompt evaluation: compare plan quality across provider/model pairs
- [ ] Failure cluster auto-labeling: tag failed tasks by root cause for corpus growth

---

## Implementation Timeline Summary

| Phase | Weeks | Focus | Status |
|-------|-------|-------|--------|
| Phase 0 | 1-2 | Eval harness + baseline | ✅ Complete |
| Phase 1 | 3-6 | Enhanced patch operations | ✅ Complete (benchmarks pending) |
| Phase 2 | 7-10 | Two-stage retrieval | 🔲 Not started |
| Phase 3 | 11-14 | Streaming, resume/rollback | ✅ Substantially complete |
| Phase 4 | 15-19 | Agentic tools, shell, MCP | 🔲 Not started |
| Phase 5 | 20-22 | Chat, inline editing, GitHub | 🔲 Not started |
| Phase 6 | 23-24 | Memory, multi-agent, autonomy | 🔲 Not started |

## Supporting Artifacts
- Implementation plan: `docs/implementation-plan.md`
- Task board: `docs/task-board.md`
- Architecture: `docs/architecture.md`
- Debugging guide: `CLAUDE.md` (Debugging Methodology section)
