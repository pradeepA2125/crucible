# Team execution modes: implementation plan

Date: 2026-10-15  
Status: Implemented on `codex/team-execution-modes` after independent review on 2026-10-16. Verification and live-smoke limitations are recorded in the companion eval.  
Design: `docs/superpowers/specs/2026-10-15-team-execution-modes-design.md`

## 1. Outcome and implementation boundaries

A user can request team deliberation that ends with the lead's final answer, choose the
full deliberate/implement/review workflow, or execute a supplied plan directly. Main can
talk to lead without waking peers and later explicitly start implementation on the same
team. Existing reports, milestones, rounds, ownership checks, and review machinery are
reused. An ordinary question never becomes execution authority.

This plan covers backend state, durable records, permissions, main/member tools, report
schemas, notices, HTTP/client contracts, host subscriptions, webview presentation, recovery,
and meaningful boundary tests. It does not add a new composer mode picker or direct
user-to-member chat interface. Implementation is authorized on `codex/team-execution-modes` after independent review.

## 2. Baseline and working-tree constraints

Investigation was read-only apart from these documents and normal test artifacts. Existing
uncommitted changes include agent transcripts, webview types/tests, provider observations,
README and landing work. Do not reset, overwrite, or bundle those changes accidentally.
Re-read `git status --short` before implementation and work against current file contents.
No subagents were used for this investigation.

The explored surfaces and current-behavior findings are enumerated in design §2. The
following baseline commands ran successfully on 2026-10-15:

```sh
# From services/agentd-py: 162 passed, 6.82 seconds
.venv/bin/pytest -o addopts='' tests/test_team_adoption.py tests/test_team_state_machine.py tests/test_team_coordinator.py tests/test_team_service.py tests/test_team_tools.py tests/test_team_scope.py tests/test_team_lead.py tests/test_team_round_visibility.py tests/test_team_results.py tests/test_team_implementation_controller.py tests/test_team_routes.py

# From repository root: 5 passed
npm run -w crucible-vscode-extension test -- test/team-views.test.ts

# From repository root: 66 passed
npm --prefix apps/vscode-extension/webview-ui test -- src/test/teams.test.ts src/test/teamJourney.test.ts src/test/teamChapters.test.ts src/test/teamWindow.test.tsx src/test/teamCard.test.tsx src/test/teamStrip.test.tsx

# From repository root: 11 passed
npm run -w @crucible/editor-client test -- test/team-contracts.test.ts test/team-activity-contracts.test.ts
```

Total: **244 existing tests passed**. This is a focused current-behavior baseline, not a
full-repository test claim and not evidence that the new behavior exists. New behavior
tests should use scripted model responses and fake hosts rather than paid model calls.

### Repository guidance to preserve

The first investigation read `CLAUDE.md`'s team/subagent guidance selectively. A subsequent
cross-check covered its general commands, build order, testing patterns, debugging rules,
rewind, gate/live-state invariants and document-writing history. Apply these concrete rules:

- Trace caller → callee → actual boundary values before asserting behavior; prompts and
  dynamic payloads are separate builders. Check recorded artifacts for live failures.
- Build `@crucible/editor-client` before extension typechecks; the extension consumes its
  compiled `dist` declarations. Build the extension and reload the development host before
  judging live UI behavior after TypeScript changes.
- Use scripted reasoning with real temporary files, SQLite and the patch engine for
  integration boundaries. Fake model/host behavior does not justify mocking filesystem
  or HTTP behavior in those integration checks. In-memory task-store references can mask
  stale-record bugs; use SQLite when testing those semantics.
- Use `pytest-asyncio`/`asyncio.run`, not `get_event_loop().run_until_complete`. Do not add
  CLI `-q` or pipe away pytest's exit status. Report skipped tests and missing tools honestly.
- Start a live test backend through `scripts/stress/start-backend.sh` in an isolated test
  workspace, with the project's normal authentication helpers. Do not start an ad-hoc
  unauthenticated uvicorn instance or disturb a running workspace backend.
- Interactive gates belong in `/live`; durable transcript versions are read-only.
  Every durable field consumed after the live dedup gate must participate in its signature.
