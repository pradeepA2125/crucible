# Implementation Plan: Enhanced Patch Operations & Retrieval

## Executive Summary

Based on thorough analysis of AI Editor's architecture, competitor research, and Git diff format standards, this plan outlines concrete enhancements to maintain AI Editor's architectural superiority while adding industry-standard capabilities.

**Core Principle**: Keep CST/AST patching as the primary approach, add complementary capabilities for specific use cases.

---

## Research Findings

### Git Diff Format Standard (RFC)

From official Git documentation (git-scm.com/docs/diff-format):

**Unified Diff Format Structure:**
```
diff --git a/file1 b/file2
index <hash>..<hash> <mode>
--- a/file1
+++ b/file2
@@ -start,count +start,count @@ context
 context line
-removed line
+added line
 context line
```

**Key Components:**
1. **Header**: `diff --git a/file b/file`
2. **Index line**: SHA hashes and file modes
3. **File markers**: `---` (old) and `+++` (new)
4. **Hunk header**: `@@` with line ranges
5. **Content**: ` ` (context), `-` (removed), `+` (added)

**Status Letters:**
- `A`: addition of a file
- `C`: copy of a file into a new one
- `D`: deletion of a file
- `M`: modification of contents or mode
- `R`: renaming of a file
- `T`: change in file type
- `U`: file is unmerged
- `X`: unknown change type

### Industry Standard: Why Unified Diff?

1. **LLM Training**: Models trained on GitHub/GitLab data know this format
2. **Tool Compatibility**: Works with `patch`, `git apply`, diff viewers
3. **Human Readable**: Developers understand it instantly
4. **Context Preservation**: Includes surrounding lines for accuracy
5. **Multi-hunk Support**: Single diff can modify multiple file sections

# Patch Formats & Apply Engines

Industry editors use structured, machine-readable diff formats to instruct AI on code changes. Common formats include: 

- **Unified Diff (Codex-style):** A patch block with `*** Begin Patch` header, `@@` context anchors, and `-`/`+` lines (e.g. `-old line` becomes `+new line`)【52†L178-L187】. This is used by OpenAI’s Codex CLI. It avoids line numbers and uses context lines to locate edits【52†L178-L187】. 
- **Search/Replace Blocks (Aider-style):** Fenced blocks with `<<<<<<< SEARCH … ======= … >>>>>>> REPLACE` that show only the removed text vs. the inserted text【65†L142-L150】. This is efficient because the model only outputs modified fragments. Aider and RooCode popularized this approach【65†L142-L150】. 
- **Unified-diff variants:** Simplified “udiff” or editor-ready formats place file paths and diff within code fences【65†L178-L185】. These are model-friendly adaptations of standard git diffs. 

Whichever format is chosen, the apply tool must parse it and merge into files reliably. In practice, systems include:

- **Fast Apply Engine:** For large files, do *search/replace*: scan for the exact “SEARCH” snippet and replace it. This is very fast (O(N) text find/replace) and scales to ~1000-line files【58†L1-L4】. Void’s IDE calls this **Fast Apply**【58†L1-L4】. It requires precise anchors to succeed.
- **Semantic Patch Merger:** A step that takes the unified diff or search/replace instructions and applies them. Cursor uses a separate “apply model” to merge patches【70†L116-L120】. You can implement a merging algorithm (like applying a git diff) or use existing libraries (e.g. `git apply`, `patch` command, or a custom CST merger).
- **Safe Fallback (Slow Apply):** If fast apply fails (anchors not found), rewrite the entire affected section: e.g., replace the old file region with the new content wholesale. Void’s **Slow Apply** does a full rewrite if needed【58†L1-L4】. Always run patches in a sandbox or branch to validate before committing.

---

## Phase 1: Enhanced Patch Operations (Immediate)

### 1.1 Search/Replace Operation (Aider-style)

#### (Aider-style): Fenced blocks with <<<<<<< SEARCH … ======= … >>>>>>> REPLACE that show only the removed text vs. the inserted text【65†L142-L150】. This is efficient because the model only outputs modified fragments. Aider and RooCode popularized this approach

**Model Definition** (`services/agentd-py/agentd/domain/models.py`):

```python
class SearchReplaceOpV2(BaseModel):
    """Apply search/replace patch to a file.
    
    Fast apply engine: O(N) text search and replace.
    Ideal for precise, targeted edits with exact anchors.
    """
    op: Literal["search_replace"]
    file: str
    search: str  # Exact text to find
    replace: str  # Replacement text
    reason: str
    
    @model_validator(mode="after")
    def validate_search_not_empty(self) -> "SearchReplaceOpV2":
        """Ensure search text is not empty."""
        if not self.search.strip():
            raise ValueError("search text cannot be empty")
        return self
```

### 1.2 New V2 Operation: `apply_diff`

**Model Definition** (`services/agentd-py/agentd/domain/models.py`):

