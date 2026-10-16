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
4. **Obsolete bug / no-change triage:** pending after the one-call cap test. Provider usage
   limits were reached during acceptance. The team was not resumed to repeat investigation;
   this remains an unverified live scenario rather than a pass.
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
The remaining live no-change triage should run only in a later explicitly bounded session.