- Preserve first-seen rewind snapshots and whole controller state; do not reconstruct
  rollback from truncated diffs or claim shell/external effects were undone.
- The removed `write_doc` tool and its gate/route must not return. The proposed result
  publication stores a team report; requested workspace-document writes continue through
  the normal edit machinery and policy.

## 3. Delivery order

| Task | Depends on | Deliverable |
|---|---|---|
| 1 | — | Durable workflow/run/result/activation fields and transactions |
| 2 | 1 | Pure mode-aware lifecycle and finalization transitions |
| 3 | 1–2 | Investigation and execution policy, including helpers and main PLAN |
| 4 | 1–3 | Lead finalization, accepted result and existing main delivery |
| 5 | 1–4 | Explicit direct implementation, same-team extension and narrowing |
| 6 | 1–4 | Main↔lead conversation with separate activation purpose |
| 7 | 2–6 | Model schemas, prompts, delegation/notice accounting |
| 8 | 1–7 | Client contracts and host event/subscription integration |
| 9 | 8 | Mode-aware cards, phases, result/exchange display and gate wording |
| 10 | 1–9 | Restart, stale-callback and rewind completeness |
| 11 | 1–10 | Integrated scenarios, live smoke and final documentation |

These are dependency boundaries, not instructions to enable incomplete production
behavior. Ship the public workflow only after the enforcement, transport, UI and recovery
paths agree. Reuse the existing teams feature flag; do not leave a half-enabled alternate
lifecycle under a different default.

## 4. Task 1 — Durable workflow and operation boundaries

Files:

- Modify `services/agentd-py/agentd/teams/models.py`, `store.py`.
- Modify `agentd/chat/models.py`, `storage.py` where activation/notice snapshots require it.
- Tests: `tests/test_team_store.py`, `test_team_activity_store.py`; new focused run/result
  transaction tests if the existing store suite becomes unwieldy.

Steps:

- [x] Define mode and deliverable types with the exact wire values in design §3. Add
  FINALIZING to explicit live-phase sets without relying on tuple slicing.
- [x] Add additive migrations for team mode/deliverable/run/budget-baseline/result/pending
  request/resume phase. Add persisted activation purpose/run/request correlation and the
  private delivery marker at the member/activation boundary.
- [x] Preserve nullable legacy leads and historical rows. Existing rows receive the
  compatibility mode; new tool requests require an explicit mode. Do not fabricate results.
- [x] Permit a passive `result` post; document payload schemas centrally rather than
  distributing untyped key names across coordinator, tools and UI.
- [x] Add transactional APIs for result acceptance, reply+pending-clear, and execution-run
  creation. Existing independently committing methods must not break these transactions.
- [x] Add durable notice idempotency support in the existing notice table/model, by reusing
  stable primary notice ids for new team logical events. Existing notices continue to work.
- [x] Define snapshot data needed to restore a preexisting team's later run. Record all
  new fields, members/assignments/cursors, post/activity boundaries and notice references.
  Full rewind integration is completed in Task 10; do not defer the storage shape to chance.

Acceptance:

- Old database migrates repeatedly without data loss. Read-only history works without lead.
- Transaction failure leaves neither a dangling result pointer nor a cleared unanswered
  request. Duplicate result/reply/notice acceptance is idempotent.
- Post and activity sequences remain independent and monotonic across ordinary new runs.
- Changing proposed ownership does not populate executable member assignments.

## 5. Task 2 — Mode-aware pure lifecycle

Files:

- Modify `agentd/teams/state_machine.py`, `coordinator.py`, `adoption.py` only where needed.
- Tests: `tests/test_team_state_machine.py`, `test_team_coordinator.py`, `test_team_adoption.py`.

Steps:

- [x] Carry mode/run/continuation policy in `TeamState`; replace the assignees→execution
  shortcut with selected recommendation→FINALIZING for deliberation workflows.
- [x] Add finalization events/actions and classify reports by expected activation purpose.
  Only the lead's valid finalization report can complete that phase.