```python
class ApplyDiffOpV2(BaseModel):
    """Apply a unified diff patch to a file.
    
    Supports standard unified diff format with @@ hunks.
    Ideal for multi-section edits and LLM-generated patches.
    """
    op: Literal["apply_diff"]
    file: str
    diff: str  # Unified diff content (without file headers)
    reason: str
    
    @model_validator(mode="after")
    def validate_diff_format(self) -> "ApplyDiffOpV2":
        """Ensure diff contains valid hunk headers."""
        if not re.search(r'@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@', self.diff):
            raise ValueError("diff must contain valid @@ hunk headers")
        return self
```

**Update PatchOperationV2 Union**:
```python
PatchOperationV2 = Annotated[
    Union[
        ReplaceNodeOpV2,
        InsertAfterNodeOpV2,
        SearchReplaceOpV2,  # NEW - Fast Apply
        ApplyDiffOpV2,  # NEW - Unified Diff
        CreateFileOpV2,
        DeleteFileOpV2
    ],
    Field(discriminator="op"),
]
```

### 1.3 Search/Replace Engine (Fast Apply)

**Implementation** (`services/agentd-py/agentd/patch/engine.py`):

```python
def _apply_search_replace_op(self, base_path: Path, operation: SearchReplaceOpV2) -> None:
    """Apply search/replace operation (Fast Apply).
    
    O(N) text search and replace - very fast for large files.
    Falls back to full rewrite if search text not found.
    """
    target = self._resolve_inside(base_path, operation.file)
    
    if not target.exists():
        raise RuntimeError(f"File not found for search/replace: {operation.file}")
    
    original_content = target.read_text(encoding="utf-8")
    
    # Fast Apply: exact text search
    if operation.search not in original_content:
        raise RuntimeError(
            f"Search text not found in {operation.file}. "
            f"File may have changed since patch was generated."
        )
    
    # Count occurrences
    occurrences = original_content.count(operation.search)
    if occurrences > 1:
        raise RuntimeError(
            f"Search text appears {occurrences} times in {operation.file}. "
            f"Search text must be unique for safe replacement."
        )
    
    # Apply replacement
    new_content = original_content.replace(operation.search, operation.replace, 1)
    target.write_text(new_content, encoding="utf-8")
```

### 1.4 Diff Application Engine (Semantic Patch)

**Implementation** (`services/agentd-py/agentd/patch/engine.py`):

```python
def _apply_diff_op(self, base_path: Path, operation: ApplyDiffOpV2) -> None:
    """Apply unified diff to file using patch library."""
    target = self._resolve_inside(base_path, operation.file)
    
    if not target.exists():
        raise RuntimeError(f"File not found for diff application: {operation.file}")
    
    original_content = target.read_text(encoding="utf-8")
    
    try:
        # Use unidiff library for parsing
        from unidiff import PatchSet
        
        # Construct full diff with file headers
        full_diff = f"""--- a/{operation.file}
+++ b/{operation.file}
{operation.diff}"""
        
        patch_set = PatchSet(full_diff)
        if len(patch_set) != 1:
            raise RuntimeError(f"Diff must target single file, got {len(patch_set)}")
        
        patched_file = patch_set[0]
        
        # Apply hunks sequentially
        lines = original_content.splitlines(keepends=True)
        offset = 0  # Track line number shifts from previous hunks
        
        for hunk in patched_file:
            # Validate hunk can be applied
            source_start = hunk.source_start - 1 + offset
            source_length = hunk.source_length
            
            # Extract expected context
            expected_lines = [
                line.value for line in hunk 
                if line.is_context or line.is_removed
            ]
            
            actual_lines = lines[source_start:source_start + source_length]
            
            if actual_lines != expected_lines:
                raise RuntimeError(
                    f"Hunk context mismatch at line {hunk.source_start}: "
                    f"expected {len(expected_lines)} lines, file may have changed"
                )
            
            # Apply hunk
            new_lines = [line.value for line in hunk if not line.is_removed]
            lines[source_start:source_start + source_length] = new_lines
            
            # Update offset for next hunk
            offset += len(new_lines) - source_length
        
        # Write patched content
        target.write_text(''.join(lines), encoding="utf-8")
        
    except ImportError:
        raise RuntimeError("unidiff library required for diff operations")
    except Exception as exc:
        raise RuntimeError(f"Failed to apply diff to {operation.file}: {exc}")
```

### 1.5 Fallback Strategy (Slow Apply)

**Add to patch engine** (`services/agentd-py/agentd/patch/engine.py`):

```python
def _apply_with_fallback(
    self,
    base_path: Path,
    operation: SearchReplaceOpV2 | ApplyDiffOpV2,
) -> dict:
    """Apply operation with fallback to full rewrite on failure.
    
    Fast Apply → Slow Apply fallback pattern from Void IDE.
    """
    try:
        # Try fast/semantic apply first
        if isinstance(operation, SearchReplaceOpV2):
            self._apply_search_replace_op(base_path, operation)
        else:
            self._apply_diff_op(base_path, operation)
        
        return {"method": "fast_apply", "success": True}
        
    except RuntimeError as exc:
        # Fallback: Full rewrite of affected section
        if "not found" in str(exc).lower() or "mismatch" in str(exc).lower():
            logger.warning(f"Fast apply failed for {operation.file}, using slow apply")
            
            # For search/replace: rewrite entire file with replacement
            # For diff: apply full content replacement
            target = self._resolve_inside(base_path, operation.file)
            
            # This requires the operation to include full_content as fallback
            if hasattr(operation, 'fallback_content'):
                target.write_text(operation.fallback_content, encoding="utf-8")
                return {"method": "slow_apply", "success": True}
            else:
                raise RuntimeError(
                    f"Fast apply failed and no fallback content provided: {exc}"
                )
        else:
            raise
```

