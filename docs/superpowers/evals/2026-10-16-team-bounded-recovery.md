# Bounded team recovery and confirmed member upgrades

Date: 2026-10-16. Branch: `codex/team-execution-modes`.

This follows the engineering acceptance report from earlier today. The user requested
runtime request caps, user-confirmed upgrades of read-only team members, useful reporting
after interruption, and completion of the remaining live acceptance scenarios.

## Implementation

- Admission is checked synchronously at the rate-limited transport boundary, after waiting
  for provider capacity and immediately before the shared usage meter reserves the call.
  Concurrent members and descendants share the same persisted-plus-live team allowance.
  A denied call never reaches the provider. Limits count logical model calls, including
  recall/compaction and finalization; provider transport retries follow the existing logical
  request accounting. This is a request cap, not a token or monetary cap.
- `budget` bounds creation. `request_limit` on board follow-ups, resumes, private lead
  requests and implementation starts bounds further admissions within the existing/fresh
  allowance. It does not merely add to the old budget. Start retries preserve the original
  cap. Exhaustion records a deterministic partial report without buying a final model call.
- `request_team_member_upgrade` creates a user confirmation card for selected members.
  It displays the definition and permission change, retains inherited workspace/command
  restrictions, and revalidates the accepted result, run, quiet state and effective
  capabilities when clicked. Rejection or stale cards cannot upgrade members. Approval
  changes no plan, starts no agents and does not authorize execution.
- Approved definitions are overrides on the existing team membership. Original agent
  snapshots and identities remain intact; existing team snapshots rewind the override.
  The next activation builds its effective context from the approved definition.
- Wrap-up records now attribute files to the activation's write-log sequence rather than
  lifetime agent edits. Paused/ended notices and `team_report` include current-run partial
  files, member status and recorded command outcomes, including stopped activations.
  Unfinished commands are explicitly distinguished from finished results.

## Independent review and checks

A fresh scoped reviewer inspected the admission, upgrade and snapshot seams without live
model/team calls. Its compaction finding was folded: cap exceptions must escape both
MemoryHarness compaction error paths rather than become generic failures. The additional
compaction regression passes.

487 backend tests passed across team, notice, subagent, permissions, rewind, write-log,
agent-loop and memory-harness paths. Two additional regressions passed for inherited
read-only upgrade rejection and invalid/follow-up limit persistence (489 distinct backend
tests total). 71 affected UI tests passed, including the permission
confirmation card. Full build, scoped Ruff and webview TypeScript checking passed.

## Live acceptance

The native VS Code Extension Development Host uses the same isolated full repository:
`/private/tmp/crucible-engineering-acceptance`. Current build loaded by Reload Window;
backend port 54762 after the final restart. Provider remains ChatGPT GPT-6.1-Sol, Low. Requests and decisions are
driven through native UI; API reads only collect evidence. No account/model switch.
Evidence: `/private/tmp/crucible-engineering-acceptance-evidence/`.

Baseline before these checks: feature thread 48 total calls / team 32; layout thread 32
calls / team 23. The old layout team is disbanded; the feature plan remains completed.

1. **Exact cap with two members:** passed. Natural read-only investigation with one total
   team model request paused at 1/1. No additional wrap-up model call, edits or commands.
   Main described the diagnosis as undetermined and did not resume or add budget. Team:
   `team-9854a6885110`, thread `chat-6be136a7b9a6`.
2. **Confirmed upgrade of the existing read-only lead:** passed. Requesting the card used
   no team calls and left both definitions unchanged. Clicking it changed only ui-spec
   from explore to general-purpose; backend stayed explore. IDs, run2, round4, result26
   and the existing board/history stayed intact, with team requests still32. Upgrade
   request/approval used three main calls. Implementation did not start automatically.
