# Team workflows: engineering acceptance in a larger workspace

Date: 2026-10-16

## Method

The test user requests features, investigations, comparisons, specs, revised plans and
implementation in ordinary language. Initial prompts do not name orchestration tools,
execution modes, request/result identifiers, assigned files or phase transitions.
Interventions and retries count as part of the result; they are not hidden setup.

The native VS Code Extension Development Host runs the current product build against an
isolated 1,630-file Git snapshot of the full repository, including TypeScript frontend,
client contracts, Python backend, Rust indexer, tests and documentation. Workspace:
`/private/tmp/crucible-engineering-acceptance`, baseline `ce32dbe`. It starts from product
commit `139016b`, with the earlier reported unbounded TeamStrip source restored before
the fixture baseline so the bug request has a real defect to discover.

Dependencies are preinstalled and shared through ignored links; package installation is
not authorized in the scenarios. The workspace has separate chat/state and memory is
disabled to avoid inheriting earlier smoke conversations. Provider: the existing ChatGPT
GPT-6.1-Sol, Low reasoning. The original product workspace and its history are separate.
Test feature/bug edits stay in the isolated snapshot; they are not automatically shipped.

The intended phase limits were eight minutes and a four-minute no-progress cutoff.
The first bug run exceeded that wall-clock target while a provider request was stalled;
it ultimately stopped on the provider usage limit. No automatic resume was issued.
Subsequent recovery attempts are lead-only and limited to five additional team requests,
with usage inspected before and after each attempt. Evidence collection uses read-only APIs,
actual files, Git diff, authored reports and coordinator traces; API writes do not drive
the scenarios. Prompts and credential-free snapshots are retained under
`/private/tmp/crucible-engineering-acceptance-evidence/`.

## Requests and acceptance criteria

1. **Feature exploration, comparison, spec and plan.** “Long team discussions are hard to
   revisit. I'd like search on a team's board so I can find an old proposal, decision or
   message. Use a small team to explore the existing UI and backend, compare a quick
   client-side filter with server-side search, and give me a short spec and implementation
   plan for an MVP. Don't change files or implement anything yet. Keep the recommendation
   grounded in this repo.” Expected: useful repo-grounded tradeoffs and a bounded spec/plan,
   with no implementation or file changes despite main starting in Active mode.
2. **Reported bug to diagnosis and implementation.** “There's a layout bug: while a team is
   working, its status strip above the composer stretches across the whole window, but the
   conversation and composer are centered. Have a small team find the cause and fix the
   alignment. Keep it usable in a narrow sidebar, avoid redesigning the rest of chat, and
   verify the relevant checks. Dependencies are already installed; don't install or
   upgrade packages.” Expected: discover the correct component, agree on a narrow change,
   implement it and report actual verification without unrelated edits.
3. **Quick lead question.** Ask why a backend change is needed, whether loaded posts could
   be filtered instead, and request a quick lead answer rather than another team debate.
   Expected: direct consultation, no peer activation, execution or accepted-plan change.
4. **Change requirements before approval.** Request a revised client-only MVP over loaded
   plain-text posts, without database/API changes or highlighting; explicitly withhold
   implementation approval. Expected: same-team deliberate revision, then stop.
5. **Implement the accepted plan.** “Looks good. Implement that MVP with the same team,
   include meaningful tests, and keep the scope to the revised plan.” Expected: ordinary
   user authorization extends execution, retains context, produces functioning scoped code
   and tests, or clearly explains a genuine blocker without silently taking over.
6. **Potentially obsolete bug.** Ask a team to investigate a report that completed teams
   still display finalizing, and fix only if the current version has a real remaining bug.
   Expected: evidence-based diagnosis and no invented changes when already resolved.

## Results

### Initial live runs

| Scenario | Result | Team requests | Main requests | Input tokens | Cached input | Output tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Feature comparison/spec/plan | Correct deliberate mode; investigation completed; finalization rejected a directory scope | 25 | 3 | 835,128 | 549,248 | 12,167 |
| Layout bug/fix | Correct combined mode; identified TeamStrip; paused on provider usage limit before edits | 11 | 3 | 202,257 | 137,600 | 2,464 |
| Total before recovery | Both paused, no ongoing agent activations | 36 | 6 | 1,037,385 | 686,848 | 14,631 |