### 1.6 Preflight Validation for All Operations

**Add to `preflight_patch_candidate`** (`services/agentd-py/agentd/patch/engine.py`):

```python
# Preflight for search/replace
if isinstance(operation, SearchReplaceOpV2):
    if operation.search not in current_source:
        code = PatchFailureCode.ANCHOR_MISSING
        if operation.file in mutated_files:
            code = PatchFailureCode.ORDER_CONFLICT
        issues.append(PatchPreflightIssue(
            op_index=index,
            code=code,
            file=operation.file,
            message=f"Search text not found in file"
        ))
        continue
    
    occurrences = current_source.count(operation.search)
    if occurrences > 1:
        issues.append(PatchPreflightIssue(
            op_index=index,
            code=PatchFailureCode.APPLY_ERROR,
            file=operation.file,
            message=f"Search text appears {occurrences} times (must be unique)"
        ))
        continue
    
    # Simulate replacement
    simulated_sources[operation.file] = current_source.replace(
        operation.search, operation.replace, 1
    )
    mutated_files.add(operation.file)
    continue

# Preflight for unified diff
if isinstance(operation, ApplyDiffOpV2):
    try:
        # Parse and validate diff
        from unidiff import PatchSet
        full_diff = f"--- a/{operation.file}\n+++ b/{operation.file}\n{operation.diff}"
        patch_set = PatchSet(full_diff)
        
        if len(patch_set) != 1:
            issues.append(PatchPreflightIssue(
                op_index=index,
                code=PatchFailureCode.APPLY_ERROR,
                file=operation.file,
                message=f"Diff must target single file, got {len(patch_set)} files"
            ))
            continue
        
        # Simulate application
        lines = current_source.splitlines(keepends=True)
        offset = 0
        
        for hunk in patch_set[0]:
            source_start = hunk.source_start - 1 + offset
            source_length = hunk.source_length
            
            if source_start < 0 or source_start + source_length > len(lines):
                issues.append(PatchPreflightIssue(
                    op_index=index,
                    code=PatchFailureCode.RANGE_INVALID,
                    file=operation.file,
                    message=f"Hunk @@ -{hunk.source_start},{source_length} out of range"
                ))
                break
            
            # Validate context
            expected = [l.value for l in hunk if l.is_context or l.is_removed]
            actual = lines[source_start:source_start + source_length]
            
            if actual != expected:
                code = PatchFailureCode.ANCHOR_MISSING
                if operation.file in mutated_files:
                    code = PatchFailureCode.ORDER_CONFLICT
                issues.append(PatchPreflightIssue(
                    op_index=index,
                    code=code,
                    file=operation.file,
                    message=f"Hunk context mismatch at line {hunk.source_start}"
                ))
                break
            
            # Apply to simulation
            new_lines = [l.value for l in hunk if not l.is_removed]
            lines[source_start:source_start + source_length] = new_lines
            offset += len(new_lines) - source_length
        
        if not issues or issues[-1].op_index != index:
            simulated_sources[operation.file] = ''.join(lines)
            mutated_files.add(operation.file)
            
    except Exception as exc:
        issues.append(PatchPreflightIssue(
            op_index=index,
            code=PatchFailureCode.APPLY_ERROR,
            file=operation.file,
            message=f"Diff validation failed: {exc}"
        ))
    continue
```

### 1.7 Dependencies

Add to `services/agentd-py/pyproject.toml`:
```toml
[tool.poetry.dependencies]
unidiff = "^0.7.5"  # Unified diff parsing
```

### 1.8 LLM Prompt Integration

**Update reasoning prompts** (`services/agentd-py/agentd/reasoning/prompt_builder.py`):

```python
PATCH_OPERATION_EXAMPLES = """
Operation Types (in order of preference):

1. replace_node (CST/AST) - Replace entire class/function:
{
  "op": "replace_node",
  "language": "python",
  "selector": {"kind": "symbol", "value": "process_data", "match": "exact"},
  "content": "def process_data(x):\\n    return x * 2",
  "reason": "Refactor algorithm"
}

2. insert_after_node (CST/AST) - Insert after class/function:
{
  "op": "insert_after_node",
  "language": "typescript",
  "selector": {"kind": "symbol", "value": "UserService", "match": "exact"},
  "content": "export class AdminService { }",
  "reason": "Add admin service"
}

3. search_replace (Fast Apply) - Precise text replacement:
{
  "op": "search_replace",
  "file": "src/utils.py",
  "search": "def helper():\\n    pass",
  "replace": "def helper():\\n    # TODO: implement\\n    pass",
  "reason": "Add TODO comment"
}

4. apply_diff (Unified Diff) - Multi-section edits:
{
  "op": "apply_diff",
  "file": "src/utils.py",
  "diff": "@@ -10,3 +10,4 @@\\n def helper():\\n     pass\\n+    # TODO: implement\\n",
  "reason": "Add TODO comment"
}

5. create_file - New files:
{
  "op": "create_file",
  "file": "config.json",
  "content": "{}",
  "reason": "Add configuration"
}

6. delete_file - Remove files:
{
  "op": "delete_file",
  "file": "old_module.py",
  "reason": "Remove deprecated code"
}

**When to use each:**
- replace_node/insert_after_node: Structural changes (classes, functions, methods)
- search_replace: Fast, precise edits with exact text anchors (O(N) performance)
- apply_diff: Multi-section edits, complex changes with context lines
- create_file/delete_file: File lifecycle operations

**Performance characteristics:**
- CST/AST operations: Best for structural changes, syntax-aware
- search_replace: Fastest for large files (~1000 lines), requires exact match
- apply_diff: Tolerates minor code shifts via context lines
"""
```

