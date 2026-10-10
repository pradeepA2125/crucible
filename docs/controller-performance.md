# Profiling controller latency

The backend has opt-in wall-time spans and Python's built-in `cProfile` for synchronous prompt construction. No extra package or MCP server is required. Captures contain block/function names and timings, without prompts, tool arguments, results, URLs, or credentials. Function identifiers use repository-relative or library basenames and line numbers.

From the repository root:

```sh
python3 scripts/profile-controller.py start
# Reload VS Code once after installing the instrumentation, then run chat turns.
python3 scripts/profile-controller.py report
python3 scripts/profile-controller.py report --all
python3 scripts/profile-controller.py stop
```

Use `--workspace /path/to/workspace` when profiling another workspace. Each workspace opts in through `.crucible/state/performance.enabled`. Profiling is off by default; disabling it affects subsequent turns, while active captures finish normally. Capture history appends to `performance.jsonl`; retain or remove that file between independent experiments as needed.

Reports group repeated spans with counts, inclusive time, self time, p50, p95, and maximum duration. Self time subtracts the union of direct child spans, so overlapping children are counted once. It includes awaits and must not be interpreted as Python CPU time. Concurrent sibling totals can overlap. `controller.turn` covers the loop. `controller.backend_turn` additionally covers controller setup and retrieval; backend startup, earlier HTTP handling, and webview rendering remain outside that boundary.

Instrumented blocks include controller turns, model steps, provider requests, rate-limit waits, OpenAI authentication/response headers/streaming/retry waits, approval waits, individual tools, Exa network calls, fetch network/conversion, and prompt/payload construction. The OpenAI first-content milestone measures time to the first text, function-argument, or reasoning delta; it includes response-header latency and is already included in provider time. Other providers still get request and rate-limit spans, without OpenAI-specific streaming milestones.

`cProfile` captures synchronous system-prompt and payload construction. Function rows aggregate the top 40 functions by self time from each capture; they are a bounded view of this code, not a whole-process CPU profile. Timings include profiling overhead. For whole-process CPU attribution, a sampling profiler can complement these spans in a separate experiment. A small number of chat turns is a diagnosis, not a reliable tail-latency benchmark.

The report command writes:

- `.crucible/state/performance-report.md`: readable grouped tables.
- `.crucible/state/performance-report.json`: grouped measurements.
- `.crucible/state/performance-trace.json`: Chrome trace-event format for a local trace viewer, with separate span lanes to show overlap.

It selects the latest completed turn by default. `--all` aggregates completed turns and excludes in-flight captures. Failed or cancelled controller runs still close their root span and carry `error_or_cancelled` status in the raw trace. Keep the model, reasoning effort, prompt, tools, context size, provider, and approval policy fixed for comparisons; collect multiple runs and compare the distributions. Provider time combines remote processing and network latency; this instrumentation cannot split those two precisely.

Numeric context rows now separate logical payload/system blocks from the final prepared physical request, including native schemas and accumulated session tails. Character/token estimates are labelled; OpenAI usage rows record actual input, cached input, output, and reasoning tokens when reported. Nested retrieval fields already contribute to the payload total and must not be added again.

Use `python3 scripts/profile-controller-context.py <controller-turn-artifact.json> --output /tmp/context.md` for a content-free component report and measured tree/catalog candidates. Older artifacts cannot attribute individual system blocks. New artifacts contain formatter component counters, including project/skill/child/team guidance. `python3 scripts/benchmark-controller-latency.py --output /tmp/latency.json` runs five matched real-command fixtures with simulated model/web delays; it makes no network or live-provider speed claim.
