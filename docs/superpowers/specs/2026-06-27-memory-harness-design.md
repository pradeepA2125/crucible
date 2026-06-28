# Memory Harness — Sturdy Agent Memory Across Context Windows

**Status:** Phase 1 design approved + planned · **Date:** 2026-06-27 · **Owner:** pradeep

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
  of the window — it compacts before degradation, losslessly on disk. *(Phase 1)*
- Cross-session recall: close a window, return later, and relevant prior facts / decisions /
  edits are retrieved into context by relevance. *(Phase 2)*
- Sturdiness against the three known failure modes: **staleness**, **change-as-evolution
  (not replacement)**, and **cross-session identity/scoping**. *(Phase 2)*
- The three content types are first-class: **semantic**, **episodic**, **procedural**. *(Phase 2)*

**Non-goals (v1)**
- No new *memory* knowledge graph. We reuse the existing Rust **code-structure** graph as a
  grounding target (different beast — see Phase 2). Merging the two is deferred.
- No external vector service. In-process SQLite only.
- No cross-encoder reranker, no cross-workspace global-prefs UI, no extension memory-inspection
  panel (Phase 3).

## Phasing & maturity — read this first

This spec deliberately documents the three phases at **different depths**, because they are at
different stages. Do not read Phase 2's detail as equivalent to Phase 1's: Phase 1 is build-ready;
Phase 2 is a committed design sketch that **gets its own brainstorm → plan before implementation**;
Phase 3 is preserved context only.

| Phase | Scope | Maturity | Artifact |
|---|---|---|---|
| **1 — Compaction** | within-run window management (no recall, no embeddings) | **Build-ready** — fully specced + TDD implementation plan written | `docs/superpowers/plans/2026-06-27-memory-harness-phase1-compaction.md` |
| **2 — Recall + write path** | cross-session memory: store, consolidate, retrieve, inject | **Design sketch** — this spec defines the shape; needs its own brainstorm → spec-refine → plan | This doc, "Phase 2" section |
| **3 — Polish** | reranker, global-prefs UI, inspection panel | **Notes only** — needs brainstorm → spec → plan | This doc, "Phase 3" section |

Each phase is independently shippable and flag-gated. The whole subsystem is dark unless
`AI_EDITOR_MEMORY_ENABLED` is truthy.

## Decisions (locked during brainstorming)

| Axis | Decision | First applies |
|---|---|---|
| Phasing | Compaction first, recall second | — |
| Target loops | `ControllerLoop` + task `ToolLoop` (within-run window overflow) | P1 |
| Compaction sizing | Hot set **token-bounded** (`MEMORY_HOT_TOKEN_FRAC × window`, default 0.4) with `MEMORY_HOT_TURNS` as a secondary count cap; `hot_frac < trigger_frac` makes reduction provable | P1 |
| Hot losslessness | Hot set = whole logical turns only (user/assistant + its `tool_result`/`tool` continuations); a turn that doesn't fully fit is evicted entirely (survives via the summary), never split or left dangling | P1 |
| Substrate | SQLite + `sqlite-vec` (embeddings) + FTS5 (BM25/keyword) — in-process, no new service | P1 (sqlite only) / P2 (vec+FTS5) |
| Write path | **Hybrid** — background consolidation (default) + agent tools (deliberate) | P2 |
| Integration shape | **Middleware + tools** (automatic compaction/recall in middleware; deliberate write/recall as tools) | P1 (middleware) / P2 (tools) |
| Segment tiering | **Decided at read time (P2), not baked at write time.** P1 persists evicted segments with no tier | P1 |
| L4 | Reuse code graph as grounding target via `query_graph`; no new memory graph | P2 |
| Content × lifecycle | episodic/semantic/procedural `kind` × temporal `valid_to`/`superseded_by` lifecycle | P2 |

---

# Shared foundations (all phases)

## Component map

New subpackage `services/agentd-py/agentd/memory/`. Each unit is tagged with the phase it first
appears in.