### 1.9 Codex-Style Patch Format Support

**Add alternative diff format parser** (`services/agentd-py/agentd/patch/engine.py`):

```python
def _parse_codex_diff(self, diff_text: str) -> str:
    """Convert Codex-style diff to unified diff format.
    
    Codex format:
    *** Begin Patch
    @@ context @@
    -old line
    +new line
    *** End Patch
    
    Converts to standard unified diff for processing.
    """
    if "*** Begin Patch" in diff_text and "*** End Patch" in diff_text:
        # Extract content between markers
        start = diff_text.index("*** Begin Patch") + len("*** Begin Patch")
        end = diff_text.index("*** End Patch")
        return diff_text[start:end].strip()
    
    return diff_text  # Already in unified format
```

---

## Phase 2: Advanced Retrieval Enhancement (Near-Term)

### 2.1 Two-Stage Retrieval Architecture

**Overview**: Implement industry-standard two-stage retrieval:
1. **Stage 1 (Fast Retrieval)**: Broad candidate gathering via ANN search + exact match
2. **Stage 2 (Reranking)**: Precise scoring with cross-encoder or LLM

**Architecture**:
```
┌─────────────────────────────────────────────────────────┐
│ Query: "implement authentication"                       │
└────────────────────┬────────────────────────────────────┘
                     │
                     v
┌─────────────────────────────────────────────────────────┐
│ Stage 1: Fast Retrieval (Broad)                        │
│ ┌─────────────────┐  ┌──────────────────┐              │
│ │ ANN Search      │  │ Exact Match      │              │
│ │ (Embeddings)    │  │ (Ripgrep/Graph)  │              │
│ │ Top 100         │  │ Symbol names     │              │
│ └────────┬────────┘  └────────┬─────────┘              │
│          └──────────┬──────────┘                        │
│                     v                                    │
│          ~100-150 candidates                            │
└─────────────────────┬───────────────────────────────────┘
                      │
                      v
┌─────────────────────────────────────────────────────────┐
│ Stage 2: Reranking (Precise)                           │
│ ┌─────────────────────────────────────┐                │
│ │ Cross-Encoder or LLM Scoring        │                │
│ │ - Relevance to query                │                │
│ │ - Code quality                      │                │
│ │ - Recency                           │                │
│ └────────────────┬────────────────────┘                │
│                  v                                       │
│          Top 20 for prompt                              │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Local Embedding Integration

**Architecture**:
```
┌─────────────────────────────────────────────────────────┐
│ Indexer-RS (Rust)                                       │
│ ┌─────────────────┐  ┌──────────────────┐              │
│ │ Tree-sitter     │  │ Embedding Model  │              │
│ │ Parser          │  │ (CodeBERT/Star   │              │
│ │                 │  │  Coder)          │              │
│ └────────┬────────┘  └────────┬─────────┘              │
│          │                    │                         │
│          v                    v                         │
│ ┌─────────────────────────────────────┐                │
│ │ Snapshot JSON                       │                │
│ │ - graph: {nodes, edges}             │                │
│ │ - embeddings: {node_id -> vector}   │                │
│ │ - diagnostics                       │                │
│ └─────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────┘
                         │
                         v
┌─────────────────────────────────────────────────────────┐
│ AgentD-PY (Python)                                      │
│ ┌─────────────────────────────────────┐                │
│ │ Hybrid Retrieval                    │                │
│ │ score = 0.6*graph + 0.4*semantic    │                │
│ └─────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────┘
```

**Implementation** (`services/indexer-rs/src/embeddings.rs`):

```rust
use rust_bert::pipelines::sentence_embeddings::{
    SentenceEmbeddingsBuilder, SentenceEmbeddingsModelType,
};

pub struct EmbeddingEngine {
    model: SentenceEmbeddingsModel,
}

impl EmbeddingEngine {
    pub fn new() -> Result<Self> {
        let model = SentenceEmbeddingsBuilder::remote(
            SentenceEmbeddingsModelType::AllMiniLmL12V2
        )
        .create_model()?;
        
        Ok(Self { model })
    }
    
