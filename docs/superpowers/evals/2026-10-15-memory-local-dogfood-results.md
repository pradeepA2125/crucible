# Local memory dogfood — 2026-10-15

This is a live smoke test, not a replacement for phase 6 quality gates. Pradeep authorized repository Git/source/docs and the new test conversation to the connected ChatGPT plan. Environment/credential files and unrelated private session data were excluded from the workload.

## Workload and evidence

The installed extension used this checkout's managed backend with experimental recall, curator and evidence compaction enabled only in this workspace, and a separate durable store at `.crucible/state/memory-v2-local.sqlite3`. The actual application conversation was `chat-77b6d0cb141a`: audit changes since v0.5.5, redesign `landing/`, then implement a fluid sculpture and refine it using Pradeep's 21st.dev reference. The active provider was ChatGPT / `gpt-6.1-sol`, low reasoning. No sub-agents were used.

Local evidence is retained under `.crucible/state/eval/memory-dogfood-2026-10-15/`: `run.json`, numbered prompt/SSE pairs, `release-audit.md`, `compaction-budget.json`, `compacted-anchor.md`, `curated-memories.json`, and the independent recall probe. Recall traces live under `.crucible/state/artifacts/chat/<thread-id>/`.

## Observations

- The first audit call stopped making progress for about seven minutes. Stopping that read-only turn and requesting a bounded answer from existing evidence succeeded. This does not establish the cause of the provider stall.
- The real workload reached about 196k provider-reported input tokens, below the normal 472k compaction trigger for the 500k budget. A temporary runtime compactor budget of 128k exercised the actual summarizer/controller on the same ChatGPT account. The original 500k runtime budget was subsequently restored and verified. This was a controlled trigger, not evidence of natural 500k exhaustion.
- Compaction persisted 127 source segments and anchor version 1 (6,775 characters), as observed read-only after the fold. The anchor retained the implementation, relevant constraints, and unresolved renderer failure. The immediate no-tools continuation probe hit a ChatGPT usage limit; its answer quality could not be scored. A subsequent real implementation turn did resume successfully from the compacted history.
- Automatic curation completed four chunks, saved eight memories, dropped one proposal, and advanced its watermark to mseq 135 with zero failures. All eight saved memories had completed embeddings. This was the background curator, not explicit `remember` calls.
- Before curation, Codex recorded three expected lessons: release/experimental claim boundaries, avoiding fabricated claims/screenshots, and mobile/reduced-motion accessibility. All three appear in the saved rows. These were Codex-authored test instructions delivered through the application's user role, not independently collected natural-human labels. Therefore their `user_said` provenance describes the application role, not who originally authored the text.
- At least one saved row is too specific to the interrupted audit (`Finish interrupted audits using already returned repository evidence`, source mseq 65). Treating a one-time no-tools recovery request as a durable lesson remains a quality concern. Eight saved rows does not mean eight independently validated useful lessons.

## Independent recall failure

A fresh application conversation, `chat-6cc4ba0c700b`, received only a read-only request for standing content/accessibility requirements for future landing updates. It received no anchor, old conversation history, repository reads, or restated answers. Its `memory-recall-00.json` found all eight saved memories as candidates but returned `status=empty`, `judge=reranker`, and `delivered=[]`. The largest Q score was approximately 0.319; the accessibility row scored approximately 0.217, and the release-claims row approximately 0.0088. The agent answered that no relevant saved memory was available.

This demonstrates candidate retrieval followed by a false-negative delivery gate on a useful fresh-session request. The production provisional gate remains the one in `services/agentd-py/agentd/memory/recall_v2.py`; no threshold was changed to make this smoke test pass. The earlier offline calibration findings remain relevant. The current result supports live plumbing for curation, embedding, evidence folding and continued work, but does **not** establish useful cross-session recall or release readiness.

Read-only verification used SQLite URI `mode=ro` plus `PRAGMA query_only=ON`, including:

```sql
SELECT * FROM anchored_summaries WHERE run_id = 'chat-77b6d0cb141a';
SELECT count(*) FROM compaction_segments WHERE run_id = 'chat-77b6d0cb141a';
SELECT id, content, applies_when, detail, provenance, source_message_id,
       embedding_state FROM memories WHERE source_thread_id = 'chat-77b6d0cb141a';
```

