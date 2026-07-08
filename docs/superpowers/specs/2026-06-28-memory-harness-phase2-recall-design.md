# Memory Harness — Phase 2: Cross-Session Recall + Write Path (Refined Design)

**Status:** Design approved (brainstorm complete) · **Date:** 2026-06-28 · **Owner:** pradeep
**Supersedes:** the "Phase 2 — DESIGN SKETCH" section of `2026-06-27-memory-harness-design.md`
(that doc stays as the cross-phase context; this is the build-targeted refinement).
**Predecessor:** Phase 1 (compaction) shipped — `feat/memory-harness` @ `09cc1ed`.

> ## ✅ Pre-implementation research pass — DONE (2026-06-28), deltas folded below
> Web-research pass on current agent-memory standards completed (Mem0, Letta/MemGPT,
> Zep/Graphiti, Generative Agents, and the episodic/semantic/procedural + consolidation
> literature). Findings validated most of the design and produced six deltas, all now
> reflected in the sections below. Sources at the foot of this doc.

## Research findings & deltas (folded into the design)

| # | Finding (source) | Delta applied |
|---|---|---|
| D1 | **Generative Agents** scoring is `recency + importance + relevance`, each min-max-normalized to [0,1]; recency = exponential decay (factor ~0.995); **importance = LLM-rated salience** (1–10). Our scoring had no importance term. | **Add an `importance` signal**: the consolidator rates each candidate; stored on the memory; folded into recall score. Recency = exponential decay w/ env half-life; **min-max normalize each signal** before weighting (§1, §3, §4). |
| D2 | **Mem0** = extract → retrieve **top-K embedding-similar** → LLM decides **ADD/UPDATE/DELETE/NOOP**. | Consolidator dedup context = **top-K similar** memories (not "all run memories"). Keep our `contradicts`-hint + **Python-deterministic dispose** as the deliberate weak-model-safe variant of ADD/UPDATE/DELETE/NOOP — documented, not changed (§2). |
| D3 | **Zep/Graphiti** is **bitemporal** (event time vs ingestion time); supersede = close valid window + open new edge; stale auto-filtered. | Make bitemporal explicit: `valid_from` = event time, `created_at` = ingestion time. Validates our `valid_to`+`superseded_by`+`valid_to IS NULL` filter unchanged (§1). |
| D4 | **Letta**: three-tier OS model (core/archival/recall); **procedural memory is the least-well-served / hardest** kind in every framework. | Framing: our **anchor ≈ core, `memories` ≈ archival, `compaction_segments` ≈ recall**. Flag **procedural as the hardest kind** — strongest few-shot, and a risk to watch (§4). |
| D5 | **Consolidation = transform episodic → durable semantic/procedural**; "the most important and least-implemented" part; **domain-specific** prompts beat task-agnostic ones. | Validates the user's call that the consolidation prompt needs real authoring + our coding-domain few-shot. Cross-episode **reflection** (abstracting recurring episodes into a procedure) noted as future (§8). |
| D6 | Importance defined **per-kind** in some work (procedural=success rate, episodic=task score). | Keep a **single LLM 1–10 importance** for v1 (simpler, Generative-Agents-style); per-kind importance noted as future (§8). |

## Decisions locked in this brainstorm

| Axis | Decision |
|---|---|
| Scope | **Full Phase 2 in one spec** — recall + write path are mutually load-bearing; half a memory is one you can't trust |
| Vector substrate | **sqlite-vec + FTS5 co-located in `memory.sqlite3`** (not lancedb) — supersede/delete are core, so a single transactional store beats a dual-write split |
| Embedder | **Reuse the existing `bge-small` via a shared `Embedder` seam** — the code index already runs it; one model load |
| Consolidation cadence | **compaction events + task terminal + edit-promoting chat turns**; QnA/`answer`/`clarify` turns skipped; async + best-effort |
| Recall candidate set | **A+link** — `memories` embedded; `compaction_segments` retained as the durable backstop (NOT embedded), linked by `seq`-range for on-demand verbatim |
| Segment embedding | **Deferred to a future phase** (only segment embeddings — `memories` are embedded) |
| Scoping | `workspace` + `thread` written (**workspace default**); `global` reserved in schema, **written by nothing until Phase 3** |
| Consolidator I/O | LLM proposes a minimal `{kind, content, entities, contradicts?}`; **Python assigns all bookkeeping** (id/scope/source/lifecycle/embedding/seq-link) |