    pub fn embed_code_chunk(&self, code: &str, context: &str) -> Result<Vec<f32>> {
        // Combine code with context for better embeddings
        let text = format!("{}\n\n{}", context, code);
        let embeddings = self.model.encode(&[text])?;
        Ok(embeddings[0].clone())
    }
}
```

**Update Snapshot Schema** (`services/indexer-rs/src/graph.rs`):

```rust
#[derive(Serialize, Deserialize)]
pub struct IndexSnapshot {
    pub version: String,
    pub workspace_root: PathBuf,
    pub generated_at_ms: i64,
    pub graph: CodeGraph,
    pub embeddings: HashMap<String, Vec<f32>>,  // NEW
    pub diagnostics: Vec<Diagnostic>,
    pub stats: SnapshotStats,
}
```

**Hybrid Scoring** (`services/agentd-py/agentd/retrieval/artifact_client.py`):

```python
def _hybrid_score_nodes(
    self,
    nodes: list[dict],
    goal: str,
    embeddings: dict[str, list[float]],
) -> list[tuple[float, dict]]:
    """Combine graph-based and semantic scoring."""
    
    # Graph-based scoring (existing)
    terms = {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", goal)}
    graph_scores = {}
    for node in nodes:
        node_id = str(node.get("id"))
        name = str(node.get("name", "")).lower()
        path = str(node.get("path", "")).lower()
        hit_count = sum(1 for term in terms if term in name or term in path)
        graph_scores[node_id] = hit_count + self._path_bias_score(path, goal.lower())
    
    # Semantic scoring (new)
    if embeddings:
        goal_embedding = self._embed_query(goal)
        semantic_scores = {}
        for node_id, node_embedding in embeddings.items():
            similarity = self._cosine_similarity(goal_embedding, node_embedding)
            semantic_scores[node_id] = similarity
    else:
        semantic_scores = {node_id: 0.0 for node_id in graph_scores}
    
    # Hybrid scoring
    scored_nodes = []
    for node in nodes:
        node_id = str(node.get("id"))
        graph_score = graph_scores.get(node_id, 0)
        semantic_score = semantic_scores.get(node_id, 0.0)
        
        # Weighted combination
        hybrid_score = 0.6 * graph_score + 0.4 * (semantic_score * 10)  # Scale semantic to match graph range
        
        scored_nodes.append((hybrid_score, node))
    
    return sorted(scored_nodes, key=lambda x: -x[0])
```

### 2.3 Cross-Encoder Reranker

**Implementation** (`services/agentd-py/agentd/retrieval/reranker.py`):

```python
from sentence_transformers import CrossEncoder

class CrossEncoderReranker:
    """Fast cross-encoder reranking for retrieval candidates."""
    
    def __init__(self):
        # Lightweight cross-encoder model
        self.model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    
    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 20,
    ) -> list[dict]:
        """Rerank candidates using cross-encoder scoring."""
        
        if len(candidates) <= top_k:
            return candidates
        
        # Prepare pairs for scoring
        pairs = []
        for candidate in candidates:
            # Combine code context for scoring
            code_text = f"{candidate['name']} in {candidate['path']}"
            if 'snippet' in candidate:
                code_text += f"\n{candidate['snippet']}"
            pairs.append([query, code_text])
        
        # Score all pairs
        scores = self.model.predict(pairs)
        
        # Sort by score and return top-k
        scored_candidates = list(zip(scores, candidates))
        scored_candidates.sort(key=lambda x: -x[0])
        
        return [c for _, c in scored_candidates[:top_k]]
```

### 2.4 LLM Reranker (Optional)

**Implementation** (`services/agentd-py/agentd/retrieval/reranker.py`):

```python
class RetrievalReranker:
    """Optional LLM-based reranking of retrieval candidates."""
    
    def __init__(self, transport: ModelJsonTransport):
        self._transport = transport
    
    async def rerank(
        self,
        goal: str,
        candidates: list[dict],
        top_k: int = 20,
    ) -> list[dict]:
        """Use LLM to rerank top candidates based on relevance to goal."""
        
        if len(candidates) <= top_k:
            return candidates
        
        # Format candidates for LLM
        candidate_text = "\n".join([
            f"{i+1}. {c['name']} ({c['path']}) - {c.get('kind', 'Unknown')}"
            for i, c in enumerate(candidates[:50])  # Limit to top 50 for cost
        ])
        
        prompt = f"""Given this coding task:
{goal}

Rank these code symbols by relevance (most relevant first):
{candidate_text}

Return only the numbers of the top {top_k} most relevant items, comma-separated."""
        
        response = await self._transport.generate_text(
            prompt=prompt,
            max_tokens=200,
        )
        
        # Parse rankings
        try:
            rankings = [int(x.strip()) - 1 for x in response.split(",")]
            return [candidates[i] for i in rankings if 0 <= i < len(candidates)]
        except:
            # Fallback to original order
            return candidates[:top_k]
