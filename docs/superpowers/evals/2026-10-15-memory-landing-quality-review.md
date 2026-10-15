# Landing-run compaction and curation quality review

Date: October 15, 2026. Retrospective manual review by Codex, requested by Pradeep.

The previous run demonstrated functioning pipelines. This review asks whether their outputs
were useful and faithful. It uses the original extraction-time evidence; the later landing
rollback is not counted as an extraction mistake. No provider calls, memory writes, settings
changes or implementation edits were made for this review.

## Assessment

**Compaction produced a useful continuation note, with one authorization overstatement and
some deliberate loss of older detail. Curation captured the requested constraints but was
too willing to store temporary instructions and design state as repository memory.** This
is stronger evidence than “the jobs ran,” but insufficient for a release-quality accuracy score.

For the 13 memories ultimately produced by the landing run, my provisional disposition is:
**8 reasonable conditional memories, 3 better treated as temporary design/thread state, and
2 that should be rejected or substantially narrowed.** These are reviewer judgments on one
already-inspected run, not a measured general precision rate.

## Evidence and method

- Read both SQLite stores with `mode=ro` and `PRAGMA query_only=ON`, scoped to the landing
  experiment's source thread. Read saved prompts, source segments and the exact saved anchor.
- Compared all 13 memories' claims, applicability and detail with their cited original source
  sequence, separately judging support, durability and scope. All source sequences exist.
- Checked the summary against 18 targeted facts/constraints and the exact eviction boundary.
  This checklist was selected retrospectively, after seeing the summary; it is not a blinded
  benchmark and its counts must not be described as total information retention.
- Retained a local bundle at `.crucible/state/eval/memory-landing-quality-2026-10-15/`:
  `mechanical-metrics.json`, `compaction-review.json`, `curation-review.json`,
  `memory-source-excerpts.md`, source/row snapshots and a SHA-256 manifest.
- The earlier [dogfood report](2026-10-15-memory-local-dogfood-results.md) documented the run
  before this deeper quality pass. Its initial eight-memory snapshot predates five later saves.

Queries used, with `:thread` bound to the landing experiment:

```sql
SELECT * FROM compaction_segments WHERE run_id=:thread ORDER BY seq;
SELECT * FROM anchored_summaries WHERE run_id=:thread;
SELECT * FROM memories WHERE source_thread_id=:thread ORDER BY created_at,id;
SELECT a.action,a.created_at,m.content
FROM memory_audit a JOIN memories m ON m.id=a.memory_id
WHERE m.source_thread_id=:thread ORDER BY a.created_at;
```

Original post-eviction messages were obtained from `chat_threads.controller_history_json` in
the workspace chat store and joined by stable `mseq`, without taking a generated summary as
original evidence. The source role is `user` for all 13 saved memories, but the experiment's
application requests were authored/relayed by Codex for the owner-authorized test. Thus this
does not independently validate direct-human preference extraction.

## Compaction: what worked

The saved anchor is exactly the historical `compacted-anchor.md`, not a newly generated note.
There are **127 original segments, with every mseq from 1 through 127 present**. Their content
totals **402,582 characters**; the anchor is **6,775 characters**, a **98.32% character reduction
for that evicted slice**. This is not a token saving measurement for the entire conversation,
and certainly not 98.32% preserved information. Original segments remain recoverable.

The shared evidence renderer reduces the uncapped evidence representation from 422,714 to
249,395 characters for this slice, **41.0%**, before summarization. This replay uses the same
current renderer; it is a diagnostic reconstruction, not a logged exact provider payload.

The source-backed targeted checklist found 15 details fully preserved, one permission detail
overstated, one older-audit topic only partially retained, and one noncritical command omitted:

| Resume requirement | Source mseq | Assessment |
| --- | --- | --- |
| Current fluid-design goal, implementation and verification | 103, 112 | Preserved |
| Landing-only scope, preserve existing work, no delegation or publication | 67, 103 | Preserved |
| Exclude credentials/private chat data from the feature task | 1, 103 | Preserved |
| Shipped/experimental separation and no fabricated claims/screenshots | 67 | Preserved |
| Actual SVG fallback, scene files and relevant lifecycle state | 103, 112, 123 | Preserved |
| Build/lint success distinguished from warnings and browser failure | 117, 124, 126, 127 | Preserved |
| Reduced-motion/copy assertions initially raced; settled checks passed | 120, 123 | Preserved |
| Latest canvas timeout remained unresolved at the eviction boundary | 125, 126 | Preserved |
| Precise immediate investigation target and preview | 103, 125, 126 | Preserved |

The particularly useful behavior is preserving the failed screenshot attempt instead of
turning successful build/lint output into “everything passed.” The note also carries the
possible context-loss lifecycle explanation as something to investigate, not a proven cause.
See the anchor's `Errors and fixes`, `Open threads` and `Next step` sections.

## Compaction: flaws and limits

1. **Authorization overstatement.** The anchor's `User instructions` section ends with
   “no plan approval needed.” Original mseq 67 instead says to present a concrete landing-only
   plan for approval **if a plan is required**. The later fluid request at mseq 103 asks it to
   make reasonable choices and complete implementation, but gives no explicit blanket
   exemption. The summary should retain that conditional. This is an overconfident permission
   paraphrase; no actual bypass of an approval gate was established by this review.
2. **Goal-directed loss of earlier detail.** The release audit at mseq 66 contains concrete
   feature limits and source citations; the anchor keeps mostly categories and headline
   caveats. That is reasonable for continuing scene work, but a later exact product audit
   would need original evidence or repository reads. Likewise the exact Windows installer
   command from mseq 120 is absent, although the Unix command and verification status remain.
