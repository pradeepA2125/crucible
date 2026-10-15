# Candidate-catalog recall implementation

Date: 2026-10-15. Owner decision: use the existing BGE model to order candidates;
the working model decides applicability and reads relevant entries. Global consolidation
is deferred. Plan: `../plans/2026-10-15-memory-catalog-recall.md`.

## Result

- V2 recall retains scoped keyword/entity/optional semantic retrieval. The existing
  bounded R → C → Q / claim-plus-detail BGE formatter orders at most 20 candidates.
  Scores never reject a candidate. At most 10 catalog entries are offered after
  successful ordering; otherwise at most 15 are offered in retrieval order. These
  are maxima, not minimums. No candidates means no catalog.
- The main model sees IDs, revisions, topic, claim and applicability with an explicit
  false-positive disclaimer and the actual selection method. Full details stay behind
  the existing scoped `read_memory` tool. Using no memory is explicitly permitted.
- No runtime LLM judge remains. Historical standalone judge evaluation lives under
  `agentd/eval/memory_judge_legacy.py`; its caller and schema checks were updated.
  Provider swaps continue to reach the curator and compaction summarizer.
- Keyword-only working-set refreshes now work without a local scorer. The six-refresh
  bound, shown-ID deduplication, append-only delivery, compaction revalidation, rewind,
  and concurrent retirement/revision protection remain.
- The inspector labels catalog offers rather than claiming relevance, full reads or
  actual model use. It does not yet link catalog offers to later `read_memory` calls.
  BGE scores are explicitly ordering hints without a cutoff or calibrated confidence.
  Older judged traces remain readable.
- No new model, dependency, local service, automatic download, live install, restart,
  hosted evaluation or global-consolidation feature was introduced.

Implementation anchors: `agentd/memory/recall_v2.py`, `memory/harness.py`,
`chat/controller_prompts.py`, and the editor-client/inspector trace adapters.

## Verification

- 196 backend checks passed across recall, tools, controller reads, warning filtering,
  memory lifecycle, curator, strict schemas, provider swaps, rewind/history, cache and
  disabled-local-model behavior. The controller test offers a catalog, then executes
  `read_memory` through the real tool registry and receives detail absent from the
  catalog. Its provider judge receives zero calls.
- 61 editor-client HTTP tests, 8 extension memory tests and 18 inspector tests passed
  (87 total). TypeScript checks and the production build passed.
- Strict mypy passed for the coordinator, harness and historical evaluation helper.
  Focused ruff checks pass. Broader lint also reports the existing B904 warning at
  `chat/controller_loop.py:1874`, outside the recall-call argument changed here.
  The build retains the existing large-chunk advisory.
- The earlier dialog and precision-design files retain their recorded SHA256 hashes.

## Offline real-case replay and limits

Artifacts: `.crucible/state/eval/memory-catalog-recall-2026-10-15/replay.json` and
`replay.py`. Input: the frozen 16-request / 68-pair BGE-versus-Kev protocol. The replay
uses cached real BGE weights with network explicitly disabled, temporary databases
and fixed candidate pools; live memory databases are never read or changed. The device
was CPU, so this is not a replacement for the earlier repeated GPU latency benchmark.

Both ordered and retrieval-only catalogs offered all 14 core, 6 secondary, 42 negative
and 6 ambiguous candidate instances. Every pool fit inside the catalog caps, so changing
ordering could not affect inclusion on this set. All 16 requests completed in both modes.
BGE catalogs ranged from 832 to 2,119 UTF-8 bytes, median 1,157 bytes; these are byte
counts, not provider token usage.

This demonstrates removal of the numerical rejection failure, not improved final-answer
precision. Negative controls intentionally reach the main model as candidates too. The
replay does not establish that the main model rejects those entries, reads the useful
ones, or answers better. Actual main-model selection/answer-quality evaluation remains
unperformed. The scripted controller integration verifies mechanics, not model judgment.
Fixed pools bypass retrieval and do not test a larger corpus's top-20/top-10 coverage.

## Explicit search alignment

`search_memory` now calls the same `RecallCoordinator.order_candidates` helper as
automatic injection and returns the exact same `catalog_notice` disclaimer, including
actual selection method, possible false positives, applicability checks and on-demand
reads. BGE orders without a cutoff; retrieval-only search works without local models.
Topic/file filters apply before ordering, with 10-entry ordered or 15-entry unscored
pages. Query-less scope browsing stays unscored. Cursors reject changed IDs, revisions
or ordering modes instead of silently skipping/repeating pages. The existing scoped
`read_memory` tool continues fetching full detail/status by ID.

Tests compare explicit and automatic catalogs for the same query and low BGE scores,
verify matching disclaimer text, check both pagination modes and changed-result cursor
rejection, and preserve child read-only access. The updated backend regression run
passed 196 tests; strict mypy and focused ruff passed. No live install occurred.

## Small candidate pools

The owner requested skipping BGE for eight or fewer candidates. Both automatic
catalogs and explicit search now bypass scoring at that boundary, retain retrieval
order and omit BGE from the selection-method disclaimer. Nine or more candidates
may use ordering. Boundary coverage includes 0, 1, 8 and 9 candidates on both paths.
The focused recall/tool/controller run passed 27 tests, mypy passed for both core
modules, and focused ruff passed. No live installation was changed. Earlier offline
replay measurements above predate this optimization.

## Checkpoint after refresh and cache review

The owner approved saving this iteration on the existing branch. No further runtime
behavior was changed during the following review:

- One initial lookup is followed by at most six conditional mid-turn attempts.
  Fresh working-set paths/entities or failed-tool first-line signatures trigger an
  attempt; several fresh items form one attempt, and empty results still consume it.
- Mid-turn Q is the latest recorded assistant task-step text plus fresh items, replacing
  the original user query in that field. C and R retain the previous answer and recap.
  Keyword/optional semantic searches run separately per nonempty signal; entity search
  receives the full detected working set. See `memory/recall_v2.py:119`, `:181`, `:401`.
- Catalog delivery appends to history. The ChatGPT-plan transport derives its session
  identifier from owner, instructions and the first input item, rather than the whole
  growing history. Its separate input builder preserves an earlier input prefix when
  history extends it. See `providers/openai_native.py:436`, `:458` and
  `providers/openai_transport.py:331`. This permits cache reuse; refresh-specific live
  cache-hit measurements have not been performed. Extra catalogs can bring compaction
  forward, at which point history rewriting can reduce prefix reuse.
- The inspector shows offers and ordering diagnostics, not linked read/use tracking.
  Automatic mid-turn refresh usefulness and main-model catalog selection quality
  remain unverified. The latest catalog iteration has not been installed locally.

Machine-specific `.vscode/settings.json` and the unrelated October 8 landing-refresh
plan are outside this memory checkpoint and remain local.