---

## 1. Components & data model

New/changed units in `agentd/memory/` (Phase-1 units unchanged unless noted):

| Unit | Role | Status |
|---|---|---|
| `embedder.py` | Shared `Embedder` wrapping `SentenceTransformer(bge-small)` — one model load; memory consumes it (`semantic_index.py` may migrate later to kill the double-load) | new |
| `store.py` | + `memories` (sqlite-vec `embedding` + `memories_fts` FTS5) + A+link `seq` columns. Still the **only** DB-aware unit | extended |
| `consolidator.py` | Async write path: LLM proposes candidates → deterministic dedupe/supersede/insert | new |
| `recall.py` | `RecallEngine` — multi-signal retrieve + score | new |
| `harness.py` | Wires recall into `prepare_turn`, fires consolidation at the locked triggers, registers `remember`/`recall` tools | extended |
| `models.py` / `config.py` | `Memory`, `CandidateMemory`; new env vars | extended |

**Dependency rule:** `MemoryStore` is the only DB-aware unit; `Consolidator` and `RecallEngine`
depend on `MemoryStore` + `Embedder` (+ a distill callable for the consolidator) and nothing
else; `MemoryHarness` is the only unit the loops see.

**`memories`** (adds to the P1 DB):
```
id            TEXT PK
scope_kind    TEXT   -- 'workspace' | 'thread' | 'global' (global unwritten in P2)
scope_id      TEXT   -- workspace path | thread_id
kind          TEXT   -- 'episodic' | 'semantic' | 'procedural'
content       TEXT   -- distilled fact / event / skill (atomic)
entities      JSON   -- ['src/tax.py', 'src/tax.py:compute_vat'] — grounding hooks
importance    INTEGER -- D1: LLM-rated salience 1-10 (recall scoring term)
valid_from    TEXT   -- D3: EVENT time — when the fact became true
valid_to      TEXT   -- NULL = currently true; set = retired
superseded_by TEXT   -- id of the memory that replaced it
source_kind   TEXT   -- 'consolidation' | 'agent_tool'
source_ref    TEXT   -- thread_id | task_id that produced it
source_seq_lo INTEGER -- A+link: compaction_segments seq span this was distilled from
source_seq_hi INTEGER
created_at    TEXT   -- D3: INGESTION time — when we recorded it (bitemporal-lite)
embedding             -- sqlite-vec virtual column (bge-small, 384-dim)
```
- **`memories_fts`** — FTS5 mirror on `content` + `entities` (exact symbol/path match; embeddings blur those).
- **`compaction_segments`** — **unchanged from Phase 1.** No `embedding` column (deferred). The
  durable backstop and verbatim source for the A+link.

---

## 2. Write path

Two paths, both ending in one deterministic post-process.

**Deliberate — `remember(content, kind, entities?)` tool.** Synchronous, high-trust, no LLM
distillation (stored as authored). `source_kind='agent_tool'`. Registered in both tool
registries, gated into per-state allowed-tools like `query_graph`.

**Background — `Consolidator`.** Async + best-effort, scheduled as a fire-and-forget
`asyncio.create_task` at the locked triggers so it never blocks a turn. Because the evicted
slice is already durable in `compaction_segments` (the WAL point), a consolidation that fails
or never runs loses nothing — it can be re-distilled later.