```

### 2.5 Exact Match Integration

**Add to retrieval client** (`services/agentd-py/agentd/retrieval/artifact_client.py`):

```python
def _exact_match_search(self, query: str, graph: dict) -> list[dict]:
    """Fast exact match search using ripgrep or graph lookup.
    
    Complements semantic search with precise symbol matching.
    """
    results = []
    
    # Extract potential symbol names from query
    tokens = re.findall(r"[A-Z][a-z]+|[a-z]+", query)
    
    for node in graph.get("nodes", []):
        node_name = node.get("name", "").lower()
        
        # Exact symbol name match
        for token in tokens:
            if token.lower() == node_name:
                results.append({
                    **node,
                    "match_type": "exact_symbol",
                    "score": 100.0  # High priority
                })
                break
    
    return results

def retrieve_with_exact_match(
    self,
    goal: str,
    snapshot: dict,
    top_k: int = 20,
) -> list[dict]:
    """Combine semantic, graph, and exact match retrieval."""
    
    # Stage 1: Gather candidates
    semantic_results = self._semantic_search(goal, snapshot, top_k=100)
    graph_results = self._graph_search(goal, snapshot, top_k=50)
    exact_results = self._exact_match_search(goal, snapshot)
    
    # Merge and deduplicate
    all_candidates = self._merge_results([
        semantic_results,
        graph_results,
        exact_results
    ])
    
    # Stage 2: Rerank
    if len(all_candidates) > top_k:
        all_candidates = self.reranker.rerank(goal, all_candidates, top_k)
    
    return all_candidates[:top_k]
```

---

## Phase 3: Streaming & Real-Time Feedback (Medium-Term)

### 3.1 Streaming Patch API

**New Endpoint** (`services/agentd-py/agentd/api/routes.py`):

```python
@router.post("/v1/tasks/{task_id}/stream-patch")
async def stream_patch_application(
    task_id: str,
    patch: PatchDocumentV2,
) -> StreamingResponse:
    """Stream patch application with incremental validation."""
    
    async def generate_events():
        for idx, candidate in enumerate(patch.candidates):
            yield f"data: {json.dumps({'type': 'candidate_start', 'id': candidate.candidate_id})}\n\n"
            
            for op_idx, operation in enumerate(candidate.patch_ops):
                # Apply operation
                try:
                    result = await engine.apply_single_operation(operation)
                    yield f"data: {json.dumps({
                        'type': 'operation_success',
                        'op_index': op_idx,
                        'file': operation.file,
                        'result': result
                    })}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({
                        'type': 'operation_error',
                        'op_index': op_idx,
                        'file': operation.file,
                        'error': str(exc)
                    })}\n\n"
                    break
            
            yield f"data: {json.dumps({'type': 'candidate_complete'})}\n\n"
    
    return StreamingResponse(generate_events(), media_type="text/event-stream")
```

### 3.2 Incremental Validation

**Implementation** (`services/agentd-py/agentd/patch/engine.py`):

```python
async def apply_single_operation(
    self,
    operation: PatchOperationV2,
    base_dir: Path,
) -> dict:
    """Apply single operation with immediate validation."""
    
    # Apply operation
    if isinstance(operation, ReplaceNodeOpV2):
        self._apply_replace_node(base_dir, operation)
    elif isinstance(operation, ApplyDiffOpV2):
        self._apply_diff_op(base_dir, operation)
    # ... other operations
    
    # Immediate validation
    target = self._resolve_inside(base_dir, operation.file)
    
    # Syntax check
    if target.suffix == ".py":
        try:
            compile(target.read_text(), str(target), "exec")
            syntax_valid = True
        except SyntaxError as exc:
            syntax_valid = False
            syntax_error = str(exc)
    else:
        syntax_valid = True
        syntax_error = None
    
    return {
        "file": operation.file,
        "syntax_valid": syntax_valid,
        "syntax_error": syntax_error,
        "size_bytes": target.stat().st_size,
    }
```

### 3.3 Post-Apply Validation

**Add validation hooks** (`services/agentd-py/agentd/patch/engine.py`):

```python
async def validate_after_apply(
    self,
    operation: PatchOperationV2,
    base_dir: Path,
) -> dict:
    """Run validation checks after applying operation."""
    
    target = self._resolve_inside(base_dir, operation.file)
    
    validation_results = {
        "file": operation.file,
        "syntax_valid": True,
        "linter_issues": [],
        "test_results": None,
    }
    
    # Syntax validation
    if target.suffix == ".py":
        try:
            compile(target.read_text(), str(target), "exec")
        except SyntaxError as exc:
            validation_results["syntax_valid"] = False
            validation_results["syntax_error"] = {
                "line": exc.lineno,
                "message": exc.msg,
            }
    
    # Optional: Run linter
    if self.config.get("run_linter"):
        linter_output = await self._run_linter(target)
        validation_results["linter_issues"] = linter_output
    
    # Optional: Run tests in sandbox
    if self.config.get("run_tests"):
        test_output = await self._run_tests_in_sandbox(base_dir)
        validation_results["test_results"] = test_output
    
    return validation_results
```

---

## Phase 4: API Integration & Tool Extensibility (Long-Term)

### 4.0 Internal API Design

**New API endpoints** (`services/agentd-py/agentd/api/routes.py`):

```python
@router.post("/v1/index")
async def index_repository(request: IndexRequest) -> IndexResponse:
    """Ingest files or repos, update semantic and graph indices."""
    # Input: file path or repo URL
    # Output: status, indexed vector count
    pass