- [x] Preserve the existing pure `apply(state, event) -> (state, actions)` seam. Keep file,
  database, scheduling and notice work in the coordinator/host.
- [x] On a deliberate result, end with `deliberation_complete` even when proposed owners
  exist. On combined actionable result, gate or implement. No-change/needs-decision ends
  successfully with a recommendation.
- [x] Support direct execution kickoff without a pretend round or unanimous adoption.
- [x] Restore FINALIZING through pause/resume/revival; include it in deadlines and lead-loss
  rules. Resume only the lead, not the full quorum, when finalization is owed.
- [x] Preserve bounded deadlock behavior and distinguish main-selected from unanimous
  recommendations. A material finalization revision returns to debate within limits.
- [x] Define public main feedback arriving during FINALIZING: invalidate the pending
  finalization continuation and process it at a safe boundary before any implementation.
  Private informational requests remain independent unless lead requests new deliberation.

Acceptance matrix:

- All mode × assignee-count × approval-policy combinations have deterministic outcomes.
- Deliberate cannot reach IMPLEMENTING through approval, override, resume or revival.
- Finalization failures/partial/forced output do not produce DONE.
- Late lifecycle/consultation reports cannot satisfy finalization or implementation.
- Existing implementation/review/fix cycles remain valid.

## 6. Task 3 — Enforce investigation and execution boundaries

Files:

- Modify `agentd/chat/controller.py`, `agentd/teams/scope.py`, `validation.py`.
- Add a small `agentd/teams/execution_policy.py` if needed to keep phase/purpose policy out
  of the growing controller. Keep it pure where possible.
- Modify `agentd/subagents/permissions.py`, `context.py` and `tools/sources.py`/`registry.py`
  only for the shared policy/execution guard seam; avoid globally changing lone agents.
- Tests: `tests/test_team_scope.py`, `test_team_implementation_controller.py`, existing
  subagent permission tests, and a focused team investigation-policy suite.

Steps:

- [x] Separate structurally valid proposed assignments from executable capability checks.
  Plan-only proposals may describe future work; implementation validation checks real owners.
- [x] Build each activation's allowed actions/tools from the original definition plus
  inherited restrictions, current team phase/mode and activation purpose.
- [x] Remove shell/MCP execution from deliberation, finalization and standalone consultation;
  retain approved read/search/retrieval and permitted web tools. Preserve verification
  command access in post-implementation REVIEWING.
- [x] Apply investigation restrictions to depth-2 helpers and guard their dispatch too.
  Helpers have team membership ancestry even without direct member tools.
- [x] Recheck run/mode/phase at mutation execution and edit acceptance boundaries, after
  approval/scheduler waits. Ensure remembered shell approvals/allow-all cannot bypass it.
- [x] Restrict `_main_team_rule` to executable ownership. Make main's actual loop PLAN
  phase reject executing creation/start, independently of the global plan-mode toggle.
- [x] Ensure a later authorized implementation restores a capable member's base edit/tool
  permissions while an intrinsically read-only definition remains read-only.

Acceptance:

- Scripted member/helper attempts to edit or run a writing command in planning do not change
  any workspace file. They are rejected even under shell allow-all.
- Proposed files do not reserve ownership or cause main to receive instructions to build.
- A queued old operation cannot land after narrowing, pause or disband.
- Existing implementation ownership/shared/protected-path checks and reviewer commands pass.

## 7. Task 4 — Lead finalization and delivery to main

Files:

- Add `agentd/teams/finalization.py` for validation/input/result construction as appropriate.
- Modify `teams/coordinator.py`, `service.py`, `report_fields.py`, `milestones.py`.
- Modify `chat/controller.py`, `chat/models.py`, `subagents/notices.py` only at existing
  report/notice integration seams.
- Extend `subagents/runtime.py`'s `ChildResult` and the corresponding durable activation
  record with structured team report metadata; plain report text alone cannot carry it.
- Tests: `tests/test_team_results.py`, `test_team_report_fields.py`, `test_team_milestones.py`,
  `test_team_coordinator.py`; new focused finalization tests.

Steps:

