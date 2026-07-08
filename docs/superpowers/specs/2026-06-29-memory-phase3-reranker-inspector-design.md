# Memory Harness Phase 3 (v1) — Reranker + Inspector Design

**Status:** Design approved (brainstorm complete) · **Date:** 2026-06-29 · **Owner:** pradeep
**Scope:** Phase-3 items **3.1 (cross-encoder reranker)** + **3.3 (memory-inspection panel)**, designed and verified together. **3.2 (global-prefs UI) is deferred** — Phase 2 does not write `global`-scope memories yet.
**Predecessors:** Phase 1 (compaction) + Phase 2 (recall + write path) shipped on `feat/memory-harness`. Phase-3 notes: "Phase 3 — Deferred Polish" in `docs/superpowers/specs/2026-06-27-memory-harness-design.md`.

## Why these two together

The reranker *improves* recall precision; the inspector lets you *observe and verify* that improvement (and recall generally). They share the recall-score surface: the inspector renders the per-signal breakdown that the reranker reorders. Built together, the inspector shows pre/post-rerank ranking so the reranker's effect is measurable — and it is exactly the observability that would have made the two Phase-2 recall bugs (FTS5 MATCH; empty query source) obvious in seconds instead of a multi-hour debug.

## Decisions locked in this brainstorm

| Axis | Decision |
|---|---|
| Phase-3 v1 scope | 3.1 reranker + 3.3 inspector (trace + browser, **read-only**); 3.2 deferred |
| Reranker model | local `sentence-transformers` **CrossEncoder** (`BAAI/bge-reranker-base`); no new dependency |
| Reranker trigger | **count-gated on auto-recall** — rerank only when `candidates > MIN_CANDIDATES` (default 8) |
| Reranker resilience | lazy load, **degrade-not-raise** (model absent → fused order), daemon-warmed at build |
| `recall()` compatibility | **unchanged signature** (`-> list[Memory]`); new `recall_with_trace` does the work; only the harness's `_fill_recall` switches |
| Trace persistence | per-turn `memory-recall-NN.json` co-located with `controller-turn-NN.json`, written by the controller loop |
| Inspector scope | recall **trace** + memory **browser** (filter + supersede chains); no curation in v1 |
| Inspector surface | dedicated **`MemoryPanel`** webview (review-panel pattern), two tabs, layout A; read-only |

---

## 1. Reranker (backend)

**New unit `agentd/memory/reranker.py`** — mirrors `Embedder`'s shape:
- `Reranker(model_name="BAAI/bge-reranker-base", *, scorer=None)` wrapping a lazy `sentence_transformers.CrossEncoder`. `scorer` is an injectable `Callable[[list[tuple[str,str]]], list[float]]` for tests (bypasses the model).
- `rerank(query: str, candidates: list[Memory]) -> list[tuple[Memory, float]]` — scores each `(query, memory.content)` pair jointly, returns candidates reordered by descending cross-encoder score. **Degrade-not-raise:** any failure → returns the input order paired with `0.0` and flips `available=False`.
- `available: bool`, `warmup()` — same pattern as `Embedder`; warmed in the build daemon thread.

**Integration in `RecallEngine`** (the `top-k → final-k` seam): after `_fuse` produces the scored candidate list and the **floor is applied on the fused score**, if `self._reranker is not None and self._reranker.available and len(passing) > min_candidates`, rerank the floor-passing candidates and take that order; else keep fused order. Result capped at `k`. The reranker **reorders but never resurrects** floor-rejected memories.

**Flags / config:** `CRUCIBLE_MEMORY_RERANKER` (default **off**, independent of `MEMORY_ENABLED`), `CRUCIBLE_MEMORY_RERANKER_MODEL` (default `BAAI/bge-reranker-base`), `CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES` (default 8). `RecallEngine.__init__` gains `reranker: Reranker | None = None, rerank_min_candidates: int = 8`; `build_memory_harness` constructs a `Reranker` only when the flag is on.

---

## 2. Recall trace + persistence

**`RecallEngine.recall_with_trace(query, scope_kind, scope_id, k) -> tuple[list[Memory], RecallTrace]`** — the signals are already computed in fusion, so the trace is near-free. **`recall(...)` is unchanged**: `return (await self.recall_with_trace(...))[0]`. `recall_grounded` and the `recall()` tool keep calling `recall`; only the harness `_fill_recall` calls `recall_with_trace` to capture the trace. **Wiring note (replacing a method):** the test `_SpyRecall` fakes must gain a `recall_with_trace` (the plan updates them in lockstep — same class of breakage as the `prepare_turn(query=…)` change).

**Models (`models.py`):**
- `RecallTraceEntry`: `memory_id`, `kind`, `content` (capped ~160 ch), `importance`, `signals: dict[str,float]` (the **normalized** [0,1] semantic/lexical/structural/importance/recency that feed the score), `fused_score: float`, `rerank_score: float | None`, `final_rank: int`, `injected: bool`.
- `RecallTrace`: `query`, `scope_kind`, `scope_id`, `k`, `floor: float`, `reranked: bool`, `entries: list[RecallTraceEntry]`.

**The trace `entries` cover *all* scored candidates, not just the returned `k`** — including below-floor ones (`injected=false`), so the inspector can show "✗ below floor" rows (and a "0 candidates" trace exposes the empty-query/FTS5 failure class directly). The returned `memories` are the injected subset; the trace is the full picture.