@router.post("/v1/search")
async def search_code(request: SearchRequest) -> SearchResponse:
    """Accept a query, return ranked code snippets."""
    # Payload: natural language query + optional file context
    # Returns: list of (file, range, snippet text, score)
    pass

@router.post("/v1/apply")
async def apply_patch(request: ApplyRequest) -> ApplyResponse:
    """Apply a patch or search/replace to a file."""
    # Payload: format tag (e.g. 'udiff' or 'search-replace'), patch content
    # Returns: success/failure, updated file diff
    pass

@router.post("/v1/ast-query")
async def query_ast(request: ASTQueryRequest) -> ASTQueryResponse:
    """Ask for AST nodes matching a pattern."""
    # Useful for path mapping or verifying anchors
    pass

@router.post("/v1/validate")
async def validate_changes(request: ValidateRequest) -> ValidateResponse:
    """Run tests/build in sandbox."""
    # Returns: pass/fail and error logs
    pass
```

### 4.1 Tool Policy Framework

**Model** (`services/agentd-py/agentd/domain/models.py`):

```python
class ToolPolicy(BaseModel):
    """Policy for tool usage."""
    tool_name: str
    allowed: bool = True
    scope: list[str] = Field(default_factory=list)  # Allowed paths/commands
    audit: bool = True  # Log all invocations
    require_approval: bool = False  # User approval required
    
class ToolInvocation(BaseModel):
    """Record of tool usage."""
    tool_name: str
    args: dict[str, Any]
    timestamp: datetime
    result: dict[str, Any] | None = None
    error: str | None = None
```

### 4.2 Terminal Tool

**Implementation** (`services/agentd-py/agentd/tools/terminal.py`):

```python
class TerminalTool:
    """Safe terminal command execution."""
    
    def __init__(self, policy: ToolPolicy):
        self._policy = policy
        self._audit_log: list[ToolInvocation] = []
    
    async def execute(
        self,
        command: str,
        cwd: str,
        timeout: int = 30,
    ) -> dict:
        """Execute command with policy enforcement."""
        
        # Policy check
        if not self._policy.allowed:
            raise RuntimeError(f"Tool {self._policy.tool_name} is not allowed")
        
        # Scope validation
        if self._policy.scope:
            if not any(command.startswith(allowed) for allowed in self._policy.scope):
                raise RuntimeError(f"Command not in allowed scope: {command}")
        
        # Audit
        invocation = ToolInvocation(
            tool_name=self._policy.tool_name,
            args={"command": command, "cwd": cwd},
            timestamp=datetime.now(timezone.utc),
        )
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            
            invocation.result = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
            
            return invocation.result
            
        except Exception as exc:
            invocation.error = str(exc)
            raise
        finally:
            if self._policy.audit:
                self._audit_log.append(invocation)