The curator status was read from `/v1/memory/curation?thread_id=chat-77b6d0cb141a`; live turn state came from `/v1/chat/threads/<thread-id>/live`.

## Final quality review after the workload

Website changes were a test workload, not an accepted product design. No further design work is part of this assessment.

At the final read, the main turn was inactive, the provider-reported window was 500k, and the latest request used 82,921 input tokens. Curation still covered only through mseq 135; the main history reached mseq 213. Later refinement turns had **not** received a second automatic curation pass. `status=complete` therefore means the earlier scheduled batch completed, not that the entire current conversation was curated.

Across the main conversation there were 34 recall traces: 21 `no_candidates` before usable saved memories were available and 13 `empty` results; none delivered a memory. The independent probe adds one more `empty` result with eight candidates and zero deliveries. Counts are a trace inventory, not 35 independent quality examples: working-set triggers are correlated and early empty-store traces are expected.

### Curated content, reviewed against its actual source rows

All eight rows map back to the four preserved user-role source messages at mseq 1, 65, 67 and 103 in `compaction_segments`. Audit entries have `action=save`, `reason=curator`. No passing-test, current-branch or implementation-status rows appeared in this saved set.

| Saved lesson | Source mseq | Manual assessment |
| --- | ---: | --- |
| Exclude sensitive runtime data from repository research | 1 | Supported boundary within this research workload; future scope should remain conditional. |
| Finish interrupted audits using already returned evidence | 65 | Supported quotation context, poor durability: a one-off no-tools recovery request. Should generally be omitted from durable memory. |
| Keep landing work scoped and directly implemented | 67 | Supported for the test task, but questionable as a standing future rule: landing-only edits and no delegation were task permissions, not necessarily permanent preferences. |
| Separate shipped capabilities from experimental memory claims | 67 | Useful and explicitly applies to future landing updates. Also retains the no-fabricated-claims/screenshots rule. |
| Ground installation instructions and links in repository evidence | 67 | Supported, but generic and lower value than a repository-specific pitfall. |
| Preserve mobile usability and reduced-motion accessibility | 67 | Useful supported design constraint. |
| Keep memory implementation details out of marketing copy | 103 | Useful supported content decision, including where the details belong. |
| Provide an actual static fallback for reduced motion | 103 | Useful specific requirement, though related to the broader accessibility row. |

The predeclared three-item checklist is covered 3/3, with two checklist items combined in one memory. This small, prompted checklist cannot support a general recall percentage. I would not present eight saves as eight high-quality durable lessons or assign a reliable precision estimate: one is clearly ephemeral, one overgeneralizes task scope, and another is generic. No independent second reviewer was used.

### Compaction assessment

The 127 preserved source segments span mseq 1–127, contain 402,582 characters, and comprise four user, 29 assistant, and 94 tool-result rows. They remain available on disk. Manual inspection of the 6,775-character anchor found the goal, stack, changed file map, user constraints, experimental/released distinction, current renderer failure, and open verification work. That supports useful continuity. It does not establish preservation of every source fact, and raw archival preservation is separate from having facts available to the next model call. No independent full-source gold or paired task-quality score was produced in this live smoke test.

### Recall diagnosis and priority

The live gate accepts Q >= 0.65, or both C and R >= 0.80 (`services/agentd-py/agentd/memory/recall_v2.py:21`, `:382`). The fresh probe has only Q, so every candidate below 0.65 was rejected. When the reranker completes successfully but selects nothing, this path does not consult the LLM judge; fallback occurs on missing/failed reranking (`recall_v2.py:394`). This locates the observed failure after candidate retrieval, rather than in missing saves or absent embeddings.

The next memory work is to calibrate and validate the delivery gate on untouched positive and negative requests, including this fresh-session failure; tighten durability handling for one-time instructions and task permission scope; and rerun cross-session behavior tests. The test does not justify lowering a threshold until its precision tradeoff is measured. No memory thresholds or curator rules were changed during this workload.