```
harness.py        # MemoryHarness — façade the loops call; orchestrates the units.        [P1]
compactor.py      # Compactor — token-bounded hot set + anchored summary.                 [P1]
store.py          # MemoryStore — SQLite. The ONLY DB-aware unit. (+vec+FTS5 in P2)        [P1]
models.py         # Pydantic models (CompactionSegment, AnchoredSummary, … Memory in P2)   [P1]
config.py         # MemoryConfig + from_env.                                               [P1]
recall.py         # RecallEngine — multi-signal retrieval + scoring.                       [P2]
consolidator.py   # Consolidator — async LLM write path: extract → dedupe → supersede.     [P2]
```

## Integration shape (C — middleware + tools)

- `MemoryHarness` is injected into `ControllerLoop` and `ToolLoop` (constructor param, mirrors the
  existing registry injection and how `retrieval_context` already flows).
- **Automatic path (middleware):** each iteration the loop calls
  `harness.prepare_turn(history, run_id)` → `(maybe_compacted_history, recalled_memories_slot)`.
  The loop drops `recalled_memories` into the **dynamic tail** of the payload (KV-cache-safe).
  Phase 1 returns an empty recall slot; Phase 2 fills it.
- **Deliberate path (tools, P2):** `remember(content, kind, entities?)` and `recall(query)`
  registered in both tool registries, gated into per-state allowed-tools sets (same mechanism as
  `query_graph`).

## Interface contract (keeps units decoupled)

- `MemoryStore` is the only DB-aware unit; everyone else speaks model objects.
- `Compactor` depends on an anchored-summary callable (built from the reasoning engine) +
  `MemoryStore`. It does **not** depend on `RecallEngine`.
- `RecallEngine` and `Consolidator` (P2) depend on `MemoryStore` + an embedder, nothing else.
- `MemoryHarness` is the only unit the loops see.

## Flag-gating & kill switch

`AI_EDITOR_MEMORY_ENABLED` (default off) is the master switch — when off, `MemoryHarness` is a
no-op pass-through and the loops behave byte-identically to today. This lets us land each phase dark
and enable per-workspace (same pattern as `AI_EDITOR_CHAT_CONTROLLER`, `AI_EDITOR_TASK_SUBSYSTEM`).

---

# Phase 1 — Within-Run Compaction  ·  BUILD-READY

**Delivers:** a long run stops degrading when it crosses ~65% of the window. Evicted history is
folded into a merged running summary (in-context) and persisted raw (on-disk). No embeddings, no
recall, no cross-session memory. Standalone value; ships alone.

## P1 data model

`MemoryStore` owns a new DB (`AI_EDITOR_MEMORY_DB_PATH`, default `.agentd/memory.sqlite3` — separate
file, same pattern as chat DB). **Phase 1 creates only these two tables.**

**`compaction_segments`** — evicted raw history (lossless on disk; recoverable in Phase 2):
```
id          TEXT PK
run_id      TEXT     -- thread_id | task_id
seq         INTEGER  -- run-monotonic order across ALL compaction rounds (via store.next_seq)
content     TEXT     -- the raw evicted message content (verbatim)
created_at  TEXT
```
No `tier` and no `embedding` in Phase 1. **Tiering is a read-time concern** (Phase 2): whether an old
segment is worth pulling back depends on the *current query's* relevance, which `RecallEngine`
computes per-turn — baking a static `warm`/`cold` label at compaction time would pre-decide it.
`seq` is **run-monotonic** (continues from `MAX(seq)+1` per run), not per-batch, so ordering is stable
across the many compaction rounds a single long run produces. Granularity is **1 evicted message =
1 segment, verbatim** — no chunking, because Phase 1 segments are write-only.

**`anchored_summaries`** — persistent running summary per run (merged, never regenerated):
```
run_id      TEXT PK
summary_md  TEXT
version     INTEGER  -- bumped on each merge
updated_at  TEXT
```

## P1 compaction algorithm

`Compactor.maybe_compact(history, run_id)` — called at the top of every loop iteration; acts only
when the live history is over the trigger.

- **Trigger:** estimated tokens ≥ `MEMORY_COMPACT_TRIGGER_FRAC × window` (default **0.65** — compact
  before degradation, per the 60–70% finding, not at the hard limit). Below trigger ⇒ no-op.

