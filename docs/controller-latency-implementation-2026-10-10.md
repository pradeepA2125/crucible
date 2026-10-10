# Controller latency implementation checkpoint

Implemented on `codex/controller-latency`, with independently controlled settings:

| Managed setting | Backend environment | Default |
|---|---|---|
| `crucible.performance.toolBatching` | `CRUCIBLE_TOOL_BATCHING_ENABLED` | off |
| `crucible.performance.adaptiveReads` | `CRUCIBLE_ADAPTIVE_READS_ENABLED` | off |
| `crucible.performance.compactContext` | `CRUCIBLE_COMPACT_CONTEXT_ENABLED` | off |

Changing a setting requires restarting the backend. Disabling it restores the conservative schema/read/seed policy for subsequent work. Previously saved batch history remains replayable, including under a terminal-only schema; existing pinned retrieval seeds retain their original bytes.

## Measured scheduler behavior

Run `services/agentd-py/.venv/bin/python scripts/benchmark-controller-latency.py --output /tmp/latency.json`. Five repeats per configuration use two actual Python subprocesses and a simulated fetch. Each simulated model response costs 50ms; each tool fixture waits 100ms. This isolates scheduling and model-round-trip elimination; it is not a live model comparison.

| Scenario | Median seconds | Range seconds | Model requests | Tool results |
|---|---:|---:|---:|---:|
| Independent, serial | 0.661 | 0.624–0.669 | 4 | 3 |
| Independent, batch | 0.289 | 0.281–0.300 | 2 | 3 |
| Dependency chain, serial | 0.668 | 0.642–0.684 | 4 | 3 |
| Dependency chain, batch | 0.547 | 0.528–0.562 | 2 | 3 |

The independent fixture improves by 56%; the ordered chain by 18%. Dependents run only after successful predecessors. `after` allows diagnostics after failure; argument values derived from earlier output require another model step. Unknown commands/tools remain conservatively serialized, while explicit parallel commands are still checked against known shared resources. Arbitrary shell effects cannot be fully inferred; model hints do not grant permissions or prove isolation.

Raw runs: [benchmark JSON](controller-latency-benchmark-2026-10-10.json).

## Prompt resizing evidence

The retained real smoke has 185,974 logical input characters. Retrieval accounts for 54.4%; the embedded tool catalog for 24.1%. Applying the implemented policies to those disjoint components saves 82,492 characters, or 44.36%: catalog whitespace saves 7.16%; a bounded tree saves 37.20%. These are character measurements, not actual token or latency reductions. The tree includes an explicit omission/expansion marker; every tool/schema remains in the compact catalog.

A controlled rendering of the current main prompt with that same tool catalog separates the previously unattributed rules:

| Component | Characters |
|---|---:|
| Core rules | 14,808 |
| Catalog before / after | 44,883 / 31,565 |
| Web guidance | 2,605 |
| Memory guidance | 660 |
| MCP guidance | 686 |
| Session guidance | 1,157 |
| Dispatch guidance | 3,789 |
| Main-team guidance | 3,381 |

This controlled rendering enables task/memory guidance and omits project instructions, skills, persona, and batch guidance; it does not reconstruct the old complete prompt. New request instrumentation measures those blocks when present. Removing the entire memory block would recover only 0.35% of the retained total and lose behavior. The catalog/tree changes provide much greater room while preserving instruction semantics. No static guidance or tool families were deleted.

See [baseline attribution](controller-context-baseline-2026-10-10.md), [current formatter counters](controller-prompt-components-2026-10-10.json), and [profiler usage](controller-performance.md). Logical attribution and final physical request sizes are recorded separately; actual provider cache/usage counters allow subsequent live comparisons.

## Correctness and rollout

Validation: **3,304 backend tests passed, 5 skipped** (optional dependency/service/platform coverage); **491 extension tests passed**; all three packages typechecked. The final command-guard integration also passed its 34-test focused suite. Existing optional reranker warmup warnings report a missing `sentence_transformers` dependency.

Tests cover graph rejection before effects, command overlap, dependency failure/diagnostics, approval waits outside worker leases, cancellation and process grandchildren, restart without replay, provider stream ordering, durable result identity, terminal-only history replay, 900-line reads, bounded pathological files, empty-read evidence, and small/unknown capacity budgets. UI routing is tested with out-of-order results. Main, child, and team controller paths share the implementation.

A full-suite check exposed an existing orphan-process reaper bug when an executable path contains spaces. The fix recognizes both recorded and resolved paths before checking identity; live-process and PID-mismatch regression checks pass. The scripted engine also accepts the context observer already forwarded by its subclasses.

The local installer successfully built and installed the VSIX and indexer. The managed runtime imports `agentd` from this checkout. Live provider/cache comparisons, a live installed main/child/team smoke after enabling the flags, and default promotion remain rollout checkpoints. Feature defaults stay off pending these checks. Lazy retrieval, lazy tool discovery, and broader static prompt rewriting remain conditional future work.
