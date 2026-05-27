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
**Status**: ✅ Substantially complete.

### Two-Stage Retrieval Architecture
- [x] **Semantic search**: LanceDB + `BAAI/bge-small-en-v1.5`, delta indexing (mtime-based, only re-embeds changed files)
- [x] **Hybrid scoring**: 0.35 × graph + 0.65 × semantic with segment-aware path matching (fixes cross-repo file bias, e.g. `pydantic-core/` vs `pydantic/`)
- [x] **Cold-start elimination**: `POST /v1/index/build` + `GET /v1/index/status` pre-warm API; `start-backend.sh` waits for index ready before printing "backend ready"; Rust indexer notifies backend after every snapshot write via `AI_EDITOR_BACKEND_URL`
- [ ] ~~**Cross-Encoder Reranker**~~: Skipped — MS MARCO models don't understand code syntax; graph signals already capture structural relevance; ripgrep + LLM reranker deferred as low ROI
- [ ] ~~**Ripgrep exact-match layer**~~: Deferred — low incremental impact given hybrid scoring quality
- [ ] ~~**`REGENERATING_PLAN` status**~~: Deferred — low user-facing impact

### What shipped vs. original plan
- Embeddings run Python-side (not in indexer-rs) — simpler, works well, Rust notifies Python after each snapshot
- Segment-aware graph scoring replaced naive substring matching — fixed real retrieval bias observed in pydantic workspace
- Pre-warm pipeline fully automated: Rust indexer → backend API → `_last_indexed_snapshot_ms` skip guard in `load_context()`

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
**Status**: Core tool loop shipped (code search + shell). Web search, MCP, context attachments, and autonomy modes not yet started.

### Code Search Tools ✅ Shipped
Two complementary tools the agent can call mid-task — replaces the need for exhaustive pre-task retrieval:

- [x] **`search_code`** (ripgrep): exact/regex pattern search across the shadow workspace; file-type filters, context lines, structured output (file, line, match).
- [x] **`search_semantic`**: vector similarity search against the live semantic index; returns ranked chunks.
- [x] Both tools produce structured results injected into the agent's step context
- [x] Tool calls recorded in step artifacts alongside patch ops

### Shell / Terminal Tool ✅ Shipped
- [x] Agent can run shell commands inside a sandboxed environment (workspace-scoped)
- [x] Command allowlist policy: configurable via `AI_EDITOR_SHELL_ALLOWLIST` env var
- [x] Output captured and injected into agent context (stdout, stderr, exit code)
- [x] Supports: `pytest`, `npm`, `cargo`, `ruff`, `mypy`, `tsc`, `eslint`

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
These are modes for a **single agent session** — not spawned subagents. A single ReAct loop runs end-to-end; the mode controls which gates require user confirmation.
- [ ] `supervised` — every step requires approval (current default)
- [ ] `auto-plan` — approve plan only, execution runs unattended
- [ ] `autonomous` — full end-to-end with no gates (requires explicit opt-in)
- [ ] Per-task mode override in submission payload

### Success Metrics
- Shell tool: <2s latency overhead vs direct execution
- Zero unauthorized file writes outside workspace root
- MCP tool round-trip: <500ms

---

## Phase 4b (Weeks 19-22): Agentic Planning + Delta Replan ✅ COMPLETE
**Status**: Shipped. Spec: `docs/superpowers/specs/2026-04-27-agentic-planning-delta-replan-design.md`. Plan: `docs/superpowers/plans/2026-04-27-agentic-planning-delta-replan.md`.

Replaces static planning (single-shot markdown critique loop) with two cooperating agents. All industry evidence (Claude Code, Cursor, SWE-agent, OpenHands) shows these operate as a single-agent-per-session ReAct loop with specialized roles — not spawned subagents.

### PlanningAgent (explore-then-commit)
- [x] Replace static markdown critique loop with `PlanningAgent`: explore-then-commit ReAct loop
- [x] Planning tools: `search_code`, `read_file`, `list_directory`, `search_semantic`
- [x] `emit_plan` response with `files_examined` + `confidence` signal
- [x] Low-confidence planning → warning diagnostic surfaced to user
- [x] `planning_tool_call` / `planning_tool_result` / `planning_complete` SSE events

### Delta Replan (execution → planning handoff)
- [x] `ToolLoop.run()` returns `StepOutcome = PatchResult | PlanHandoff` — no exceptions across agent boundaries
- [x] `revision_needed` action: step signals fundamentally wrong plan, hands off to `PlanningAgent`
- [x] `PlanningAgent.revise()` explores real workspace, returns targeted step revision
- [x] `_apply_revision()`: checkpoint rollback + wholesale step replacement
- [x] `max_delta_replans` budget guard
- [x] Automatic (no user gate) — `delta_replan_applied` SSE event

