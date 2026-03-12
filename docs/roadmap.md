# Roadmap

Program baseline: 24-week parity+ roadmap targeting Cursor/Windsurf core parity first, then differentiation.

## Phase 0 (Weeks 1-2): Productization Baseline + Eval Harness
- [x] Unified evaluation harness (plan/patch/promotion/UX reliability).
- [x] Benchmark corpus freeze (100 internal + 50 OSS tasks).
- [x] Deterministic replay bundle and loader.
- [x] Baseline metrics dashboard and weekly scorecard.

## Phase 1 (Weeks 3-6): Enhanced Patch Operations ✅ COMPLETE
**Status**: Implementation complete, 12/12 tests passing, pending benchmark validation.

### Core Features (Implemented)
- [x] **SearchReplaceOpV2** (Fast Apply): O(N) text search/replace for precise edits
  - Exact text matching with uniqueness validation
  - Preflight validation for anchor presence
  - Fallback to slow apply on failure
- [x] **ApplyDiffOpV2** (Unified Diff): Multi-section edits with context lines
  - Standard unified diff format (`@@ -start,count +start,count @@`)
  - Hunk-by-hunk application with context validation
  - Offset tracking for sequential hunks
- [x] **Codex-style diff format support**: `*** Begin/End Patch` marker parsing
- [x] **Preflight validation**: Simulated apply for all operations
  - Anchor stability checks
  - Context mismatch detection
  - Order conflict identification
- [x] **Enhanced LLM prompts**: Hybrid structured approach with operation examples
- [x] **Comprehensive test suite**: 12/12 tests passing
  - 5 search_replace tests (exact, partial, multi-line, edge cases)
  - 6 apply_diff tests (single/multi-hunk, additions, deletions, modifications)
  - 1 Codex format test

### Technical Implementation
- [x] Model definitions in `agentd/domain/models.py`
- [x] Search/replace engine with Fast Apply pattern
- [x] Diff application engine using `unidiff` library
- [x] Newline normalization in validation
- [x] Codex diff parser integration
- [x] Updated `PatchOperationV2` union type

### Exit Criteria
- [x] All unit tests passing (12/12)
- [ ] Benchmark validation: 70% reduction in syntax/indent/anchor-drift failures
- [ ] Command: `ai-editor-eval phase1-gate-report`

### Success Metrics (Target)
- 95%+ search/replace success rate
- 90%+ LLM-generated diff application success
- 95%+ preflight catch rate for invalid operations
- <50ms for search/replace, <100ms for diff application
- 80%+ fallback success rate

## Phase 2 (Weeks 7-10): Advanced Retrieval Enhancement
**Status**: Planned - Two-stage retrieval architecture.

### Stage 1: Fast Retrieval (Broad Candidate Gathering)
- [ ] **ANN Search**: Semantic search with local embeddings
  - Integrate embedding model in indexer-rs (CodeBERT/StarCoder)
  - Update snapshot schema with embeddings
  - Top 100 candidates via approximate nearest neighbor
- [ ] **Exact Match**: Symbol name lookup
  - Ripgrep integration for fast text search
  - Graph-based symbol lookup
  - 100% recall for symbol name queries
- [ ] **Hybrid scoring**: Combine graph-based + semantic scoring
  - Weight: 0.6 * graph + 0.4 * semantic
  - Path bias for relevant directories

