# Team execution modes: post-implementation review

Date: 2026-10-16

Branch: `codex/team-execution-modes`

Reviewed implementation: `8bfe7aa` (parent `6eeebd4`)

## Review scope and method

The main agent reviewed the implementation alongside three independent agents:

- Backend reviewer: coordinator/finalization, SQLite migrations and snapshots, service
  contracts, approval validation and persistence boundaries.
- Controller reviewer: tool authorization, activation/report correlation, exchanges,
  restart/rewind, gates, replay and team admission.
- Frontend reviewer: editor-client contracts, extension subscriptions, webview state,
  replay ordering, consultation rearming and rewind cursors.

Reviewers traced callers and consumers and reproduced findings with real temporary
SQLite stores/files, scripted controller activations and deterministic frontend streams.
The controller reviewer independently rechecked the backend repair delta; the frontend
reviewer rechecked the subscription/history changes. No remaining concrete issues were
identified in those reviewed paths after fixes. This is separate from the earlier
independent spec and plan reviews.

## Confirmed findings and fixes

| Finding | Reproduction / effect | Final correction |
|---|---|---|
| Finalization feedback instructed the wrong report status | Following the member prompt's `partial` instruction paused finalization instead of reopening debate. | Both prompt sources explicitly require `completed` with `finalization_feedback`; `partial` remains a pause. Recaptured prompt golden and verified the transition. |
| Canonical file identity drift crashed stale-plan checks | Replacing a planned regular file with an internal symlink changed the fingerprint map key, raising `KeyError`, including with identical bytes. | Missing canonical keys count as drift. A real HTTP approval regression verifies `409 stale_plan`, retaining the approval gate and phase. |
| Reply/cancellation resolution lacked a checkpoint | Rewinding just the resolving turn retained a cancellation/reply and lost the unanswered request. | Checkpoint before either resolution. Real-store regressions restore the blocked pending request and remove discarded reply/cancel posts and notices. |
| Rewind restored an inert live lifecycle | Snapshot restoration removed the coordinator while retaining a live phase or running exchange. The resulting team could neither progress nor recover normally. | Restored live teams become FAILED with their resumable phase preserved; pending exchanges become blocked/retryable; queued starts and obsolete gates are cleared. Explicit recovery recreates the coordinator and required approval without automatically activating execution. |
| Replayed state and discarded history could overwrite current board state | Old DONE events hid a newer same-run consultation. Rewind could retain future rows or stall on lower cursors, including when run and round stayed unchanged. | Monotonic `workflow_revision` orders state; separate `history_revision` marks snapshot restoration. Both flow through summary/detail/live/phase contracts with legacy defaults. New history replaces board rows and resets cursors; late old snapshots are rejected. Restored/deleted team replay and restored member replay are cleared before restored phase publication. |
| Revival double-counted an admission slot | A FAILED team with an unresolved exchange already counted toward the live limit, then its revival was denied when another team occupied the remaining slot. | Revival reuses the slot already held by that exchange. A real-controller regression admits two teams and successfully revives the first without exceeding the limit. |

Production changes and regressions are included in the review-fix commit. The spec, plan
and `CLAUDE.md` now describe the final status/revision/recovery contracts.

## Final verification

All checks below ran against the completed fixes:

- **528 backend tests passed** (28.85s):
  `.venv/bin/pytest -o addopts='' tests/test_team*.py tests/test_notice*.py tests/test_agent_notices_store.py tests/test_subagent*.py tests/test_tool_sources.py tests/test_tool_batching.py tests/test_rewind*.py tests/test_controller_delegated_hint.py tests/test_prompt_goldens_teams.py tests/test_prompt_goldens_subagents.py`
- **1,135 webview tests passed, 1 skipped** (41.62s):
  `npm --prefix apps/vscode-extension/webview-ui test -- --maxWorkers=2`.
  The skip is the existing optional stream-render performance benchmark. Browser layout
  tests ran with headless Chrome.
- **14 client contract tests passed**: team and team-activity contracts.
- **103 extension tests passed**: team-views and controller suites.
- Full `npm run build` and `npm run typecheck`: **passed**. The build retains the existing
  graph chunk-size warning.
- Scoped Ruff across team modules, modified controller/prompt modules, and both execution
  mode foundation/review regression files: **passed**.
- `git diff --check`: **passed**.

The new controller review suite covers cancellation/reply rewind, discarded replay,
approval recovery without member activation, and pending-exchange admission-slot reuse.
Foundation tests cover additive migration, revision monotonicity across ordinary updates,
restart and repeated rewind, plus canonical identity drift through HTTP approval.
Frontend regressions cover old terminal replay during consultation, stale summaries,
run/round rollback, streamed run advancement before rewind, and same-run/same-round
history replacement.

## Limits

These are deterministic regression and browser-layout results. The earlier bounded local
Ollama smoke timed out with provider/empty-response failures; successful real-model
finalization remains unverified. The user's running backend/extension was not reloaded.

The earlier full extension run had 15 unrelated runtime-configuration fixture failures
and backend mypy had existing errors outside the new modules, as detailed in
[the implementation evidence](2026-10-15-team-execution-modes-eval.md). Those unchanged
failures are not claimed resolved, and the entire repository test/typecheck set is not
claimed clean. The affected suites and frontend typechecks above are clean.
