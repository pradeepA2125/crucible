# Memory redesign: continuation results, 2026-10-15

The new pipelines remain off by default. Backend regressions pass; real-provider checks
exposed quality failures, so this is not a release approval. Claude is unavailable until
October 18; Codex continued the implementation, experiments and local review.

## Corrections made

- A partially successful compaction fold retained raw messages on disk but omitted the
  unsummarized suffix from the next model context. It now retains the last good anchor **and**
  that suffix, and a retry starts after the committed prefix (`memory/compactor.py:184`, `:238`).
- A hosted summarizer answered a historical user question. Gated compaction now requires
  summary tags and a recognized note heading, retries once, and preserves unsummarized input
  on failure (`memory/harness.py`, `make_engine_summarizer`). Historical requests are explicitly
  data. A separate observed overclaim about a truncated file read led to an explicit rule
  distinguishing requested and delivered ranges. Format validation cannot prove coverage.
- Both uncapped and capped curator controls now exclude summaries, memory echoes, instruction
  bodies and framed notices. Escaped frames inside serialized assistant actions are stripped
  after decoding (`memory/curation_input.py:64`, `:101`). An earlier gold run that accidentally
  included compaction summaries was stopped and retained as invalid; it is not scored.
- Private thoughts are included in gated compaction. File caps are now 2,000 head + 1,000 tail;
  successful commands 1,600 + 3,200; failed commands 800 + 1,200; edits 4,000 + 1,000. The cap
  profile and thoughts changed together; this is not an isolated thoughts ablation
  (`memory/evidence.py`, `render_evidence`; canonical design cap table).
- The curator prompt now separates a short claim from conditions/detail, requests a short
  exact delivered quote, and calls out direct user process constraints. Python still drops
  invalid proposals without repairing their contents (`memory/curator.py:49`, `:295`).
- Repository probing now uses `child_env()`; semantic readiness returns a real boolean for
  legacy injected index adapters; the read-only GET review includes curation status and the
  existing Claude registration/attempt status reads (`memory/scope.py:120`, `local_models.py:65`,
  `tests/test_get_routes_read_only.py`). The latter two Claude routes also failed that review
  test on HEAD; a baseline copy confirmed this was not introduced by memory work.

All source paths above are relative to `services/agentd-py/agentd/`, unless beginning `tests/`.

## Current size diagnostic

`agentd.eval.memory_evidence` opens SQLite with `mode=ro` and `PRAGMA query_only=ON`; queries:

```sql
SELECT thread_id,controller_history_json FROM chat_threads ORDER BY thread_id;
SELECT * FROM compaction_segments ORDER BY run_id,seq;
```

| Input | Cases | Approximate raw characters | Rendered characters | Reduction |
|---|---:|---:|---:|---:|
| Retained typed controller histories | 69 | 3,997,215 | 1,733,152 | 56.64% |
| Legacy segments with unavailable type metadata | 66 | 9,702,208 | 8,094,378 | 16.57% |

Source hashes, mechanical budget replays and counts are in
`.crucible/state/eval/memory-phase6-2026-10-15/evidence-sizes-v2.json`.
The old approximately 70% reduction used smaller caps. These character diagnostics are not
provider token measurements, a summary-coverage result, or whole-thread source completeness.

## Curator experiment

The owner explicitly approved the 15 redacted chunks from 10 threads for ChatGPT. Frozen
payload SHA-256: `b489ebdbef7fe03ca8dd6bd828c57bbb0849602814f1fc252ab4632093f6882e`.
Payloads remain local under `.crucible/state/eval/memory-phase6-2026-10-15/curator-redacted/`;
no raw conversations are copied into this tracked report.

The matched uncapped/capped gold run finished before extraction. The old and new prompts ran
on the same production-rendered and production-chunked spans, using ChatGPT `gpt-6-luna`.
The old prompt is an ablation on the **new input**, not a full legacy-pipeline baseline. Model
labels were frozen before extraction; the five prewritten Codex label sets remain unchanged.
They disagree: operational-guide failure lessons selected by Codex were absent from model
labels. Combined references total 15 lesson occurrences, including repeated preferences in
separate chunks. This is a provisional denominator, not independent Claude agreement.

New proposals were passed through the actual `Curator._prepare` and audited batch writer in
fresh temporary SQLite stores. No populated production store was copied or modified; semantic
and existing-store duplicate rates remain unverified. Local Codex review of all output entries
is post-extraction and not blind. Review files preserve reasons and reference indices.

| Run | Proposed | Saved by validator | Local keepers / saved | Reference lessons retained | Saved status notes |
|---|---:|---:|---:|---:|---:|
| New prompt v2 | 23 | 9 | 9/9 | 4/15 | 0 |
| New prompt v3, prompt-only retest | 22 | 18 | 16/18 | 8/15 | 0 |
| Old prompt control | 33 | 33 candidates reviewed | 7/33 | 3/15 | 21 |

There were no locally identified unsupported **causal** claims among saved entries; broader
unsupported generalizations were still rejected as non-keepers. V3 development precision was
9/9 and recall 4/10. The five set-aside chunks were a **retest after v2 was viewed**, with
precision 7/9 and recall 4/5. They also share threads with development, so they are not an
independent thread holdout. Aggregate recall 8/15 misses the two-thirds bar; set-aside precision
7/9 misses the four-fifths bar. Neither gate is passed.

Most v2 validation losses were overlong claims and absent exact quotes. V3 reduced losses
from 14 to 4. Literal visibility checks find all 15 reference quote fragments in current
rendered input. This does not establish that every complete lesson survives stripping;
raw/stripped model labels and exact-quote drops still need semantic adjudication. Gold
invalid-quote records are retained, not silently repaired or counted as verified lessons.

