# Background discovery cache warmup

Implemented on `codex/sidecar-reliability-experiments` and activated in the workspace backend, PID 85309, on-demand delivery preserved. No candidate reranker or automatic candidate delivery was enabled by this change.

## Behavior

- Startup schedules a background worker to parse and compact the current snapshot, normalize node paths, and prepare node/file and incident-edge lookups. It runs independently of local-model availability.
- Rust's existing post-snapshot `/v1/index/build` notification now warms discovery even when semantic retrieval is disabled. Semantic indexing remains optional.
- A one-second file-version poll covers missed notifications and snapshot writes by other processes. A 150 ms debounce coalesces observed changes before preparing the latest revision. Concurrent warmup calls reuse preparation for the same revision.
- Discovery preparation has its own cache, so planner or semantic-index preparation cannot evict it. Query scoring, semantic lookup when available, filtering, sorting and candidate selection still run for each `search_index` call. No query results or source bodies are cached here; warmup adds no model tokens.

The retained graph uses the existing limits: 64 MiB serialized snapshot, 100,000 nodes and 300,000 edges. Oversized snapshots use the uncached retrieval path. These limits bound input retention rather than strict process RSS; parsing and replacement can require temporary additional memory.

## Freshness and concurrency

Every discovery load checks the snapshot's path, device, inode, size, modification time and change time. A changed revision is loaded rather than served from the prior cache, even before the background worker catches up. A request arriving during preparation can still incur preparation latency. A snapshot changed during preparation is not reported ready; deleted or corrupt snapshots do not fall back to old data. An older in-flight query cannot replace a newer warmed graph.

This establishes freshness relative to the snapshot. It does not establish that the indexer has incorporated every source edit: locations and relationships remain discovery hints, and the agent must read current source before relying on them. Existing in-flight requests can finish against the revision they loaded.

`GET /v1/index/status` exposes `discovery.state`, `warmup_count`, `last_warmup_ms`, graph counts and `generated_at_ms`. `ready` means the warmed revision matches the current snapshot at the status check. `building=false` alone is not a readiness guarantee.

## Validation

86 focused cache/discovery/API/candidate-context tests passed, plus 14 retrieval, semantic lifecycle and startup regression tests. Coverage includes atomic replacement with unchanged generation/mtime, corruption/deletion, changes during preparation, concurrent warmers, old-query publication races, independent planner caches, oversized fallback, worker lifecycle, notification bursts and semantic-disabled API warmup. The new worker and tests pass Ruff; the worker passes mypy. The artifact client still has its pre-existing type diagnostics. Git whitespace validation passed.

Live startup warmed 25,984 nodes and 71,194 edges in 373.03 ms. A subsequent atomic replacement with an identical snapshot preserved graph contents and generation, changed the inode, and produced `pending → warming → ready`; warmup count advanced from 1 to 2. Preparation took 267.48 ms and readiness was observed 1,116.53 ms after replacement, including polling/detection. No model calls, discovery queries or index-build notifications were used. This validates automatic refresh, not a new search-latency benchmark.

The Rust watcher was not running in this instance. A disposable source probe therefore produced no snapshot update and was removed. The live refresh test used an unchanged atomic snapshot replacement instead of rebuilding or temporarily injecting a different graph. Content-change refresh is covered by controlled tests. Automatic source-to-snapshot updating still depends on the Rust watcher or an explicit index build.

Evidence: [restart](validation/discovery-warmup/restart.json), [live atomic refresh](validation/discovery-warmup/live-atomic-refresh.json).

## Files

- `services/agentd-py/agentd/retrieval/discovery_warmer.py`: startup/change worker and lifecycle.
- `services/agentd-py/agentd/retrieval/artifact_client.py`: static preparation, separate versioned cache, freshness checks and status.
- `services/agentd-py/agentd/main.py`: startup/shutdown wiring.
- `services/agentd-py/agentd/api/routes.py`: model-independent notification warmup and readiness documentation.
- `services/agentd-py/tests/test_discovery_warmup.py`, `test_discovery_cache.py`, `test_index_build_api.py`: new coverage and updated cache/API expectations.