- [x] Build lead-only finalization input after the completed round, including current-run
  member reports and all relevant public posts/notes. Preserve member-DM privacy.
- [x] Clear active deliberation cutoff on phase exit. Do not let an old cutoff omit the
  reviewers whose reports triggered adoption.
- [x] Validate `final_result` metadata against phase/purpose/run/basis proposal. Full text
  is the existing report `summary`; bounds are explicit and never silently truncate.
- [x] Specify the material-revision response concretely as a team report with
  `status: completed`, `finalization_feedback: {reason: string}` and no `final_result`.
  `partial` pauses finalization. Only lead/finalization
  can use it. It transitions back to debate without accepting a result.
- [x] Persist accepted result and pointer atomically; attach underlying executable proposal
  and planned file fingerprints. Result acceptance never schedules peers.
- [x] Reuse wrap-up storage and main notice paths. Suppress redundant adoption wake during
  synthesis and emit one `recommendation_ready` with the accepted result/actual continuation.
- [x] Extend `team_report` with accepted result and run metadata while retaining existing
  members/proposals/objections. Mark old-run member reports explicitly or exclude them
  from current-run summaries. Consultation wrap-ups do not replace the accepted result.
- [x] Remove deliberate completion language telling main to carry out the plan. Keep
  post-implementation completion truthful about what was built and unresolved review.

Acceptance:

- Full result reaches main without `post_board`, polling, or a second reporting subsystem.
- Peers remain idle while lead synthesizes and after result publication.
- Small agree-notes are incorporated without superseding an unobjected candidate again.
- A capped member report cannot silently truncate the separate accepted result.
- Retry after commit emits neither a second accepted result nor a second completion wake.

## 8. Task 5 — Creation modes, explicit execution and narrowing

Files:

- Modify `teams/tools.py`, `validation.py`, `service.py`, `coordinator.py`.
- Modify `chat/controller.py` creation/reopen/revival/resume/gate/ownership helpers and
  `api/routes.py` existing gate decision handling where required.
- Tests: `tests/test_team_tools.py`, `test_team_controller.py`, `test_team_routes.py`,
  `test_team_implementation_controller.py`; focused mode/start tests.

Steps:

- [x] Require mode/deliverable at new creation; validate implement kickoff and concrete
  assignments before any row is written or member is scheduled.
- [x] Add `start_team_implementation` with design §7's exact run/result/capability/gate checks.
  Reuse the same roster and histories. Record an immutable main execution instruction linked
  to the selected result; do not change the original result to fit a new assignment mapping.
- [x] Compare planned-file fingerprints and existence before extension, then recheck exact
  instruction/run/snapshot at approval/start. Return actionable stale-plan paths.
- [x] Bind duplicate start requests to the source result and canonical instruction. Return
  the existing run for the same accepted start; reject conflicting/stale requests.
- [x] Keep DONE `post_board` reopening, with a fresh deliberate run and historical result
  retained. Distinguish it from FAILED same-run phase revival.
- [x] Add `set_team_mode` narrowing. Fence execution immediately, drain/stop members and
  helpers, clear obsolete gates, and only then start deliberation about the current state.
  Retain already applied changes and report interruptions accurately.
- [x] Fix run-relative budget caps without resetting lifetime request/token totals. Fresh
  deliberation/implementation runs get explicit default allowance; resume adds within cap.
- [x] Preserve user-only approval consumption. The gate references the finalized executable
  instruction/run; main messages and result acceptance cannot consume it.

Acceptance:

- The same team goes plan→DONE→explicit implementation with identical agent ids/history.
- A normal follow-up remains deliberate even after a previous combined/implementation run.
- Main in PLAN and main in an automatic notice cannot launch new execution authority.
- An all-read-only roster fails with a useful capability error rather than escalating.
- Approval feedback for direct implementation enters the specified revise-then-execute flow.
- Double-click/replayed tool calls schedule once. Narrowing and stale approvals cannot race.
- A reused team whose lifetime usage exceeds one run's cap can still get a valid new run.

## 9. Task 6 — Main↔lead directed conversation

Files:

