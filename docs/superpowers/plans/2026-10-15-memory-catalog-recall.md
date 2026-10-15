# Memory recall: candidate catalogs and agent-directed reads

Owner-approved October 15 iteration. Supersedes the phase-4 relevance gate, not the
curator, compactor, legacy pipeline, or deferred global consolidation discussion.

## Contract

- Retrieval enforces scope and liveness. Keep keyword/entity search; add semantic
  retrieval when the embedder is ready. Preserve warning filtering and bounded Q/C/R.
- BGE-reranker-base may order a bounded candidate pool using claim plus detail.
  The main model also receives applicability in each catalog entry.
  Scores never veto a memory. Offer at most 10 entries after successful BGE ordering,
  otherwise at most 15 retrieved candidates. Never invoke a separate hosted LLM judge.
- Each offered line contains ID, revision, claim, topic and applicability, not full
  detail. Explain the actual retrieval/ordering method, possible false positives,
  and that the main model should ignore irrelevant entries and use `read_memory`.
- Run automatic recall once at user-turn start. New files and errors do not start
  additional automatic searches. Keep append-only history, shown-ID deduplication,
  compaction revalidation and scoped explicit search/read tools during the turn.
- Inspector labels distinguish a catalog offer from relevance, a full read or use.
  Existing traces remain readable. Do not invent model verdicts for offered entries.
- Reuse the installed BGE loader and bounded combined-context/full-detail formatter.
  No new model runtime or dependencies. The owner chose BGE for ordering on October 15.


## Work

- [x] Update the canonical design with this approved override.
- [x] Implement catalogs, remove the runtime LLM judge, and cover delivery lifecycle.
- [x] Reuse BGE ordering with validated inputs/output and bounded time.
- [x] Update prompt teaching, client contracts and inspector semantics.
- [x] Validate no-local and local paths, controller reads, rewind/cache behavior,
  historical trace compatibility, Python checks, TypeScript checks and build.
- [x] Replay the frozen real diagnostic cases locally and record limitations.

Global consolidation, curation-policy changes and live installation are outside this
branch iteration. Historical LLM judge experiments remain runnable as evaluation code.


## Completion

Branch implementation and validation are recorded in
`../evals/2026-10-15-memory-catalog-recall-implementation.md`. Functional verification
is complete. Subsequent owner-approved turn-start-only recall and local installation
are recorded there too. Actual main-model selection/answer-quality evaluation remains
unperformed for the catalog iteration.


Explicit-tool follow-through: `search_memory` now shares the automatic ordering policy
and disclaimer; query-less scope browsing remains unscored. Scoped pagination and
changed-result cursor rejection are covered by focused tests.

Small-pool optimization: skip BGE for eight or fewer candidates on both automatic
and explicit search paths. Offer all candidates in retrieval order with an accurate
selection-method disclaimer. Nine or more candidates retain optional BGE ordering.