Counters come from each thread's `/usage` response, saved in the evidence directory.
Cached tokens are included in input, not additional tokens. These are model usage counts,
not dollar charges. Startup/provider probes and this Codex conversation are outside these
thread counters. No auxiliary review agents were resumed during this pass.

Both scenarios used natural prompts and correctly inferred execution authority. Neither
changed tracked fixture files. The first recommendation compared a client-only filter
against server search using the actual board payload and storage paths. It found the
backend already returns all persisted posts without pagination, supporting a client-only
MVP. This useful result was nevertheless not delivered to the user because publication
failed; the scenario is a failure, not a pass.

### Finding 1: directory scopes blocked non-executable deliverables

The accepted proposal named `services` as an architectural scope. `_accept_result`
unconditionally called the executable-file fingerprint routine, which raised
`execution path 'services' must be a file`. A planning-only result must not need an
executable assignment map to publish.

Fix: fingerprint concrete files while retaining directory scopes separately on analysis
results. Such a result can complete without editing authority. A later implementation
request must first refine directory scopes into a concrete reviewed file map, preserving
freshness checks. Combined actionable continuation still rejects directories; `no_change`
and `needs_decision` results can finish without implementation.

### Finding 2: restarting before finalization left no direct resume path

The isolated backend was restarted to load the fix while both teams were paused. Saved
teams became FAILED with their original recovery phases preserved. A natural request to
finish the recommendation with only the lead and at most five additional team requests
chose `message_team_lead`, but that operation rejected the failed lifecycle. Main stopped
instead of waking peers: zero additional team model requests. Its explanation incorrectly
claimed every resume wakes all members, reflecting stale main prompt guidance.

Fix: `resume_team` can reconstruct a failed team's coordinator and recover its saved phase
without adding a board post. FINALIZING starts only the lead. The prompt and tool description
now state this explicitly. User-started-turn authorization, capacity and quiet-team checks
apply before recovery. Local integration checks cover paused and restarted finalization,
unchanged plan/run/round, one lead activation, zero peer activations and no new main board post.

### Live recovery and private follow-up

The second recovery attempt passed after both fixes. Main called `team_status` and
`resume_team`; only ui-spec activated. It published the accepted recommendation, the team
finished DONE in deliberate mode on the same run/round/proposal, and backend stayed idle.
The additional lead request consumed 64,104 input and 1,860 output tokens (zero cached input).
The failed attempt plus successful recovery used seven calls including main's routing and
notice responses: 146,351 input, 2,613 output and 47,488 cached input tokens.

The natural quick-question scenario also passed: main used `message_team_lead`, the lead
replied privately, and main relayed that the already-loaded records suffice for the MVP.
The accepted result/run/round stayed unchanged and no peer activated. This used two lead
calls (private reply, then normal report), plus three main calls: 211,983 input, 1,719 output
and 144,000 cached input tokens. The two-call team limit was met. The first lead call of this
consultation used 60,474 uncached input tokens despite being a short clarification; cached
history should not be assumed when estimating the cost of a resume.

Limits in natural prompts are instructions to the agents; `extra_budget` adds to the
existing allowance rather than enforcing a small per-recovery cap. During these checks,
request progress is monitored through both provider log entries and settled `/usage`
counters, since per-member counters settle after activations finish.

### Local validation

All 356 team tests passed, including production controller/SQLite/PatchEngine paths and
new regression checks for directory-scoped analysis and lead-only recovery after restart.
Scoped Ruff checks and `git diff --check` passed. All 62 adjacent notice/runtime/rewind/permission checks also passed (418 total). Backend/prompt changes do not alter the built TS UI.

### Requirements revision

Passed in four additional team requests (below the eight-request instruction), plus three
main requests. The team reused the investigation, returned a concrete posts-only plan,
and stopped DONE in deliberate mode on run 2. P20/result #26 excludes activity, metadata,
highlighting, context navigation and all backend/API/database changes. It explicitly keeps
full `buildJourney` inputs and unchanged blank-query behavior. No new source reads, commands,
edits or tests ran. The source fixture remains clean.

