# Memory redesign phase 6 — evaluation and rollout

Date: 2026-10-15. Design: `../specs/2026-10-14-memory-recall-redesign-design.md`.

- [x] Preserve the frozen legacy SQLite/trace baseline and keep all new pipeline flags off.
- [x] Add a labelled 40-case Q/C/R matrix (five cases per pattern), development/held-out
  split, reproducible rule comparison, strict ID validation and precision/miss/harm reporting.
- [x] Run real cached cross-encoder scores offline, compare max/weighted/Q-gated rules,
  select only on development data and report untouched results without automatic enablement.
- [ ] Probe provider availability and run the same matrix through representative judges.
- [ ] Prepare 15 real production-shaped curator chunks from >=10 threads, source hashes,
  prewritten gold lessons, five independent labels and a held-out five-chunk split. Evaluate
  old/new extraction on copies; measure status, unsupported causes, precision/recall and cost.
- [ ] Compare raw/capped evidence and five paired compaction summaries; verify actual
  resumed tasks and live injection/search/correction/forget/scope/cache per provider.
- [ ] Review quality gates; only then flip defaults, adapt browse/audit inspection and remove
  legacy recall/consolidation, old flags/weights and importance/recency code.

The 20 redacted samples already approved for ChatGPT may be reused for that experiment.
Self-authored matrix cases contain no project-session payload. New production payloads must
be prepared and redacted before any necessary approval. No deployment, live restart or
default flip follows merely from source tests or synthetic evaluation.

## Evidence and remaining gates

The frozen matrix is `../evals/2026-10-15-memory-recall-matrix-v2.json`: 40 fictional cases,
five per Q/C/R pattern, 24 development and 16 held-out by topic. Labels preceded scores;
SHA-256 `8fe24333e863583fcbd8a7bd98882736bb247540f465ccaaebf7647496908eb4`.
The author is Codex; independent agreement and historical-turn/recap coverage are **unverified**.

Reproducible tools are `agentd/eval/memory_judges.py`, `memory_local_scores.py`,
`memory_llm_judges.py`, `memory_curator_corpus.py`, `memory_curator_quality.py`,
`memory_curator_score.py`, and `memory_compaction_pairs.py`. Local result files live under
`.crucible/state/eval/memory-phase6-2026-10-15/`; `report.json` summarizes them. Rule fitting
never uses held-out scores/labels; score coverage, finite sigmoid range and the four-ID cap
are checked. The local scorer uses an existing cached BGE snapshot, explicit sigmoid, 512
truncation, CPU and one thread; no download or production model runtime was changed.

The weighted family won the declared development objective (avoid harm, maximize precision,
then reduce misses), with weights .6/.25/.15 and threshold .2. Held-out: 12/12 needed entries,
no extra/harmful delivery. The original Q-gated .65/.8 rule found 6/12; a development-fitted
Q-gated .3/.5 found 9/12. This tiny, templated synthetic dataset does **not** justify deploying
those thresholds; production constants remain provisional and unchanged.

Hosted checks found an ID-format defect missed by fake judges: catalog headers were being
returned as IDs. The first ChatGPT run rejected 29 such IDs; NIM rejected 24. Only the judge
prompt was clarified to require the raw ID between `m:` and `@`. The unchanged matrix was
rerun and both providers then returned valid IDs. ChatGPT `gpt-6-luna`: 30 needed hits,
one harmless extra, no harmful entries, precision 30/31, no misses, all 40 calls succeeded.
NIM `nvidia/nemotron-3-ultra-550b-a55b`: 22 needed hits, no extras/harm, eight timeouts and
one malformed reply among 40 cases. Every needed case with a successful response was found;
all-case miss rate is 8/30. Preserve failed/invalid-ID runs and distinguish successful-case
quality from availability. The LLM prompt rerun is a retest, **not** a fresh held-out study.
NVIDIA lists the requested model at https://build.nvidia.com/models.

Claude subscription probing reached `ClaudeAccessStopped` with the usage-limit category.
Gemini's representative alias failed the judge runner's probes, including a no-thinking
retry; a separate one-off default ping succeeded. Its end-to-end judge availability is
therefore **unverified**, not reported as permanently unavailable. Neither provider matrix
is falsely marked complete. Token cost and real provider cache acceptance remain unverified.

Read-only queries found 62 retained controller histories, none with `mseq`, and 4,474 legacy
segments without source-ID columns. The local curator review file contains 15 chunks across
10 threads (five compacted and five never-compacted threads), with source hashes and five
extra chunks set aside. It explicitly marks source IDs as offline diagnostic values and
counts excluded untyped legacy evictions. It includes original raw spans for stripping-loss
review. The owner subsequently explicitly approved its **15 redacted chunks** for ChatGPT;
that frozen redacted payload was uploaded for gold and extraction, with no live store writes.
`curator-codex-prelabels.json` records five preliminary keeper lists before extraction;
independent Claude-side labels and agreement remain pending. Matched gold, old/new extraction,
local review and a five-chunk retest are now recorded in the continuation results below.

Outstanding release work: independent labels/review; curator recall/held-out precision and
semantic stripping comparison; five actual production compaction rounds and resumed tasks;
remaining provider-wide live verification and cost/duplicate studies; then rollout review.
Until those checks pass, all new pipeline defaults stay off and the legacy path stays wired.

