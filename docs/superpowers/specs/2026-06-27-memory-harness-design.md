# Memory Harness — Sturdy Agent Memory Across Context Windows

**Status:** Design approved · **Date:** 2026-06-27 · **Owner:** pradeep

## Summary

A flag-gated `agentd/memory/` subsystem that keeps the agent coherent when its work
outgrows a single context window — both *within one run* (compaction) and *across
sessions* (recall). It is the "agent-memory module" the controller loop's CLAUDE.md note
defers to ("the real within-turn limit is the context window until the agent-memory module
lands").

The harness is **tier-agnostic retrieval + within-run compaction**. Its single job: answer
*"what, from everything outside the live window, is relevant to what I'm doing right now?"* —
where "everything" spans this session's evicted history (L2) and durable cross-session
memory (L3).

## Goals / Non-goals

**Goals**
- A long `ControllerLoop` / task `ToolLoop` run no longer degrades when it crosses ~50–65%
  of the window — it compacts before degradation, losslessly on disk.
- Cross-session recall: close a window, return later, and relevant prior facts / decisions /
  edits are retrieved into context by relevance.
- Sturdiness against the three known failure modes: **staleness**, **change-as-evolution
  (not replacement)**, and **cross-session identity/scoping**.
- The three content types are first-class: **semantic**, **episodic**, **procedural**.

**Non-goals (v1)**
- No new *memory* knowledge graph. We reuse the existing Rust **code-structure** graph as a
  grounding target (different beast — see §6). Merging the two is deferred.
- No external vector service. In-process SQLite only.
- No cross-encoder reranker, no cross-workspace global-prefs UI, no extension memory-inspection
  panel (Phase 3, out of scope here).

## Decisions (locked during brainstorming)

| Axis | Decision |
|---|---|
| Phasing | Compaction first, recall second |
| Target loops | `ControllerLoop` + task `ToolLoop` (within-run window overflow) |
| Substrate | SQLite + `sqlite-vec` (embeddings) + FTS5 (BM25/keyword) — in-process, no new service |
| Write path | **Hybrid** — background consolidation (default) + agent tools (deliberate) |
| Integration shape | **Middleware + tools** (automatic compaction/recall in middleware; deliberate write/recall as tools) |
| L4 | Reuse code graph as grounding target via `query_graph`; no new memory graph |
| Content × lifecycle | episodic/semantic/procedural `kind` × temporal `valid_to`/`superseded_by` lifecycle |

## §1 — Component architecture & boundaries

New subpackage `services/agentd-py/agentd/memory/`:

```
harness.py        # MemoryHarness — façade the loops call; orchestrates the units below.
compactor.py      # Compactor — within-run window mgmt (Phase 1): hot/warm/cold + anchored summary.
recall.py         # RecallEngine — multi-signal retrieval + scoring (Phase 2).
consolidator.py   # Consolidator — async LLM write path: extract → dedupe → supersede.
store.py          # MemoryStore — SQLite + sqlite-vec + FTS5. The ONLY DB-aware unit.
models.py         # Memory, MemoryKind, Scope, CompactionSegment, RecallResult (Pydantic).
```

**Integration (shape C):**
- `MemoryHarness` injected into `ControllerLoop` and `ToolLoop` (constructor param, mirrors the
  existing registry injection and how `retrieval_context` already flows).
- **Automatic path (middleware):** each iteration the loop calls
  `harness.prepare_turn(history, scope)` → `(maybe_compacted_history, recalled_memories_slot)`.
  The loop drops `recalled_memories` into the **dynamic tail** of the payload (KV-cache-safe).
- **Deliberate path (tools):** `remember(content, kind, entities?)` and `recall(query)` registered
  in both tool registries, gated into per-state allowed-tools sets (same mechanism as `query_graph`).
- **Async writes:** `Consolidator` runs off the hot path via fire-and-forget `asyncio.create_task`
  at compaction events and at turn/task terminal — same best-effort pattern as
  `_finalize_task_narrative`.

**Interface contract (keeps units decoupled):**
- `MemoryStore` is the only DB-aware unit; everyone else speaks `Memory` / `RecallResult` objects.
- `RecallEngine` and `Consolidator` depend on `MemoryStore` + an embedder, nothing else.
- `Compactor` depends on the reasoning engine (anchored summaries) + `MemoryStore` (offload). It
  does **not** depend on `RecallEngine`.
- `MemoryHarness` is the only unit the loops see.

## §2 — Data model

`MemoryStore` owns three tables in a new DB (`AI_EDITOR_MEMORY_DB_PATH`, default
`.agentd/memory.sqlite3` — separate file, same pattern as chat DB).

**`memories`** — L3 long-term (and durable L2):
```
id            TEXT PK
scope_kind    TEXT   -- 'workspace' | 'thread' | 'global'        ← concern #3 (scoping)
scope_id      TEXT   -- workspace path / thread_id / user id
kind          TEXT   -- 'episodic' | 'semantic' | 'procedural'    ← content types
content       TEXT   -- distilled fact / event / skill
entities      JSON   -- ['src/tax.py', 'src/tax.py:compute_vat']  grounding hooks → code graph
valid_from    TEXT   -- when this became true
valid_to      TEXT   -- NULL = currently true; set = retired       ← concern #1 (staleness)
superseded_by TEXT   -- id of the memory that replaced it           ← concern #2 (evolution)
source_kind   TEXT   -- 'consolidation' | 'agent_tool'
source_ref    TEXT   -- thread_id / task_id that produced it
created_at    TEXT
embedding            -- sqlite-vec virtual column
```
Plus FTS5 mirror `memories_fts` on `content` + `entities` (exact symbol/path match embeddings blur).

**`compaction_segments`** — Phase-1 offload (evicted raw history, recallable):
```
id, run_id (thread_id|task_id), seq, tier ('warm'|'cold'), content, embedding, created_at
```

**`anchored_summaries`** — persistent running summary per run (merged, never regenerated):
```
run_id PK, summary_md, version, updated_at
```

**Runtime resolution of the three concerns:**
1. **Staleness** — retrieval default filters `valid_to IS NULL`; scoring applies `recency_decay`
   so even live-but-old facts sink. Consolidator may proactively set `valid_to` on contradiction.
2. **Change-as-evolution** — when the consolidator writes a fact contradicting an existing one, it
   sets old `valid_to=now` + `superseded_by=new.id` in one transaction. History preserved
   (auditable); only the current fact retrieves by default. **Episodic memories are exempt** —
   immutable, never superseded, only accumulated.
3. **Scoping** — retrieval filters `(scope_kind='workspace' AND scope_id=<cwd>)` ∪
   `scope_kind='global'`; thread-scoped memories join when recalling within the same thread.
   Adapted Mem0 four-scope model minus `app_id` (one app).

## §3 — Write path (hybrid)

**Deliberate (agent tools)** — synchronous:
- `remember(content, kind, entities?)` → `Consolidator.write_explicit(...)`: embed, run
  dedupe+supersede, insert, return id. High-trust → no distillation; stored as authored.

**Background (consolidation)** — async workhorse, triggered at (1) each compaction event (distill
the evicted slice) and (2) turn/task terminal (distill the whole run).

Consolidation = one structured LLM call (`ScriptedReasoningEngine`-compatible):
```
input:  evicted_segment | full run  +  the run's existing memories (dedup context)
output: list[CandidateMemory{kind, content, entities, contradicts?: memory_id}]
```
Then a **deterministic** post-process (no LLM — the high-value test surface):
1. **Embed** each candidate.
2. **Dedupe** — cosine ≥ `MEMORY_DEDUP_THRESHOLD` (default 0.92) vs an existing live memory of
   same kind+scope → drop (or merge entities).
3. **Supersede** — candidate `contradicts` set, OR same-entity semantic conflict flagged → txn:
   old `valid_to=now`, `superseded_by=new.id`, insert new. **Episodic never supersedes** — always insert.
4. **Insert** survivors.

Rationale: LLM *proposes* (distill + spot contradiction — what it's good at); Python *disposes*
(consistent dedup math + irreversible DB mutation — deterministic, unit-testable). Consolidation
is best-effort; a failure writes nothing that round and never fails the turn.

## §4 — Retrieval & scoring (multi-signal)

`RecallEngine.recall(query, scope, k)` — three parallel passes fused:
```
semantic   = sqlite-vec ANN over embeddings              → cosine [0,1]
lexical    = FTS5 BM25 over content+entities             → normalized [0,1]
structural = entity overlap (query symbols/paths ∩ memory.entities) → [0,1]

score = w_sem*semantic + w_lex*lexical + w_struct*structural
        + recency_decay(valid_from)     # configurable half-life
        + scope_boost                    # thread > workspace > global
        − staleness_penalty              # valid_to set but within grace window
```
Defaults `w_sem=0.5, w_lex=0.3, w_struct=0.2`, all env-tunable (measure, don't hardcode).
- **Filter before score:** `valid_to IS NULL` (unless explicitly recalling history) + scope filter.
- **Rerank:** top-3k by fused score → final-k. v1 rerank = fused score (no cross-encoder); seam left.
- **Query source:** automatic path → current user message + active goal/active-todo; tool path →
  the agent's explicit `recall(query)` string.
- **Budget:** ≤ `MEMORY_RECALL_TOKEN_BUDGET` (default ~1500 tokens) injected into the dynamic tail.
  Hard cap — memory never crowds out the task.

## §5 — Compaction (Phase 1, ships first)

`Compactor.maybe_compact(history, run_id)` — called every iteration, acts only when over threshold:
- **Trigger:** est. tokens ≥ `MEMORY_COMPACT_TRIGGER_FRAC` × window (default **0.65** — compact
  before degradation, per the 60–70% finding, not at the hard limit).
- **Tiering:**
  - **Hot** (last N turns, default 10): verbatim, untouched.
  - **Warm** (next band): merged into the **anchored summary** —
    `summarize(old_anchor + warm_band) → new_anchor`, **never regenerate from scratch** (anchoring
    beats reconstruction on continuity — Factory 36K-message finding).
  - **Cold:** dropped from context, but raw text already in `compaction_segments` → recallable.
    Lossy in-window, lossless on-disk.
- **In-window after compaction:** system block (cached head) + anchored summary + hot turns +
  recalled-memories tail. Bounded and stable.
- **Fallback:** summarize failure → hard-truncate warm band + `⚠️ memory degraded` breadcrumb;
  loop never dies.

## §6 — Code-graph grounding (L4 reuse)

Memories carry `entities` (paths, `path:Symbol`). After retrieval, for the top 1–2 memories only,
an optional expansion calls the existing `GraphWalker.query_graph(node=entity)` for one structural
hop (callers/callees/imports) — grounding a recalled memory in the code as it exists *now*, and
passively catching staleness (symbol gone ⇒ memory suspect).
- Gated exactly like `query_graph` today: only when `index-snapshot.json` exists; else skipped.
- Cost-bounded behind `MEMORY_GRAPH_GROUNDING` (default on). No new graph maintained — pure read
  against the Rust snapshot.

## §7 — Error handling (best-effort everywhere)

Mirrors `retrieval_context` ("never blocks orchestration") and `_finalize_task_narrative` (try/except).

| Failure | Behavior |
|---|---|
| Embedder unavailable | Degrade to FTS5-only; log once. Store `embedding=NULL`, backfill later. |
| Retrieval throws | Empty `recalled_memories` slot; loop proceeds. |
| Consolidation throws | Nothing written that round; log; turn unaffected. |
| Compaction summarize throws | Hard-truncate warm band + `⚠️` breadcrumb; continue. |
| `sqlite-vec` missing | Boot FTS5-only mode + startup WARNING (like `warn_if_incoherent_flags`). |
| Master kill switch | `AI_EDITOR_MEMORY_ENABLED=0` → `MemoryHarness` is a no-op pass-through; loops behave as today. |

The kill switch lets us land dark and enable per-workspace (flag-gating pattern:
`CHAT_CONTROLLER`, `TASK_SUBSYSTEM`).

## §8 — Testing

- **`MemoryStore`** — migrations, vec + FTS5 round-trips, scope filtering, supersede txn
  (old `valid_to`/`superseded_by` atomic), episodic-never-superseded invariant. Real `tmp_path`
  SQLite, no mocks.
- **`Consolidator`** — `ScriptedReasoningEngine` canned candidates; assert dedupe-by-threshold,
  supersede-on-contradiction, episodic-insert-always.
- **`RecallEngine`** — domain golden set `(query → expected memory id, ranked)` over symbol/path
  queries (benchmark scores don't transfer); assert weight tuning moves ranks.
- **`Compactor`** — scripted run crossing 0.65; assert hot verbatim, anchor **merges** (old anchor
  content survives — not regenerated), cold lands in `compaction_segments` and is recallable.
- **Integration** — one long scripted `ControllerLoop` run that (a) crosses compaction, (b) writes
  memories, (c) a *second* run in the same workspace recalls them. The "sturdy between windows"
  acceptance test.
- **KV-cache guard** — byte-position assertion that `recalled_memories` lands in the dynamic tail,
  never the cached head (finding #13: unit byte-identity tests miss turn-over-turn prefix breaks).

## §9 — Phase plan

- **Phase 1 — Compaction (ships standalone).** `MemoryStore` (segments + anchored_summaries only),
  `Compactor`, wire into `ControllerLoop` + `ToolLoop`, kill switch. Can ship FTS5-only / no
  embeddings. Value: long runs stop degrading mid-window.
- **Phase 2 — Recall + write path.** `memories` table, `Consolidator` (hybrid), `RecallEngine`,
  agent tools, scoping, temporal/supersede, graph grounding, payload injection. Value: sturdy
  between windows.
- **Phase 3 (deferred, not this spec)** — cross-encoder reranker, cross-workspace global-prefs UI,
  extension memory-inspection panel. Detailed in §9a so a future session can resume without losing
  this context.

Each phase is independently shippable and flag-gated.

## §9a — Phase 3 detail (deferred — context preservation)

Not in this spec's scope; captured so a cold-start session keeps the intent, the seam each item
plugs into, and the rough approach. None of these are committed designs — they are starting points
to brainstorm into their own spec when Phase 3 begins.

### 9a.1 — Cross-encoder reranker

- **Why:** §4 v1 rerank is just the fused linear score (`w_sem*semantic + w_lex*lexical + ...`).
  Linear fusion ranks well at the top but blurs the middle band; a cross-encoder reads
  `(query, memory.content)` *jointly* and reorders far more accurately — the standard
  retrieve-cheap-then-rerank-precise pattern.
- **Seam already left:** `RecallEngine.recall()` does `top-3k by fused score → final-k`. The reranker
  slots exactly at that `→` — it consumes the top-3k candidates and re-sorts to final-k. No data-model
  or store change; purely a swap of the rerank function behind a flag
  (`AI_EDITOR_MEMORY_RERANKER`, default off).
- **Approach options to weigh later:** (a) local cross-encoder (e.g. a small `bge-reranker`/MiniLM
  cross-encoder via fastembed/ONNX — offline, in-process, ~tens of ms for 30 candidates); (b) a
  provider rerank endpoint (network cost per recall — likely too expensive on the hot path).
  Lean local-first, consistent with the embedder decision.
- **Risk:** latency on the automatic (every-turn) recall path. Mitigation: only rerank when candidate
  count > N, or only on the deliberate `recall()` tool path, not the always-on injection.
- **Test:** extend the §8 `RecallEngine` golden set — assert the reranker improves rank of the known
  answer vs. the linear baseline on the domain (symbol/path) queries.

### 9a.2 — Cross-workspace global-prefs UI

- **Why:** the data model already supports `scope_kind='global'` (user-level memory that retrieves in
  *every* workspace — e.g. "I prefer pytest over unittest", "always use absolute imports"). Phase 2
  can *write* and *retrieve* global memories, but there is **no surface to view/edit/curate them**.
  Without curation, global memory is the highest-risk staleness vector (a wrong global pref poisons
  every project).
- **Seam:** read/write `memories WHERE scope_kind='global'` via new API routes
  (`GET/POST/DELETE /v1/memory/global`) → editor-client contracts → an extension settings view.
  Reuses the existing `MemoryStore`; no schema change.
- **Approach:** a VS Code settings/webview surface listing global memories with edit + delete +
  "promote a workspace memory to global" + "demote/forget". Manual curation is the point — this is the
  human override for concern #1 (staleness) at the global tier.
- **Open question for later:** should the agent be *allowed* to write `global` memories autonomously,
  or only the user via this UI? Leaning user-only writes for global (agent proposes; user promotes),
  to keep the blast radius small. Decide in the Phase 3 spec.
- **Test:** route-level (CRUD + scope filtering), contract round-trip, extension view interaction.

### 9a.3 — Extension memory-inspection panel

- **Why:** memory is invisible today — you cannot see what was recalled into a turn, why it scored,
  or what got written/superseded. This is the debugging surface (the memory analog of the task
  artifacts dir) and the trust surface for the user.
- **Seam:** `RecallEngine` already computes per-memory score breakdowns
  (`semantic/lexical/structural/recency/scope` components) and the harness knows what it injected.
  Expose via `GET /v1/memory/inspect?thread_id=...&turn_id=...` (last recall set + scores) and
  `GET /v1/memory?scope=...` (browse the store). Persist a per-turn recall trace under the existing
  artifacts path (`.agentd/artifacts/.../memory-recall-NN.json`) — mirrors `controller-turn-NN.json`.
- **Approach:** a webview panel with two tabs — **Recalled this turn** (what was injected + the score
  breakdown that ranked it, so weight-tuning is observable) and **Memory browser** (search/filter the
  store by scope/kind/validity; see superseded chains; manual forget/edit, overlapping with 9a.2).
- **Dependency note:** 9a.2 and 9a.3 share CRUD routes + the browser surface — build the store-facing
  API once and let both consume it. Recommend doing 9a.3's read-only inspector *before* 9a.2's
  curation UI (observe before you edit).
- **Test:** artifact-trace write on recall, inspect-route shape, panel render of score breakdown +
  superseded chains.

**Phase 3 ordering recommendation:** 9a.1 (reranker — pure backend, isolated, immediate quality win)
→ 9a.3 read-only inspector (observe recall behavior, validates 9a.1's effect) → 9a.2 + 9a.3 curation
(shared CRUD surface). Each still gets its own brainstorm → spec → plan cycle.

## Config (new env vars)

```
AI_EDITOR_MEMORY_ENABLED            # master kill switch (default off — land dark)
AI_EDITOR_MEMORY_DB_PATH            # default .agentd/memory.sqlite3
AI_EDITOR_MEMORY_COMPACT_TRIGGER_FRAC  # default 0.65
AI_EDITOR_MEMORY_HOT_TURNS          # default 10
AI_EDITOR_MEMORY_DEDUP_THRESHOLD    # default 0.92
AI_EDITOR_MEMORY_RECALL_TOKEN_BUDGET   # default ~1500
AI_EDITOR_MEMORY_WEIGHTS            # w_sem,w_lex,w_struct — default 0.5,0.3,0.2
AI_EDITOR_MEMORY_GRAPH_GROUNDING    # default on
```

## Open questions / risks

- **Embedder choice** (local sentence-transformer/fastembed vs provider embeddings) — implementation
  detail; lean local-first for offline + zero per-write API cost. Resolve in the plan.
- **Token estimation** for the compaction trigger — needs a cheap per-provider tokenizer estimate;
  reuse whatever the loops already use for budget.
- **Golden-set authoring** for `RecallEngine` is manual and domain-specific — budget time for it.
