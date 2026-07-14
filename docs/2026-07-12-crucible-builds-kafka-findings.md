# Findings: Crucible builds a mini-Kafka clone (dogfood campaign)

**Date:** 2026-07-12 (session start) — in progress
**Setup:** Fresh workspace `workspaces/kafka-clone`, agentd on TurboQuant (`qwen3.6:35b-a3b-q4_K_M`),
`CRUCIBLE_CHAT_CONTROLLER=1`, task subsystem off (controller-only inline EDIT path). Driven
end-to-end through the real VS Code webview UI via raw CDP (see `/tmp/cdp.js`, a second isolated
VS Code instance on `--remote-debugging-port=9335` with `--user-data-dir=/tmp/ccdp`), not the
HTTP API directly — findings reflect what a real user driving the extension would hit.

**Goal being built:** Phase 1 of a Kafka-style commit log in Go — single-partition, segmented,
CRC-framed, crash-recoverable — as a scale/systems stressor for the whole stack (planning vs.
controller-edit routing, todo ledger, exec sessions, memory compaction, long-running turns).

**Status:** Campaign concluded as a smoke test — it did what it was for. Phase 1 itself did not
reach a green `go test` (stalled in a fix/retry loop on two test-logic bugs the model kept
misdiagnosing as implementation bugs; root cause was correctly identified via manual analysis
mid-campaign but the model didn't converge on it before the session pivoted to fixing the product
bugs the campaign had already surfaced). The Kafka clone itself was not the deliverable — the bug
list below was. Fix status for each finding is recorded inline.

---

## Findings (severity-ordered)

### 1. [HIGH] SSE stream silently dies on long turns; client has no reconnect/heartbeat
After ~60 minutes of continuous turn activity, the chat webview's SSE stream stopped updating —
new tool-call pills and edit cards stopped rendering, while the `/live` 1s poll kept the
spinner/todo card fresh (which made the staleness easy to miss). The turn was still running
server-side the whole time (`agentd.log` kept advancing, iteration count kept climbing). Backend
history was never lost — clicking "Back to history" and re-entering the thread re-subscribed and
backfilled everything correctly.
- **Root cause (not yet located in code, inferred from behavior):** the client's EventSource/SSE
  connection has no heartbeat detection or auto-resubscribe; once it silently drops, nothing
  notices until the user manually navigates away and back.
- **Impact:** on any turn long enough to matter (which weak local models produce routinely — this
  one ran 80+ iterations), a user watching the live view will believe the agent is frozen and may
  intervene unnecessarily, when it's actually working fine.
- **Fix direction:** SSE heartbeat + client-side auto-resubscribe on silence (mirrors the durable
  `/live`-poll-is-the-backstop pattern CLAUDE.md already documents for other signals — this is
  the same class of bug as the `lastLiveSignature` dedup-swallowing incidents).
- **FIXED (2026-07-13):** the auto-resubscribe machinery already existed
  (`controller.ts::resumeLiveOverlay`, triggered by the `/live` poll) but was permanently gated
  off by `this.turnAbort === null` — a condition that could never become true because the dead
  `reader.read()` promise in `HttpBackendClient` hung forever with no timeout, no error, no
  resolution. Root cause confirmed by reading the actual reader loop in
  `apps/editor-client/src/client/http-backend-client.ts` (`sendChatMessage`, `postModeDecision`,
  `postClarifyDecision`, `streamChannel` all had the identical unbounded-`await reader.read()`
  loop — a pre-existing 4x code duplication). Fix: extracted a shared
  `consumeChatEventStream()` generator with a `readWithIdleTimeout()` wrapper
  (`SSE_IDLE_TIMEOUT_MS = 120_000`) that resolves `{done:true}` — not a rejection — after 2
  minutes of silence, so a stalled stream looks exactly like a graceful end and the existing
  resume path takes over on the next poll tick. 2 minutes was chosen with margin above the
  longest observed single-iteration gap on TQP (~28s prompt eval) while recovering in a small
  fraction of the ~60-minute stall actually observed. Regression tests added
  (`apps/editor-client/test/http-backend-client.test.ts`, fake-timer-driven, a `ReadableStream`
  that never enqueues/closes) for `sendChatMessage` and `streamChannel`. All four call sites
  verified via full editor-client (62/62) + vscode-extension (171/171) suites, plus a clean
  root-level `npm run typecheck` across all three TS workspaces.

### 2. [MEDIUM] Memory inspector stays disabled all session if backend isn't ready at VS Code activation
`extension.ts` fetches `GET /v1/config` exactly once, at activation, to set the
`crucible.memoryEnabled` when-context (gates the `crucible.openMemoryPanel` command). In the
managed-runtime flow the backend is frequently not yet healthy at that exact moment (spawn +
health-poll takes several seconds), so the fetch throws, the catch defaults `memoryEnabled =
false`, and **nothing ever refetches it** afterward — even once the backend reports
`memory_enabled: true` on every subsequent `/v1/config` call. The command's rejection toast also
lies about the cause: *"The memory inspector is disabled (CRUCIBLE_MEMORY_ENABLED=0)"* — sending
the user to check env vars/settings that are actually fine.
- Same one-shot-fetch pattern affects `taskSubsystemEnabled` and `skillsEnabled` — not confirmed
  broken live, but structurally identical and worth the same fix.
- **Workaround confirmed:** `Developer: Reload Window` re-runs activation against the by-then-healthy
  backend and the panel opens correctly.
- **Fix direction:** re-fetch `/v1/config` and re-run the three `setContext` calls once
  `BackendProcess.start()` reports healthy (and again after `RuntimeManager.restart`), not only at
  activation. Make the toast distinguish "backend says disabled" from "config was never
  successfully fetched."
- **FIXED (2026-07-13):** `RuntimeManager` gained `onBackendReady(listener)`, fired at the end of
  `startForWorkspace()` — which is the single choke point for the initial managed start, a
  crash-triggered auto-respawn (`watchCrash`), and an explicit `restart()`, so one registration
  covers all three. `extension.ts`'s one-shot config fetch became `refreshCapabilityFlags()`,
  called once at activation (unchanged) AND registered as the `onBackendReady` listener. Added a
  `capabilitiesKnown` flag so the `startTask`/`openMemoryPanel` toasts now say "couldn't reach the
  backend to check" instead of falsely naming an env var when the flags are simply unknown, not
  actually disabled. Verified: `npm run typecheck` clean, `runtime-backend-process.test.ts` (14/14)
  still green (no test exercised `RuntimeManager` directly — vscode-API-heavy, untested before and
  after; the fix is structural and typecheck-verified).

### 3. [MEDIUM] EditGate has no rejection-reason input in the UI, though the backend supports one
`POST /v1/chat/threads/{id}/edit-decision` accepts `{decision, reason}` and the reason is fed back
to the model (confirmed live — a reasoned reject visibly changed the model's next attempt, see
below), but the **webview's Reject button sends no reason** — it's a bare click with no text
field. A UI-only reject is therefore strictly worse than what the API already supports.
- **Live A/B confirmed the value of a reason:** a blind UI-driven reject on an edit that used the
  wrong batch API shape got a near-identical retry (same defect pattern). A `POST
  /edit-decision {reason: "..."}` sent directly against the API, spelling out exactly what to keep
  and what to change, produced a correct single-record-API rewrite on the very next attempt.
- **Fix direction:** add a reason textarea to the Reject flow in `EditGate`/`StepGate` components,
  wired straight through to the existing `reason` field — no backend change needed.