- Add `teams/communication.py` for request/reply validation/correlation if useful.
- Modify `teams/tools.py`, `service.py`, `coordinator.py`, `chat/controller.py`.
- Modify supervisor/runtime/inbox integration only as needed to distinguish a private
  activation purpose and avoid concurrent activations of the same member.
- Tests: new `tests/test_team_communication.py`, existing round visibility/controller tests.

Steps:

- [x] Add `message_team_lead(team, text, expected_run_id, extra_budget?)` and lead-only
  `team_reply_main(text, request_seq?)`; reserve new author labels without breaking legacy
  label interpretation. Preserve ordinary member-to-member `team_message` behavior.
- [x] Add `cancel_team_lead_request(team, request_seq, expected_run_id)` for stopping a
  pending exchange without disbanding the completed result or cancelling lifecycle work.
- [x] Store directed requests/replies in the existing post timeline with explicit channel
  and reply reference. Persist one outstanding request and resolve it atomically on reply.
- [x] Route lead replies to existing main notices/inbox, with idempotency and bounded
  unsolicited questions. Do not route recipient `main` through member wake lookup.
- [x] Deliver a private request outside the public round cutoff without moving the ordinary
  board cursor over undelivered public posts. Use the separate private marker for dedup.
- [x] If lead is already active, deliver at its next safe step; do not start a second copy.
  If lead is idle, schedule a communication activation that cannot satisfy a round,
  assignment, review or finalization obligation accidentally.
- [x] Support bounded read-only DONE consultation while keeping result and lifecycle DONE.
  Surface exchange activity separately. Reuse remaining run allowance and admission caps.
- [x] Honor PAUSED/AWAITING_APPROVAL/FAILED/DISBANDED behavior from design §6. No bypass of
  pause, gate, provider limit or completed-result semantics for a private question.
- [x] Keep pending/retry/failed exchange correlation durable and expose actionable status;
  an unrelated lead report must not clear the pending request.

Acceptance:

- Main→lead wakes only lead; lead→main wakes only main. Saving or reading replies wakes none.
- Other members cannot read either side of a directed exchange.
- A private request between two held public posts does not skip either public post later.
- A lead reply during implementation cannot mark its unfinished assignment done.
- DONE consultation does not alter the accepted result or reopen an execution phase.
- Repeated pending requests/replies do not create wake loops or unbounded budgets.

## 10. Task 7 — Model schemas and orchestration guidance

Files:

- Modify `chat/controller_prompts.py`, `controller_loop.py`, relevant render context/reasoning
  schema callers only when needed; `teams/service.py` headers/status/briefs/tool descriptions.
- Tests: `tests/test_team_prompts.py`, `test_team_lead.py`, `test_prompt_goldens_teams.py`,
  `test_controller_delegated_hint.py`, existing strict/tight-schema tests.

Steps:

- [x] Add explicit schemas for `final_result` and `finalization_feedback` only on team reports.
  Keep lone-agent and non-team main response variants unchanged. Handle flat/oneOf/anyOf,
  all-fields-required and strict-provider object serialization with real property schemas.
- [x] Teach main the three workflows, requested deliverables and stopping points; include
  valid `lead`, `mode`, `deliverable` in every creation example.
- [x] Teach lead synthesis and direct reply, and members that assignments may be proposed.
  Remove unconditional “once adopted, implement” and “notes applied during implementation”.
- [x] Put mutable mode/run/purpose/continuation in the dynamic status tail. Do not rebuild
  a cache-stable system prefix each time team mode changes.
- [x] Update forced-final guidance for finalization/consultation so budget interruption
  cannot be described as successful accepted output.
- [x] Register new main/member tool names in correction and tool-link sets. Track start,
  narrow and private requests in delegation/notice bookkeeping with scoped dedup keys.
- [x] Permit `team_report` for content actually absent from a compact notice while avoiding
  redundant polling. Completion wording never encourages main to take over implementation.

Acceptance:

- Schema variants preserve structured metadata without encoding it as unvalidated prose.
- Model-free notice scenarios result in one useful user-facing answer, not repeated nudges.
- Main PLAN cannot evade scope through a team. All examples parse against actual tool schemas.
- Update team-specific goldens intentionally; explain any necessary other golden change.