3. **Accepted plan to bounded implementation and review:** implementation ran under
   request_limit16, absolute budget48 from baseline32. The approved lead changed only
   Journey.tsx and teamWindow.test.tsx. Targeted21 tests and typechecking passed. The
   read-only reviewer approved the source/test scope. Full-suite Chrome-launch timeouts
   were honestly reported. All15 affected browser tests passed when rerun locally; a full
   local-suite rerun passed: 1,139 passed, one benchmark skipped. Formal closing review paused at exactly48/48
   (16 further team calls), with no paid final report or automatic budget increase.
   A completion-before-wait registration race caused main to post a recovery message under
   the existing cap. That race now has a coordinator fix and three regression cases:
   reconcile newer current-run completion, exclude prior-run reports and later questions.
   An additional manual request to consume the remaining call arrived after the team had
   already paused and did not spend another team call. After restart, main accurately
   reported the saved IMPLEMENTING recovery phase and declined a closing-review-only
   instruction. Explicitly authorizing recovery of the completed assignments with FOUR
   further calls entered closing review, used only TWO calls and reached DONE at50/52.
   No edits/tests were repeated. Main clearly attributed the local green rerun separately
   from the team's earlier timeouts. Final DONE and completion notice were inspected in
   the native UI without further model calls.
4. **Obsolete bug / no-change triage:** initially pending after the one-call cap test;
   subsequently passed in the authorized continuation below. Provider usage
   limits were reached during acceptance. The team was not resumed to repeat investigation;
   at that checkpoint this was an unverified live scenario rather than a pass.
5. **Wide/narrow status strip visual inspection:** passed in the native editor. The
   working strip shares the centered composer column in a wide window. Tiling the window
   to its top-left quarter narrowed the editor to roughly375 CSS pixels: name/status
   truncated while avatars and Open board remained within the column, without overflow.
   The original window size was restored. Completed-phase inspection passed: the finished
   chat displays DONE and the working strip disappears, matching its terminal-team filter.

## Usage boundary and remaining work

Final local ledger snapshot: `cost-stop-*.json`. No running acceptance teams remain.

| Thread | Total logical calls | Team calls | Input tokens | Cached input | Output tokens |
|---|---:|---:|---:|---:|---:|
| Feature/spec/revision/upgrade/implementation | 82 | 50 | 3,539,889 | 2,700,032 | 31,292 |
| Layout bug | 32 | 23 | 670,683 | 479,488 | 6,832 |
| One-call diagnostic cap | 3 | 1 | 59,885 | 24,832 | 938 |
| Total | 117 | 74 | 4,270,457 | 3,204,352 | 39,062 |

This acceptance pass added 37 calls to the earlier 80-call baseline: 19 team and 18 main calls.
Repeated lead/main context replay was expensive even when team request counts were small.
Cached input is included in input, not additional tokens. These counters exclude this
Codex conversation and its independent code-review agent; they are not monetary estimates.
After the latest cost warning, no product model calls or agent resumes were made. Remaining
verification used saved evidence, native UI inspection and local deterministic tests.

The fixture search implementation's full suite is green, but its search controls were not
separately exercised in the live host: that host still loads the repository's webview bundle.
This is an acceptance-fixture feature, not part of the production team-mode change.
The following authorized continuation resolves these two outstanding live checks.

## Final two live checks — authorized continuation

The user subsequently authorized finishing the two outstanding checks. Baseline remained
117 product calls; all teams were idle. No account/model switch or new team was used.

**Search controls: passed.** Temporarily loaded the acceptance fixture's built webview
into the Extension Development Host, retaining a backup of the original generated bundle.
On the completed feature team's real 47-post board, native UI checks confirmed:

- `  POST.TEXT.TOLOWERCASE().INCLUDES(Q)  ` returned exactly six matching posts, matching
  the retained post corpus (sequences 19, 20, 21, 25, 26, 28). Case folding, trimming and
  punctuation behaved as literal text matching.
- The matches remained available with Posts and Messages toggled off.
- An absent term returned zero and “No matching posts.” Clear restored the normal board
  with both filters still off. A whitespace-only query retained normal board rendering.