**Persistence:** `TurnPreparation` gains `recall_trace: RecallTrace | None` (filled by `_fill_recall`). The **controller loop** writes it to `<workspace>/.crucible/state/artifacts/chat/<thread>/<turn>/memory-recall-NN.json` (same turn dir as `controller-turn-NN.json` — the loop owns thread_id/turn_id/workspace). Best-effort: a write failure never breaks the turn. The task loop (dormant) skips persistence. NN = the recall count within the turn (recall runs once per turn, cached, so typically `00`).

---

## 3. Routes + contracts

**Backend (`api/routes.py`, registered when memory is enabled; read-only GETs):**
- `GET /v1/memory/inspect?thread_id=<id>` → latest `RecallTrace` for the thread (newest `memory-recall-*.json` across its turn dirs); soft-empty when none.
- `GET /v1/memory?scope_kind=&scope_id=&kind=&include_retired=` → filtered `MemoryView` list (live-only unless `include_retired=true`).
- `GET /v1/memory/{id}/chain` → the supersede chain (walk `superseded_by`).

**`MemoryStore` read-only browse helpers:** `list_memories(scope_kind, scope_id, kind=None, include_retired=False) -> list[Memory]`; `get_supersede_chain(memory_id) -> list[Memory]` (ordered oldest→newest via `superseded_by`).

**editor-client (`task-contracts.ts`):** Zod `RecallTrace` + `RecallTraceEntry` + `MemoryView` (snake↔camel mapping) and `BackendTaskClient` methods `getMemoryInspect(threadId)`, `listMemories(filter)`, `getSupersedeChain(id)`.

---

## 4. Webview panel (extension)

Dedicated **`memory-panel.ts`** webview (mirrors `review-panel.ts`; *not* folded into the chat view), opened by command **`crucible.openMemoryPanel`** (registered in `extension.ts`, gated by an `crucible.memoryEnabled` `when`-context fed from `GET /v1/config`, mirroring `taskSubsystemEnabled`). `controller.ts` gains read-only fetchers wrapping the three client methods.

- **Recall trace tab (layout A):** turn-summary line (query · scope · candidate count · `reranked ✓/✗` · floor · k); per recalled memory a row with **five labeled signal bars** (semantic / lexical / structural / importance / recency — full words), `fused`, `rerank` with an **▲/▼ rank-change arrow**, and an **injected / below-floor** badge (below-floor rows greyed).
- **Browser tab (layout A):** filter bar (scope ▾ / kind ▾ / ☑ include retired) + live·retired count; list (kind badge · importance · snippet; retired greyed/struck) → detail pane (full content, entity chips, metadata incl. A+link seq span, **supersede-chain timeline**).
- **Refresh** button re-fetches (recall runs every turn → refresh shows the latest); refresh-on-focus; **no live polling** (v1). Read-only — no mutations.

---

## 5. Error handling

| Failure | Behavior |
|---|---|
| Reranker model / `sentence-transformers` missing, or rerank throws | recall falls back to **fused order**; trace records `reranked=false`; `available=False` |
| Reranker disabled (flag off) | recall is pure-fused; no model constructed |
| Trace-artifact write fails | swallowed; turn unaffected |
| `inspect` with no trace yet | soft-empty payload; panel shows "no recall recorded" |
| `browse`/`chain` with no data | empty list |

All three routes are read-only — they cannot affect a running turn.

## 6. Testing

- **`Reranker`** — injected fake scorer asserts reorder; **count-gate** (≤`min` candidates → scorer never called); degrade path (scorer raises → input order, `available=False`).
- **`recall_with_trace`** — trace entries carry all five normalized signals + `fused` + `rerank` + `final_rank` + `injected`; reranked vs not; **`recall()` unchanged** returns the same memories. `_SpyRecall` fakes gain `recall_with_trace`.
- **`MemoryStore`** — `list_memories` (scope/kind/`include_retired` filters) + `get_supersede_chain` (ordered walk, single + chained).
- **Routes** — `inspect` (latest trace / soft-empty), `browse` (filtered), `chain` via FastAPI test client.
- **editor-client** — Zod round-trip + snake↔camel mapping (vitest).
- **extension** — `MemoryPanel` renders trace (bars + rerank arrows + badges) and browser (list/detail + supersede chain) from stubbed data; command gated by `memoryEnabled`.
- **Integration** — a recall writes a `memory-recall-*.json`; the inspect route serves it back as a `RecallTrace`.

## 7. Config (new env vars)

```
CRUCIBLE_MEMORY_RERANKER               # default off — enable the cross-encoder reranker
CRUCIBLE_MEMORY_RERANKER_MODEL         # default BAAI/bge-reranker-base
CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES  # default 8 — only rerank when more candidates than this
```

## 8. Open questions (carried into the plan, not blockers)

- **Reranker model choice** — `BAAI/bge-reranker-base` vs a smaller MiniLM cross-encoder; confirm latency on the count-gated path is acceptable (measure, don't assume).
- **Trace `NN` index** — recall runs once per turn (cached), so it's effectively `00`; keep the NN scheme for forward-compat if recall ever runs multiple times per turn.
- **Browser pagination** — not needed at current memory volumes; revisit if a workspace accumulates thousands.