- **FIXED (2026-07-13), EditGate only:** clicking Reject now opens an inline (optional) reason
  textarea with Back/Reject-confirm buttons instead of rejecting immediately; empty reason still
  works (one click through, same as before) for cases with nothing to say. `StepGate` has the
  identical gap and was NOT touched — out of scope for this pass, tracked as a fast-follow. 5 new
  tests in `EditGate.test.tsx` + 1 pre-existing `gates.test.tsx` test updated for the new two-step
  flow; full webview-ui suite 345/345 green, build clean.

### 4. [MEDIUM] Zero KV-cache reuse on the first several controller iterations (Finding #13, reconfirmed)
Every controller call early in the turn logged `cache_n=0` with ~13-19s prompt evaluation time at
just ~8-9k prompt tokens (`turboquant timings: ... prompt_n=8166 cache_n=0 prompt_ms=18631`).
This matches the existing memory `project_controller_zero_kv_reuse.md` finding: the current
user message is placed first in the payload, breaking the prefix cache on every single turn.
Once the turn ran long enough (many iterations in the same turn, not across turns), cache reuse
did kick in (`cache_n=32768` → `cache_n=57344` by iteration 80+), so the bug is specifically about
the **turn-start** cost, not sustained iteration — but for a workflow made of many short turns
(the common case), this cost recurs every time.
- **Fix direction:** already scoped in the existing memory/CLAUDE.md backlog — move the current
  message out of the cached prefix head.

### 5. [LOW] `run_command` "Allow & remember" persists the full literal command string
`CommandDecision.rule_value` defaults to the exact command text (e.g.
`'go test ./pkg/commitlog/...'` — literally, with the shell quoting baked in) rather than a
sensible default scope like a prefix (`go test`). This is by design (the UI does offer
prefix/binary radio options), but the **default selection is "exact command only,"** so a
click-through "Allow & remember" without changing the radio produces a rule so narrow it barely
ever fires again — the model re-triggers the same gate for the next slightly-different `go test`
invocation almost immediately. Observed live: 4 separate `go test ...` command gates in one turn,
each with a different flag/arg combination, each needing its own approval despite "remember"
being clicked earlier.
- **Fix direction:** default the radio to "prefix" for recognized test/build tool commands (go
  test, pytest, cargo test, npm test, ...), or at minimum default to prefix generally and reserve
  "exact" for genuinely dangerous one-offs.
- **FIXED (2026-07-13):** `CommandGate` now defaults to `scope: "prefix"` with a heuristic prefix
  length — 2 tokens (binary+verb, e.g. "go test") for known subcommand-style tools (go, cargo,
  npm, yarn, pnpm, mvn, gradle, docker, kubectl), 1 token (binary only, e.g. "pytest") otherwise.
  Deliberately did NOT blanket-default every command to a broader auto-approve scope — that's a
  security-relevant UX change for arbitrary (non-test/build) commands, so "exact" stays reachable
  and visible as a radio option, just no longer the default. "Allow once" is unaffected (still
  hardcoded `scope: "exact"`, never persists a rule). 3 new tests + 2 pre-existing tests updated
  (one asserted the old default-unchecked state of the now-default-checked prefix radio); full
  suite 345/345 green.

### 6. [LOW] Weak-model spec drift on batch vs. single-record API (self-corrected via reasoned reject, not a product bug per se)
Two independent times the model produced a commit-log implementation with a batch API
(`Append([][]byte)`/`Read(offset, n)`) despite the prompt explicitly specifying single-record
signatures. This is a TQP/qwen3.6-35B capability observation, not a Crucible defect — but it's the
finding that most directly demonstrates the value of Finding #3 (reasoned reject): the blind
reject didn't fix it, the reasoned reject did, on the first retry.

### 7. [LOW] Malformed patch: duplicate closing brace from a `search_replace`-style edit op
Once, an `edit` action inserted a new closing `}` after `return startOffset, nil` while the
original function's closing brace remained — producing a syntax error the model had not run
`go build`/`go vet` against before proposing the edit. This is the same *class* of bug as the
CLAUDE.md-documented "Finding #10" edit-op malformation (`smoke_controller_cdp_driving_recipe`
memory) — worth checking whether the specific op-shape teaching fix from that finding fully
covers this case or if it's a distinct recurrence. Self-corrected cleanly on a reasoned reject
(30s turnaround, correct fix, no repeat of the mistake).
- **INVESTIGATED, NOT the same bug as Finding #10, DEFERRED (2026-07-13):** read
  `agentd/patch/engine.py` directly. Finding #10 was a model-side op-shape malformation (code
  landing in the wrong field). This is different and more structural: `_apply_search_replace`'s
  `check_syntax` path only runs `_python_syntax_check` when `operation.file.endswith(".py")` —
  **there is no syntax validation at all for Go, Rust, TypeScript, or Java on `search_replace`**,
  even though tree-sitter parsers for TS/Rust already exist in this same file and are used
  elsewhere (`replace_node`/`insert_after_node`, gated to `Literal["typescript", "rust"]`) — they're
  just not wired to the far-more-common `search_replace` op. Go and Java have no tree-sitter
  grammar available in the patch engine at all. This mirrors the CLAUDE.md-documented pattern
  where "the retrieval pipeline mirrors the extension list" and goes stale — language support
  expanded (indexer Go/Java, 2026-07-08) without the patch-safety layer keeping pace. **Not fixed
  this session** — building real per-language syntax checks (shell out to `gofmt`/`go vet` for
  Go, extend the existing tree-sitter wiring to cover `search_replace` for TS/Rust, no easy option
  for Java without a new grammar dependency) is a scoped feature addition to a core patch-safety
  mechanism, not a same-session bugfix — it deserves its own design pass.

### 8. [OBSERVATION] Endurance under a long single turn was otherwise strong
Across ~89 controller iterations and 80+ minutes of continuous activity in one turn on a weak
35B-class local model: the todo ledger stayed accurate throughout (never desynced from actual
progress), the model recovered gracefully from at least one engine-side edit-apply error (anchor
miss → re-read the file → clean re-apply), and — aside from the two findings above — did not lose
track of the original spec or its own prior corrections. This is a positive signal for the
controller loop's robustness independent of the bugs found around it.