```

---

## Implementation Timeline

### Immediate (Weeks 1-2): Enhanced Patch Operations
- [ ] Add `SearchReplaceOpV2` model (Fast Apply)
- [ ] Add `ApplyDiffOpV2` model (Unified Diff)
- [ ] Implement search/replace engine with fallback
- [ ] Implement diff application engine with `unidiff`
- [ ] Add Codex-style diff format parser
- [ ] Add preflight validation for all new operations
- [ ] Update LLM prompts with operation examples and performance guidance
- [ ] Write unit tests for all patch operations

### Near-Term (Weeks 3-6): Two-Stage Retrieval
- [ ] Design two-stage retrieval architecture
- [ ] Integrate local embedding model in indexer-rs (Stage 1)
- [ ] Add exact match search with ripgrep integration (Stage 1)
- [ ] Update snapshot schema with embeddings
- [ ] Implement cross-encoder reranker (Stage 2)
- [ ] Add optional LLM reranker for complex queries (Stage 2)
- [ ] Implement candidate merging and deduplication
- [ ] Benchmark retrieval quality improvements (precision@k, recall@k)

### Medium-Term (Weeks 7-12): Streaming & Validation
- [ ] Design streaming patch API with SSE
- [ ] Implement incremental validation (syntax, linting)
- [ ] Add post-apply validation hooks
- [ ] Add SSE endpoint for real-time feedback
- [ ] Implement sandbox testing integration
- [ ] Update VS Code extension for streaming UI
- [ ] Add approval flow for multi-file changes

### Long-Term (Weeks 13-24): API & Tool Extensibility
- [ ] Design internal API endpoints (/index, /search, /apply, /ast-query, /validate)
- [ ] Implement API request/response models
- [ ] Design tool policy framework
- [ ] Implement terminal tool with sandboxing
- [ ] Add browser tool integration
- [ ] Implement git tool with policy controls
- [ ] Build tool audit dashboard
- [ ] Add CI/CD integration for automated testing

---

## Success Metrics

### Phase 1 (Enhanced Patch Operations)
- ✅ 95%+ of search/replace operations succeed (Fast Apply)
- ✅ 90%+ of LLM-generated diffs apply successfully
- ✅ Preflight catches 95%+ of invalid operations
- ✅ Performance: <50ms for search/replace, <100ms for diff application
- ✅ Fallback success rate: 80%+ when fast apply fails

### Phase 2 (Two-Stage Retrieval)
- ✅ 40%+ improvement in retrieval relevance (measured by precision@20)
- ✅ Stage 1 recall: 90%+ of relevant code in top 100 candidates
- ✅ Stage 2 precision: 80%+ of top 20 results are relevant
- ✅ Semantic search finds conceptually related code (e.g., "auth" → `verify_token`)
- ✅ Exact match integration: 100% recall for symbol name queries
- ✅ Indexing time increase <20%
- ✅ Query latency: <500ms for two-stage retrieval

### Phase 3 (Streaming & Validation)
- ✅ Real-time feedback within 500ms of operation completion
- ✅ Fail-fast: Stop on first error within 1 second
- ✅ UI responsiveness: No blocking during patch application
- ✅ Validation coverage: 100% syntax checking, 80% linter integration
- ✅ Sandbox test execution: <30 seconds for typical test suite

### Phase 4 (API & Tools)
- ✅ API response time: <100ms for /search, <200ms for /apply
- ✅ API uptime: 99.9%
- ✅ Zero security incidents from tool usage
- ✅ 100% audit coverage for sensitive operations
- ✅ User approval flow <5 seconds latency
- ✅ CI/CD integration: Automated testing on every patch

---

## Risk Mitigation

### Risk 1: Search/Replace Ambiguity
**Mitigation**: Require unique search text, preflight validation, fallback to slow apply

### Risk 2: Diff Format Complexity
**Mitigation**: Use battle-tested `unidiff` library, support multiple formats (Git, Codex), extensive preflight validation

### Risk 3: Embedding Model Size
**Mitigation**: Use lightweight model (all-MiniLM-L12-v2, 120MB), optional feature flag, lazy loading

### Risk 4: Reranking Latency
**Mitigation**: Use fast cross-encoder (6-layer MiniLM), cache results, optional LLM reranking only for complex queries

### Risk 5: Streaming Reliability
**Mitigation**: Fallback to synchronous mode, comprehensive error handling, connection retry logic

### Risk 6: Tool Security
**Mitigation**: Strict policy enforcement, sandboxing, audit logging, user approval for high-risk operations

### Risk 7: API Performance
**Mitigation**: Rate limiting, caching, async processing, horizontal scaling

---

## Migration Checklist

Based on industry research, the following steps ensure smooth adoption:

- [x] **AST library integration**: Already using `tree-sitter` and `libcst` ✅
- [ ] **Search/Replace parser**: Implement Aider-style format with Fast Apply engine
- [ ] **Unified diff parser**: Support Git and Codex-style formats
- [ ] **Fallback strategy**: Implement Fast Apply → Slow Apply pattern
- [ ] **Two-stage retrieval**: Add ANN search + exact match (Stage 1), then reranking (Stage 2)
- [ ] **Cross-encoder reranking**: Integrate lightweight model for precise scoring
- [ ] **Exact match integration**: Add ripgrep or graph-based symbol lookup
- [ ] **Sandbox validation**: Ensure all patches tested in shadow workspace
- [ ] **API endpoints**: Design /index, /search, /apply, /ast-query, /validate
- [ ] **Prompt templates**: Update LLM prompts with format examples and performance guidance
- [ ] **CI/CD hooks**: Integrate automated testing post-apply

---

## Conclusion

This enhanced implementation plan maintains AI Editor's architectural advantages (CST/AST patching, deterministic orchestration, shadow workspace) while adding industry-standard capabilities based on thorough competitive research:

**Key Additions from Extra Research:**
1. **Search/Replace (Fast Apply)**: O(N) text replacement for large files, inspired by Void IDE
2. **Codex-style diff format**: Support OpenAI's patch format with `*** Begin/End Patch` markers
3. **Two-stage retrieval**: Industry-standard architecture with broad gathering + precise reranking
4. **Cross-encoder reranking**: Fast, accurate scoring without full LLM calls
5. **Exact match integration**: Complement semantic search with symbol name lookup
6. **Fallback strategy**: Fast Apply → Slow Apply pattern for robustness
7. **Internal API design**: Modular endpoints for indexing, search, apply, validation
8. **Post-apply validation**: Syntax checking, linting, sandbox testing

**Key Principles:**
1. **Additive, not replacement**: CST/AST remains primary, new formats are complementary
2. **Standards-based**: Support Git diff, Codex format, Aider search/replace
3. **Performance-aware**: Fast Apply for large files, semantic patching for complex changes
4. **Safety-first**: Preflight validation, fallback strategies, sandbox testing, audit logging
5. **Industry-proven**: Two-stage retrieval, cross-encoder reranking, exact match integration
6. **Incremental rollout**: Phase-by-phase implementation with clear success metrics

**Next Steps:**
1. Review and approve Phase 1 implementation (search/replace + unified diff)
2. Allocate engineering resources (2-3 developers for parallel workstreams)
3. Set up benchmark suite for measuring improvements (precision@k, recall@k, apply success rate)
4. Begin implementation with Fast Apply (highest ROI, lowest risk, proven by Void IDE)
5. Follow with two-stage retrieval (addresses current retrieval limitations)