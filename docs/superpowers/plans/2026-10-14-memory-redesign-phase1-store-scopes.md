# Memory redesign phase 1 — store, scope and evidence substrate

Date: 2026-10-14. Design: `../specs/2026-10-14-memory-recall-redesign-design.md`.

This plan follows the owner's approved scope. The interrupted compaction experiment is
reported separately; its result determines the phase 2 evidence policy. This phase supplies
storage and scope boundaries without enabling the new recall or curator by default.

## Compatibility boundary

Keep the current workspace database for compaction segments and anchors. Introduce a separate
machine-wide durable-memory store for v2, with no import of old memories. Preserve the old
recall path until the phase 6 evaluation passes. Keep v2 construction and routing behind
`CRUCIBLE_MEMORY_RECALL_V2`; do not create an empty global database merely by importing a module.
Keep the legacy `Memory` representation usable by the legacy consolidator/recall callers until
those callers are removed. Give the new representation an explicit name during this overlap.

## Tasks

### 1. Scope identity and context

Files: new `agentd/memory/scope.py`; tests `tests/test_memory_scope.py`; startup wiring in the
existing `agentd/memory/harness.py:build_memory_harness` factory used by the backend.

- [x] Define typed `user`, `repo`, `thread` scope identifiers and a context containing all three.
- [x] Make normalization pure: receive origin, root commit and absolute checkout path as inputs.
  Collect git inputs once at backend startup, with bounded subprocess calls.
- [x] Normalize equivalent HTTPS, scp-style SSH and ssh:// origins, stripping credentials and
  trailing `.git`; preserve path case and distinguish non-default ports and different remotes.
- [x] Use root commit when origin is absent, absolute path when git identity is unavailable.
  Define deterministic handling of multiple roots rather than relying on subprocess ordering.
- [x] Test moved checkouts/worktrees sharing an origin, distinct forks, credential removal,
  malformed/unavailable origin fallback, and two thread IDs in one repo.

### 2. New durable entry and global store

Files: `agentd/memory/models.py`, new `agentd/memory/durable_store.py`,
`agentd/memory/config.py`; tests `tests/test_memory_durable_store.py`.

- [x] Define the v2 entry fields and bounds from the design: claim/topic/applicability/detail,
  provenance, revision, source thread/message, entities, scope, kind and embedding bookkeeping.
- [x] Resolve `CRUCIBLE_MEMORY_STORE_PATH` with default `~/.crucible/memory/memory.sqlite3`,
  independently of the workspace compaction database path.
- [x] Enable WAL and a busy timeout. Keep each connection owned by its caller; ensure transactions
  cover row, FTS/vector and audit updates together.
- [x] Create new tables, `porter unicode61` FTS over claim/topic/applicability/entities, and audit
  rows preserving the old entry and mutation reason.
- [x] Test opening an empty store, reopening, validation failures, and two independent stores
  writing concurrently. Verify that legacy workspace memory is not imported.

### 3. Scope enforcement and mutations

Files: new durable store and tests; v2 tool facade wiring is completed in phase 4.

- [x] Require scope context for every ID lookup, search, entity lookup, browse and replacement
  chain. A foreign ID returns the same result as an absent ID.
- [x] Permit scoped retired-entry reads for `read_memory` status, while excluding retired entries
  from every candidate search. Avoid exposing a replacement ID outside the permitted context.
- [x] Deduplicate normalized exact claims within the same scope; do not combine identical claims
  belonging to different repos or threads.
- [x] Implement immediate save, correction, forget, same-scope supersession and rewind retirement
  with transactions and audit records. A failed insert must roll back retirement.
- [x] Enforce user-requested origin for user scope, preserve user-said entries against curator
  rewrites, and reject cross-scope or already-retired supersession targets.
- [x] Test foreign IDs, retired entries, thread isolation, replacement-chain cycles, transactional
  rollback, duplicate IDs/claims, audit fidelity and rewinding one source thread only.

### 4. Scope-filtered vectors and pending embeddings

Files: durable store, embedder integration; tests `tests/test_memory_durable_vectors.py`.

- [x] Create the fresh vec0 table with a scope partition key and live metadata. Search each
  eligible scope with the filter inside KNN selection; do not filter after over-fetching.
- [x] Embed topic + applicability + claim. Track text hash/model and pending/done state.
- [x] Support keyword-only writes/search without the extension or embedder; drain pending work
  only when the embedder is ready. Hash checks prevent stale async results from publishing.
- [x] Rebuild vectors on dimension changes and invalidate embeddings on model changes. Keep
  pending memory available to FTS/entity search while semantic rebuilding runs.
- [x] Test adversarial foreign/retired nearest neighbours, a revision during embedding, model
  changes, extension failure and scope/liveness synchronization on retire/supersede.

### 5. Stable sequence and compaction-segment metadata

Files: `agentd/memory/models.py`, `agentd/memory/store.py`, `agentd/memory/compactor.py`,
`agentd/chat/storage.py`, history append sites in `agentd/chat/controller_loop.py`, and rewind
checkpoints in `agentd/chat/rewind.py`; focused existing compaction/rewind tests plus new
`tests/test_memory_message_sequence.py`.

- [x] Introduce one sequence-assignment mechanism for controller history, assigning monotonic
  `mseq` once at append. Persist the next sequence across restarts; do not renumber retained
  history after compaction. Preserve a tool-result slot's sequence during batch updates.
