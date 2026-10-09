# Main-chat context-capacity tracker design

Date: 2026-10-08. Status: user-approved design, implementation-team proposal P6 adopted with the clarifications below. This is a design contract, not a claim that implementation or live verification is complete.

## Goal and scope

Show a compact, live context-capacity meter near the MAIN chat composer model/footer controls. Prioritize Sign in with ChatGPT: its denominator is the selected account/model catalog `context_window`. API-key providers use the effective context length from Settings, explicitly labeled `configured` rather than `chatgpt_catalog`.

The numerator is the input of ONE current/latest MAIN physical model request. It is not cumulative thread usage, agent/team usage, account quota, or exact continuously growing hidden state. Requests replace previous observations. Input remains fixed while generating; output, reasoning, cached-input discounts and unsent drafts never change it.

### Global constraints

- Unknown is NOT zero.
- Unknown ChatGPT catalog capacity stays unknown, NEVER fallback to API-key settings or inherited model budget.
- Never pair old input with new-model capacity.
- Preserve UsageChip/account Manage usage/accounting and compaction policy.
- No arbitrary warnings/analytics/DB migration.
- No prompt/credentials/account IDs/token IDs in events.
- Do not change branches, reset, commit or merge.
- Preserve unrelated untracked README/landing plan and scripts/*devhost*.mjs.
- Exclude workspaces/, .worktrees/, build copies from investigation and edits.

## Existing seams and constraints

Lead read the current `ProviderRuntime.swap` in `services/agentd-py/agentd/providers/runtime.py`: candidate ping validates before model/engine/window mutation; absent contextWindow preserves the compaction setting. That setting cannot prove ChatGPT catalog capacity. Lead read `apps/vscode-extension/src/extension.ts` composer/catalog paths: a five-minute registration-only cache and known catalog window forwarding already exist; unknown metadata deliberately leaves Settings capacity intact. Preserve that behavior for compaction while making meter provenance independent.

Lead read `services/agentd-py/agentd/providers/openai_transport.py:368–486`: the ticker estimate uses instructions + serialized input, not the complete schema/tool-bearing body; completed input reporting currently requires output too through `on_usage`. Independent context input observation must run before native/action/no-text decoding errors. The early first-action return has no completed usage and cannot be reported as exact input. Lead read `chatgpt_auth/service.py:23–88,120–121`: existing catalog parsing supplies strictly positive numeric context windows and rejects booleans.

Lead read `apps/vscode-extension/src/controller.ts:2139–2270`: polling captures a thread before await without a post-await navigation guard, and generic signature dedup can return early. Context acceptance goes before that dedup and requires additional fetch/navigation/connection identities. Lead read `/live` route in `services/agentd-py/agentd/api/routes.py:1450–1518`; it is the current live-state overlay seam. Lead read `webview-ui/src/settings/sections/ProviderSection.tsx:147–173`: the existing Context window (tokens) field is the API-key setting. Lead read `webview-ui/src/components/InputArea.tsx:339–447`: the nonwrapping footer holds model/effort/settings/UsageChip plus task abort/toggles/Send/Stop; wrapping groups must preserve action availability.

Additional grounding supplied and checked by backend/review in team posts 3/5/8/9: native `SessionInputs.build` and `actions.tools` are assembled in `openai_transport.py:246–268`; `token_progress.int_or_none` is unsuitable strict input validation; API-compatible `on_usage` is winning-decoded-logical-call accounting and must not be changed. `CredentialRecord` lacks auth-session epoch, OAuth reuses registration, and refresh changes saved_at, so neither registration nor saved_at identifies a new auth session. Frontend/review posts 2/4/7 locate strict `/live` parsing, auth refresh callbacks and UsageChip's cumulative meaning. These are attributed implementation seams; owners must read exact functions before editing.

## Wire contract

SSE event name: `context_usage`. Its JSON data is the object below. `/live` provides the IDENTICAL object as `context_usage` plus an independent top-level `process_id:string`, even when telemetry is unavailable. Nested process_id must match top-level process_id. Context validation failure must not discard unrelated live gates, teams, provider access or transcript state.

| Field | Type / meaning |
| --- | --- |
| thread_id | nonempty string, owning thread |
| provider | string, request/selected backend |
| model | string, request/selected model |
| scope | literal `main` |
| process_id | nonempty opaque backend-instance string |
| generation | nonnegative safe integer, per-thread epoch |
| revision | nonnegative safe integer, monotonically increasing publication order per thread within one process; not reset across generations |
| turn_id | string or null |
| request_id | opaque internal string or null; not provider/account/token identity |
| status | `awaiting_request`, `in_flight`, `last_request`, `invalidated`, `unavailable` |
| input_tokens | nonnegative safe integer or null; genuine zero valid |
| input_quality | `estimated`, `provider_reported`, `unavailable` |
| context_window | positive safe integer or null |
| capacity_source | `chatgpt_catalog`, `configured`, `unknown` |
| observed_at | UTC ISO8601 string or null; measurement time, not fetch time |

Safe integers are at most 9007199254740991. Booleans, strings, fractional, negative and nonfinite token values are invalid. Null input iff input_quality is unavailable. Null capacity iff capacity_source is unknown. Awaiting/invalidated/unavailable have null input; no observation has null observed_at. Client may consistently map fields to camelCase internally, but the wire spelling and enums above are shared.

An example completed observation (not a test result):

```json
{"thread_id":"thread-a","provider":"chatgpt","model":"selected-model","scope":"main","process_id":"opaque-process","generation":4,"revision":9,"turn_id":"turn-a","request_id":"opaque-request","status":"last_request","input_tokens":48960,"input_quality":"provider_reported","context_window":272000,"capacity_source":"chatgpt_catalog","observed_at":"2026-10-08T12:00:00Z"}
```

## Backend state and observation

Create a focused per-thread process-local state owner. Emit the same snapshot for SSE and /live; no persistence migration. Generation advances for EACH physical dispatch, retry/fallback and lifecycle invalidation. Revision advances for estimates, reports, finish and capacity enrichment. Captured generation/request/provider/model/auth identity guards prevent retired callbacks from publishing. Callback isolation must not fail the underlying chat.

At dispatch, estimate the FULL assembled request including instructions, input/history, native session tails, tools and applicable schemas. Publish in-flight approximate input once, after assembly. A physical retry replaces it with a new generation. A valid response.completed INPUT independently upgrades quality even if output usage is absent or action decoding subsequently fails. Count cached input as input, without subtraction. Existing on_usage/accounting callbacks keep their own policy. Failure, incomplete, cancellation and early-return settle a valid estimate as last_request or show unavailable when no observation exists; they never fabricate provider_reported quality. Unsupported transports explicitly show unavailable.

Only MAIN loop requests get observers. Background agents/teams, validation pings, compaction summarization and other unrelated calls do not overwrite the main meter. Finally/cleanup settles only the still-current request. Process restart starts a new process_id and no previous input; reopen in the same process hydrates retained last-request state.

## Capacity and authentication

ChatGPT capacity comes only from trusted account/model catalog metadata resolved internally, never from a webview-supplied number or inherited compaction window. Keep a numeric-only internal cache scoped to account/auth-session/model, finite TTL and coalesced concurrent resolution. Use cached known capacity immediately after successful selection; otherwise publish unknown capacity awaiting request and resolve best-effort in the background. Catalog failure or unknown model stays unknown; catalog fetching never blocks chat or runs per output tick.

API-key capacity is the effective successful Settings contextWindow value, including runtime initialization from that setting. Successful provider/model/account/auth-session/configured-window changes clear prior input and publish a versioned awaiting/invalidation snapshot. Candidate validation rejection preserves state. A reasoning-effort-only/no-op swap preserves context unless effective capacity identity changes.

Add a backward-compatible private auth-session epoch. Successful OAuth sign-in/reconsent creates a new epoch; sign-out retires it; ordinary token refresh preserves it. Epoch and account identity stay backend-internal. Only affected ACTIVE-account changes invalidate the meter; unrelated account sign-in/sign-out must not erase active context. Requests/catalog fetches captured under a retired epoch cannot restore state. Capacity enrichment may enrich the CURRENT same-identity snapshot, never resurrect captured old input or change its measurement time.

Frontend registration-only catalog caching requires explicit invalidation with its own epoch and post-await guard. Successful same-registration sign-in, reconsent, sign-out and account activation refresh composer/catalog/context. Settings/model mutation success refreshes authoritative /live; frontend must not merge a prior-model numerator with a new local denominator.

## Lifecycle and recovery

Compaction, rewind and history replacement bump generation and publish versioned invalidated/unavailable tombstones, not null state and not subtracted evicted-message counts. Successful identity changes retire in-flight callbacks. Rejected mutation and ordinary refresh are not invalidations.

One host acceptance path handles valid SSE and /live snapshots. Guarded /live handshake alone establishes backend process_id, independent of context validity. Unknown-process SSE is not accepted and schedules hydration. For the same thread/process, reject a rollback in either generation OR revision, including higher-generation/lower-revision payloads. Revision increases across generation changes, not globally across threads. Duplicate snapshots do not rewrite measurement time. Only a guarded /live handshake establishing a new process resets ordering. Separate navigation/fetch epoch and connection/client identity guards rechecked after await prevent A–B–A late responses, stale streams and restarted-client data from repopulating current state.

Navigation clears local view only. Reopening hydrates backend-retained state. Panel reload replays dedicated context view. Context acceptance precedes generic /live signature dedup. Disconnect keeps a visibly stale retained value; successful unchanged polling clears stale without changing observed_at. A guarded handshake with a new process replaces prior-process state with unavailable/awaiting until a new observation.

## UI and accessibility

Separate compact ContextMeter adjacent to main composer model/footer controls; keep UsageChip unchanged. Visible labels:

| Observation | Visible treatment |
| --- | --- |
| In-flight estimate | `Context ~18%` |
| Last provider-reported input | `Last context 18%` |
| Last estimate only | `Last context ~18%` |
| Known input, unknown limit | tokens + `limit unknown`; no percentage/bar |
| No input after selection | awaiting request; known capacity can appear in details |
| Unsupported/no observation | unavailable, never fake 0 |
| Disconnected retained value | visibly stale modifier |

Fill clamps to 0–100%, while details retain raw over-limit count/ratio. Genuine zero has a truthful 0% treatment distinct from no observation. Details show count, window, request model, capacity source, quality/time and an explanation that this is one main request's input, not ChatGPT plan usage. Approximation/current-vs-last must be visible without opening details.

Use existing styling and a keyboard-focusable details button with expanded semantics, click/focus access and Escape dismissal. Do not rely on color alone. Keep announcements restrained rather than per-token live-region updates. Footer wraps logical groups; Send/Stop and abort controls remain visible, usable and nonshrinking at narrow widths.

## Acceptance and evidence gates

Automated backend: full native/tool/schema request estimate; estimate-to-reported; input-only and invalid-action completed response; missing/string/bool/negative/fractional input rejected and zero accepted; fixed streaming/replacement; failed/incomplete/cancel/early return; catalog-only known/unknown and configured capacity; accepted/rejected/no-op swap; active vs unrelated account auth changes; ordinary refresh; compaction/rewind/reset/late callbacks; TTL/coalescing and retired catalog results; SSE/live equality and privacy.

Automated frontend: malformed telemetry isolation; top-level handshake and nested match; older/duplicate generation/revision, unknown-process hydration and restart; SSE/poll/missed SSE/reload/reopen; A–B–A and connection replacement; unchanged reconnect clears stale; model/settings/auth mutation success vs rejection; cache epoch; meter zero/partial/100/over-limit/unknown; visible labels, keyboard/Escape and usage/access regressions.

Manual when available: real signed-in ChatGPT catalog/model/account/auth change smoke; API-key Settings capacity; webview narrow footer/Send/Stop, keyboard/focus/Escape and light/dark styling. Mock tests do not establish actual signed-in behavior or layout. Record unavailable smoke as a limitation, stop temporary servers, run fresh focused tests/typechecks/builds after final edits, and obtain independent read-only code review. Docs/tasks describe intended acceptance, not evidence of passing checks.
