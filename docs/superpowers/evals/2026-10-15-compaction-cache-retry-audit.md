# Compaction, memory, and retry cost audit

## Evidence

The local backend log for `chat-19c1214a10a4` reports:

| Completion time (IST) | Input tokens | Cached input | Session header | Outcome |
| --- | ---: | ---: | --- | --- |
| 23:29:34 | 172,487 | 0 | absent | summary rejected |
| 23:31:53 | 172,487 | 0 | absent | summary rejected |
| 23:35:06 | 172,583 | 0 | absent | summary rejected |
| 23:37:11 | 172,583 | 0 | absent | summary rejected |

These were completed provider responses rejected by local note validation. The
old log does not preserve the response or distinguish which validation check
failed. Its exact format cannot be reconstructed from the log.

### Quota interpretation correction (October 16)

Provider usage telemetry for the continuation trace reports nine completed
responses: 3,116,452 input tokens, including 547,072 cached tokens, leaving
2,569,380 uncached input tokens; 25,427 output tokens (including 1,938 reasoning
tokens). The first completed main request reported 476,875 input tokens, of
which 16,768 were cached. These are provider counts, not UI context estimates.

Four of five main responses had approximately 3% cache reuse despite carrying
session headers. The compaction header omission does not explain these main-path
misses. Replaying the saved logical request snapshots through `to_native_input`
and `SessionInputs` shows a stable instruction hash and session ID, but prefix
changes: `controller-turn-00` to `01` adds an image-budget tombstone to an earlier
tool result; `04` to `06` inserts a memory/image-tombstone message at the beginning
of the history after failed compaction; `06` to `07` expands that leading message.
These changes reset the local append-only input assembly and explain why a stable
session header did not preserve the full conversation prefix. The `01` to `04`
transition remains append-only, matching the one 98% cache-hit response.

The user subsequently confirmed that the newly selected account's Usage page
showed its five-hour allowance exhausted, in addition to Crucible's refusal. An
app-specific-only cap therefore does not explain their observation. We still
have no before/after allowance measurements for the nine requests, no historical
per-request account identity, and no verified conversion from these input/output
counts to included subscription percentages. The repeated misses explain high
input processing, but do not independently prove a 0%-to-100% allowance debit.

The first pair reused the same prompt; the second pair reused another prompt.
The text entry point bypassed the native JSON path that attached `session_id`.
Prior repo experiments document zero cache reuse without that header on this
ChatGPT plan route. Provider cache hits remain best effort; request-shape tests
cannot guarantee live cache hits or quantify subscription-limit discounts.

## Changes

| Path | Behavior after this change |
| --- | --- |
| Shared OpenAI `_run` | Plan text and strict JSON requests receive stable stream headers before the retry loop. Existing native headers remain intact. Unowned work has a transport-scoped fallback. |
| Transient / 401 retries | Preserve request input and session header; 401 refresh remains bounded to one attempt. |
| Account quota refusal | No transport retry. Compaction propagates the refusal to the existing account-access UI. |
| Compaction | Uses `generate_json` with `summary_markdown`; plan mode uses a forced native response function, API-key mode uses strict JSON. |
| Local summary rejection | Logs a reason and character count; no automatic full-prompt validation retry. Accepts ordinary bold Markdown headings without requiring XML delimiters. |
| Failed foreground compaction | Retains unsummarized history and any committed fold progress, then stops before another foreground request. No misleading successful-compaction event. |
| Failed child / task compaction | Stops the activation instead of continuing inference or reporting successful completion. |
| Consolidation / curation | Already used JSON. Covered by shared session handling, including unowned requests. |
| Idle curator quota refusal | Suspends sweeps until a validated provider switch. An in-flight failure from an older provider cannot suspend the newly selected provider. |
| Auxiliary accounting | OpenAI calls with no engine usage callback record prompt/output totals. Curator usage has a dedicated owner and is added to thread totals on exit. |

Failed summary content is not logged. Existing raw evidence and edits remain
available; failure does not discard them to make the conversation fit.

## Verification

All verification uses local fake providers, streams, and temporary databases.
No live model requests were made. Regression coverage checks stable headers for
text, strict JSON, native JSON, independent repeats, transient retries, and token
refresh; account-limit non-retry; structured compaction and auxiliary accounting;
foreground/task stopping; curator suspension and provider-switch recovery.

The related transport, memory, controller/task, image-budget, provider-switch,
and context-observation suites passed (310 tests), followed by a passing
regression for quota refusal after a partially committed evidence fold. Core
modified transport/memory files pass Ruff. Existing unrelated lint findings in
the large controller/task loop modules were left unchanged.

The running backend needs a restart to load these Python changes. It was not
restarted during the audit.
