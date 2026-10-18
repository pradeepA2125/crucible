# Discovery retrieval cache optimization

Implemented on `codex/sidecar-reliability-experiments`. The live backend has not been restarted. Qwen remains an experimental scorer and was not adopted by this change.

Warm retrieval median fell from roughly **300–314 ms to 92–95 ms** in the actual local hybrid pipeline. The keyword, semantic and graph scoring rules remain unchanged. Full candidate discovery/delivery measurements were:

| Configuration | Before median | After median | Before p95 | After p95 |
|---|---:|---:|---:|---:|
| BGE selector + reranker | 339 ms | 143 ms | 391 ms | 197 ms |
| Qwen plain context | 699 ms | 473 ms | 826 ms | 674 ms |

Each run used seven fixture queries and three warm repetitions on the same development Mac with Metal. Cold model loading and an initial pass over the query shapes were excluded. These are separate sequential live-index measurements; workspace snapshots can change between runs. The separate frozen comparison below establishes output parity. The first query after model loading still took about 417 ms with BGE and 800 ms with Qwen: snapshot preparation is not eliminated, just reused.

## Changes

- `discovery_cache.py` retains one compact discovery metadata snapshot per retrieval client, with repeated strings shared within the snapshot. It excludes diagnostics and arbitrary extra payload fields. Graphs over 64 MiB serialized, 100,000 nodes or 300,000 edges fall back to the uncached path.
- `artifact_client.py` reuses normalized discovery nodes, node/file lookups and incident-edge indexes for that exact snapshot. Edges are selected through lookups while preserving original order and parallel edges. Path eligibility is computed once per file per request. Query scoring, sorting and result selection still execute for each request.
- The cache key includes file path, device, inode, size, nanosecond modification time and change time. Snapshot replacement invalidates it even when the graph generation field or modification time is unchanged. Changed-during-read snapshots are retried; unreadable, deleted or malformed snapshots never fall back to a previously cached snapshot. Age warnings are recomputed on cache hits. Existing source-read/write guards remain authoritative.
- Full planner retrieval keeps its existing non-discovery path. Local-model opt-out still bypasses semantic selection and reranking; keyword/graph candidate retrieval uses this optimization too. Catalog limits and visible-read suppression still apply.

## Controlled comparison

The old source and one snapshot were frozen under the ignored `.tmp/retrieval-optimization` directory before editing. Actual semantic matches were captured once for all 66 earlier benchmark cases. Old and new clients then ran separately with identical snapshot, requests and semantic matches. This isolates metadata processing and excludes embedding/vector lookup from these timings.

| Frozen metadata-processing comparison | Before | After |
|---|---:|---:|
| Warm median | 261 ms | 62 ms |
| Warm p95 | 298 ms | 96 ms |

All 66 discovery contexts matched exactly across repeated runs, excluding advancing snapshot age: candidate rows, symbols, ranges, file ordering, graph neighbors, relationships and other context fields. The metadata-processing median reduction was **76%**. This preserves the existing retrieval behavior, including its known relevance limitations; it is not a retrieval-quality improvement or proof of general ranking correctness.

The retained graph and lookups occupy approximately **34.4 MiB** of Python objects for this snapshot, counting shared objects once. This excludes allocator and model overhead. Caching adds resident metadata between requests; graph replacement still incurs parsing/preparation and temporary peak allocations. Caps bound retention by input size and counts rather than a strict process-memory budget. Query scoring still scans symbols, so this does not introduce an inverted keyword index or constant-time retrieval.

## Verification and evidence

**104 focused backend tests passed**, including 14 new cache tests, retrieval/planner regressions, candidate lifecycle, local-model gating, context selection and delivery. New checks cover query-dependent output, snapshot replacement with unchanged generation/time/size, deletion/corruption, change during read, concurrent cache readers, oversized fallback, snapshot-age warnings and duplicate IDs/parallel edges. New cache code passes strict mypy and Ruff; the edited artifact client introduces no additional Ruff diagnostics beyond its pre-existing diagnostics. Git whitespace validation passes.

Historical files preserved under `docs/experiments/2026-10-18-instruction-reranker/` inside [the experiment archive](archive/sidecar-experiments-2026-10-19/README.md):

- `benchmark_discovery_cache.py`, `discovery-cache-before.json`, `discovery-cache-after.json`: controlled output fingerprints and timings. Reproduction requires the ignored frozen source, snapshot and semantic-match fixtures.
- `profile_pipeline.py`, `pipeline-optimized-bge.json`, `pipeline-optimized-qwen.json`: real hybrid/controller/delivery measurements. Original `pipeline-bge.json` and `pipeline-qwen.json` are preserved. A result tag selects a new output file rather than overwriting prior evidence.
- `discovery-cache-memory.json`: retained-object measurement and accounting limits.

No provider calls, index rebuilds, global model-setting changes or live backend restart were performed for this historical optimization. No source bodies or verification manifests were added to the candidate payload. The automatic per-turn candidate cache used in those trials has since been retired. On-demand `search_index` retains the versioned parsed graph and lookups described here; background startup/change warming is documented in [the warmup report](search-index-cache-warmup-2026-10-19.md).