### One-Step-Per-File Constraint
- [x] Planning prompt enforces one step per file
- [x] Post-emit validation rejects plans with duplicate file targets across steps
- [x] Same check applied after `_apply_revision()`

### Verify Phase Enhancements (shipped alongside)
- [x] Verify phase always runs after patch — no skip for missing `test_command`
- [x] `testing_strategy` (always set) vs `test_command` (only when test file is a step target) split
- [x] Guard 2: blocks `verify_done(verified=true)` when last `run_command` exited non-zero
- [x] PRE-EXISTING FAILURES rule: agent runs scoped test command instead of full suite
- [x] Pre-existing failure baseline normalization for pytest + cargo test output

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
The `PlanningAgent` + `ExecutionAgent` (ToolLoop) shared-state pipeline from Phase 4b is the foundational multi-agent architecture — specialized roles coordinated through `TaskRecord`, not spawned subagents. Phase 6 extends it:
- [ ] Parallel step execution where the dependency graph allows genuine concurrent subagents (steps with no shared file targets run simultaneously)
- [ ] Per-role model selection: cheap/fast model for retrieval and search, strong model for planning and patching
- [ ] Verifier agent: dedicated post-step validation pass with its own tool scope (runs tests, checks types, reports structured results)
- [ ] Dependency graph inference: planner emits step preconditions; coordinator uses them to schedule parallel vs. sequential execution

### Autonomous Refactor Mode
- [ ] Long-running tasks with staged promotion (promote file-by-file, not all-or-nothing)
- [ ] Agent checkpoints after each file; user can review incrementally
- [ ] Automatic rollback on test failure in shadow

### Evaluation & Continuous Improvement
- [ ] Live benchmark runs on every significant model/prompt change
- [ ] A/B prompt evaluation: compare plan quality across provider/model pairs
- [ ] Failure cluster auto-labeling: tag failed tasks by root cause for corpus growth

---

## Distribution & Packaging (Shadow Forge installer) — PARKED, design pending

**Status**: 🔲 Not started. To be designed. Goal: a one-command install for "AI Editor" (officially **Shadow Forge**) that stands up the whole stack on a clean machine.

Open questions to resolve in the design discussion:
- [ ] Cross-platform story (macOS arm64 first; Linux x86_64/arm64; Windows?)
- [ ] Rust indexer (`indexer-rs`): ship prebuilt binaries per platform vs. build-from-source on install (`cargo build --release` is a multi-minute compile). Relates to the `start-backend.sh` watcher autobuild question — gate behind `AI_EDITOR_INDEXER_AUTOBUILD`.
- [ ] Python backend (`agentd-py`): venv bootstrap + `pip install -e .` vs. packaged wheel vs. container image
- [ ] VS Code extension: package as `.vsix` + marketplace listing vs. sideload
- [ ] Model/provider setup: interactive prompt for provider + API keys → write `.env`; local-model path (Ollama/TurboQuant) detection
- [ ] Semantic index deps (LanceDB, sentence-transformers, torch) — heavy; optional install profile (`--semantic`)?
- [ ] Health-check + first-run pre-warm so semantic search isn't cold on first task

---

## Implementation Timeline Summary

| Phase | Weeks | Focus | Status |
|-------|-------|-------|--------|
| Phase 0 | 1-2 | Eval harness + baseline | ✅ Complete |
| Phase 1 | 3-6 | Enhanced patch operations | ✅ Complete (benchmarks pending) |
| Phase 2 | 7-10 | Two-stage retrieval | ✅ Substantially complete |
| Phase 3 | 11-14 | Streaming, resume/rollback | ✅ Substantially complete |
| Phase 4 | 15-19 | Agentic tools, shell, MCP | 🔶 Tool loop shipped; web search/MCP/autonomy modes not started |
| Phase 4b | 19-22 | Agentic planning + delta replan | 🔲 Spec complete, implementation planned |
| Phase 5 | 22-24 | Chat, inline editing, GitHub | 🔲 Not started |
| Phase 6 | 24-26 | Memory, multi-agent, autonomy | 🔲 Not started |

## Supporting Artifacts
- Implementation plan: `docs/implementation-plan.md`
- Task board: `docs/task-board.md`
- Architecture: `docs/architecture.md`
- Debugging guide: `CLAUDE.md` (Debugging Methodology section)