LLM call (proposes), `ScriptedReasoningEngine`-compatible, **structured** (`generate_json`):
```
input:  transcript (evicted slice | full run)  +  top-K embedding-similar existing memories
        (D2 — each tagged with its id, as dedup/contradiction context; NOT all run memories)
output: list[ CandidateMemory{ kind, content, entities, importance, contradicts?: memory_id } ]
```
> **D2 note:** Mem0's standard is the LLM deciding ADD/UPDATE/DELETE/NOOP against the retrieved
> similar set. We keep the **dispose** step in Python (deterministic dedupe/supersede) — a
> deliberate weak-model-safe variant: the model proposes + flags conflicts, Python owns the
> irreversible mutation. The retrieval-of-similar context (D2) is adopted; the decision locus is not.

Deterministic post-process (disposes — no LLM, the high-value test surface):
1. **Embed** each candidate (shared `Embedder`).
2. **Dedupe** — cosine ≥ `MEMORY_DEDUP_THRESHOLD` (0.92) vs a *live* memory of same `kind`+`scope` → drop (or merge `entities`).
3. **Supersede** — **primary signal: the candidate's `contradicts: <id>`** (the LLM's explicit conflict reference) → one txn: old `valid_to=now` + `superseded_by=new.id`, insert new. A *secondary* deterministic heuristic (same `scope`+`kind`+overlapping `entity` with cosine in a conflict band below the dedupe threshold) is **optional and its exact rule is a plan-time decision** — primary is the LLM hint. **Episodic never supersedes — always insert.**
4. **Insert** survivors with Python-assigned `source_kind`, `source_ref`, `scope`, `valid_from=now`, `source_seq_lo/hi`.

**Scope:** consolidation defaults to `workspace`; the `remember()` tool may pass `workspace`
or `thread`; `global` is never written (Phase 3 gates it behind a curation UI).

**Best-effort:** consolidation throws → nothing written that round, log once, turn unaffected.

---

## 3. Read path (recall + injection + grounding)

`RecallEngine.recall(query, scope, k)` — signals fused (each **min-max normalized to [0,1]**, per D1):
```
semantic   = sqlite-vec ANN over memories.embedding   → cosine
lexical    = FTS5 BM25 over content + entities
structural = query symbols/paths ∩ memory.entities
importance = memory.importance / 10                    (D1 — LLM-rated salience)
recency    = exp(-Δ / half_life) over valid_from        (D1 — exponential decay)
score = w_sem·sem + w_lex·lex + w_struct·struct + w_imp·importance + w_rec·recency + scope_boost
```
Defaults `w_sem=0.5, w_lex=0.3, w_struct=0.2` (retrieval signals) + `w_imp`, `w_rec`, and the
recency half-life env-tunable — all **tuned against a golden set**, not shipped blind. (Staleness
is handled by the `valid_to` filter below, not a score penalty — D3.)