## 11. Task 8 — Transport and host subscriptions

Files:

- Modify `apps/editor-client/src/contracts/task-contracts.ts`, `client/http-backend-client.ts`.
- Modify `apps/vscode-extension/src/team-views.ts`, `controller.ts`, and bridge code in
  `chat-panel.ts`/`extension.ts` only if signatures change.
- Backend summary/detail/live builders and `team_phase` events in `chat/controller.py`.
- Tests: editor-client `test/team-contracts.test.ts`, `team-activity-contracts.test.ts`;
  extension `test/team-views.test.ts`, relevant `controller.test.ts` cases.

Steps:

- [x] Add mode/deliverable/run/result-summary/exchange and pause-origin metadata to the
  appropriate wire/client schemas and mappings. Preserve conservative old-backend reads.
- [x] Send only stable compact metadata in `/live`; keep full result Markdown in detail
  and main completion delivery. Include new event fields through parser and host unions.
- [x] Add a host `noteState`/equivalent hook to rearm an open dormant team view on new run
  or consultation activity. Track whether a follow task is active rather than assuming
  any existing map entry is subscribed.
- [x] Support DONE backfill with active exchange and final backfill after exchange ends.
  Preserve independent seq/aseq cursors and avoid polling closed boards.
- [x] Ignore old-thread/old-generation async backfills. Reconcile list/detail/live ordering
  so old terminal data cannot overwrite a newly opened run.
- [x] Refresh summaries when result/exchange metadata changes; keep fields under the
  existing `lastLiveSignature.teams` dedup input.

Acceptance:

- Contract parsing preserves every new stable field, null/legacy values and directed posts.
- An open DONE board follows again after `post_board`, explicit execution or consultation.
- Reconnect/replay/backfill produces no duplicate post/result/reply/activity rows.
- Terminal streams get final backfill, but a prior terminal backfill cannot stop a newer run.

## 12. Task 9 — Webview mode, result and conversation presentation

Files:

- Modify `webview-ui/src/types.ts`, `hooks/useAppState.ts`, `teams.ts`, `teamJourney.ts`,
  `teamChapters.ts` and the existing team components enumerated in design §2.2.
- Add a small result component if needed instead of forcing final answers into proposal UI.
- Modify `components/messages/gates/TeamPlanGate.tsx` wording/metadata presentation.
- Tests: `src/test/teams.test.ts`, `teamJourney.test.ts`, `teamChapters.test.ts`,
  `teamCard.test.tsx`, `teamWindow.test.tsx`, `teamStrip.test.tsx`, and gate-specific tests.

Steps:

- [x] Mirror and merge new client metadata. Preserve previous-result versus current-run
  result identity and avoid replacing new data with a stale live/detail snapshot.
- [x] Replace the fixed phase list with mode-aware paths. Show FINALIZING accurately and
  PAUSED at its actual origin. Mark no-change execution stages skipped rather than complete.
- [x] Show mode/deliverable, meaningful completion labels and accepted result Markdown.
  Reuse report disclosure and file-reference rendering; do not show final results as
  proposals with pending stances.
- [x] Render directed exchanges with main/lead identities and existing message filters.
  Proposed ownership must not look like work already assigned for execution.
- [x] Distinguish lead finalization/consultation chapters and live activity. A completed
  result can coexist with “lead replying” in the pinned strip.
- [x] Update tool-pill navigation for existing `team_report` and new tools.
- [x] Rename gate action/purpose to implementation approval and show exact instruction/run.
  Keep existing decision retry/error handling and composer independence of team gates.

Acceptance:

- Each workflow displays only its real path, stopping point and result type.
- Long final answers and questions are readable on narrow panels; existing Markdown and
  file links work. Lead/main identities and phase labels remain accessible.
- Deliberation completion contains no false “implementation complete” or unused-stage ticks.
- DONE consultation shows communication activity without removing/replacing the result.

## 13. Task 10 — Recovery, stopping and rewind

Files:

