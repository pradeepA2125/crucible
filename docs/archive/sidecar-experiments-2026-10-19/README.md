# Retired sidecar experiments

Archived on 19 October 2026 after selecting agent-directed `search_index` discovery.

`retired-experiments.tar.gz` contains the old seed/source-delivery reports, reranker comparisons, experiment runners, calibration/validation evidence, implementation modules and tests for automatic candidate delivery, and pre-cleanup copies of shared files. Original repository-relative paths are preserved. `manifest.json` records the paths removed from the active tree and the reference copies. Inspect or extract the archive into a separate scratch directory; these files are historical and are not an executable rollback package against the current backend.

The retired implementations include automatic candidate injection, source-visibility suppression for automatic catalogs, embedding-based recap/summary selection, BGE/Qwen candidate filters, and their environment/settings switches. The old provider-session catalog-stripping workaround is removed because automatic candidate fields are no longer emitted.

Active code retains `tools/index_search.py`, `retrieval/candidates.py`, `retrieval/artifact_client.py`, `retrieval/discovery_cache.py` and `retrieval/discovery_warmer.py`. Content-free search result token measurement lives in `context_metrics.py`. Independent memory recall/reranking and task-planner retrieval remain supported; they are separate product paths.

Current behavior and limits: [on-demand discovery](../../on-demand-code-discovery-trial.md) and [cache warmup](../../search-index-cache-warmup-2026-10-19.md). Current validation evidence stays under `docs/validation/on-demand-index/` and `docs/validation/discovery-warmup/`.

Nine local scratch directories and the unused Qwen candidate-reranker weights were moved into `.tmp/archive/sidecar-experiments-2026-10-19/`. They remain ignored and were not copied into this repository archive, because scratch data can include raw workspace context. Unrelated scratch files and user conversation/memory databases were left in place.

The live backend was restarted without retired candidate/Qwen/context-selection flags. Its discovery cache is ready; the selected provider/model remains ChatGPT `gpt-6.1-sol`, with the editor's persisted Medium reasoning effort and 500,000-token configured window. The first restart reused an older Low launch setting; this was corrected from the saved editor selection. Installed extension manifest/bundle were also updated; a window reload may be needed to refresh the settings UI. Existing graph snapshot contents were preserved; normal source-to-snapshot freshness still depends on the indexer.

Cleanup validation receipts are under `docs/validation/on-demand-cleanup/`. The first full backend run exposed one outdated explore-tool expectation that omitted `search_index`; it was updated to reflect the supported read-only discovery tool. No runtime authorization was widened by that test correction.

Final checks: 3,971 backend tests passed, 12 skipped; all 601 extension tests passed. TypeScript/build, targeted Ruff/mypy and Git whitespace checks passed. The archive digest and absence of all retired active-tree paths were verified.
