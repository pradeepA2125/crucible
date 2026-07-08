# Competitive Analysis: Crucible vs. Market Leaders

## Executive Summary

Crucible has a **superior architectural foundation** compared to Cursor, Windsurf, Cline, and Void. The system's CST/AST-based patching, deterministic orchestration, and graph-based retrieval position it ahead of competitors who rely on fragile text-based edits and ad-hoc context management.

**Key Differentiators:**
- ✅ **Structural patching** (CST/AST) vs. competitors' text-based diffs
- ✅ **Graph-based retrieval** (symbol relationships) vs. vector-only or keyword search
- ✅ **Deterministic orchestration** with transactional checkpoints vs. ad-hoc loops
- ✅ **Preflight validation** prevents errors before applying patches
- ✅ **Shadow workspace** isolation for safe experimentation

**Strategic Gaps:**
- ⚠️ No semantic embeddings (Cursor/Codex have vector search)
- ⚠️ Limited streaming/real-time feedback (Cursor has fast apply model)
- ⚠️ No multi-agent parallelization (Codex App has parallel agents)

---

## 1. Indexing & Retrieval Comparison

### Current Crucible Implementation

**Architecture** ([`artifact_client.py`](services/agentd-py/agentd/retrieval/artifact_client.py:26-65)):
```python
@dataclass(frozen=True)
class RetrievalContext:
    repository_structure: list[str]      # File tree summary
    related_files: list[str]             # Top 20 relevant files
    related_symbols: list[str]           # Top 40 symbols
    graph_neighbors: list[str]           # Connected nodes (imports/calls)
    file_outlines: dict[str, list[str]]  # Symbol outlines per file
    file_contents: dict[str, str]        # Full source for patch targets
    diagnostics_excerpt: list[str]       # LSP errors/warnings
    snapshot_stats: dict[str, int]       # Graph size metrics
```

**Indexing Pipeline** ([`indexer-rs`](services/indexer-rs/README.md:5-9)):
- Tree-sitter parsing (TS/Py/Rust)
- Symbol graph materialization (nodes + edges)
- LSP diagnostics enrichment
- Incremental filesystem watching

**Retrieval Strategy** ([`artifact_client.py:300-323`](services/agentd-py/agentd/retrieval/artifact_client.py:300-323)):
1. **Keyword matching**: Extract terms from goal, score nodes by term frequency
2. **Graph traversal**: Follow edges to find connected symbols
3. **Ranking**: Top 500 nodes → Top 20 files + Top 40 symbols
4. **Adapter boundary**: Repo/domain heuristics are opt-in adapters, not part of the default retrieval core

### Competitor Approaches

| System | Indexing | Retrieval | Strengths | Weaknesses |
|--------|----------|-----------|-----------|------------|
| **Cursor** | Vector embeddings (entire codebase) | ANN + LLM reranker | Semantic understanding, scales to large repos | Privacy concerns, embedding costs |
| **Windsurf** | Internal search map + import paths | LLM-powered search tool | Follows code relationships | Less documented, proprietary |
| **Codex CLI** | FAISS vector DB (200-400 line chunks) | Embedding similarity search | OpenAI-quality embeddings | Requires local FAISS, chunking overhead |
| **Cline** | None (text search via ripgrep) | Keyword/regex matching | Fast, simple | Misses conceptual relationships |
| **Void IDE** | None (filesystem awareness) | Agent-driven search | Privacy-focused, local control | Limited context depth |
| **Crucible** | Tree-sitter + LSP graph | Keyword + graph traversal | Structural relationships, LSP diagnostics | No semantic embeddings |

### Gap Analysis

**Crucible Advantages:**
- ✅ **Graph-based retrieval** understands code structure (imports, calls, inheritance)
- ✅ **LSP integration** provides real-time diagnostics
- ✅ **Incremental indexing** with filesystem watching
- ✅ **Privacy-preserving** (no external embedding API calls)
- ✅ **Generic core by default** with optional repo/domain adapters

**Missing Capabilities:**
- ❌ **Semantic embeddings** for conceptual similarity (e.g., "authentication" → `verify_token()`)
- ❌ **Hybrid retrieval** combining symbolic + semantic search
- ❌ **LLM reranking** to refine results based on task context

