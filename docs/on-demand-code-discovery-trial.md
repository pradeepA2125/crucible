# On-demand code discovery

Code discovery is agent-directed through `search_index`. There is no automatic chat retrieval seed, source delivery, candidate catalog injection, candidate reranker, or recap/summary selector. The old automatic alternatives and their settings have been retired; previous experiment code and comparisons are preserved in [the archive](archive/sidecar-experiments-2026-10-19/README.md).

## Agent flow

The shared tool description asks the model to write one focused natural-language question with relevant technical keywords and known symbol/path names, searching distinct behaviors separately. For example:

```json
{"query":"Where is local-model availability checked, and what happens when models are disabled?","limit":4}
```

The first controller request recommends `search_index` when code is needed and locations are unknown, after required skill/todo setup. Known locations and sufficient visible source can be used directly. Conversation needs no discovery. This is guidance, not a forced tool choice.

The workspace is supplied by the harness. The tool accepts a query of 1–1,600 characters and a file limit (default four, maximum eight). It runs hybrid lexical/available semantic retrieval off the event loop, returns bounded paths, symbols, available read ranges and relationship hints, and never emits source bodies. The model must use `read_file` for current source, `query_graph` for connections, and ordinary search/refined queries when needed. Hints can be incomplete, unrelated or stale. The tool cannot rebuild a missing snapshot or select another workspace.

The shared execution/planning registries expose it to main chat, task execution and permitted child agents. Existing allowlists and workspace boundaries still apply. Independent memory recall/reranking and task-planner retrieval are separate supported paths.

## Cache and freshness

A bounded versioned snapshot/graph cache avoids repeated parsing and lookup construction. A background worker prepares it at startup and after snapshot replacement; Rust notifications provide immediate warmup, and polling covers missed notifications. Query-specific scoring and semantic lookup still run per tool call. See [cache behavior and live validation](search-index-cache-warmup-2026-10-19.md).

Freshness is relative to the indexed snapshot. Source edits only reach the snapshot when the indexer runs. Read current files before relying on candidate contents or making edits. Tool results remain in normal conversation history and are not silently replaced on later turns. Existing stored legacy seeds remain for compatibility but are not supplied by the chat controller.

## Evidence and accounting

The original live behavioral trial sent two code questions plus acknowledgements/greetings. Both code questions chose `search_index` first without the user prompt naming the tool; conversation chose no tools. The returned catalogs measured 278 and 311 `o200k_base` text tokens. Suggested files directly read were one of four and zero of four, so reliable retrieval quality has not been established. Exact queries, paths and observations remain in [behavior.json](validation/on-demand-index/behavior.json).

The old recorded seeds measured roughly 6,300–7,200 estimated tokens; the automatic seed contribution is now zero. The small requested result blocks do not establish whole-task savings: source reads, tool schema/guidance, model round trips, history replay and cached inputs also count. An earlier candidate-only payload reconstruction measured 28,063 → 21,120 estimated first-input tokens (24.7% smaller), before the final on-demand change. Current fresh on-demand first provider requests measured about 20,300 input tokens on different questions. There is no matched end-to-end savings percentage for the final implementation.

A subsequent user UI check confirmed the natural-language guidance was working. The automated live measurements above predate that guidance change and must not be presented as new ranking-quality evidence.

## Active implementation

- `services/agentd-py/agentd/tools/index_search.py`: tool contract, read-only execution and content-free timing/result-size metrics.
- `services/agentd-py/agentd/retrieval/candidates.py`: compact metadata catalog and relationship encoding.
- `services/agentd-py/agentd/retrieval/artifact_client.py`: hybrid discovery, workspace filtering and prepared graph lookups.
- `services/agentd-py/agentd/retrieval/discovery_cache.py`, `discovery_warmer.py`: bounded versioned snapshot cache and background lifecycle.
- `services/agentd-py/agentd/chat/controller_prompts.py`: conditional discovery guidance and source-verification instructions.

Current activation/behavior evidence is under `docs/validation/on-demand-index/`; warmup evidence is under `docs/validation/discovery-warmup/`. Historical scoring experiments and their runners are archived, outside the active source/test paths.
