# Local profiling smoke: 10 October 2026

Fresh Crucible chat in VS Code, GPT-5.6-Terra, medium reasoning effort. Prompt:

> Which Node.js version should I use for a new production service today, and on what date does that version reach end of life? Keep it brief.

The answer completed successfully after a skill read, one Exa search, and three native fetch calls. Six model requests were made; one transient provider error caused an extra streaming attempt and retry backoff. Two approval waits were observed. This is one diagnostic run, not a comparison against other harnesses.

| Non-overlapping category | Seconds | Share of controller wall time |
|---|---:|---:|
| Provider requests excluding retry backoff | 56.335 | 80.7% |
| Provider retry backoff | 5.855 | 8.4% |
| Approval waits | 3.995 | 5.7% |
| Search and fetch execution | 2.922 | 4.2% |
| Remaining controller/local work | 0.705 | 1.0% |
| Total | 69.812 | 100% |

Provider request time combines network, remote model processing, and a small amount of local transport work. It cannot attribute latency precisely to remote inference versus network. Prompt and payload construction took about 33ms total across six model steps; the largest captured synchronous function group was JSON encoding. The dominant bottleneck in this run was repeated provider round trips, with a material retry penalty. These numbers do not establish that all Python work is fast on every workload, or quantify any speed difference versus Codex/Claude/OpenCode.

Candidates to measure next: reduce unnecessary model/tool round trips, evaluate prompt size with the same model and task, and collect repeated runs to distinguish ordinary provider latency from transient errors. Compare independent tool batching only where dependencies and approvals allow it. No execution or approval behavior was changed by this profiling work.

Profiling was disabled after the capture. The raw capture remains under `.crucible/state/performance.jsonl`; the local timeline is `performance-trace.json`. The grouped report below is preserved for review.

# Crucible performance capture

1 completed turn(s), 69.812s total wall time.

| Block | Calls | Self total (s) | Inclusive total (s) | p50 (s) | p95 (s) |
|---|---:|---:|---:|---:|---:|
| provider.stream | 7 | 34.042 | 56.219 | 6.978 | 15.807 |
| provider.response_headers | 7 | 22.177 | 22.177 | 2.188 | 6.256 |
| provider.retry_wait | 1 | 5.855 | 5.855 | 5.855 | 5.855 |
| approval.wait | 2 | 3.995 | 3.995 | 1.373 | 2.622 |
| web.search_network | 1 | 1.571 | 1.571 | 1.571 | 1.571 |
| web.fetch_network_and_conversion | 3 | 1.351 | 1.351 | 0.476 | 0.586 |
| controller.turn | 1 | 0.609 | 69.812 | 69.812 | 69.812 |
| provider.auth | 7 | 0.074 | 0.074 | 0.000 | 0.071 |
| provider.request | 6 | 0.041 | 62.190 | 6.983 | 19.339 |
| model.step | 6 | 0.036 | 62.259 | 6.991 | 19.354 |
| context.system_prompt | 6 | 0.032 | 0.032 | 0.004 | 0.008 |
| tool.read_skill | 1 | 0.016 | 0.016 | 0.016 | 0.016 |
| tool.web_fetch | 3 | 0.007 | 2.731 | 0.477 | 1.965 |
| tool.web_search | 1 | 0.005 | 4.197 | 4.197 | 4.197 |
| context.payload | 6 | 0.001 | 0.001 | 0.000 | 0.000 |
| provider.rate_limit_wait | 6 | 0.000 | 0.000 | 0.000 | 0.000 |

First-content milestones (included in provider time):

- provider.first_content: 6 calls, mean 6.436s, max 12.391s

| Synchronous function | Calls | Self (ms) | Inclusive (ms) |
|---|---:|---:|---:|
| encoder.py:334:_iterencode_dict | 81552 | 12.933 | 18.600 |
| encoder.py:278:_iterencode_list | 27468 | 3.965 | 21.796 |
| encoder.py:414:_iterencode | 24756 | 2.653 | 24.450 |
| encoder.py:183:encode | 6 | 1.854 | 26.905 |
| ~:0:<built-in method builtins.isinstance> | 20967 | 1.500 | 1.500 |
| ~:0:<built-in method _json.encode_basestring_ascii> | 8046 | 1.309 | 1.309 |
| ~:0:<built-in method builtins.sorted> | 1908 | 1.274 | 1.274 |
| ~:0:<method 'disable' of '_lsprof.Profiler' objects> | 12 | 1.070 | 1.070 |
| controller_prompts.py:912:format_controller_system_prompt | 6 | 0.755 | 30.842 |
| ~:0:<built-in method builtins.id> | 2334 | 0.690 | 0.690 |
| ~:0:<method 'join' of 'str' objects> | 45 | 0.599 | 0.663 |
| tagged.py:184:render_prompt | 8 | 0.448 | 0.878 |
| config.py:26:from_env | 6 | 0.257 | 0.523 |
| __init__.py:183:dumps | 6 | 0.255 | 27.164 |
| ~:0:<method 'replace' of 'str' objects> | 12 | 0.235 | 0.235 |
| ~:0:<method 'items' of 'dict' objects> | 1908 | 0.182 | 0.182 |
| ~:0:<method 'get' of 'dict' objects> | 1222 | 0.163 | 0.163 |
| ~:0:<method 'finditer' of 're.Pattern' objects> | 467 | 0.112 | 0.112 |
| ~:0:<built-in method _io.open_code> | 1 | 0.112 | 0.112 |
| controller_prompts.py:1058:build_controller_step_payload | 6 | 0.108 | 0.164 |
| ~:0:<method 'validate_python' of 'pydantic_core._pydantic_core.SchemaValidator' objects> | 6 | 0.091 | 0.091 |
| ~:0:<built-in method builtins.any> | 30 | 0.065 | 0.401 |
| controller_prompts.py:990:<genexpr> | 264 | 0.062 | 0.092 |
| controller_prompts.py:985:<genexpr> | 240 | 0.056 | 0.082 |
| <frozen os>:709:__getitem__ | 75 | 0.056 | 0.137 |

Self time excludes direct child spans, but includes awaits. It is not CPU time. Concurrent span totals may overlap. First-content latency is a milestone, not an additional execution block. Function profiles cover synchronous prompt construction only; timings include profiler overhead.

Function rows retain the top 40 self-time entries from each profiled call. p50/p95 describe this capture, not a production benchmark. Controller time excludes request handling before the controller starts.
