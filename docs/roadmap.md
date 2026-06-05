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
- [x] Post-apply linter integration — agent runs ruff/mypy/tsc/clippy in verify phase
- [x] Sandbox test execution in shadow workspace before promotion — verify phase runs tests in shadow; promotion only on verified=true

---

## Phase 4a (Weeks 15-17): Agentic Tools & Shell Integration ✅ COMPLETE

### PlanningAgent — Explore-then-Commit Loop ✅ Done
- [x] `PlanningAgent` with `PlanningLoop`: ReAct explore-then-commit loop before committing to a plan
- [x] `PlanningToolRegistry`: read-only tools (`search_code`, `read_file`, `list_directory`) for planning phase
- [x] `create_planning_step()` protocol on `ReasoningEngine` — all providers implement it
- [x] Markdown plan emitted at `AWAITING_PLAN_APPROVAL`; user can provide feedback to re-explore
- [x] Delta replan (`revision_needed` signal → `PlanningAgent.revise()`) — targeted step revisions without full restart; budget: `max_delta_replans`
- [x] Planning tool call trace written to `planning-trace.json` artifact

### ToolLoop — Two-Phase ReAct Execution ✅ Done
- [x] `ToolLoop`: per-step ReAct loop (Thought → Tool → Observe) replacing single-shot patch call
- [x] Phase 1 (explore): agent calls tools, emits patch when confident
- [x] Phase 2 (verify): agent always enters verify after patch — no skip for missing `test_command`; agent discovers run targets from `testing_strategy` and touched files
- [x] Guard 2: blocks `verify_done(verified=true)` when last `run_command` in verify phase exited non-zero
- [x] `testing_strategy` vs `test_command` split: planner sets `testing_strategy` (hint) on every code step; `test_command` only when test file is itself a step target — prevents running stale tests before import is updated
- [x] Pre-existing failure baseline: `_normalize_error_message` fingerprints pytest + cargo failures before patching; post-patch comparison filters them out so pre-existing red tests don't block promotion
- [x] PRE-EXISTING FAILURES prompt rule: agent instructed to run scoped test command (e.g. `pytest tests/test_foo.py::test_bar`) instead of full suite when unrelated failures are present
- [x] `read_file` source-of-truth rule: agent always reads original workspace; must reason from conversation history for post-patch state — documented in `tool_prompts.py`
- [x] Scope extension callback: out-of-scope file writes routed to approval callback; conventional files auto-approved
- [x] Step tool call trace written to `step-<id>/tool-trace.json` artifact

### Code Search Tools ✅ Done
- [x] **`search_code`** (ripgrep): exact/regex pattern search; file-type filters, context lines, structured output
- [x] **`search_semantic`**: vector similarity search against live semantic index; ranked chunks
- [x] Both tools available in both planning and execution loops with phase-gated access
- [x] Tool calls recorded in `AgentToolTrace` alongside patch ops

### Shell / Terminal Tool ✅ Done
- [x] **`run_command`**: agent runs shell commands in shadow workspace; stdout/stderr/exit code injected into context
- [x] Command allowlist policy: `AI_EDITOR_SHELL_ALLOWLIST` (default: pytest, npm, cargo, ruff, mypy, tsc, eslint)
- [x] **`find_binary`**: probes `.venv/bin`, `node_modules/.bin`, PATH; emits `AGENT SHOULD: setup_env` hint on miss
- [x] **`setup_env`**: installs dependencies from manifest (uv, pip, npm ci, yarn, pnpm, go mod, rustup); reads patched shadow manifest
- [x] **`init_workspace`**: scaffolds minimal valid manifest for bare workspaces (Python/Node/Rust/Go)
- [x] Pre-existing failure baseline: cargo/pytest failures present before patching are fingerprinted and filtered from post-patch validation

---

## Phase 4b (Weeks 19-22): Extended Agentic Capabilities
**Status**: 🔲 Not started.

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

## Deferred Refactors / Tech Debt

- [ ] **Unify the three decision gates** — `AWAITING_SCOPE_DECISION`, `AWAITING_VALIDATION_DECISION`, and the new `AWAITING_COMMAND_DECISION` each duplicate the same future-dict + status-transition + broadcast + resume boilerplate in `orchestrator/engine.py`. Extract a shared `_pause_for_decision(kind, payload, timeout)` scaffold (rule of three). **Deferred on purpose:** do it *after* the command-approval gate lands — folding it into that feature would risk the two already-working gates. See `docs/superpowers/specs/2026-05-28-shell-command-approval-gate-design.md`.

---

## Implementation Timeline Summary

| Phase | Weeks | Focus | Status |
|-------|-------|-------|--------|
| Phase 0 | 1-2 | Eval harness + baseline | ✅ Complete |
| Phase 1 | 3-6 | Enhanced patch operations | ✅ Complete (benchmarks pending) |
| Phase 2 | 7-10 | Two-stage retrieval | ✅ Substantially complete |
| Phase 3 | 11-14 | Streaming, resume/rollback | ✅ Substantially complete |
| Phase 4a | 15-17 | Agentic tools, shell, verify phase | ✅ Complete |
| Phase 4b | 19-22 | Web search, MCP, context attachments, autonomy modes | 🔲 Not started |
| Phase 5 | 20-22 | Chat, inline editing, GitHub | 🔲 Not started |
| Phase 6 | 23-24 | Memory, multi-agent, autonomy | 🔲 Not started |

## Supporting Artifacts
- Implementation plan: `docs/implementation-plan.md`
- Task board: `docs/task-board.md`
- Architecture: `docs/architecture.md`
- Debugging guide: `CLAUDE.md` (Debugging Methodology section)
