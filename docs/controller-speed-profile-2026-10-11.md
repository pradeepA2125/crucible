# Controller speed profile — 11 October 2026

Earlier performance captures exist from 10 October. Profiling was off during the recent
prompt and batching experiments. These captures enable the existing timing hooks for two
fresh chats against the installed extension's backend. The backend configuration was left
unchanged; the current batching penalty wording was present in the hello request.

## Completed hello

Thread: `chat-9cb1fea46c4d`. Response: `Hello! How can I help you today?`
One model action (`answer`); no workspace tool calls.

| Measurement | Time |
|---|---:|
| HTTP POST to response/completion SSE | 35.290 s |
| Retrieval seed construction | 27.861 s |
| Provider request | 6.790 s |
| Provider response headers, included in provider time | 2.337 s |
| System prompt construction | 3.710 ms |
| Step payload construction | 0.172 ms |

Provider usage: 50,397 input tokens, zero cached input tokens, 206 output tokens, including
168 reasoning tokens. Even the greeting received the normal repository context.

Within retrieval, `_normalize_snapshot_path` ran 98,271 times and consumed 24.588 seconds
inclusive under cProfile. The current snapshot contains 84,563 graph nodes with 4,356 distinct
file paths. Normalization occurs per graph node in `_build_context`, so reuse of normalized
paths is a concrete optimization candidate. This profile does not measure its benefit yet.

Raw measurements: [hello JSON](controller-speed-hello-2026-10-11.json).

## Interrupted two-file read capture

Thread: `chat-9f39752b7bff`. Prompt requested lines 1–700 of `controller_prompts.py` and all
of `batch_history.py`, in one batch, followed by an explanation. The user explicitly approved
this source transmission after automatic approval review rejected the initial attempt.

The profiling client had a fixed 30-second socket timeout. Retrieval delayed HTTP headers
longer than that, so the client disconnected. Its finally block disabled the watchdog without
stopping the backend. An explicit stop request subsequently closed the backend capture at
76.095 seconds. The intended 55-second bound was therefore not maintained. No final answer
was captured, and this is not a completed response-latency measurement.

| Measurement | Time |
|---|---:|
| Retrieval seed construction | 48.790 s |
| Three provider requests, including the cancelled request | 26.548 s |
| First two reads, overlapping execution interval union | 4.821 ms |
| Individual first-batch read durations | 3.353 ms / 2.757 ms |
| Overlap between the two tool spans | 1.289 ms |
| System prompt construction across three steps | 13.054 ms |

The first batch issued and executed two reads; pool high-water was two. These are overlapping
tool execution intervals, not physical disk-access timestamps. A second model action reread
lines 241–250 solely for a focused citation, despite acknowledging the requested reads had
succeeded. The prompt change has not eliminated redundant reads.

The longer prompt also made graph scoring expensive: `_dir_seg_score` was called 17,050,446
times, consuming 6.574 seconds of self time under cProfile. Path normalization and repeated
graph scoring deserve measurement before micro-optimizing the few-millisecond file reads.

Raw measurements: [batch JSON](controller-speed-batch-2026-10-11.json). Backend records were
recovered after explicit cancellation; missing HTTP/SSE timings are not reconstructed.

## Limits and next measurements

These are two diagnostic captures, not a latency distribution or an A/B speedup measurement.
The older capture used a different model and repeated tool loop, so its total latency is not
a matched baseline. cProfile adds substantial overhead to code making millions of Python
calls; the retrieval timings above must not be presented as ordinary uninstrumented latency.
Provider spans combine remote inference and network time. Extension IPC and webview rendering
are excluded.

`scripts/profile-live-hello.py` now allows response-header timeouts consistent with the requested
diagnostic deadline and explicitly stops the backend on client errors before disabling its
watchdog. The script passed Python compilation, Ruff, and diff checks; no additional live
request was made to test the change.

Profiling was disabled after these captures. Recommended next comparisons are repeated
unprofiled fresh versus existing chats with the same model, and normalized-path caching with
matched profiled inputs. Retrieval is pinned per thread, so an existing-chat follow-up should
reuse it rather than rebuilding the seed.