- Modify `teams/store.py`, `coordinator.py`, `chat/controller.py`, `chat/storage.py`,
  `chat/rewind.py`, related supervisor/snapshot paths identified in Task 1.
- Tests: focused restart/rewind tests alongside existing team/controller and notice suites.

Steps:

- [x] Persist recoverable phase/purpose before side effects. Restore FINALIZING to lead-only
  synthesis after the existing FAILED reap/revive mechanism.
- [x] Mark DONE consultations interrupted on restart without failing the accepted result.
  Requests remain correlated for explicit retry; no execution is revived by a question.
- [x] Reject reports/events/gates from earlier runs or purposes after reopen, narrowing,
  disband, restart and rewind. Remove associated obsolete approvals and pending wakes.
- [x] Add team-run checkpoint snapshots for preexisting teams, not just teams created after
  a rewind point. Restore team/member fields and prune later posts/activity/notices before
  rearming subscriptions. Restore budget baselines and result pointers together.
- [x] Include active/pending consultation in stop-all, admission, provider-budget checks,
  and existing rewind active-work blockers. Define explicit cancellation of an unanswered
  pending consultation so it cannot permanently block a new user request or execution.
- [x] Preserve cumulative accounting and write/checkpoint guards; avoid inventing rollback
  of an already executed external command.

Acceptance:

- Restart at finalization, result commit, pending private reply and pre-execution gate has
  a deterministic, nonexecuting recovery path unless execution was already authorized.
- Rewind before team creation removes everything; rewind before a later run restores the
  earlier result/history and removes future execution authority.
- Interrupted/direct reply callbacks cannot complete another run or erase its result.
- A DONE consultation can be stopped/cancelled without disbanding its completed result.

## 14. Task 11 — Integrated validation and release evidence

Steps:

- [x] Run relevant backend `tests/test_team_*.py`, main delegation/notice/schema/permission,
  restart and rewind suites. Use the repository venv; do not rely on a system interpreter.
- [x] Run editor-client team contracts, extension host/controller subscription cases, and
  affected webview suites. Run workspace typechecks after client contracts/build artifacts
  are in sync. Do not overwrite unrelated generated assets.
- [x] Run a scripted end-to-end scenario through actual tool creation and event transport:
  deliberate plan with proposed assignments → lead-only finalization → one main result →
  DONE follow-up diagnosis → explicit implementation extension → gate → execute/review.
- [x] Run independent scenarios for direct implement, no-change, deadlock with dissent,
  private message during a held round, DONE consultation, narrowing, and restart/rewind.
- [x] Inspect notices and recorded activation purposes/counts, not just model prose. Verify
  that no unauthorized file changed and only intended agents were scheduled.
- [x] Attempt one bounded real-model smoke and perform UI visual inspection after scripted tests pass,
  using normal project/provider access policy. Real-model behavior supplements deterministic
  checks; it is not the only proof of mode enforcement.
- [x] Update `CLAUDE.md`'s team architecture notes and applicable user docs/README around final
  behavior. Do not edit historical superseded specs as if they had always contained it.
- [x] Record exact checks, results, remaining limitations, and any scope changes in an eval
  note under `docs/superpowers/evals/`. Review the final diff for unrelated working-tree work.

Suggested command groups at implementation time:

```sh
# From services/agentd-py; supplement with the actual affected notice/rewind tests
.venv/bin/pytest -o addopts='' tests/test_team_*.py tests/test_controller_delegated_hint.py tests/test_prompt_goldens_teams.py

# From repository root
npm run -w @crucible/editor-client test -- test/team-contracts.test.ts test/team-activity-contracts.test.ts
npm run -w @crucible/editor-client build
npm run -w crucible-vscode-extension test -- test/team-views.test.ts test/controller.test.ts
npm --prefix apps/vscode-extension/webview-ui test -- src/test/teams.test.ts src/test/teamJourney.test.ts src/test/teamChapters.test.ts src/test/teamWindow.test.tsx src/test/teamCard.test.tsx src/test/teamStrip.test.tsx
npm run typecheck
npm run build
```

