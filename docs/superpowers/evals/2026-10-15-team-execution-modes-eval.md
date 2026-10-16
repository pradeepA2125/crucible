# Team execution modes: implementation evidence

Date: 2026-10-16
Branch: `codex/team-execution-modes`
Spec: `../specs/2026-10-15-team-execution-modes-design.md`
Plan: `../plans/2026-10-15-team-execution-modes.md`

## Independent review

Independent spec and plan reviewers checked the design against backend and frontend code
before implementation. Findings folded into both documents: unresolved exchanges block
DONE reopening/extension; blocked requests have explicit retry/cancellation; attached
requests preserve lifecycle purpose while dedicated replies bypass coordinator reports;
evidence aggregation filters run and purpose; publication intent commits with the result;
busy lead lifecycle starts queue durably; request and pointer commit together; full build
precedes UI inspection.

Post-implementation independent code review and subsequent fixes are recorded in
[the review evidence](2026-10-16-team-execution-modes-review.md). The counts below describe
the initial implementation; that review records the final regression run.

Subsequent real-model testing through the native VS Code UI is recorded in
[the live UI smoke report](2026-10-16-team-execution-modes-live-ui.md), including the
completed deliberate-only result, private lead reply, and same-team execution extension.

## Implemented behavior

- Explicit deliberate, combined and direct-implement modes, plus arbitrary requested
  analysis deliverables. Nullable legacy mode preserves historical workflows.
- Lead-only finalization through the existing terminal report, immutable result posts,
  structured result references and normal main-agent notices.
- Same-team explicit execution extension with run/result identity, file fingerprints,
  capability checks, approval preservation and idempotent retries.
- Private main/lead exchanges, including bounded DONE consultation, retry and cancellation,
  separate public/private cursors and lifecycle-independent reply activations.
- Investigation tool restrictions apply to members/helpers and are checked again after
  tool preparation/waits. Main PLAN cannot create or explicitly start executing teams.
- Additive SQLite storage, transactional boundaries, replay-safe notice ids, queued starts,
  restart publication recovery and later-run rewind snapshots. Provider usage remains spent
  after rewind; external command effects are not claimed to be undone.
- Client/live/detail/phase contracts, rearmed open-board subscriptions, mode-aware progress,
  passive result cards, directed exchange presentation and implementation approval wording.

## Deterministic verification

Scripted integrations use real temporary SQLite databases, files and the patch engine.
Assertions check activation counts/purposes, accepted result content, actual changed files,
notices and gate state, rather than trusting model prose. The integration suites cover
plan-only assigned proposals, combined execution/review, direct execution, no-change,
lead-only consultation, blocked request retries/cancellation, explicit extension, failed
publication recovery, stale gate refusal, public feedback during finalization, queued
commands after policy changes, a busy consulting lead's deferred finalization, and rewind.

- Backend: **520 passed** in the final focused regression group (40.20s):
  `.venv/bin/pytest -o addopts='' tests/test_team*.py tests/test_notice*.py tests/test_agent_notices_store.py tests/test_subagent*.py tests/test_tool_sources.py tests/test_tool_batching.py tests/test_rewind*.py tests/test_controller_delegated_hint.py tests/test_prompt_goldens_teams.py tests/test_prompt_goldens_subagents.py`
- After the final consultation-policy refinements, **102 passed** across workflow,
  foundation, service, prompts and controller implementation suites (7.05s).
- Full webview suite: **1,131 passed, 1 skipped** with two workers (40.63s). A preceding
  fully parallel run hit one isolated headless Chrome timeout; limiting contention made
  the whole suite pass. The skipped test is the existing optional stream-render benchmark.
- Client team contracts: **14 passed**.
- Extension team-view/controller suites: **99 passed**.
- Full `npm run build` and `npm run typecheck`: **passed**, including webview and client declarations. Build reports the existing graph chunk-size warning.
- Ruff on changed runtime modules, team modules and new workflow/foundation tests: **passed**. `git diff --check`: **passed**.

The final scripted end-to-end case uses the actual creation/start tool adapters, real
SQLite/PatchEngine and event broadcaster, reads `/live`, approves through the HTTP route,
then asserts actual files, final phase and one streamed result publication.

## Frontend visual verification

Built the full editor-client/extension/webview bundles before inspecting actual rendered
components in isolated headless Chrome. A component preview at 1180 pixels showed
plan-only completion, combined finalization, direct implementation approval, skipped
execution for no-change, and plan/diagnosis result cards including a 320-pixel panel.
The expanded-result browser layout test measures 280/360/640-pixel panels and checks
wrapping without hidden/clipped content. This is component visual verification, not a
claim of reloading the user's running VS Code extension host.

## Limits and unrelated checks

The whole extension unit suite has 15 failures in the unchanged runtime configuration
fixtures (`runtime-performance-settings.test.ts`, `runtime-web-search.test.ts`): their
mocked `cfg.get` returns `undefined` for `crucible.memory.experimental.storePath`, then
`src/runtime/vscode-runtime.ts:370` calls `.trim()`. The affected team-view/controller suites
pass. This change does not alter that runtime file or those fixtures.

Backend mypy also reports preexisting errors across unrelated modules and existing
controller/tool-source typing. New workflow/finalization/policy modules have no reported
errors; mypy is not claimed clean repository-wide. Targeted Ruff and frontend typechecks
are the scoped static checks.


## Bounded real-model smoke

Started an isolated authenticated backend through `scripts/stress/start-backend.sh` on
port 8016, workspace `/tmp/team-modes-live/ws`, using the installed local Ollama
`gpt-oss:20b` model. Main was explicitly in PLAN. It created a two-member team with
`mode=deliberate` and a diagnosis deliverable. The local provider produced an empty
response and repeated 90-second request timeouts; the four-minute probe stopped the
turn and disbanded the test team. No accepted result or successful live finalization is
claimed. No product files were written. The isolated backend was stopped and port 8016
was verified closed. The unused semantic warmup also reported the preexisting missing
`sentence_transformers` dependency; semantic retrieval was not needed for this empty
workspace probe.

Artifacts for the bounded attempt are under `/tmp/team-modes-live/`; the component preview
is `/tmp/team-modes-preview.html` and `/tmp/team-modes-preview.png`. These are disposable
verification artifacts, not production source. No cloud model was substituted and the
user's running backend or extension host was not restarted.