- **Filter before score:** `valid_to IS NULL` + scope filter `(workspace=cwd) ∪ (thread=current)`. No `global` in P2.
- **Rerank:** top-3k by fused score → final-k; v1 rerank = the fused score (cross-encoder is P3, seam left at the `→`).
- **Injection:** `prepare_turn` returns `(compacted_history, recalled_memories)`; the loop drops
  `recalled_memories` into the **dynamic tail** of the payload (where `instruction`/`budget_status`
  already sit), **never the cached head** (finding #13). Hard cap `MEMORY_RECALL_TOKEN_BUDGET` (~1500 tok).
- **Cadence:** automatic recall is cheap (embed + ANN + FTS, no LLM), but the auto-query
  (current user message + active goal/todo) is stable within a turn → **computed once per turn,
  reused** across inner iterations. Tool path: the agent's explicit `recall(query)` string.
- **A+link verbatim:** each recalled memory carries its `source_seq` span → exact original text
  fetchable on demand (via the `recall()` tool), not embedded, not in the auto hot path.
- **Graph grounding (default on):** for the top 1–2 recalled memories only, one
  `GraphWalker.query_graph(node=entity)` hop — grounds the memory in current code and passively
  flags staleness (entity gone ⇒ memory suspect). Gated like `query_graph` (needs
  `index-snapshot.json`), behind `MEMORY_GRAPH_GROUNDING`. No new graph.

---

## 4. Consolidation prompt (normative contract; full text authored in the plan)

The hardest surface — a weak model decides classification, extraction, and contradiction here.
Reframe that shrinks it: **the model proposes only content-level fields; Python owns all
bookkeeping.** The model never sees `source_kind`, scope, lifecycle, ids (except as references),
or the DB schema. The output schema is deliberately minimal:
`CandidateMemory{ kind, content, entities, importance, contradicts? }`.

The prompt must teach exactly five things:

**(1) The `kind` taxonomy — defined + few-shot:**

| kind | definition (no-jargon) | example | lifecycle |
|---|---|---|---|
| **episodic** | a specific thing that *happened* this session | "User rejected the first plan and asked to keep the change minimal." | immutable — never superseded, only accumulated |
| **semantic** | a durable *fact* about the code/project/user | "Patch ops apply in `patch/engine.py`; it supports 7 op types." | superseded when the fact changes |
| **procedural** | a reusable *how-to* / process | "Run the backend via `start-backend.sh`, always quoting `--workspace`." | superseded when the process changes |

Carries **1–2 worked examples per kind** (the only reliable way a weak model learns the line)
and the explicit rule **"episodic is immutable; never mark it `contradicts`."** **Procedural is
the hardest kind to extract well (D4 — least-served across all frameworks) — give it the
strongest few-shot and watch its quality in the golden set.**

**(1b) Importance (D1):** rate each candidate **1–10** on how much it would help a future session
("the project uses bge-small" = high; "read file X this turn" = low). One integer; drives recall
ranking so salient facts beat recent-but-trivial ones.

**(2) Atomicity + entities:** one fact per memory; extract `entities` as the verbatim
`path` / `path:Symbol` tokens (structural-recall + graph-grounding hooks).

**(3) Contradiction detection:** existing memories are passed in **each tagged with its `id`**;
set `contradicts: <id>` only on a direct conflict. The one place the model touches an id, as a
reference.

**(4) What NOT to remember:** skip ephemeral chit-chat, tool mechanics, restating obvious code,
and anything non-durable. Without this filter a weak model spams low-value memories.

**Mechanism + hardening (carries the Phase-1 summarizer lessons):**
- Structured output via the **`generate_json` schema-constrained path** (like
  `create_planning_step`) — structurally prevents the JSON-echo failure on grammar-capable
  providers; schema-in-prompt for the rest.
- **Single-key input payload** (`{transcript}` + an existing-memories block) — never a multi-key
  dict shape (the shape a weak model echoes).
- **Best-effort validation:** malformed/unparseable → skip the round (segments survive as
  re-distill source); the next trigger re-attempts. No retry storm.
- **No comparative jargon** in the kind definitions — state each kind's nature + when it applies,
  don't rank them.

---

## 5. Error handling (best-effort throughout)

| Failure | Behavior |
|---|---|
| Kill switch off | no-op pass-through (Phase 1) |
| Embedder/model unavailable | recall degrades to FTS5 + structural; consolidation stores `embedding=NULL` (backfill later); log once |
| `sqlite-vec` extension missing | boot FTS5-only + startup WARNING (`warn_if_incoherent_flags` pattern) |
| Recall throws | empty recall slot; loop proceeds |
| Consolidation throws | nothing written that round; log; turn unaffected (segments survive) |
| Graph grounding throws / no snapshot | skip grounding; recall still returns |

---

## 6. Testing

- **`store.py`** — memories CRUD; FTS5 mirror sync; sqlite-vec ANN round-trip; supersede txn
  atomic (`valid_to`+`superseded_by` together); scope & `valid_to` filters; A+link `seq` columns.
  Real `tmp_path` SQLite, no mocks.
- **`Embedder`** — embed dims; degraded path when model absent.
- **`Consolidator`** — `ScriptedReasoningEngine` canned candidates → dedupe-by-threshold,
  supersede-on-contradiction, episodic-insert-always, workspace-default scope, A+link span set,
  best-effort swallow on engine failure.
- **`RecallEngine`** — domain **golden set** `(query → expected memory id, ranked)` over
  symbol/path queries; weight changes move ranks; filter-before-score; FTS5-only degrade.
- **KV-cache guard** — byte-position assertion that recalled memories land in the dynamic tail
  **turn-over-turn** (finding #13: same-turn byte-identity tests miss prefix breaks).
- **Loop wiring** — recall slot filled; consolidation scheduled at the three triggers and **not**
  on QnA turns; `remember`/`recall` registered + gated.
- **Integration** — write in one scripted run; a **second run in the same workspace recalls them**
  ("sturdy between windows" acceptance) + disabled-harness parity.

---

## 7. Config (new env vars)

```
CRUCIBLE_MEMORY_DEDUP_THRESHOLD      # default 0.92
CRUCIBLE_MEMORY_RECALL_TOKEN_BUDGET  # default ~1500
CRUCIBLE_MEMORY_WEIGHTS              # w_sem,w_lex,w_struct — default 0.5,0.3,0.2
CRUCIBLE_MEMORY_GRAPH_GROUNDING      # default on
CRUCIBLE_EMBEDDING_MODEL             # reuse existing — default BAAI/bge-small-en-v1.5
```

## 8. Open questions (carried into the plan, not blockers)

- **Web-research deltas** (the pre-implementation gate above) feed back into taxonomy, scoring,
  dedupe threshold, and the consolidation prompt before planning.
- **Golden-set authoring** for `RecallEngine` — manual, domain-specific; budget time.
- **Scoring weights / recency half-life** — tune against the golden set, don't ship defaults blind.
- **Token estimation** — reuse the Phase-1 `len//4` heuristic; per-provider window derivation deferred.
- **`thread` vs `workspace` default** — consolidation defaults to `workspace`; revisit whether any
  consolidated kinds should auto-scope to `thread`.
- **Deterministic supersede heuristic** — whether to add the secondary same-entity/conflict-band
  rule (§2.3) beyond the LLM `contradicts` hint, and its exact cosine band if so.
- **Importance scale + weights** (D1) — confirm 1–10 vs categorical; tune `w_imp`/`w_rec` + the
  recency half-life against the golden set.
- **Reflection / episodic→semantic abstraction** (D5) — periodic consolidation of recurring
  episodes into higher-level semantic/procedural insights. Deferred (Phase 3-class); the
  per-trigger consolidator does not abstract across episodes in v1.
- **Per-kind importance** (D6) — procedural=success-rate, episodic=task-score. v1 uses a single
  LLM 1–10; revisit if golden-set ranking needs it.

---

## Sources (research pass, 2026-06-28)

- [Mem0: Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413) — extract → retrieve-similar → ADD/UPDATE/DELETE/NOOP.
- [Letta (MemGPT) memory tiers + framework comparison 2026](https://mcp.directory/blog/mem0-vs-letta-vs-zep-vs-cognee-2026) — core/archival/recall; procedural least-served.
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956) — bitemporal valid_at/invalid_at supersession.
- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442) — recency(exp decay) + importance(LLM 1–10) + relevance scoring.
- [Position: Episodic Memory is the Missing Piece for Long-Term LLM Agents](https://arxiv.org/pdf/2502.06975) and [Episodic-Semantic Memory Architecture for Long-Horizon Agents](https://arxiv.org/html/2605.17625v1) — consolidation = episodic→semantic; domain-specific prompts.