A subsequent actual ChatGPT controller-loop smoke test used a fictional temporary workspace
and real scoped tools, preserving live app settings/databases. The agent applied JUNIPER from
the injected memory; an arithmetic follow-up returned 4 with no new candidates/injection.
An explicit correction changed the stored claim to CEDAR and retired the old entry; explicit
forget retired the replacement. These were verified by store reads, not the agent's claims.
Provider-cache observations were 5,888 cached tokens on the needed/correction turns and 6,912
on forgetting (the unrelated turn reported zero). See `live-plan-toy.json` and its preserved
runner. This establishes a limited real-provider/real-controller smoke check, not a real
project continuation, mid-read discovery, multi-repository/provider coverage, or a universal
cache guarantee. Token callbacks may omit judge token totals, so total cost stays unverified.

Final source checks: core/evaluation Ruff and strict mypy pass, with no new Ruff issues in
changed large backend modules relative to HEAD. The memory/provider/controller/schema/rewind
regression selection passed 412 checks; the final curator/storage/rewind refinement selection
passed 102. Editor-client tests passed 59, host memory/setup tests 32, webview setup/inspector
checks 13 plus recall-trace 6; all three TypeScript checks pass. Tests are overlapping groups,
not an additive suite total. Shared dialog/old precision spec hashes remain unchanged.

## Continuation while Claude is unavailable, 2026-10-15

Detailed findings, methods, local artifact paths, SQL queries and release limitations:
`../evals/2026-10-15-memory-phase6-continuation-results.md`.

- [x] Finish the approved redacted gold and paired old/new curator extraction, preserving
  invalid/control runs and frozen prelabels. Score locally through production validators
  and audited writes in fresh temporary stores.
- [x] Improve the curator prompt and retest: saved proposals rose from 9/23 to 18/22.
  Local keepers are 16/18; provisional reference recall is 8/15. Five-chunk retest precision
  7/9 fails the precision bar; overall recall fails the two-thirds bar. No quality pass claimed.
- [x] Complete the larger-cap paired compaction development checks and held-out diagnostics.
  Automated judges remain imperfect; a real truncated-read overclaim remains a release
  finding. The follow-up prompt correction is a diagnostic retest, not a fresh holdout.
- [x] Fix partial-fold history loss; keep the unsummarized suffix after a later fold fails.
  Require a structured note in gated compaction, fail closed on answers/echoes, and clarify
  requested versus delivered file ranges. Focused regression coverage added.
- [x] Remeasure current caps/thoughts read-only: 56.64% smaller typed histories; the earlier
  70.1% result describes the old profile. Update the canonical design and phase-2 plan.
- [x] Verify live file-driven explicit search and two-repository isolation through ChatGPT's
  real controller and tools in fictional temporary workspaces; all six checks passed.
- [x] Diagnose Gemini with fictional pings and safe error categories: timeout, 503, and
  a LOW-configuration 400. Provider-wide quality/availability remains incomplete.
- [x] Full backend regressions: 3,770 passed, 12 skipped after socket/process permissions;
  later targeted refinements 125 passed and summarizer/evaluation refinements 36 passed,
  overlapping. Unsupported plan access now stops evaluation after one refusal.
- [ ] Resolve label disagreements and improve failed quality gates on fresh held-out data.
- [ ] Finish actual production-boundary/resumed-task, provider, cost and populated-store checks.
- [ ] Independent implementation/release review; default flip and legacy removal only after pass.

Automatic approval review rejected sending derived labels/results for external scoring as a
new private payload. No upload followed that rejection; the scoring runner now uses local
review labels and makes no network calls. Earlier approval of the 15 source chunks remains
valid and was used for extraction retests. No further permission is needed for the local work.

## Owner-authorized local testing deployment, 2026-10-15

After reviewing the failed quality gates, the owner explicitly requested enabling the new
pipelines locally for testing. The installed VSIX and indexer were rebuilt with
`scripts/dev/install-local.sh`; the prior installed extension is backed up under
`.crucible/state/deploy-backups/memory-local-2026-10-15/`. The managed Python runtime already
imports this checkout, so its virtual environment did not need replacement.

The three `crucible.memory.experimental.{recall,curator,compaction}` settings default to false
in the extension and now map into managed backend environment variables. Only this workspace's
`.vscode/settings.json` enables them, with `experimental.storePath` pointing to
`.crucible/state/memory-v2-local.sqlite3`. The legacy memory database is retained. Disable the
three workspace settings and run **Crucible: Restart Backend** to return to the old pipelines;
the experimental database can remain for inspection.

The installed bundle matched the built VSIX and checkout. Reloading VS Code restarted its
backend; authenticated health and curator-status reads confirmed it was healthy with all
three flags enabled. Embedder and reranker both reached ready. The separate store initially
contained zero memories/audit rows. The existing Claude provider selection was retained.
Runtime wiring/process tests passed 48 checks and extension typecheck/build passed. Deployment
verification is recorded locally in the backup folder's `after.json`. This is an explicit
local testing override; it does not pass the remaining release gates or change shared defaults.