- `  P5  ` returned the expected eleven older retained posts. Visual inspection confirmed
  the labelled input, Clear, scope explanation, count and full plain-text results.

No model requests were needed. Restored the original generated bundle and reopened chat
before the diagnosis run; fixture search source is still separate from production changes.

**Recovery finding and fix.** The first authorized triage recovery used four of ten
further team calls (5/11 cumulative) and reached DONE with an inconclusive result. Members
performed no reads because their original goal contained a one-request/no-resume
instruction at higher prompt priority than the framed follow-up. Main correctly reported
the lack of investigation and distinguished runtime usage from members' incorrect
activation-based claim of exceeding the cap. This was not a no-change diagnosis pass.

Member briefs now identify the current persisted request ceiling and latest visible
main-authored public request, explain how dispatcher task revisions supersede conflicting
historical task instructions, and retain content framing and runtime permission/mode
boundaries. Member claims cannot impersonate main; private messages and future-round
requests remain excluded. The brief changes on task/allowance revision, while changing
member status remains in the payload tail. Regression plus team/notice/prompt tests:
493 passed; scoped Ruff and diff checks passed. The backend was restarted only while idle.

The six-call retry gathered the requested UI/backend source evidence and lifecycle's agree
stance on P14, then paused at 11/11 before final publication. A three-call publication
recovery produced the lead's complete no-change report (#21), but main posted a board
reminder after adoption (#20), which intentionally invalidated acceptance and closed P14
for reconsideration. The next four-call recovery was also insufficient: keeping the test
instruction “do not supersede P14” prevented the lead from replacing that now-closed
proposal. These were recorded as unsuccessful closeouts, not passes. The main prompt now
explains both hazards. Those additional allowances were explicitly initiated by the test
driver; the product main did not autonomously add budget after any cap.

The test driver corrected the closed-proposal restriction and allowed a final six-call
publication attempt, sending the correction before resume and forbidding further board
reminders after adoption. No further source investigation or execution was authorized.

**No-change diagnosis: passed after correction.** The lead published replacement P30 from
the existing evidence. At the round-limit deadlock, main adopted it under the test driver's
explicit authorization, then sent no further reminder. Lead-only finalization published
accepted result #34 with `outcome=no_change`; the team reached DONE in deliberate mode at
20/24 cumulative team requests, using only two of that last six-call allowance. Member IDs
and history were preserved. There were no executable assignments, edits, commands, package
operations or implementation transitions. Native UI showed “diagnosis ready,” DONE and the
no-change result, and the working strip disappeared.

The accepted diagnosis is deliberately scoped: `teams.ts:181–183` maps FINALIZING to
“Finalizing recommendation” and DONE to completion labels; the inspected fixture's
`coordinator.py:223–246` persists accepted-result phase/completion metadata consistently.
Completed members must not be confused with a completed team. Stale snapshots, other
render/transition paths and a runtime reproduction of the alleged bug were not established;
they remain limits on the finding, not demonstrated defects. No implementation plan was
needed or produced for this result.

**Final usage and verification.** Evidence: `acceptance-complete-*.json` and
`final-two-assertions.json`; backend port 55862. This continuation added 36 product model
calls: 19 team and 17 main; 951,204 input tokens (707,200 cached) and 12,353 output tokens.
The complete three-thread ledger is 153 calls: 93 team and 60 main; 5,221,661 input tokens
(3,911,552 cached) and 51,415 output tokens. It excludes this Codex conversation. These
counts include all unsuccessful closeouts; no token or monetary cap is claimed. The
allowance was increased explicitly by the driver for closeout, not automatically by main.

493 focused backend tests passed after the dispatcher fix;68 affected workflow/prompt/
service tests and 137 prompt/leak tests passed after the closeout guidance updates. Scoped
Ruff and diff checks passed. The original generated webview bundle was restored and its
file hashes verified. Final saved evidence asserts current-run DONE/no_change, zero active
agents and turns, and no executable assignments. Both previously listed live checks are
resolved; no further acceptance work or agent resumes are scheduled.