3. **The anchor is an eviction-boundary snapshot.** It ends at source 127. Successful reload,
   screenshot and mobile checks at mseq 128–135 are later original messages, not omissions
   from this summary. Judging the anchor as if it summarized those later checks would be wrong.
4. **Continuation evidence is confounded.** The immediate no-tools probe hit the plan's usage
   limit and cannot establish summary-only answer quality. Later implementation succeeded,
   but used additional user instructions, retained hot messages and file/tool reads. It proves
   the session could continue, not that compaction alone preserved everything needed.

This was a **controlled** compaction trigger using a temporary 128,000-token runtime budget;
the normal 500,000 setting was restored. It does not demonstrate natural exhaustion of the
normal window. No old-compactor control was run on the identical conversation, so a numerical
quality improvement over the old compactor cannot be claimed here.

## Curation: all 13 original extractions

The dispositions below concern suitability as durable repository memory at extraction time.
An accurate record of a temporary design choice can still be the wrong kind of durable memory.

| Saved claim | Source mseq | Disposition and reason |
| --- | ---: | --- |
| Exclude sensitive runtime data from repository research | 1 | Reasonable conditional boundary; keep its research scope. |
| Finish interrupted audits using already returned evidence | 65 | Reject durable: one stalled-call recovery instruction, including a one-time word limit/no-tools constraint. |
| Keep landing work scoped and directly implemented | 67 | Reject or narrow: task-local no-delegation/edit/publication authorization is broadened to future landing work. |
| Separate shipped capabilities from experimental memory claims | 67 | Retain: explicit future-facing constraint; detail also keeps no fabricated claims/screenshots. |
| Ground installation instructions and links in repository evidence | 67 | Reasonable but generic; low additional value beyond normal source checking. |
| Preserve mobile usability and reduced-motion accessibility | 67 | Retain conditionally for future landing updates. |
| Keep memory implementation details out of marketing copy | 103 | Retain: clear public-copy boundary with the intended condition. |
| Provide an actual static fallback for reduced motion | 103 | Retain conditionally for animated artwork; more specific than merely slowing motion. |
| Favor adaptive particle budgets over matching reference counts | 137 | Thread/design state: source-backed for this reference, not an established permanent preference. |
| Use warm editorial sculpture language for landing visuals | 137 | Thread/design state: the selected visual direction is not a general user preference. |
| Check repeated scene transitions for false failure latching | 137 | Retain conditionally: useful lifecycle check; detail preserves the cause as potential. |
| Make landing sculpture dominant through geometry and shading | 194 | Thread/design state: composition targets for this specific artwork. |
| Verify settled artwork in a clean browser context | 207 | Retain conditionally: useful test-isolation lesson after synthetic slow-frame testing. |

The two clearest problems are the interrupted-audit instruction and the broad future-work
scope of the no-delegation instruction. Both were visible problems before any rollback; the
pre-recorded `gold-before-curation.json` explicitly listed one-task scope/no-publication
authorization as **not required durable knowledge**. The three design-state entries are
accurate for a continuing project, but would fit temporary/thread knowledge or explicit
expiry better than general repository memory. The later rollback merely made that distinction
more visible; it is not the justification for these classifications.

The original three predeclared positive lessons were all covered: release/experimental
separation, unsupported claims/screenshots, and mobile/reduced-motion accessibility—**3/3**.
Two are bundled into one memory's detail, so this is lesson coverage, not three distinct rows.
No passing-test counts, current Git state or completed-build status were saved as memory.

All 13 memories reference existing user-role source rows, and their underlying content is
traceable to those rows. That is not the same as complete applicability entailment: the
no-delegation row illustrates a source-supported statement with an overbroad future condition.
Their `user_said` labels also reflect the application's role rather than independent human
authorship. This run mainly tests extraction of prompted requirements, not discovery of
unprompted repository engineering lessons.

An auditability limitation remains: proposal validation checks an exact `source_quote`, but
that quote is not persisted in the durable memory row. The audit preserves old/new rows and
source IDs; curation batch markers preserve counts. I could verify source text independently,
but could not inspect the original exact quotation selected by the curator for every row.
See `services/agentd-py/agentd/memory/curator.py:300`,
`services/agentd-py/agentd/memory/durable_store.py:23` and `:223`.

## Timing, delivery and practical conclusion

The first eight curator saves occurred after one landing request completed and before the
next request began; five further saves followed more than thirty idle minutes after the final
landing turn. None of the recorded save times overlaps a driver-recorded active request.
The initial source-through-135 snapshot assembles 141,862 prepared characters, supporting the
size exception for the first batch. This is a local retrospective assembly, not a recorded
exact eligibility-check byte count.

Historical recall ran 34 checks in the main landing conversation: 21 had no candidates and
13 rejected all candidates. **Zero memories were injected.** Useful automatic memory delivery
therefore was not demonstrated in that live run. The later combined-context/full-body replays
and branch implementation are separate evidence; they do not retroactively make that run a
successful recall test.

Practical judgment: **keep the compaction direction, tighten permission preservation, and
improve curator durability/scope selection.** The biggest curation weakness is deciding what
deserves to persist, rather than inventing unrelated facts. All 13 landing memories have since
been retired at Pradeep's request; this review leaves them retired. It changes no pipeline
behavior, and a broader independently labeled comparison is still needed before claiming an
overall accuracy or an improvement percentage against the previous implementation.