Reproducible runners: `agentd.eval.memory_curator_quality` (gold/extraction) and
`agentd.eval.memory_curator_score` (strict validation and **local** labels only). The latter
refuses changed input hashes. V2/v3 `gold.json`, `extraction.json`, `local-review.json` and
`score.json` preserve the controls, decisions and counts. Calls per chunk and conservative
input estimates are recorded; observed input/output tokens, total cost and the NIM comparison
remain unverified.

Automatic approval review rejected uploading the derived labels/results for an external
scoring pass as an additional private payload. No such upload occurred. Local Codex review
completed the scoring instead; it does not replace independent blind labels.

## Compaction comparisons

`agentd.eval.memory_compaction_pairs` reconstructs typed histories from the original 20
approved redacted UI-export spans. Development and held-out sets each select five distinct
threads. Both controls use identical exclusions and thoughts; only caps differ. The actual
production summary prompt, budget estimator and fold selector are used. Inputs fit a single
fold; multi-fold mechanics are covered by tests, not these small hosted cases.

The first profile lost resume details. Enlarging caps improved results, but subsequent runs
still contained missing file names and inconsistent judge decisions. For example, a judge
called an exactly preserved user instruction only partial, and another treated an empty
`git diff` as proving an untracked file's modification history. Those are not reliable facts.
The v3 raw/capped notes for `chat-8fa48bcdfff2:0` also disagree about whether a requested
700-line read was actually delivered; the original excerpt is truncated. That is a real
uncertainty-preservation failure, not a passing numeric score. The prompt was tightened and
a one-case diagnostic retest recorded separately; earlier runs and failures remain intact.
The retest stopped asserting the full requested range, but its capped note omitted
`batch_history.py` entirely. This is still a failed preservation check. The five-thread v3
held-out run also completed; every capped note had at least one lower automated fact grade.
Those grades need manual adjudication, but they cannot be reported as a quality pass.

These reconstructions do **not** establish five actual recorded production compaction
boundaries, independent summary judgments, or successful resumed project tasks. Do not mark
that gate complete from automated scores. Full reports live under `compaction-pairs-*` in
the phase-6 local result directory. New runs record renderer, prompts and reference hashes;
resuming across changed inputs is rejected.

## Live checks and availability

The existing fictional real-controller ChatGPT check verified injection/application,
unrelated-turn silence, audited correction and forgetting, plus observed cached-token counts.
A further real-controller run, `live-discovery-scope.json`, used two fictional temporary
repositories sharing the durable store, with local models disabled:

- The agent read `job.txt`, then used `search_memory` and `read_memory`, and applied checksum SKY.
- In the other repository it found checksum RIVER; reading the first repository's known memory
  ID returned `not_found`. The second history contained no SKY value.
- All six recorded checks passed; the repository identities differed. These verify real tools
  and application wiring against temporary fixtures, not historical task continuation.

Gemini representative-model diagnosis (`gemini-probe-diagnostic.json`) used fictional pings
only: default timed out at 20 seconds, explicit OFF received HTTP 503 UNAVAILABLE, and LOW
received HTTP 400 INVALID_ARGUMENT. This confirms transient/service and configuration failures
on those attempts; it does not prove permanent unavailability or a specific unsupported
thinking option. Earlier NIM judge availability and Claude usage-limit results remain in the
phase-6 plan; Claude was not probed again during its known limit period.

A stronger ChatGPT request using `gpt-6.1-sol` was refused with
`PlanUnsupportedCapability`; that attempted comparison is retained in
`curator-redacted/quality-v4-stronger/extraction.json`, with no successful extraction or cost
claim. A subsequent live account-catalog read offered only `gpt-6-luna`, `gpt-5.6-terra` and
`gpt-5.6-luna` (`ChatGPTAuthService.list_models`; `chatgpt-model-availability.json`). Do not
assume the desktop agent's model list is the subscription route's catalog. The evaluation
runner now stops after the first `ProviderAccessStopped` refusal rather than repeating it
across chunks; a regression test verifies that behavior. Token callbacks are recorded when
provided, but no successful new token-measured comparison was obtained here.

## Validation and remaining release work

Full backend run with permission for temporary local sockets/processes: **3,770 passed,
12 skipped**, two dependency deprecation warnings, 350.66 seconds. This follows a sandboxed
run where local-server/process restrictions caused failures. The final focused selection after
summary/prompt refinements passed **125**; a later summarizer/scoring selection passed **36**,
including fail-fast account access. These
are overlapping groups, not additive totals. Evaluation/core Ruff and strict mypy pass.
Earlier client/host/webview checks remain recorded in the phase-6 plan; no client changes were
made during this continuation.

Remaining: resolve disputed/incomplete gold with independent labels; improve curator recall
and verify on genuinely fresh held-out threads; adjudicate raw/stripped semantic loss;
record actual compaction boundaries and resumed-task outcomes; complete remaining provider
live checks, token/cost and populated-store duplicate studies; then review rollout. No default
flip, legacy removal, deployment or live restart was performed. These are remaining project
items, not work assigned to an unavailable Claude agent.

Subsequently, the owner explicitly approved enabling all three experimental pipelines for
local testing in this workspace. The extension/indexer were installed and the managed backend
reloaded, with a separate durable test store. Health, flags, curator enablement and ready local
models were verified. The phase-6 plan records this local override and rollback; shared
defaults and release-quality decisions remain unchanged.