### 9. [PRODUCT IDEA] Live token count during model generation
Both campaigns hit long silent gaps (observed: single TQP controller iterations taking 30-90s+
with zero visible progress — no chunk, no pill, nothing but a static "Thinking…" spinner) where
the UI is indistinguishable from actually being stuck, independent of the Finding #1 SSE bug
(which made a truly-dead connection look the same as a slow-but-alive one). User's suggestion:
add a live token/progress indicator during generation (à la Claude Code's live token count),
so forward progress is visible even during a single long model call.
- **Why it's non-trivial:** the controller loop's structured/JSON-schema calls
  (`schema=controller_step_response`) appear to be single request/response, not incrementally
  streamed — `turboquant_transport`'s logging only reports `predicted_n` etc. AFTER the call
  completes (`timings: ...`), suggesting no partial-token events exist yet to surface. The
  existing chunk-streaming paths (`chat_response`, `chat_agent_thinking_chunk`,
  `tool_thinking_chunk`) are for different content, not the structured tool-call/decision JSON
  that dominates controller iterations.
- **Scope:** would need provider-transport-level partial-token support for schema-constrained
  calls (where the provider allows it — local llama.cpp-family servers typically do via SSE
  streaming even with grammar-constrained output; cloud providers vary), threaded through to a
  new SSE event type and a live UI counter. A real design pass, not a quick fix — tracked as
  task #14 in this session, not started.

---

## Open / not yet investigated
- Confirm whether `taskSubsystemEnabled`/`skillsEnabled` when-contexts have the same one-shot
  activation-time staleness as `memoryEnabled` (Finding #2) — structurally likely, not yet
  reproduced live for those two flags specifically.
- Phase 1 not yet complete at time of writing — outcome of the test-fix steering message pending.

---

## Round 2 (2026-07-13): verification campaign in a fresh workspace

**Setup:** Fresh `workspaces/kafka-clone-2`, TQP restarted clean (`scripts/start-tqp.sh`), a
**second** fresh VS Code Extension Development Host instance (`--extensionDevelopmentPath`, not
the stale installed VSIX from round 1) so the round-1 fixes were actually exercised. The managed
runtime's Setup wizard hit an unrelated installer bug (bundled manifest pins
`crucible-agentd[memory]==0.0.0`, unsatisfiable) — fell back to the documented dev flow
(`scripts/stress/start-backend.sh --backend turboquant`, `crucible.managedRuntime.enabled: false`,
explicit `crucible.backendBaseUrl` in the fresh profile's `settings.json`). Same Go commit-log
goal, with one deliberate addition to the prompt ("Append and Read handle exactly ONE record per
call — no batch/slice variants") to skip re-discovering round 1's known model-capability quirk
(Finding #6) and focus verification time on the product fixes instead.

**Outcome: Phase 1 reached a genuinely green `go test ./... -race`, promoted to the real
workspace — round 1 never got this far.** Three edit-review rounds, each caught and fixed via a
reasoned reject before the model ever had to discover the problem itself via a failing `go test`:
1. Two real compile errors (`os.File` has no `Size()` method; an unused `curSize` var left over
   from the model's own visible self-correction) plus a design smell (unbounded `pending` slice
   with no purpose since every write already flushed synchronously) and a dead test assertion.
2. A genuine, subtler correctness gap: `readSegment` didn't treat a torn CRC field or a
   zero-byte payload read as "torn tail" — it returned a hard error instead, which would have
   made `Open()` fail entirely (not just skip the last record) on certain realistic crash-timing
   windows, contradicting the "earlier records survive a crash" requirement. Found by reading the
   full shadow file (the SSE payload truncates diffs), not just the visible diff.
3. A leftover unused loop variable (`for i, p := range` where `i` was never used) — caught by
   actually running `go build`/`go vet`/`go test -race` against the shadow content myself before
   deciding accept/reject, not just eyeballing the diff.

**Fix verification (live, this round):**
- **Fix #4 (EditGate reason box): CONFIRMED end-to-end through the real webview UI**, not just
  the API — click Reject → box opens (screenshotted) → typed a real multi-paragraph reason →
  clicked Reject again → breadcrumb shows the exact typed text reached the backend. Used 3 times
  this round, each one visibly and correctly steering the next attempt.
- **Fix #1 (SSE idle timeout) and Fix #3 (config refresh on backend-ready): NOT re-exercised
  live this round** — the installer-bug fallback to the dev flow means `RuntimeManager` (which
  Fix #3 patches) was never in the loop, and this round's turns never ran long enough to hit the
  120s idle threshold Fix #1 guards. Both remain typecheck+unit-test verified only, not
  re-confirmed live.
- **Fix #6 (command-remember default): not re-tested** — only "Allow once" was used this round
  (no "Allow & remember" click), so the prefix-default behavior wasn't live-exercised again.

**New findings this round:**
- **[FIXED same-session] Diff panes render a blank line between every single row.**
  `agentd/patch/diffing.py::compute_diff_entries` read file content with
  `splitlines(keepends=True)` (each line already carries its own trailing `\n`) but then joined
  the `difflib.unified_diff(..., lineterm="")` output with `"\n".join(diff)` — doubling every
  newline. Confirmed both in the raw SSE payload (`"+package commitlog\n\n+\n\n+import (\n\n"`)
  and visually in a live screenshot of the EditGate diff pane. Fix: `splitlines()` (no
  `keepends`) pairs correctly with the existing `lineterm=""` + `"\n".join`. **Deferred applying
  the fix until the turn went idle** — CLAUDE.md's documented `--reload` footgun (editing
  `agentd/*.py` mid-turn orphans the in-flight turn and wedges the SSE) applies directly here,
  and the user caught this in real time before I ran the edit. Applied after the turn completed;
  `tests/test_unified_diff_wire.py` gained a regression test asserting the rendered diff's
  content rows exactly match the source lines (no interleaved blanks); 4/4 pass. Full agentd-py
  suite: 1297 passed, 2 failed (`test_literal_escape_sequences_decoded`,
  `test_ctrl_c_interrupts` — both PTY Ctrl-C timing tests, unrelated to diffing.py), 1 skipped in
  465s. Both failures reproduced clean in isolation (0.72s, no flakes) — confirmed environmental
  contention (2 VS Code instances + TQP + live backend + the 7m45s suite itself, all running
  concurrently), not a regression from this fix.
- **[OPEN, unresolved] "Light grey text" in the EditGate diff pane** — user flagged low-contrast
  text but a follow-up request for which specific element (diff content vs. a label vs. the
  rejected-reason breadcrumb) went unanswered before the session moved on.
  `--color-text-3: #62626e` (used for unchanged diff context lines) is a plausible candidate —
  worth a contrast pass on the diff pane specifically next time it's on screen, not a blind
  global color change.
- **[PRODUCT IDEA] Live token count during long model generation** — see Finding #9 above.

---

## Phase 2 (2026-07-13, same session): TCP wire protocol + concurrent producers

**Same workspace, same thread** (`chat-c7ba7376c207`) — continuing the campaign rather than
restarting, partly to test whether the model retains its own Phase 1 architecture decisions
across turns. It did: the Phase 2 plan sketch correctly referenced Phase 1's exact `commitlog.Log`
API without being re-told it, and the implementation never touched the Phase 1 package (as
instructed).

**Goal:** a `pkg/broker` package — length-prefixed binary TCP protocol, a `Server` wrapping a
single `commitlog.Log`, a `Client` helper, and a concurrent-producers test under `go test -race`.

**Deliberately varied reject-feedback precision this round, per user instruction:**
1. **Vague attempt (compile error):** first edit had a real compile error (`"os" imported and not
   used`). Rejected with only "hmm this doesn't look right, i tried building it and it failed. can
   you check it again?" — no line number, no error text. **Framing flaw caught live by the user:**
   a real non-programmer wouldn't personally run `go build`. Corrected the philosophy afterward
   (memory: `feedback_vary_reject_feedback_precision`) — vague feedback should come from reading
   the diff with unease, or reacting to the product's own surfaced signals, never from personally
   executing dev commands. **Outcome despite the flawed framing: the model still self-diagnosed
   and fixed the unused import correctly on the very next attempt** — a positive data point that
   even non-specific "something's wrong, look again" signal can be enough for a class of trivial
   errors.
2. **Precise attempt (deadlock):** the retry built cleanly but `go test -race ./pkg/broker/...`
   **hung indefinitely** (verified: process alive for 3+ minutes with ~0.2s total CPU time — the
   signature of a blocked read, not real work; killed manually). Root-caused by tracing the wire
   protocol byte-by-byte rather than skimming: the spec required requests framed as
   `[4-byte frame length][1-byte type][body]`, and `server.go`'s `handleConn` correctly read a
   4-byte length prefix before the body — but `client.go`'s `Produce`/`Fetch` never wrote that
   outer length prefix, sending `[type][...]` directly. The server misread the first 4 bytes of
   the client's type+length fields as an ~16MB frame length and blocked in `io.ReadFull` waiting
   for data that would never arrive, while the client blocked waiting for a response that would
   never be written — a genuine mutual deadlock, not a flaky test. This needed precise technical
   feedback (a non-programmer could not plausibly diagnose a missing length-prefix from vague
   unease) — rejected with the exact byte-level trace and fix location. **Fixed correctly on the
   next attempt**, verified independently with `go build && go test -race -timeout 30s` (both
   tests pass, 2.1s) before accepting.

**Final state:** `go build ./...`, `go vet ./...`, `go test -race -timeout 60s ./...` all pass
clean across both `commitlog` and `broker` packages in the real (non-shadow) workspace.

**Takeaway on the vague/precise experiment:** one data point each way isn't enough to generalize,
but the contrast is suggestive — trivial/mechanical errors (unused import) were recoverable from
vague signal alone, while a genuine multi-file protocol bug (deadlock from a missing length
prefix) needed precise, byte-level feedback to fix in one attempt. Matches intuition: vague
feedback tests "can it re-diagnose obvious problems," precise feedback tests "can it follow exact
instructions" — different bugs call for different feedback styles, and always-vague would likely
have cost several more retry cycles on the deadlock specifically.

**Process note:** user asked that future campaigns vary reject-feedback precision (some
maximally technical like this round, some deliberately vague/non-programmer-style) to test
whether the model can re-diagnose from weak signal, not just follow precise instructions. Saved
as a durable preference (`feedback_vary_reject_feedback_precision` memory) for the next round.

---

## Phase 3 (2026-07-13, same session): consumer group offsets — accept-as-is methodology

**Same workspace, same thread**, continuing past Phase 2. Goal: a `pkg/group.OffsetStore`
(JSON-persisted commit/fetch offsets) plus two new wire-protocol request types
(`CommitOffset`/`FetchOffset`) wired into the existing `pkg/broker` server/client, without
touching Phase 1/2 code.

### Methodology change (user-directed)

Round 2's Phase 1/2 reviews were all me pre-verifying with my own `go build`/`go test` calls
*before* the model's own verify phase ever ran, then feeding it my diagnosis via reject reasons.
User pushback: *"what i wanted was for model to fail the test and figure out what went wrong.
not just you giving it feedback."* Fair — that methodology was doing the model's job for it.
Phase 3 switched to **accept-as-is**: click Accept on every edit gate with no review, approve
every command gate so the model's own `run_command` calls actually execute, and only observe —
no injected diagnosis. This is the cleanest test yet of the controller loop's unaided
self-correction, and it worked well:

- **Two real deadlocks self-diagnosed and fixed with zero help from me**, both by the model
  reading actual Go goroutine stack traces from its own failed `go test -race` runs (Go's runtime
  deadlock detector dumps every goroutine's state when all goroutines are asleep). The model's
  own `thought` field showed genuine multi-step debugging: forming a hypothesis, checking it
  against the stack trace, discarding it ("Wait — I think I see the issue..."), and correctly
  landing on "the client's `CommitOffset` never prepends the request-type byte, so the server
  misreads the group-ID-length field as the type, silently falls through `dispatch()`'s default
  case, and never responds — client blocks forever waiting for a reply that will never come."
  This is the *exact same bug class* as the Phase 2 deadlock (Finding: missing wire-protocol
  framing byte), except this time diagnosed entirely unaided.
- **One trivial compile error self-fixed from deliberately vague feedback.** First vague-feedback
  attempt ("this doesn't look right, i tried building it and it failed") had a framing flaw the
  user caught live — a real non-programmer wouldn't personally run `go build`. Corrected
  philosophy: vague feedback should come from reading the diff with unease or reacting to the
  product's own surfaced signals, never from claiming to have run dev commands. Despite the flawed
  framing, the model still correctly self-diagnosed and fixed the unused `os` import on the very
  next attempt.
- **One subtle off-by-one self-corrected on a *second* pass**, unprompted. After landing a
  passing fix, the model re-examined its own already-green code against `dispatch()`'s actual
  contract (`body[1:]` is passed to handlers, so `body[0]` inside a handler is *not* the request
  type) and caught that its first fix used the wrong byte offset — a latent bug the test suite
  hadn't exercised either way (both variants happened to pass `go test`). Verified independently:
  the corrected version is objectively right per `dispatch()`'s contract; the test suite's failure
  to distinguish the two is itself a minor test-coverage gap worth noting, not a Crucible bug.

### New finding, fixed same-session

**[FIXED] `run_command`'s timeout leaks the killed process.** Prompted by the user's question
"do we even have the capability in crucible to detect such things [deadlocks]?" — traced
`agentd/tools/shell.py::run_command`: on `asyncio.TimeoutError`, it returned a clean `is_error`
message to the model (so the *agent loop* doesn't hang), but never called `proc.kill()` — the
underlying OS process (e.g. a deadlocked `go test -race` holding a TCP listener) kept running
indefinitely, orphaned from the tool call. **Confirmed empirically**, not just by reading code: a
`sleep 30` spawned via the exact same `asyncio.create_subprocess_exec` + `wait_for(timeout=1)`
pattern was still alive after the timeout fired; after adding `proc.kill()` + `await proc.wait()`
in the except branch, the same repro showed the process gone. Split the single `try` block into
two (subprocess spawn vs. the timeout-guarded wait) to fix a `Pyright` "possibly unbound" warning
on `proc` as a side effect of the restructure. Regression test added
(`tests/test_shell_pythonpath.py::test_run_command_timeout_kills_the_process`, spawns a real
`sleep 30` with `timeout_sec=1`, asserts via `pgrep` that nothing is left running) — 13/13 pass.
**Answer to the capability question: partial before this fix** — Crucible detected a hang (bounded
wait, clean signal to the model) but didn't clean it up; now it does both.

### Fix verification (live, this round)

- **Fix #1 (SSE idle timeout / resume) — indirectly but concretely exercised.** The driving VS
  Code instance was killed by the environment three separate times mid-campaign (root cause
  undetermined — not laptop sleep, since running `caffeinate -dis` in the background didn't
  prevent the third kill; possibly something about the remote-control session's process lifecycle
  reaping backgrounded GUI processes). Each time, reconnecting via CDP and navigating back into
  the thread correctly resumed the live overlay via the existing `resumeLiveOverlay` /
  `channelActive && turnAbort === null` poll-driven path — the exact machinery Fix #1 unblocks.
  The backend and the in-flight turn were never affected by any of the three kills (separate
  process, no dependency on the driving UI being open).
- **Fix #4 (EditGate reason box) and Fix #6 (command-remember prefix default) — reconfirmed live**
  in the resumed UI: the reason textarea worked correctly for the vague-feedback attempt, and a
  `go test -race` command gate rendered with "Prefix — lock first 1 token(s)" pre-selected
  (not "Exact command only") after reconnecting, confirming the default survives a fresh gate
  render, not just the specific session where the fix landed.

### Process-lesson (my own driving methodology, not a Crucible bug)

After the first VS Code kill+relaunch, a click I sent (`Accept`) returned `"CLICKED: Accept"`
from my own script but the target real-workspace file showed no change — the webview had reset to
the thread-*history list* view (expected behavior on a fresh window), and my click matched an
unrelated element there rather than the actual pending gate, which I never re-verified via
screenshot before clicking. Lesson: after any reconnect, always screenshot and visually confirm
the actual rendered view before resuming API-state-driven blind clicks — a successful click-script
return does not guarantee the click landed on the intended element.

### Corrections to earlier claims in this doc (caught by direct user questions)

- **Memory-harness compaction did NOT fire during this campaign** — `grep -c "\[memory\]
  compacted"` on the full session log returns 0. I had speculatively attributed the
  `cache_n=49152` plateau (constant across dozens of TQP calls) to compaction hitting its
  documented hot-floor; that attribution was wrong (compaction only triggers at ~83K tokens of
  history, and `prompt_n` never exceeded ~14.5K here). The real cause of the 49152 plateau is
  still unconfirmed — likely llama.cpp's own server-side context-shift behavior (49152 is exactly
  half the configured `-c 98304`), not anything in Crucible's memory harness. Flagged as a
  retracted claim, not re-asserted as fact.

### Side investigation: real-token-volume cost modeling across providers

Parsed actual `prompt_n`/`cache_n`/`predicted_n` from every TQP call across both threads (141
calls total: 90 in round 1, 51 in round 2+Phase 1-3) to answer "what would this campaign have
cost on a paid model" with real data instead of guessing — 136,807 fresh-input tokens, 4,571,569
cache-hit tokens, 87,307 output tokens. Cross-checked several models' pricing directly against
OpenRouter's live `/api/v1/models` and `/endpoints` APIs (not search-snippet summaries, which
were sometimes stale/wrong by 2x — caught and corrected live for both GLM-5.2 and DeepSeek V4
Pro). Headline results at this exact token volume: true frontier models (Claude Fable 5 — the
model driving this very session, Claude Sonnet 5, Grok 4.5) all cost $1.70-$10.30; DeepSeek V4
Pro (open-weight, cited as leading current coding-score benchmarks) came in at a verified **$0.15**
via its own first-party OpenRouter listing — 6x cheaper than the next real contender (GLM-5.2 at
$0.79). Z.ai's own direct API and ZenMux both listed GLM-5.2 at the *same* $1.40/$4.40 — neither
beat OpenRouter's competitive multi-host marketplace price for that model. No new Crucible
provider code needed for any of these — OpenRouter is already wired.

### Final state

`go build ./...`, `go vet ./...`, `go test -race -timeout 60s ./...` all pass clean across
`commitlog`, `broker`, and `group` packages in the real (non-shadow) workspace. Three-phase build
(commit log → TCP broker → consumer group offsets) complete end to end.

## Cloud-model campaign (2026-07-13, fresh `kafka-clone-3` workspace): Ollama Cloud

Switched provider to `ollama` backend with `CRUCIBLE_OLLAMA_MODEL=nemotron-3-super:cloud`
(NVIDIA, 120B MoE / 12B active, reached transparently through the local `ollama serve` daemon
once signed in — no `OLLAMA_HOST` change needed) to probe for silent failures under a free,
rate-limited cloud model, per explicit user direction: *"idea is to find silent failures/bugs
when running with cloud models... if UI seems stuck / unanswered it's a bad UX."* Same Kafka task
reused for comparability with the TQP rounds above.

### 10. [HIGH] A provider exception mid-turn killed the SSE stream with zero visible failure signal

User watched a turn sit for several minutes and flagged it live: *"cloud model shouldn't take
this long."* Root cause traced to two exact lines:
- `agentd/providers/ollama_transport.py` (`_extract_text`): `raise RuntimeError("Ollama response
  contained no text content")` — fires when the model's `<think>` reasoning consumes the entire
  output budget and no actual response text is ever emitted. Confirmed live: Nemotron-3-Super
  burned its whole `num_predict` budget on thinking on this one call.
- `agentd/chat/controller.py::_run_loop`: the `try/except asyncio.CancelledError` around the
  `ControllerLoop.run(...)` call had no handler for any *other* exception. `create_controller_step`
  (`reasoning/engine.py`) and the loop body (`controller_loop.py::_iterate`) both call straight
  into the transport with no try/except either — a plain `RuntimeError` from the provider
  propagated uncaught all the way out of the SSE route.

Effect on the UI: the stream died mid-flight with no `chat_done`, no breadcrumb, nothing written
to the transcript. `turn_active` still flipped false (cleanup runs regardless of how the turn
ends), so the composer correctly re-enabled — but the todo list stayed frozen at its last
persisted state and nothing anywhere (transcript, live gate, toast) indicated a failure had
happened. This is worse than a stuck UI: there wasn't even a stuck *state* to notice, it just
looked like the agent silently gave up.

**Fix:** added `except Exception as exc:` in `_run_loop`, mirroring the existing CancelledError
branch's partial-history persistence (so whatever the turn accomplished before failing — e.g. a
completed `read_file` — survives into the next turn) but ending the turn as a normal `"answer"`
outcome instead of re-raising: `⚠️ The turn failed and had to stop: <exc>`. This flows through the
already-correct `chat_response`/`chat_done` broadcast path — no new outcome kind, no frontend
change needed. Verified via 2 new regression tests (`test_controller_loop_generic_exception.py`:
does-not-propagate + partial-history-persists) plus the full existing `test_controller*.py` suite
(173 tests) and the CancelledError-specific suite (7 tests, confirming `/stop` still re-raises
through unaffected) all green.

### 11. [MEDIUM] `num_predict` was a flat constant equal to `num_ctx`, leaving no real headroom — and not configurable per model

Secondary, related bug found while fixing #10: `ollama_transport.py` hardcoded
`num_predict=32768` for JSON calls while `num_ctx` (context window, input+output combined) was
*also* hardcoded to the same `32768` inside `_build_body`. Since `num_ctx` bounds prompt + output
together, setting `num_predict` equal to `num_ctx` never actually gave the model the full 32768
for output — the real ceiling was `num_ctx` minus whatever the prompt consumed, tighter than the
constant implied. Nothing was configurable, so a cloud model with a much larger real context
window (Nemotron's cloud endpoint supports well beyond 32K) couldn't be given more thinking room
without a code change.

**Fix:** `OllamaJsonTransport` now takes `num_ctx` (default `32768`, unchanged behavior) and
`json_predict_frac` (default `0.5`) constructor params; `num_predict` for JSON calls is computed
as `num_ctx * json_predict_frac`, always strictly less than `num_ctx`. Wired through
`factory.py` as `CRUCIBLE_OLLAMA_NUM_CTX` / `CRUCIBLE_OLLAMA_JSON_PREDICT_FRAC` env vars, so
raising the window for a bigger-context cloud model is a config change, not a code change.
`generate_text`'s fixed small output cap (2048, deliberately bounded against runaway answers) is
untouched but now tracks the configured `num_ctx` too. 6 new unit tests added (default fraction,
custom `num_ctx` scaling, custom fraction, `generate_text` num_ctx passthrough, factory env
wiring) — all green alongside the full `test_ollama_transport.py` + `test_provider_factory.py`
suites (38 tests).

**Explicitly not attempted (at first):** neither fix stops a model from *choosing* to spend its
whole budget on thinking — #11 just raises the ceiling and makes it tunable; #10 is the safety net
for when a model exhausts whatever ceiling it's given.

### 12. [FOLLOW-UP] Researched how Codex/Ollama actually bound reasoning tokens, added the real lever

User pushback on #11: *"we should have done a web search before how codex/cursor handle this.
again setting 32k will exhaust it with thinking ig."* Correct — raising `json_predict_frac`
doesn't address a model that decides to think past whatever ceiling it's given. Researched before
writing more code:

- **Codex (OpenAI)** controls reasoning via `reasoning_effort` (minimal/low/medium/high/xhigh) —
  a genuinely *separate* server-side token budget from output, only possible because OpenAI's
  Responses API tracks reasoning and output as distinct streams. Not reproducible against Ollama's
  `/api/chat` — there is no equivalent split.
- **Ollama itself** (`docs.ollama.com/capabilities/thinking`, confirmed via fetch): the actual
  lever is a **top-level `think` request field** (bool, or for some models a level string
  `"low"/"medium"/"high"/"max"`) — `num_predict` remains one shared pool for thinking+output
  regardless. Model-dependent: GPT-OSS requires the string form and can't fully disable its trace;
  this repo's own code comment already documented that qwen3 ignores the flag and emits implicit
  thinking regardless (why `think` support was ripped out entirely in an earlier session).
- **TQP (this repo's llama.cpp-turboquant transport)**, by contrast, *does* have a true numeric
  reasoning cap (`thinking_budget_tokens` via `chat_template_kwargs`) — confirming Ollama's
  shared-pool limitation is a genuine gap versus what's achievable elsewhere in this codebase, not
  an implementation oversight.

**Fix:** reintroduced `think` as an opt-in dial, not a default — `OllamaJsonTransport(think=...)`,
wired via `CRUCIBLE_OLLAMA_THINK` (accepts bool-like strings or a level string; unset omits the
field entirely, preserving current behavior for every existing deployment). Deliberately did
**not** add an automatic retry-with-think-false-on-empty-content: for a model that ignores the
flag (qwen3, potentially others), that would silently double the wait before the graceful #10
error shows — directly working against the "if UI seems stuck it's bad UX" thing this whole
campaign is testing for. Instead this is a dial an operator sets per-model after testing it
empirically (exactly the kind of live A/B this campaign's methodology already uses). 5 new unit
tests (3 transport-level: default-omitted / bool-top-level / level-string, 2 factory-level:
default-unset / env-string-parsing across 7 cases) — all green (58/58 across the 5 affected test
files). CLAUDE.md's stale "thinking support intentionally removed" note corrected to describe the
current state accurately.

A hard per-model thinking-token cap (the way TQP's Qwen3 profile bounds it numerically) would need
Ollama's response to expose thinking-vs-output tokens as they stream, which its non-streaming
`/api/chat` shape here doesn't — genuinely not achievable against this API today, not just
undone work.

### Live verification (2026-07-13, same thread, backend restarted with `CRUCIBLE_OLLAMA_THINK=low`)

Killed and restarted the kafka-clone-3 backend with the new env var, then resumed the exact
stalled thread via the real webview (not an API call) using the established CDP-driving recipe.
Two calls observed:

1. A DECIDE-phase `propose_mode` call completed in 28s with `output_tokens=1059` — clean JSON,
   no truncation, nowhere near the 16384-token budget. Zero sign of the thinking-exhaustion
   failure mode that killed the original stalled turn. Suggestive (not a controlled A/B) that
   Nemotron honors `think: "low"`.
2. The follow-up EDIT-phase call (writing the actual `commitlog/log.go`) legitimately exhausted
   the full `output_tokens=16384` budget — but this time on *real content* (a complete Go file
   with CRC32 framing, segment rolling, etc.), not `<think>` — and got cut off mid-string,
   producing unrepairable malformed JSON. **This is Finding #11's failure mode, not #10's**: a
   large file genuinely needs more than half of a 32768-token window once the JSON-escaping
   overhead is added. `CRUCIBLE_OLLAMA_NUM_CTX`/`CRUCIBLE_OLLAMA_JSON_PREDICT_FRAC` are the actual
   lever for this one, not `think`.

Crucially, **Finding #10's fix fired for real**: `[controller] turn failed with an unhandled
exception` appeared in the log, and — confirmed via direct read of the persisted thread messages
and a live screenshot of the actual VS Code webview — the transcript rendered `⚠️ The turn failed
and had to stop: Ollama output is not valid JSON for controller_step_response (after repair): ...`
as a normal chat bubble, composer re-enabled, no hang. This is the first time this exact
"lands, renders, un-stuck" path has been observed against the real UI rather than only asserted by
unit tests — the fix generalizes across failure causes (thinking-exhaustion, malformed-JSON,
anything else `generate_json` can raise), which is exactly the point of catching at the
`_run_loop` level rather than patching each root cause individually.

### 13. [MEDIUM] Every provider exception ended the whole turn — even ones a retry could recover

User pushback on watching #10/#12 in practice: *"this should not be loop stopper right? looks
like something error can be passed back to model for better output."* Correct — `_run_loop`'s
`except Exception` (Finding #10) is a good *last-resort* safety net, but it was also the *first*
and *only* line of defense: a single empty-content or malformed-JSON response killed the entire
turn immediately, even though the same failure often resolves on a bare retry (a cloud model
occasionally returning garbage on one call, not every call).

Checked whether this codebase already had a pattern for it before designing anything new: it did.
`PlanningLoop` (`agentd/planning/loop.py`) wraps its `create_planning_step` call in a bounded
retry loop (`_MAX_STEP_RETRIES = 2`) that, on ANY exception, injects a correction message into
history and retries — only re-raising once retries are exhausted. `PARSEFAIL_CORRECTION`, the
exact correction string for this, already existed in `agentd/reasoning/react_common.py` —
**completely unused anywhere in the codebase**. `ControllerLoop` (the chat/agentic-turn loop this
whole campaign exercises) never got this treatment; every provider hiccup went straight to
Finding #10's turn-ending catch.

**First fix attempt (revised):** initially extracted a brand-new `react_common.call_step_with_retry`
helper with its own separate `max_retries` bound, mirroring `PlanningLoop`'s structure wholesale.
User pushback caught the over-engineering: *"not this like when action is done as tool call, it
goes back even with current code.. why add another retry mechanism."* Right — `_iterate` already
had a bounded correct-and-continue mechanism (`consecutive_malformed`/`_MAX_MALFORMED`) for
exactly this class of problem (a parsed-but-semantically-invalid response already gets corrected
and retried through it); adding a SECOND parallel retry mechanism for "response that couldn't even
be parsed" was unnecessary duplication, not a distinct need. Revised to: `_iterate` wraps the
`create_controller_step` call in a plain `try/except Exception` that, on failure, feeds the SAME
`consecutive_malformed` counter and appends the same-shaped correction (`PARSEFAIL_CORRECTION`,
which — like the tool_call → tool_result error path the user pointed at — already existed and
already worked, just was never wired to this specific failure site) — no new function, no new
bound. `ControllerLoopExhausted`'s message embeds the original exception text so the eventual
`_run_loop` fallback message still surfaces something specific rather than a generic wrapper
message. `planning/loop.py`'s separately-implemented analogous retry was left as-is (already
correct, has its own tests, not worth the regression risk of unifying further this session). 4
tests (2 unit-level on the existing `react_common` primitives, unchanged; 2 integration-level via
a new `_RaisesThenRecovers` scripted engine proving a `ControllerLoop` turn actually recovers with
the real answer instead of the ⚠️ fallback when a failure resolves within the existing budget) —
all green, plus the full `test_controller*.py` suite (176 tests) unaffected.

**Follow-up, user caught a second gap:** *"does it feed the error back"* — no, the correction
appended to history was the generic static `PARSEFAIL_CORRECTION` string (same one
`planning/loop.py` uses), not the actual exception text. For the exact failure observed live (a
large file truncated mid-write), that's a real problem: a generic "give me JSON" nudge doesn't
tell the model it needs to write something *shorter* — it would likely retry the same oversized
write and hit the same truncation again, burning the retry budget for nothing. Fixed: the
correction now embeds the real error (capped to 300 chars via the already-existing
`cap_event_output` helper from `orchestrator/broadcaster.py`, reused rather than writing a new
capping function — same "check for an existing primitive first" lesson as Finding #13) plus
explicit guidance to produce a shorter response or split a large change across more `patch_ops`
entries. `PARSEFAIL_CORRECTION` is no longer used at this call site (removed the now-dead import).

### 14. [FOLLOW-UP] `temperature=0` made retries deterministic — added a knob, live-confirmed recovery

Live-testing Finding #13's fix on the same stalled thread produced a real occurrence, but the
WRONG one to observe: 3 semantically-malformed `edit` responses had already consumed the shared
`_MAX_MALFORMED` budget before the raised exception even happened, so it went straight to
exhaustion (`⚠️ Controller failed 4 consecutive times — last error: Ollama response contained no
text content`, correctly rendered in the actual webview) without ever exercising the
correction-with-real-error being *seen and acted on* by the model. User's diagnosis: *"use
temperature and continue thread"* — `OllamaJsonTransport` hardcoded `temperature: 0` for every
call. At greedy decoding, a retry whose only input change is one appended correction message is
likely to reproduce a similar failure rather than genuinely explore a different completion.

**Fix:** added a `temperature` constructor param (default `0.0`, unchanged behavior) wired via
`CRUCIBLE_OLLAMA_TEMPERATURE`. Restarted the backend with `CRUCIBLE_OLLAMA_TEMPERATURE=0.7` and
resumed the same thread. Result, live:
- The DECIDE call that previously needed 4 attempts to fail now landed a clean `propose_mode` on
  attempt 3 with zero raises.
- The EDIT call wrote a **complete, valid `commitlog/log.go`** (3978 bytes on disk, not truncated)
  where the `temperature=0` run had truncated mid-file — approved via the EditGate, landed on the
  real workspace.
- Deeper into the same EDIT phase, the mechanism this finding exists to test fired for real:
  ```
  15:24:34  iter=5 phase=EDIT action=tool_call   (succeeded)
  15:24:49  create_controller_step raised: Ollama output not valid JSON  → corrected, retried
  15:25:22  create_controller_step raised: Ollama output not valid JSON  → corrected, retried
  15:25:45  iter=8 phase=EDIT action=tool_call   (succeeded — recovered)
  ```
  Two consecutive raised exceptions, each fed the real error back to the model (not a generic
  nudge), and the third attempt produced a valid response — the turn continued without ever
  reaching the ⚠️ fallback. This is the live confirmation Finding #13 + its error-feedback
  follow-up were missing: an in-loop recovery actually happening, not just the exhaustion path.
  4 new unit tests (transport temperature pass-through, factory default + env override) — all
  green.

**Correction (see Finding #15):** the "complete, valid `commitlog/log.go`" claim above was wrong
— non-truncated (right byte count) is not the same as valid. The file was actually corrupted by a
different bug, caught by the user asking "log.go is single line? is this patch error?"

### 15. [HIGH] The model can double-escape its own JSON content — technically valid JSON, silently corrupts the written file, false "Applied" success

User: *"log.go is single line? is this patch error?"* — a sharp catch from glancing at the
EditGate diff (`additions: 1`, which should have been ~130 for a real Go file). Verified at the
byte level: `wc -l commitlog/log.go` → 0, `xxd` showed `5c 6e` (literal `\` + `n`, two bytes) where
a real newline (`0x0a`) should be. The file on disk was one giant line of escaped text — not valid
Go, would fail to compile.

Traced the corruption to its exact origin using the per-iteration debug artifacts
(`controller-turn-NN.json`, `raw_result` = the exact dict `generate_json` returns): searching every
artifact across the thread for the same file path showed **some attempts came back with real
newlines, others came back already broken at that exact point** — i.e. before ANY Crucible code
(Pydantic validation, `PatchEngine._apply_create_file`'s plain `write_text`, `compute_diff_entries`)
had touched the content. Confirmed `_apply_create_file` is a pure passthrough and Pydantic
performs no string transformation — ruling out our own code as the corruption source. Conclusion:
**Nemotron itself sometimes double-escapes its own JSON string values** — emitting
`"content": "line1\\nline2"` (JSON-escaped backslash + literal `n`) instead of
`"content": "line1\nline2"` (JSON-escaped newline) — technically valid JSON (parses fine, schema
passes, no exception, no retry triggered anywhere) but semantically garbage. This is the purest
form of "silent failure" this whole campaign has been hunting: everything downstream reports
success.

**Fix:** `edit_session.py::_validate_patch_ops` (the same pre-flight gate that already catches the
file/content-field-swap bug) gained a `_looks_double_escaped()` heuristic — content with literal
`\n`/`\t` sequences (≥3) but zero real newlines anywhere is almost certainly this failure mode, not
intentional single-line content. Checked across `content`/`search`/`replace`/`diff` (all
content-bearing op fields, future-proofed for op types the controller schema doesn't expose yet).
Raising `ValueError` here routes through the EXISTING "PATCH FAILED" corrective-retry path in
`controller_loop.py` (no new plumbing needed — same mechanism the file/content-swap bug already
uses). 5 new tests: the real corrupted-content fixture (trimmed slice of the actual bytes observed
live) rejected correctly, a short single-line file NOT falsely flagged, a real multi-line file that
legitimately mentions `\n` in a comment NOT falsely flagged, plus direct heuristic boundary tests
— all green.

### 16. [FOLLOW-UP] Implemented real streaming — thinking now visible live, not just post-hoc

User, after the `think`/temperature work: *"fix streaming as well. if model is thinking or its set
for provider. its thought should be streamed."* Checked first: `ollama_transport.py` hardcoded
`"stream": False` — the whole HTTP call blocked until Ollama returned the COMPLETE response, so a
191-second call (observed live, Finding #14) showed the user nothing — no partial thinking, no
partial content, just a static "Working…" timer for over 3 minutes. The frontend already had the
full live-append pipeline built and waiting (`tool_thinking_chunk` SSE event →
`ui.appendChatThinkingChunk()`, an append-not-replace method) — confirmed by reading
`controller.ts` before touching anything; only the Ollama transport's producer side needed fixing.

**Fix:** `_build_body` now sends `"stream": true`; a new `_stream_chat` consumes Ollama's
newline-delimited-JSON streamed response, firing `on_chunk` with each `message.thinking` delta AS
IT ARRIVES (this is what makes the live "Thinking (N steps)" pane actually live), while merging all
chunks into a single dict shaped exactly like the old non-streaming response (`_extract_text` /
`_log_usage` / `_parse_output_object` downstream are byte-for-byte unchanged). This also
substantially closes the earlier-logged "we never read Ollama's structured `message.thinking`
field" gap (Finding, now superseded) — the field is read on every call now, not just as a
diagnostic afterthought. Retry/backoff semantics preserved via a small internal
`_RetryableHttpStatus` marker exception (the streamed-response status check happens inside an
`async with self._client.stream(...)` block, which can't `continue` an outer loop directly the way
the old plain-response status check could).

Test infra: `_FakeAsyncClient`/`_FakeResponse` gained a `.stream()` async-context-manager surface;
every existing single-shot fixture degenerates cleanly into a one-line stream, so only ONE test
assertion needed updating (`stream: False` → `True`) out of 34 — the rest passed unchanged, a good
sign the merge-into-old-shape design was right. **Live-verified against the real Ollama server**
(not just the test fake): a real streamed call completed in 4.5s with usage stats
(`prompt_tokens=39771`, `output_tokens=209`) correctly read from the merged final chunk, confirming
Ollama's real API places summary stats on the last `done:true` line exactly as assumed.

**Process note:** verified `/stop` does not lose history at either layer before relying on it
again this session — checked both the persisted display transcript (`messages`, ends cleanly with
a `✗ Stopped` breadcrumb) and the internal prompt-context history (`controller_history_json` in
the SQLite row directly, 117 entries preserved to the exact point of interruption) after stopping
the corrupted-file turn. Both matched `_run_loop`'s `CancelledError` handler, which persists both
before re-raising.

**Model switched mid-session:** `nemotron-3-super:cloud` → `nemotron-3-ultra:cloud` (user request,
reason not given — likely testing whether the larger variant is less prone to the escaping/thinking
issues found above). Backend restarted cleanly, `/v1/config` confirms the swap.

### 17. [MEDIUM] Model misdiagnosed its own tool output, then a real product bug: pipes silently fail in `run_command`

Resumed on `nemotron-3-ultra:cloud`: told the model directly that `commitlog/log.go` was
corrupted and asked it to delete + rewrite. It called `read_file`, received the raw garbled
content verbatim in its own tool_result (`1: package commitlog\n\nimport (...` — one numbered
line, literal `\n` visible as text, confirmed byte-for-byte via the debug artifact's
`conversation_history`), and still concluded *"the file appears to be valid Go code now (read_file
shows proper formatting)"* — a genuine misdiagnosis with the corruption plainly present in its own
context, not a display artifact. Not fixed (a reasoning gap, not a code bug) — approved "Run tests
now" instead of correcting it, letting `go test` fail and force real discovery (Phase 3
methodology).

While diagnosing itself, the model repeatedly tried `xxd file.go | head -20` / `cat file.go |
head -5` / `hexdump -C file.go | head -5` — all failed (exit 1, xxd/hexdump usage errors). Root
cause: `agentd/tools/shell.py::run_command` executes via `create_subprocess_exec` (argv list, no
shell) — `|`/`head`/`-20` get passed as literal filename arguments to `xxd`, which rejects them.
The tool's own description says *"Run a **shell** command"*, actively misleading the model into
expecting pipe support that doesn't exist — a real, cheap-to-fix prompt/capability gap, not a
one-off model mistake (4+ wasted iterations on the same pattern).

User's framing, once I proposed just fixing the description: *"it should be able to do that, tool
shouldn't restrict models commands. tool should be able to do what model says. only user has power
to say no."* — the human-approval gate (`CRUCIBLE_SHELL_POLICY=ask`) is already the actual safety
boundary (no static command allowlist exists), so a tool that silently refuses valid shell syntax
isn't adding real safety, just breaking expected behavior. **Fix:** switched
`create_subprocess_exec` → `create_subprocess_shell`, joining `command`+`args` into a real shell
command line. Each token is `shlex.quote()`'d individually UNLESS it's a recognized shell operator
(`|`/`&&`/`||`/`;`/`>`/`>>`/`<`/`2>&1`/`2>`/`&`), which stays raw so the shell actually interprets
it — preserving argv-exec's per-argument safety for the common case (an argument containing a
space or `;` that ISN'T meant as an operator stays one literal argument) while letting a model
compose real pipelines exactly like a human typing at a terminal. Tool description updated to
match.

**Safety follow-through, caught before it shipped:** a shell pipeline forks a child process per
stage; the existing timeout-kill (`proc.kill()`, Fix #7 from earlier this campaign) only signals
the tracked shell-wrapper PID — for a real pipeline this would leave later stages orphaned and
still running, reintroducing the exact leak class Fix #7 closed for the single-command case.
Fixed alongside: `start_new_session=True` (own process group) + `os.killpg(...)` on timeout
instead of `proc.kill()`. 9 new tests (pipe/redirect/chaining actually interpreted, a non-operator
argument with spaces/metacharacters staying literal, the command-line-builder unit-tested
directly, and — the one that would have caught the leak regression — a real 2-stage pipeline with
a `sleep 30` second stage confirmed dead after a 1s timeout) — all green, plus the existing
PYTHONPATH-focused shell test suite (21 total) unaffected.

### 18. [OPEN, unresolved] The graceful-failure message renders live but does not survive a reload

Hit Ollama Cloud's actual account rate limit mid-campaign: `429 you have reached your session
usage limit, upgrade for higher limits`. The retry-then-exhaust mechanism (Findings #13/#14)
worked exactly as designed at the LOG level — 4 full HTTP retry cycles (5/10/20/40s backoff each),
each ending in a 429, correctly accumulated against the shared `consecutive_malformed` budget, and
`ControllerLoopExhausted` fired on schedule (confirmed via full traceback in `agentd.log`,
`turn failed with an unhandled exception`).

**Corrected mid-investigation** by the user, who was watching the actual webview and asked "UI
shows rate limit error no?" — right: the `⚠️ The turn failed and had to stop: ...` message IS
visibly rendered in the live transcript (confirmed via screenshot). My first read of this
finding — "no new chat message ever appeared" — was wrong. What's actually true, checked directly
against the SQLite `messages_json` column repeatedly (before AND after the correction, same 42
messages both times, unchanged for 8+ minutes spanning the whole exhaustion): **the message is
NOT persisted**. `_finish`'s `outcome.kind == "answer"` branch does the persist call
(`_write_turn_message`) THEN the live broadcasts (`chat_response`/`chat_done`) — the broadcasts
clearly fired (that's what the user sees live in their still-open webview session, holding it in
React state), but the preceding persist call produced no durable row. The correct framing:
**this is a "renders live, dies on reload" bug** (the same class CLAUDE.md's gate-invariants
section already warns about elsewhere), not a total silent failure — a real reload/reconnect
would lose it, but the user watching in real time did see it.

A DIFFERENT earlier exhaustion in the SAME session (from the empty-`edit`-response class, Finding
#13) DID persist correctly — this is not a universal regression, something about THIS specific
occurrence's path differs.

Wrote two targeted reproductions driving the REAL `handle_message` → `resolve_mode` →
`_run_loop` → `_finish` path (no mocking, unlike other `resolve_mode` tests) — one matching the
bare shape (explore → propose_mode → resolve_mode("edit") → sustained-raising exhaustion), one
adding a `run_command` approval-gate interleaved via a background `asyncio.Task` (matching the
live shape, which had 5 command approvals before the failure). **Both pass** — the core
`_run_loop`/`_finish`/`resolve_mode` logic correctly persists the failure message in every
in-process reproduction attempted, narrowing this to something environment-specific to the live
occurrence (not a straightforwardly reachable code bug in the general shape) — most likely
something in `_write_turn_message`/`finalize_inflight_pills`'s interaction with this specific
turn's in-flight-pills bookkeeping, or a 409 conflict I personally triggered earlier in the same
session from a duplicate `/mode-decision` POST racing an already-in-flight resolve — neither
confirmed. Live re-verification (checking whether the message actually vanishes on a real reload)
is blocked until the rate limit clears, per the user: *"you add try again right, it'll hit rate
limit."* Both reproduction tests kept as regression coverage regardless.