### Stage 2: Reranking (Precise Scoring)
- [ ] **Cross-Encoder Reranker**: Fast, accurate relevance scoring
  - Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`
  - Rerank top 100 → top 20 for prompt
  - <500ms query latency
- [ ] **LLM Reranker** (Optional): For complex queries
  - Fallback for ambiguous queries
  - Cost-aware (limit to top 50 candidates)

### Success Metrics (Target)
- 40%+ improvement in retrieval relevance (precision@20)
- Stage 1 recall: 90%+ relevant code in top 100
- Stage 2 precision: 80%+ of top 20 results relevant
- Indexing time increase <20%
- Query latency: <500ms for two-stage retrieval

## Phase 3 (Weeks 11-14): Streaming & Real-Time Feedback
**Status**: Planned - Incremental validation and streaming API.

### Streaming Patch API
- [ ] **SSE Endpoint**: Real-time patch application events
  - `/v1/tasks/{task_id}/stream-patch`
  - Event types: candidate_start, operation_success, operation_error, candidate_complete
- [ ] **Incremental Validation**: Per-operation validation
  - Syntax checking (Python, TypeScript, Rust)
  - Immediate error feedback
  - Fail-fast on first error

### Post-Apply Validation
- [ ] **Syntax validation**: Language-specific parsers
- [ ] **Linter integration**: Optional linting (configurable)
- [ ] **Sandbox testing**: Run tests in shadow workspace
- [ ] **Approval flow**: User confirmation for multi-file changes

### Success Metrics (Target)
- Real-time feedback within 500ms of operation completion
- Fail-fast: Stop on first error within 1 second
- UI responsiveness: No blocking during patch application
- Validation coverage: 100% syntax, 80% linter integration
- Sandbox test execution: <30 seconds

## Phase 4 (Weeks 15-19): API Integration & Tool Extensibility
**Status**: Planned - Internal API design and tool framework.

### Internal API Endpoints
- [ ] **POST /v1/index**: Repository indexing
  - Input: file path or repo URL
  - Output: indexed vector count, status
- [ ] **POST /v1/search**: Code search
  - Input: natural language query + optional context
  - Output: ranked code snippets with scores
- [ ] **POST /v1/apply**: Patch application
  - Input: format tag (udiff/search-replace), patch content
  - Output: success/failure, updated file diff
- [ ] **POST /v1/ast-query**: AST node queries
  - Input: pattern matching criteria
  - Output: matching nodes with locations
- [ ] **POST /v1/validate**: Sandbox validation
  - Input: changes to validate
  - Output: test results, build status

### Tool Policy Framework
- [ ] **ToolPolicy model**: Allowlist, scope, audit controls
- [ ] **Terminal tool**: Safe command execution with sandboxing
- [ ] **Browser tool**: Web interaction with policy controls
- [ ] **Git tool**: Version control operations with audit
- [ ] **Audit dashboard**: Tool invocation logging and monitoring

### Success Metrics (Target)
- API response time: <100ms for /search, <200ms for /apply
- API uptime: 99.9%
- Zero security incidents from tool usage
- 100% audit coverage for sensitive operations
- User approval flow <5 seconds latency

## Phase 5 (Weeks 20-24): Multi-Agent Orchestration & Differentiation
**Status**: Planned - Specialized agents and autonomous workflows.

### Multi-Agent Architecture
- [ ] **Planner Agent**: Task decomposition and planning
- [ ] **Retriever Agent**: Context gathering and ranking
- [ ] **Patcher Agent**: Code modification execution
- [ ] **Verifier Agent**: Validation and testing
- [ ] **Agent communication**: Message passing and coordination

### Advanced Capabilities
- [ ] **Autonomous refactor mode**: Long-running tasks with staged promotion
- [ ] **Knowledge spaces**: Memory ingestion and retrieval
- [ ] **Issue-driven flow**: Issue → plan → patch → review artifacts
- [ ] **Collaboration metadata**: Provenance, approvals, shareable context

### Exit Criteria
- +20% complex-task success vs Phase 3
- No safety regression
- Multi-agent coordination latency <2 seconds

## Implementation Timeline Summary

| Phase | Duration | Focus | Status |
|-------|----------|-------|--------|
| Phase 0 | Weeks 1-2 | Eval harness + baseline | ✅ Complete |
| Phase 1 | Weeks 3-6 | Enhanced patch operations | ✅ Complete (pending benchmarks) |
| Phase 2 | Weeks 7-10 | Two-stage retrieval | 🔄 Planned |
| Phase 3 | Weeks 11-14 | Streaming & validation | 🔄 Planned |
| Phase 4 | Weeks 15-19 | API & tool extensibility | 🔄 Planned |
| Phase 5 | Weeks 20-24 | Multi-agent & differentiation | 🔄 Planned |

## Supporting Program Artifacts
- Implementation plan: `docs/implementation-plan.md`
- Parity+ plan: `docs/program/parity-plus-6-month-plan.md`
- Linear operating model: `docs/program/linear-execution-model.md`
- Notion operating model: `docs/program/notion-structure.md`
- Phase 1 completion summary: `docs/phase1-completion-summary.md`