Release acceptance is the design §12 scenario matrix plus the following concrete evidence:

| Evidence | Required proof |
|---|---|
| Plan-only boundary | Unanimous assigned proposal ends as a delivered result; zero implementation activations/mutations |
| Result routing | Existing notice path delivers substantive final text to main; zero peer wakes from publication |
| Private conversation | Request/reply references, private visibility, only intended activations, no cursor loss |
| Explicit extension | Same roster/context; separate authorized run; immutable source result and checked execution instruction |
| Lifecycle/UI agreement | Mode, phase, outcome, pauses and subscription state match after reconnect and reopen |
| Recovery | Pause/restart/rewind/duplicate callbacks cannot grant or retain stale execution authority |

## 15. Proposed choices to settle during design review

The implementation order is concrete, but the following choices deserve explicit review
before coding because they affect scope or user-visible capability:

- Are the three required wire modes and deliverable object the preferred tool interface?
- Is one outstanding main→lead request sufficient initially, including read-only DONE
  consultation, or should that consultation be a later increment?
- Are the proposed full-result limits appropriate for long plans?
- Accept the shell/MCP investigation restriction as the enforceable first version, or
  separately scope an isolated read-only runtime before promising command-based diagnosis.
- Confirm planned-file fingerprint refusal versus a later richer assumption/revalidation flow.

These are review points on the prepared proposal, not permission requests embedded in
runtime flows. Already authorized combined execution should proceed without an extra
main approval ceremony; deliberate-only work ends successfully with its result.

## 16. Independent review findings folded before implementation

The independent spec review identified four exchange contracts: reject DONE reopening
while a request is unresolved; provide `retry_request_seq`; preserve lifecycle obligations
for attached requests while dedicated replies bypass all coordinator report side effects;
and filter report evidence by run plus purpose. The independent plan review identified
four reliability/build corrections: persist result-delivery intent atomically; queue busy
lead lifecycle starts durably; create request+pending pointer atomically; and run the full
`npm run build` before UI smoke. All are accepted and incorporated in design §14.

Migration uses nullable legacy mode rather than manufacturing modern finalization steps
for historical teams. New tool creation still requires an explicit mode. Baseline rerun
on 2026-10-16: all 289 existing backend `test_team_*.py` tests passed before runtime edits.


## 17. Implementation reconciliation

Implementation and deterministic verification are complete. See
`../evals/2026-10-15-team-execution-modes-eval.md` for commands and results. The bounded
local-model smoke created the correct deliberate team but did not finish within four
minutes because of empty output and provider timeouts; no live finalization success is
claimed. Component browser inspection passed; the user's running extension host was not
reloaded.

Concrete storage choices: nullable legacy mode; run/phase/input in member queued-start
JSON; purpose/run in activation activity and report posts; logical result/reply identity
in the existing primary notice id. A separate nullable notice-key column and duplicate
activation-record fields were unnecessary. Runtime validation and the client schemas
share the wire values documented in the spec. Lifetime provider counters survive team
snapshot restoration, while run/budget authority returns to the checkpoint.

The scripted end-to-end controller case drives actual tool creation, result publication,
explicit extension, `/live`, HTTP approval, team-channel events, file edits and review.
Independent boundary cases cover ordinary follow-up, private exchanges, narrowing,
restart and rewind rather than requiring one model script to exercise every branch.


## 18. Post-implementation independent review

Root and three independent agents reviewed the implementation at `8bfe7aa` across backend
storage/coordinator, controller/tool recovery, and client/host/webview boundaries. Confirmed
findings were reproduced and fixed on the same branch. Evidence and final regression results:
`../evals/2026-10-16-team-execution-modes-review.md`.

The final implementation adds monotonic workflow revisions and a separate rewind history
generation to all workflow contracts. Rewind clears discarded replay, resets UI cursors and
rows, blocks restored pending exchanges for explicit retry, and puts restored live teams into
FAILED with their resumable phase preserved. Replies/cancellations now checkpoint resolution;
revival reuses an admission slot already held by a pending exchange. Material finalization
feedback explicitly uses completed reports, and canonical path changes refuse stale approval.