- [x] Add additive workspace schema columns for segment `mseq`, `role`, `tool` and the metadata
  needed to recover action thoughts and delivered-read ranges. Old segments remain readable;
  missing old provenance is explicitly unavailable, not invented.
- [x] Store raw evicted content before rendering/summarizing, including the oversized-hot-turn
  backstop, as today. Keep source metadata alongside content without changing it.
- [x] Capture thoughts as private evidence metadata while retaining thought stripping from the
  ordinary assistant action content. Also retain tool result status/is_error and read metadata.
- [x] Restore retained message sequences on rewind and delete the rewound segment span, keeping
  the allocation high-water mark monotonic so later source IDs cannot alias retired ones.
- [x] Test multiple compactions, restart, batch interruption, rewind and reappend, and a hot
  message later evicted after it was already consumed by curation.

### 6. Provider projection and phase handoff

Files: `agentd/reasoning/react_common.py`, `agentd/chat/controller_prompts.py`, provider input
builders and existing provider/schema/prompt tests.

- [x] Add an explicit projection that removes private evidence metadata before any provider
  serializes history. Today the controller payload contains the history dictionaries directly;
  an underscore-prefixed key alone does not hide it from JSON providers.
- [x] Verify native and JSON transports, image references, pending batch history and append-only
  caching. Preserve actionable fields and supported tool-call identities.
- [x] Run focused store/scope/compaction/rewind/provider checks, ruff and mypy on changed modules.
  Run the existing memory integration tests to establish legacy compatibility.
- [x] Record changes, checks and remaining gates. Phase 2 must tune output caps against real
  controller history/segments; phase 4 adds the v2 tools and recall wiring, phase 5 enables the
  idle curator only after its separate evaluation, and phase 6 owns default rollout.

## Issues to resolve before their implementation phases

- The design's four-character token estimate differs from the current conservative
  three-character estimator in `memory/compactor.py`. Reuse the current estimator and include
  the anchor and prompt overhead in per-call budgets; do not weaken overflow protection.
- The reranker-failure fallback must respect the owner's one-LLM-judge-call-per-user-message
  limit. Clarify the mid-turn failure path explicitly before phase 4 wiring.
- A real compaction/folding comparison and separate curator evaluation remain gates. A
  UI-captured transcript experiment cannot establish that first-300-character file caps are
  safe for every real history input.

## Implementation handoff (2026-10-14)

Phase 1 is complete in source and tests; the live backend was not restarted or switched to v2.
`CRUCIBLE_MEMORY_RECALL_V2` remains false by default. Enabling it at this stage constructs the
storage/evidence substrate; the legacy recall/consolidator is still wired until the later
pipeline phases are implemented and evaluated. No legacy durable memories are imported.

Implementation details:

- The sequence allocator and curator watermark live in the workspace `MemoryStore` tables
  `history_sequences` and `curation_progress`. Existing controller-history and checkpoint JSON
  already persist message metadata, so no additional `ChatThreadStore` columns were needed.
  `DurableMemory.source_message_id` is the original controller `mseq`, not the UI chat-message ID.
  Sequence reservations commit before append; gaps are allowed, IDs are never reused after
  rewind/restart, and allocation failure marks source identity unavailable instead of inventing
  a temporary ID.
- Exact-claim deduplication strips boundary whitespace and preserves case/interior whitespace,
  which can distinguish code identifiers and literals. Explicit user confirmation of an inferred
  duplicate upgrades provenance with a revision and audit record.
- Vector queries apply scope and liveness inside each vec0 KNN query. Model/dimension changes
  invalidate vectors atomically; extension failure keeps FTS/entity search usable, and a shared
  dirty flag blocks stale vector use by other connections. Reopening with the same model preserves
  its learned dimension.
- The pending embedding writer runs model encoding off the event loop and keeps SQLite reads and
  publication on the owning thread. It waits for the existing serial model warmup, checks hashes
  and model identity before publication, and stops retrying a failed encoder. Phase 3 replaces this
  temporary readiness event with the common local-model capability.
- Private source IDs/thoughts are projected out by controller, planning and tool prompt builders
  and again at the provider boundary. Ordinary tool arguments, provider call IDs, batch slots and
  supported image references retain their existing behavior. Compaction still renders its legacy
  input; capped evidence rendering and skill/AGENTS body eviction are phase 2 work.

The legacy evaluation baseline is frozen under
`.crucible/state/eval/memory-baseline-2026-10-14/`: a SQLite backup from a read-only source connection,
4,880 recall trace files and their hashed manifest. Capture occurred after phase 1 source edits,
before any live-backend restart or v2 enablement; this timing is recorded in the manifest.
The API discovers traces by glob, so the manifest freezes that index rather than copying an
additional trace-index database.

Validation: 537 regression tests passed; ruff passed on all changed Python modules and new
tests; strict mypy passed on the 12 storage, evidence, embedding and provider-boundary modules. It covers fresh/reopened stores, concurrent
writers, scoped search and mutation paths, rollback/audit fidelity, adversarial KNN filtering,
stale asynchronous embeddings, multiple compactions, pending batch slots, restart/rewind,
legacy nullable metadata, native/JSON provider projection, image handling and prompt goldens.
No live model inference, production rollout or recall-quality evaluation is claimed.