- **Hot set (kept verbatim) — token-bounded, not count-bounded.** Walking newest→oldest, keep turns
  while they fit `MEMORY_HOT_TOKEN_FRAC × window` (default **0.4**), capped at `MEMORY_HOT_TURNS`
  (default 10). Token-bounding is what makes compaction *provably* reduce the window: since
  `hot_frac (0.4) < trigger_frac (0.65)`, crossing the trigger guarantees there is something to evict.
  - **Always keep ≥1 turn** (the newest — the loop needs the current turn). **Single-message
    backstop:** if that one turn alone exceeds the hot budget, truncate its in-window copy
    (head + `…[truncated]…` + tail, sized to the budget) and persist its full original as a segment.
    This handles "history shorter than `hot_turns` but already over budget" and "one turn bigger than
    the whole window" — cases a count-based window silently failed.
  - **Lossless at turn boundaries.** A logical turn = a user/assistant message plus its following
    continuation messages (`tool_result`, `tool`). The hot set contains only *whole* turns: if the
    budget boundary falls inside a turn, the partial remainder is pushed entirely to eviction (it
    survives via the anchored summary) rather than kept as a dangling half-turn (e.g. a `tool_result`
    with no preceding action). `_select_hot` enforces this by trimming leading continuation messages
    so the hot set always begins at a turn start. (The single oversize-turn backstop above is the one
    intentional exception, and even there the full original is persisted.)

- **Eviction (everything older than the hot set):** each evicted message does **two things at once**:
  - **Folded into the anchored summary** via merge: `summarize(old_anchor, evicted) → new_anchor` —
    **never regenerated from scratch** (anchoring beats reconstruction on continuity — Factory
    36K-message finding). The new anchor replaces the old at an incremented `version`. This is the
    in-context, lossy representation the model reads next turn.
  - **Persisted raw** as `compaction_segments` rows — the on-disk, lossless copy Phase-2 recall can
    pull back. (Segment and summary are two representations of the *same* evicted content.)
  - Phase 1 folds **all** evicted history into the anchor — no information cliff before recall exists.

- **Post-compaction window (provably bounded):** system block (cached head) + anchored summary
  (small) + hot set (≤ `hot_frac × window`) + recall tail (Phase 2, ≤ `MEMORY_RECALL_TOKEN_BUDGET`).
  Every term bounded ⇒ the window cannot grow without bound across a long run.

- **Fallback (best-effort):** if the summarize call fails, keep the prior anchor + hot set, drop the
  evicted band from the window (already persisted as segments), emit a `⚠️ memory degraded`
  breadcrumb, continue. The single-oversize-turn truncation is likewise marked `degraded`. A
  compaction failure never raises out of a loop iteration.

## P1 error handling (best-effort)

Mirrors `retrieval_context` ("never blocks orchestration") and `_finalize_task_narrative` (try/except).

| Failure | Behavior |
|---|---|
| Master kill switch off | `MemoryHarness` no-op pass-through; loops byte-identical to today. |
| `prepare_turn` throws (any reason) | Return history untouched; loop proceeds. |
| Compaction summarize throws | Keep prior anchor + hot set; evicted dropped from window (still persisted); mark `degraded`; continue. |
| Single turn > hot budget | Truncate in-window copy; persist full original as a segment; mark `degraded`. |

## P1 testing

- **`MemoryStore`** — migrations; segment round-trip ordered by `seq`; scope-by-`run_id`;
  `next_seq` run-monotonic; anchor insert-then-version-bump; missing-anchor → `None`. Real `tmp_path`
  SQLite, no mocks.
- **`Compactor`** — scripted summarizer; assert: below-trigger no-op; over-trigger keeps the hot set
  verbatim and within the token budget; anchor **merges** (prior content survives — not regenerated);
  evicted lands in `compaction_segments`; single oversize turn truncated in-window with full original
  persisted; summarizer failure degrades without raising. Plus `_select_hot` unit tests (token bound,
  count cap, always-keeps-one).
- **`MemoryHarness`** — disabled = pass-through (same list object, `compacted=False`); enabled
  delegates; `prepare_turn` swallows internal errors.
- **Loop wiring** — scripted long `ControllerLoop` and `ToolLoop` runs cross the threshold; harness
  invoked with the live history + correct `run_id`.