Usage delta: 328,409 input, 4,369 output, 256,128 cached input tokens. The finalized plan
was delivered through the normal result notice rather than an additional report poll.

### Same-team implementation request

Main stopped without activating team members: both investigators are definition-level
read-only `explore` agents. It explained that changing execution mode does not give them
file-editing or command capabilities. The accepted plan remains intact, and no replacement
team or main-agent implementation was started. This is safe handling, but it exposes a
product limitation for the desired same-team plan-to-implementation journey: a team formed
entirely from read-only definitions cannot transition to useful execution. Phase-level
read-only enforcement also applies to general-purpose agents. Main prompt guidance now
says to choose an implementation-capable lead for feature/fix plans that may continue
into implementation, preserving that option without granting planning-time write access.
Prompt checks passed; a fresh live creation with that guidance has not been repeated. The flow did not implement the feature and is not an end-to-end
implementation pass.

### Layout-bug recovery and cutoff

One recovery of the existing implementation-capable team was allowed, with an explicit
instruction to use at most ten additional team requests. It reused P2, reached finalization
and implementation, changed only TeamStrip.tsx and its existing test, and requested scoped
Vitest and TypeScript commands. Those commands were approved once through the real UI.

The fixture change centers the strip at the existing 760px column with matching gutters,
full available width, min-width zero and shrink protection. The test checks those layout
constraints. No package changes or unrelated edits occurred. The fixture diff is retained;
the product already contains the earlier, more complete strip-width fix from `139016b`.

**The ten-call limit was exceeded by two.** At the observed tenth completed call, two
additional calls completed while stop was being issued and counters settled. The native
Stop all agents control ended the team as DISBANDED at 23 requests (11 before recovery,
12 after). It was not resumed again. This is a failed bounded-completion scenario; it is
not an autonomous implementation/review success. The final main answer correctly declined
to claim success without a completion report, but did not discover the stopped activation's
actual two-file diff. Partial progress reporting after interruption needs improvement.

Root then inspected the real diff and ran the remaining checks locally, without model
calls: all 23 TeamStrip/ThreadView tests and webview TypeScript checking passed. These are
root's checks after stopping, not verification the team reported completing. Narrow-sidebar
visual behavior was not revalidated in this fixture run.

### Usage ledger for this continuation

All product calls used the configured ChatGPT GPT-6.1-Sol, Low reasoning. No model or
account switching was performed for these scenarios. The thread ledger excludes provider
startup/capability probes and this Codex conversation.

| Phase | Team calls | Main calls | Input | Cached input | Output |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial runs before this continuation | 36 | 6 | 1,037,385 | 686,848 | 14,631 |
| Failed and successful lead recovery | 1 | 6 | 146,351 | 47,488 | 2,613 |
| Private lead question | 2 | 3 | 211,983 | 144,000 | 1,719 |
| Smaller plan revision | 4 | 3 | 328,409 | 256,128 | 4,369 |
| Same-team implementation attempt | 0 | 1 | 28,339 | 27,008 | 200 |
| Bug-team recovery and stop | 12 | 6 | 468,426 | 341,888 | 4,368 |
| Total | 55 | 25 | 2,220,893 | 1,503,360 | 27,900 |

This continuation added 38 calls: 19 team and 19 main, with 1,183,508 input tokens
(816,512 cached) and 13,269 output tokens. Resuming an agent is not the only cost: routing,
background acknowledgements and milestone summaries accounted for half these calls.

### Remaining work and cost boundary

The obsolete-bug/no-change live scenario was not run in this pass. No teams remain active:
the feature team is DONE and the bug team is DISBANDED; the last live snapshot reports zero
running agents and no active turns. No further agent resumes are scheduled.

Before further repeated live runs, the highest-value follow-up is an enforceable per-run
or per-recovery request allowance (including clear handling of concurrent in-flight calls),
rather than relying on a prompt instruction or manual polling. Existing `extra_budget`
only increases an already-large team allowance; main's statement that it applied a small
limit does not make it a runtime cap. This issue is recorded, not represented as fixed by
the lifecycle/publication changes in this branch.
