# Team execution modes: live VS Code smoke tests

Date: 2026-10-16

Branch: `codex/team-execution-modes`, starting at `0ebb5fd`.

## Environment and method

Native VS Code UI was driven through computer use. The installed extension frontend
predated this branch, so testing used an Extension Development Host with the current
`apps/vscode-extension` build and editable backend source. The disposable workspace is
`/private/tmp/team-modes-ui-smoke`; product repository files were not assigned to teams.
The configured provider was ChatGPT `gpt-6.1-sol`, Low reasoning, with teams enabled.
The workspace stayed in Restricted Mode, Accept edits; verification commands were
allowed once through their visible command gates. No broader command permission was saved.

The original workspace had no active teams/exchanges before testing. It was not reloaded.
The isolated host used backend ports 65368, 49590 and 50072 across reloads between
completed/stopped scenarios. UI observations were cross-checked against the fixture file and
backend logs. A credential-free API snapshot of the completed first scenario is under
`/private/tmp/team-modes-ui-evidence/a-deliberating-chat-f8eccc8a745e.json`.

## Scenario results

| Scenario | Observed result |
|---|---|
| A: deliberate-only executable plan, main in Plan Mode | Two members inspected `greeting.py`, proposed P2, agreed in round 2, then only lead finalized. UI showed Plan ready and main received the final plan. Team `team-43e992894a85`, run 1, result #10. Fixture remained `return f"Hello, {name}"`; no execution occurred. |
| B: private question after completion | Main used `message_team_lead`; lead replied directly. UI showed the reply notice and main's answer. Accepted result/mode remained unchanged. Logs showed only lead reactivated; reviewer stayed at its previous activation. No edit or public deliberation restart. |
| C: explicit implementation extension | Main used `start_team_implementation` on the existing result #10/run 1. Same team entered run 2, Implementing, then Review and DONE. Lead changed only `greeting.py` to `return f"Hello, {name}!"`. Two visible command gates ran the Ada/empty-name assertions, each allowed once. Reviewer agreed. Main received the normal completion notice. |
| Reload completed team after fix | After build/reload and reopening the scenario, the completed card showed Implementation reviewed, `team is done`, and 23 posts. Its prior stale finalizing/reviewer message and counts were replaced. |
| D: combined mode, no change needed (fresh post-fix run) | Two members inspected the now-correct greeting. Lead finalized a diagnosis with `no_change`. The card showed Diagnosis ready, Implementation and review skipped, `team is done`, and 9 posts. No execution or additional edit occurred. The completed card refreshed correctly without reload. |
| E: direct implementation with team approval | Team `team-9bfac23c4c3b` submitted supplied P1 without deliberation. The UI showed Execute this plan / Approve implementation. `direct-smoke.txt` was absent before approval, then created after the click. Team implemented/reviewed, and main reported completion. Direct byte inspection confirmed `b'DIRECT_OK\n'`; README, greeting and fixture settings stayed unchanged. The reviewer disclosed that its own scope check relied partly on the lead's hash inventory; main preserved that limitation. |
| G: discussion-only follow-up on completed implementation | `post_board` preserved the existing direct-smoke roster and opened a new deliberate run. Lead and reviewer compared terminal-newline requirements and recommended no change. Main remained in Plan Mode; there were no new commands or edits. Final notice wording exposed the second finding below. |
| G, repeated after notice fix | Another `post_board` discussion kept deliberate mode and the original roster. Lead finalized after reviewer agreement; the live UI now displayed `Team 'direct-smoke': result ready — main agent woke`. The exact `DIRECT_OK\n` artifact stayed unchanged. |
| F, first attempt: actionable combined mode | `team-381bcf7ca226` proposed the correct single-file assignment, but reviewer stalled after its initial missing-file read. After four minutes without further response, Stop all agents disbanded the team and stopped the outstanding activation. `combined-smoke.txt` remained absent. The UI delivered a disband notice and main correctly declined to claim completion. This attempt is not counted as a pass. |
| F, clean retry | Team `team-f70fca77811e` completed deliberation, lead finalization, automatic implementation and review. UI showed a plan-ready notice while implementation continued, followed by the reviewed-completion notice and main's answer. Its coordinator trace records ReviewEvaluated followed by DONE/reviewed. Direct byte inspection confirms `b'COMBINED_OK\n'`. No shell commands were requested. |

## Findings and corrections

### Completed cards retained stale live-only facts

Before the fix, the first team card's stepper correctly showed Plan ready while its
latest line still said `team is finalizing its recommendation` and displayed 8 posts;
the completed board held 10 posts. After implementation, the latest line retained a
reviewer's report and 20 posts although the completed history held 23.

Cause: `/live` stops listing completed teams. The terminal summary/detail refresh
omitted `latest`, `counts` and `round_progress`, and client summary parsing omitted them
as well. Frontend merges consequently retained the last live values.

Correction: summary/detail and live reads now share backend presentation aggregation;
client summary/detail contracts and mapping preserve those fields, including explicit
null round progress. Added real HTTP terminal-read, client mapping and webview reducer
regressions. Rebuilt and reloaded the isolated host and verified the completed card in
the actual UI. The fresh no-change scenario also verified the fix without a reload.

### Discussion follow-up announced “implementation ready”

An ordinary follow-up correctly changed a completed implementation team to deliberate
mode, but retained its `implementation` deliverable kind. The result notice consequently
said `implementation ready` while the UI's result label was generic and no execution was
authorized. Corrected that specific notice case to `result ready`, preserving the stored
deliverable metadata and explicit no-implementation guidance. Added a milestone regression.
Reloaded the isolated backend and repeated the discussion: the live notice now said
`result ready`, main presented the comparison, and the fixture was unchanged.

## Validation

- 530 backend team/notice/subagent/tool/rewind/prompt regressions passed (22.98s).
  The narrower route/foundation/review/workflow group also passed all 46 tests.
- All 167 editor-client tests passed, including 10 team contract tests.
- 61 affected webview state/team tests passed.
- 103 extension controller/team-view tests passed.
- Full build and frontend typechecks passed.
- Scoped Ruff and diff whitespace checks passed.

## Observations and limits

VS Code displayed `Agent process failed to restart after 5 consecutive crashes` during
the private-question scenario and again during direct implementation. A later notification
identified its source as Gemini Code Assist. Crucible remained healthy: the reply completed, the next
implementation completed, and the isolated backend log contained no corresponding
exception. The unrelated Gemini notification is not claimed fixed.

One later API evidence query was not executed because automatic approval review hit an
account usage limit. The delegated fix agent also stopped on that usage limit after
editing the production paths; the main agent completed the regressions and validation.
UI operation and workspace checks continued. Unrelated full-suite failures from the
earlier implementation/review evidence are not claimed resolved.

The first actionable combined run stalled in a single activation after a tool result;
other scenarios continued successfully. No exception identified the cause before the
bounded stop. The clean retry passed. This isolated stall is recorded as an unresolved
reliability observation rather than attributed to the team state machine or claimed fixed.

At the end, all five smoke-test teams had ended: four DONE and the stalled first combined
attempt DISBANDED. Main's composer was available and Plan Mode was off, matching its
initial setting. The isolated host and fixture history remain available for inspection.
Credential-free final fixture contents and terminal trace references are saved in
`/private/tmp/team-modes-ui-evidence/final-summary.json`. No smoke fixture is part of the
product commit, and the unrelated repository `.vscode/` files were left untouched.