- **Integration** — a long run crosses compaction, persists segments, versions the anchor, keeps hot
  verbatim, carries the anchor forward across two rounds; plus a disabled-harness parity check.

## P1 open questions

- **Token estimation** — Phase 1 uses a cheap `len//4` char heuristic with a seam to plug a real
  per-provider tokenizer. It only needs to be *monotone*, since the trigger and the hot bound both
  use it. Reuse whatever the loops already use for budget if available.
- **`MEMORY_WINDOW_TOKENS`** is a single configured number in Phase 1; ideally derived per active
  provider/model. Acceptable to defer to Phase 2.

---

# Phase 2 — Cross-Session Recall + Write Path  ·  DESIGN SKETCH

> **Maturity:** this section defines the intended shape and the seams Phase 1 leaves for it. It is
> **not** at implementation-plan depth — Phase 2 gets its own brainstorm → spec-refinement → plan
> cycle before any code. Treat specifics (thresholds, weights) as starting defaults, not commitments.

**Delivers:** "sturdy between windows." Distilled memories persist across sessions, are retrieved by
relevance into the dynamic tail of each turn, stay correct over time (staleness/evolution), and are
scoped per workspace/thread/global.

## P2 data model (adds to the P1 DB)

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
Plus an FTS5 mirror `memories_fts` on `content` + `entities` (exact symbol/path match embeddings blur).
Phase 2 also adds an `embedding` column to `compaction_segments` if raw segments are made directly
retrievable (see chunking below).

## P2 — the three concerns, resolved

1. **Staleness** — retrieval default filters `valid_to IS NULL`; scoring applies `recency_decay` so
   even live-but-old facts sink. The consolidator may proactively set `valid_to` on contradiction.
2. **Change-as-evolution (not replacement)** — when the consolidator writes a fact contradicting an
   existing one, it sets old `valid_to=now` + `superseded_by=new.id` in one transaction. History
   preserved (auditable); only the current fact retrieves by default. **Episodic memories are
   exempt** — immutable, never superseded, only accumulated.
3. **Scoping (identity)** — retrieval filters `(scope_kind='workspace' AND scope_id=<cwd>)` ∪
   `scope_kind='global'`; thread-scoped memories join when recalling within the same thread. Adapted
   Mem0 four-scope model minus `app_id` (one app).

## P2 write path (hybrid)

**Deliberate (agent tools)** — synchronous: `remember(content, kind, entities?)` →
`Consolidator.write_explicit(...)`: embed, run dedupe+supersede, insert, return id. High-trust → no
distillation; stored as authored.

**Background (consolidation)** — async workhorse, triggered at (1) each compaction event (distill the
evicted slice) and (2) turn/task terminal (distill the whole run). One structured LLM call
(`ScriptedReasoningEngine`-compatible):
```
input:  evicted_segment | full run  +  the run's existing memories (dedup context)
output: list[CandidateMemory{kind, content, entities, contradicts?: memory_id}]
```
Then a **deterministic** post-process (no LLM — the high-value test surface):
1. **Embed** each candidate.
2. **Dedupe** — cosine ≥ `MEMORY_DEDUP_THRESHOLD` (default 0.92) vs a live memory of same kind+scope
   → drop (or merge entities).
3. **Supersede** — candidate `contradicts` set, OR same-entity semantic conflict → txn: old
   `valid_to=now`, `superseded_by=new.id`, insert new. **Episodic never supersedes** — always insert.
4. **Insert** survivors.

Rationale: LLM *proposes* (distill + spot contradiction); Python *disposes* (consistent dedup math +
irreversible DB mutation — deterministic, unit-testable). Consolidation is best-effort.

## P2 retrieval & scoring (multi-signal)