**Recommendation:**
Implement **Phase 5 Retrieval v2** ([`roadmap.md:40`](docs/roadmap.md:40)) with:
1. Local embedding model (e.g., CodeBERT, StarCoder embeddings)
2. Hybrid scoring: `score = 0.6 * graph_score + 0.4 * semantic_score`
3. Optional LLM reranker for top-N candidates

---

## 2. Code Editing (Patching) Comparison

### Current Crucible Implementation

**V2 Operations** ([`models.py:142-170`](services/agentd-py/agentd/domain/models.py:142-170)):
```python
class ReplaceNodeOpV2:  # CST/AST-based replacement
    op: "replace_node"
    language: "python" | "typescript" | "rust"
    selector: NodeSelector  # Symbol-based matching
    content: str
    
class InsertAfterNodeOpV2:  # CST/AST-based insertion
    op: "insert_after_node"
    language: "python" | "typescript" | "rust"
    selector: NodeSelector
    content: str
```

**Patch Engine** ([`engine.py:687-818`](services/agentd-py/agentd/patch/engine.py:687-818)):
- **Python**: libcst transformers with position metadata
- **TypeScript/Rust**: tree-sitter byte offset replacement
- **Preflight validation**: Simulates patches before applying
- **Transactional checkpoints**: Rollback on failure

### Competitor Approaches

| System | Patch Format | Application | Strengths | Weaknesses |
|--------|--------------|-------------|-----------|------------|
| **Cursor** | Semantic diffs with context | Fast apply model (~1000 tok/s) | Streaming, efficient | Text-based (anchor drift) |
| **Codex CLI** | Triple-asterisk format (`@@` anchors) | Unified diff merger | GPT-4.1 trained on format | Manual anchor specification |
| **Void IDE** | Fast Apply (search/replace) or Slow Apply (full rewrite) | Dual-mode executor | Scales to 1000-line files | Search/replace fragility |
| **Cline** | File edit tools | Direct file I/O | Simple | No structural awareness |
| **Crucible** | CST/AST node operations | libcst/tree-sitter transformers | **Structural safety, anchor stability** | No streaming apply yet |

### Gap Analysis

**Crucible Advantages:**
- ✅ **CST/AST patching** eliminates anchor drift and syntax errors
- ✅ **Preflight validation** catches conflicts before applying
- ✅ **Transactional rollback** ensures safe experimentation
- ✅ **Multi-language support** (Python, TypeScript, Rust)

