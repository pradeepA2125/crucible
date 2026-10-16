# Lead document writing: live smoke acceptance

Passed against commit `7474400` on 2026-10-16, using the native VS Code extension
development host and isolated workspace `/private/tmp/crucible-engineering-acceptance`.
The idle acceptance backend was restarted before creation to load the permission change.

## Scenario

Main created `plan-write-smoke` (`team-647ac6266b46`) in chat
`chat-9136cac2e664`. Both members, `lead` and `reviewer`, used the built-in `explore`
definition. Configuration: deliberate mode, plan deliverable, shared request budget 12,
maximum two rounds, no approval gate, no executable assignments.

The task was to agree a supplied three-step future README welcome-sentence plan.
The original goal, deliverable description and kickoff required the lead to save it as
`docs/team-finalization-smoke-plan.md` during FINALIZING using ordinary edits. No source
investigation, commands or README implementation were requested. Main was instructed to
read the document after acceptance, without writing it or sending adoption reminders.

## Observed result

- Both members agreed with P1 during round 1 and changed no files in deliberation.
- The team transitioned DELIBERATING → FINALIZING → DONE, with
  `end_reason=deliberation_complete`, run 1, accepted result sequence 8.
- Only the lead activated during finalization. Its finalization report records an ordinary
  edit applied and promoted to the requested path; activity records that single changed file.
- Root independently read the resulting file from disk. It contains the agreed three steps,
  P1 attribution, and an explicit future-work-only scope.
- Main's persisted tool trace contains a successful `read_file` of the saved path after
  the result milestone. Its final UI answer confirms the readback and team usage.
- Root compared README SHA-256 before and after: unchanged.
- No implementation assignments, commands, budget increases, resumes, replacement teams
  or post-adoption reminders occurred. The team is DONE and requires no further activation.

## Recorded cost

Configured product model: GPT-6.1-Sol, Low reasoning. Request counts include normal main
acknowledgement and result readback; this Codex conversation is outside the product ledger.

| Owner | Requests | Input tokens | Cached input | Output tokens |
| --- | ---: | ---: | ---: | ---: |
| Lead | 3 | 36,157 | 12,416 | 893 |
| Reviewer | 1 | 9,039 | 0 | 240 |
| Team total | 4 / 12 allowed | 45,196 | 12,416 | 1,133 |
| Main | 4 | 110,968 | 81,024 | 816 |
| Chat total | 8 | 156,164 | 93,440 | 1,949 |

## Evidence and limits

Local raw snapshots are retained under
`/private/tmp/crucible-engineering-acceptance-evidence/`, including
`plan-write-baseline.json` and `plan-write-final-chat-9136cac2e664.json`.
The generated plan remains in the isolated acceptance workspace.

This smoke verifies a newly created team with an explore lead saving one plan through
ordinary edits during finalization, followed by main's readback. It does not live-test
editing an existing document, saving multiple documents, rejection/recovery, or finalization
of a team created before the update. Local regression coverage separately passed 451 tests,
including real patch writes for general-purpose/explore leads, protected-file refusal,
lead/helper/purpose boundaries and refusal of acceptance after a pause.