`RecallEngine.recall(query, scope, k)` — three parallel passes fused:
```
semantic   = sqlite-vec ANN over embeddings              → cosine [0,1]
lexical    = FTS5 BM25 over content+entities             → normalized [0,1]
structural = entity overlap (query symbols/paths ∩ memory.entities) → [0,1]

score = w_sem*semantic + w_lex*lexical + w_struct*structural
        + recency_decay(valid_from)  + scope_boost  − staleness_penalty
```
Defaults `w_sem=0.5, w_lex=0.3, w_struct=0.2`, env-tunable (measure, don't hardcode).
- **Filter before score:** `valid_to IS NULL` (unless recalling history) + scope filter.
- **Rerank:** top-3k by fused score → final-k. v1 rerank = fused score (cross-encoder is Phase 3).
- **Segments become recoverable here** — recall can fold the raw segment store into its candidate
  set; that is the read-time realization of "warm vs cold" (relevance decides, not a write-time label).
- **Query source:** automatic path → current user message + active goal/active-todo; tool path → the
  agent's explicit `recall(query)` string.
- **Budget:** ≤ `MEMORY_RECALL_TOKEN_BUDGET` (default ~1500 tokens) injected into the dynamic tail.
  Hard cap — memory never crowds out the task.

## P2 code-graph grounding (L4 reuse)

Memories carry `entities` (paths, `path:Symbol`). After retrieval, for the top 1–2 memories only, an
optional expansion calls the existing `GraphWalker.query_graph(node=entity)` for one structural hop
(callers/callees/imports) — grounding a recalled memory in the code as it exists *now*, and passively
catching staleness (symbol gone ⇒ memory suspect). Gated exactly like `query_graph` today (needs
`index-snapshot.json`), cost-bounded behind `MEMORY_GRAPH_GROUNDING` (default on). No new graph
maintained — pure read against the Rust snapshot.

## P2 segment chunking (a real design decision for the Phase-2 plan)

Phase 1 stores one segment per evicted message, verbatim — fine while segments are write-only. When
Phase 2 embeds + retrieves them, message-shaped granularity breaks down (a 40KB message → one
averaged, useless embedding; wildly uneven units). Phase 2 must **re-chunk** evicted content to a
**token target** (~256–512 tokens, optional small overlap, respecting message/turn boundaries) so
each embedding covers a coherent unit. The oversize-turn full-original segment needs the same
treatment. Chunk size/overlap is a Phase-2 decision.

## P2 error handling

| Failure | Behavior |
|---|---|
| Embedder unavailable | Degrade to FTS5-only; log once. Store `embedding=NULL`, backfill later. |
| Retrieval throws | Empty `recalled_memories` slot; loop proceeds. |
| Consolidation throws | Nothing written that round; log; turn unaffected. |
| `sqlite-vec` missing | Boot FTS5-only mode + startup WARNING (like `warn_if_incoherent_flags`). |

## P2 testing

- **`Consolidator`** — `ScriptedReasoningEngine` canned candidates; dedupe-by-threshold,
  supersede-on-contradiction, episodic-insert-always.
- **`RecallEngine`** — domain golden set `(query → expected memory id, ranked)` over symbol/path
  queries (benchmark scores don't transfer); assert weight tuning moves ranks.
- **KV-cache guard** — byte-position assertion that `recalled_memories` lands in the dynamic tail,
  never the cached head (finding #13: unit byte-identity tests miss turn-over-turn prefix breaks).
- **Integration** — write memories in one scripted run; a *second* run in the same workspace recalls
  them. The "sturdy between windows" acceptance test.

## P2 open questions

- **Embedder choice** (local sentence-transformer/fastembed vs provider embeddings) — lean
  local-first for offline + zero per-write API cost.
- **Chunk size/overlap** for segment re-chunking (above).
- **Golden-set authoring** for `RecallEngine` is manual and domain-specific — budget time.
- **Scoring weights / recency half-life** — tune against the golden set, don't ship the defaults blind.

---

# Phase 3 — Deferred Polish  ·  NOTES

Not in scope; captured so a cold-start session keeps the intent, the seam each item plugs into, and
the rough approach. None are committed designs — each gets its own brainstorm → spec → plan.

## 3.1 — Cross-encoder reranker

- **Why:** Phase-2 rerank is just the fused linear score. Linear fusion ranks well at the top but
  blurs the middle band; a cross-encoder reads `(query, memory.content)` *jointly* and reorders far
  more accurately — the standard retrieve-cheap-then-rerank-precise pattern.
- **Seam already left:** `RecallEngine.recall()` does `top-3k → final-k`. The reranker slots at that
  `→`. No data-model change; a flagged swap of the rerank function (`AI_EDITOR_MEMORY_RERANKER`, off).
- **Approach:** (a) local cross-encoder (small `bge-reranker`/MiniLM via fastembed/ONNX — offline,
  in-process, ~tens of ms for 30 candidates) — preferred; (b) provider rerank endpoint — likely too
  costly on the hot path.
- **Risk:** latency on the every-turn recall path. Mitigation: rerank only when candidate count > N,
  or only on the deliberate `recall()` tool path.
- **Test:** extend the `RecallEngine` golden set — assert reranker improves the known answer's rank
  vs. the linear baseline.

## 3.2 — Cross-workspace global-prefs UI

- **Why:** the data model already supports `scope_kind='global'` (retrieves in every workspace —
  "I prefer pytest", "always absolute imports"). Phase 2 can write/retrieve them, but there's no
  surface to view/edit/curate — the highest-risk staleness vector (a wrong global pref poisons every
  project).
- **Seam:** `memories WHERE scope_kind='global'` via new routes (`GET/POST/DELETE /v1/memory/global`)
  → editor-client contracts → an extension settings view. Reuses `MemoryStore`; no schema change.
- **Approach:** a VS Code surface listing global memories with edit/delete + "promote a workspace
  memory to global" + "demote/forget". Manual curation is the human override for staleness at the
  global tier.
- **Open question:** may the agent write `global` autonomously, or only the user (agent proposes, user
  promotes)? Leaning user-only, to keep blast radius small.
- **Test:** route CRUD + scope filtering, contract round-trip, view interaction.

## 3.3 — Extension memory-inspection panel

- **Why:** memory is invisible — you can't see what was recalled, why it scored, or what got
  written/superseded. The debugging surface (memory analog of the task artifacts dir) and the trust
  surface for the user.
- **Seam:** `RecallEngine` already computes per-memory score breakdowns
  (`semantic/lexical/structural/recency/scope`) and the harness knows what it injected. Expose via
  `GET /v1/memory/inspect?thread_id=…&turn_id=…` (last recall set + scores) and
  `GET /v1/memory?scope=…` (browse). Persist a per-turn recall trace under the artifacts path
  (`.agentd/artifacts/.../memory-recall-NN.json`) — mirrors `controller-turn-NN.json`.
- **Approach:** a webview panel — **Recalled this turn** (injected items + score breakdown, so
  weight-tuning is observable) and **Memory browser** (filter by scope/kind/validity; superseded
  chains; manual forget/edit, overlapping 3.2).
- **Dependency:** 3.2 and 3.3 share CRUD routes + the browser surface — build the store-facing API
  once. Do 3.3's read-only inspector *before* 3.2's curation UI (observe before you edit).
- **Test:** artifact-trace write on recall, inspect-route shape, panel render of score breakdown +
  superseded chains.

**Phase 3 ordering:** 3.1 (reranker — isolated backend win) → 3.3 read-only inspector (observe recall,
validates 3.1) → 3.2 + 3.3 curation (shared CRUD). Each gets its own brainstorm → spec → plan.

---

# Config (all env vars)

```
# Phase 1
AI_EDITOR_MEMORY_ENABLED               # master kill switch (default off — land dark)
AI_EDITOR_MEMORY_DB_PATH               # default .agentd/memory.sqlite3
AI_EDITOR_MEMORY_COMPACT_TRIGGER_FRAC  # default 0.65 — compact when est. tokens cross this × window
AI_EDITOR_MEMORY_HOT_TOKEN_FRAC        # default 0.4  — primary token bound on the hot set (< trigger_frac)
AI_EDITOR_MEMORY_HOT_TURNS             # default 10   — secondary max-count cap on the hot set
AI_EDITOR_MEMORY_WINDOW_TOKENS         # default 128000 — context window size (see P1 open question)

# Phase 2
AI_EDITOR_MEMORY_DEDUP_THRESHOLD       # default 0.92
AI_EDITOR_MEMORY_RECALL_TOKEN_BUDGET   # default ~1500
AI_EDITOR_MEMORY_WEIGHTS               # w_sem,w_lex,w_struct — default 0.5,0.3,0.2
AI_EDITOR_MEMORY_GRAPH_GROUNDING       # default on

# Phase 3
AI_EDITOR_MEMORY_RERANKER              # default off
```