**Missing Capabilities:**
- ❌ **Streaming apply** for real-time feedback (Cursor's fast model)
- ❌ **Diff-based format** for LLM familiarity (unified diff is industry standard)
- ❌ **Partial file edits** without full AST parsing (for large files)

**Recommendation:**
Extend V2 with **hybrid operations**:
1. Keep CST/AST for structural edits (classes, functions)
2. Add **diff-based operation** for simple edits:
   ```python
   class ApplyDiffOpV2:
       op: "apply_diff"
       file: str
       diff: str  # Unified diff format
       reason: str
   ```
3. Implement **streaming apply** with incremental validation

---

## 3. Planning & Agent Workflows Comparison

### Current Crucible Implementation

**Orchestration** ([`engine.py`](services/agentd-py/agentd/orchestrator/engine.py)):
- **State machine**: `QUEUED → CONTEXT_READY → AWAITING_PLAN_APPROVAL → PLANNED → EXECUTING → VALIDATING → VALIDATED → READY_FOR_REVIEW → SUCCEEDED`
- **Plan structure** ([`models.py:67-78`](services/agentd-py/agentd/domain/models.py:67-78)):
  ```python
  class PlanDocument:
      analysis: str
      steps: list[PlanStep]  # Sequential execution
      expected_files: list[str]
      stop_conditions: list[str]
  ```
- **Repair loop**: Automatic retry with critic feedback
- **Budget enforcement**: Max iterations, tokens, runtime

### Competitor Approaches

| System | Planning | Execution | Strengths | Weaknesses |
|--------|----------|-----------|-----------|------------|
| **Cursor** | ReAct loop (reason → act → observe) | Tool calls (search, apply, terminal) | Flexible, agentic | Unpredictable, resource-intensive |
| **Windsurf Cascade** | AI Flows (up to 20 tool invocations) | User-approved action plan | Transparent, adaptable | Requires user approval |
| **Cline** | Plan Mode (read-only) → Act Mode (edit) | Explicit mode separation | Clear mental model | Manual mode switching |
| **Codex App** | Multi-agent orchestration | Parallel agents across branches | Scales to large projects | Complex coordination |
| **Crucible** | Deterministic state machine | Sequential step execution | **Predictable, transactional** | No parallel execution |

### Gap Analysis

**Crucible Advantages:**
- ✅ **Deterministic orchestration** with clear state transitions
- ✅ **Transactional checkpoints** for rollback
- ✅ **Budget enforcement** prevents runaway costs
- ✅ **Repair loop** with automatic retry

**Missing Capabilities:**
- ❌ **ReAct-style agentic loops** for flexible tool use
- ❌ **Parallel execution** of independent steps
- ❌ **User-in-the-loop** approval for high-risk actions
- ❌ **Tool extensibility** (terminal, browser, git commands)

**Recommendation:**
Implement **Phase 4 Workflow Layer** ([`roadmap.md:33-36`](docs/roadmap.md:33-36)) with:
1. **Plan graph v2** with preconditions/postconditions ([`roadmap.md:22`](docs/roadmap.md:22))
2. **Parallel step execution** for independent tasks
3. **Tool policy controls** ([`architecture.md:66-67`](docs/architecture.md:66-67)) for safe extensibility
4. **Interactive approval** for destructive operations

---

## 4. Architecture Patterns & Trade-offs

### Crucible's Design Philosophy

**Core Principles** ([`architecture.md:21-26`](docs/architecture.md:21-26)):
1. **Deterministic boundaries**: Model output never executed directly
2. **Artifact-backed retrieval**: Loaded once per task (no per-loop chatter)
3. **Validation gates**: Plan targets validated against real workspace
4. **Transactional safety**: Checkpoints with rollback

**Polyglot Architecture** ([`architecture.md:3-19`](docs/architecture.md:3-19)):
- **TypeScript**: UI/client (editor-client, vscode-extension)
- **Python**: Orchestration (agentd-py)
- **Rust**: Indexing (indexer-rs)

### Competitor Patterns

**Cursor/Windsurf**: Monolithic agentic loops
- **Pros**: Flexible, handles novel cases
- **Cons**: Unpredictable, hard to debug, resource-intensive

**Cline**: Explicit Plan/Act separation
- **Pros**: Clear mental model, prevents premature edits
- **Cons**: Manual mode switching, less automated

**Void IDE**: Open-source Cursor alternative
- **Pros**: Privacy-focused, any LLM support
- **Cons**: Less mature, limited documentation

**Codex App**: Multi-agent orchestration
- **Pros**: Parallel execution, specialized agents
- **Cons**: Complex coordination, higher costs

### Crucible's Competitive Position

**Strengths:**
1. **Structural patching** (CST/AST) > text-based diffs
2. **Graph-based retrieval** > keyword-only search
3. **Deterministic orchestration** > ad-hoc loops
4. **Preflight validation** > apply-then-fix
5. **Shadow workspace** > direct file modification

**Strategic Gaps:**
1. **Semantic retrieval** (embeddings)
2. **Streaming apply** (real-time feedback)
3. **Multi-agent parallelization**
4. **Tool extensibility** (terminal, browser, git)

---

## 5. Enhancement Opportunities

### Priority 1: Hybrid Retrieval (Phase 5)

**Goal**: Combine symbolic (graph) + semantic (embeddings) search

**Implementation**:
1. Add local embedding model (CodeBERT, StarCoder)
2. Embed code chunks during indexing
3. Hybrid scoring: `0.6 * graph_score + 0.4 * semantic_score`
4. Optional LLM reranker for top-N

**Roadmap Alignment**: Phase 5 - Retrieval v2 ([`roadmap.md:40`](docs/roadmap.md:40))

**Complexity**: Medium (requires embedding model integration)

### Priority 2: Diff-Based Patch Operation (Phase 1)

**Goal**: Support unified diff format for LLM familiarity

**Implementation**:
```python
class ApplyDiffOpV2(BaseModel):
    op: Literal["apply_diff"]
    file: str
    diff: str  # Unified diff format (@@, +/-)
    reason: str
```

**Benefits**:
- Industry-standard format
- LLMs trained on git diffs
- Handles multi-hunk edits

**Roadmap Alignment**: Phase 1 - Patch Engine v2 ([`roadmap.md:11-19`](docs/roadmap.md:11-19))

**Complexity**: Low (diff parsing library)

### Priority 3: Streaming Apply with Feedback (Phase 3)

**Goal**: Real-time patch application with incremental validation

**Implementation**:
1. Stream patch operations as they're generated
2. Apply and validate incrementally
3. Provide immediate feedback on conflicts
4. Abort on first error (fail-fast)

**Roadmap Alignment**: Phase 3 - Core Parity Surface ([`roadmap.md:27-31`](docs/roadmap.md:27-31))

**Complexity**: Medium (requires streaming API changes)

### Priority 4: Plan Graph v2 with Parallelization (Phase 2)

**Goal**: Execute independent steps in parallel

**Implementation**:
```python
class PlanStepV2(BaseModel):
    id: str
    goal: str
    targets: list[str]
    preconditions: list[str]  # Step IDs that must complete first
    postconditions: list[str]  # Verification checks
    risk: Literal["low", "med", "high"]
```

**Benefits**:
- Faster execution for multi-file changes
- Better resource utilization
- Clearer dependency tracking

**Roadmap Alignment**: Phase 2 - Planner/Executor/Critic v2 ([`roadmap.md:21-25`](docs/roadmap.md:21-25))

**Complexity**: High (requires parallel orchestration)

### Priority 5: Tool Policy & Extensibility (Phase 3)

**Goal**: Safe integration of terminal, browser, git tools

**Implementation**:
```python
class ToolPolicy(BaseModel):
    tool_name: str
    allowed: bool
    scope: list[str]  # Allowed paths/commands
    audit: bool  # Log all invocations
```

**Roadmap Alignment**: Phase 3 - MCP policy controls ([`roadmap.md:31`](docs/roadmap.md:31))

**Complexity**: Medium (requires policy engine)

---

## 6. Prioritized Recommendations

### Immediate (Phase 1 Completion) ✅ COMPLETE
1. ✅ **Keep CST/AST patching** - Don't revert to line-based (V1 failed for good reasons)
2. ✅ **Add diff-based operation** - SearchReplaceOpV2 + ApplyDiffOpV2 implemented
3. ✅ **Codex format support** - `*** Begin/End Patch` markers handled
4. ✅ **Comprehensive testing** - 12/12 tests passing with newline normalization
5. 🔄 **Complete Phase 1 exit criteria** - 70% reduction pending benchmark validation

### Near-Term (Phase 2-3)
4. **Implement hybrid retrieval** - Symbolic + semantic search
5. **Add streaming apply** - Real-time feedback like Cursor
6. **Plan graph v2** - Parallel execution with preconditions
7. **Tool policy controls** - Safe terminal/browser/git integration

### Long-Term (Phase 4-5)
8. **Multi-agent orchestration** - Parallel agents like Codex App
9. **Issue-driven workflow** - GitHub issue → plan → patch → PR
10. **Knowledge spaces** - Project-specific memory and rules

---

## 7. Conclusion

**Crucible's architectural foundation is superior to competitors.** The CST/AST patching, graph-based retrieval, and deterministic orchestration solve fundamental problems that plague text-based editors (anchor drift, syntax errors, unpredictable behavior).

**Strategic focus should be:**
1. **Complete Phase 1** - Prove CST/AST superiority with 70% failure reduction
2. **Add semantic retrieval** - Match Cursor's embedding-based search
3. **Implement streaming apply** - Match Cursor's real-time feedback
4. **Extend with diff operations** - Provide LLM-familiar format
5. **Build tool extensibility** - Enable terminal/browser/git safely

**Do NOT:**
- ❌ Revert to line-based patching (V1 failed for good reasons)
- ❌ Copy competitor's ad-hoc agentic loops (deterministic is better)
- ❌ Sacrifice safety for speed (preflight validation is a differentiator)

**The roadmap is sound.** Execute Phase 1 → Phase 2 → Phase 3 as planned, adding semantic retrieval and streaming apply as enhancements, not replacements.
