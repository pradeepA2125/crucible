# indexer-rs

Rust indexing service for AI Editor.

## Responsibilities
- Incremental filesystem watching
- Tree-sitter parsing pipeline
- Symbol and reference graph materialization
- LSP-assisted enrichment hooks

## LSP runtime defaults
- `CRUCIBLE_LSP_ENABLED=1`
- `CRUCIBLE_LSP_TS_CMD="typescript-language-server --stdio"`
- `CRUCIBLE_LSP_PY_CMD="pyright-langserver --stdio"`
- `CRUCIBLE_LSP_RS_CMD="rust-analyzer"`
- `CRUCIBLE_LSP_STARTUP_TIMEOUT_MS=3000`
- `CRUCIBLE_LSP_REQUEST_TIMEOUT_MS=3000`
- `CRUCIBLE_INDEX_SNAPSHOT_PATH=<workspace>/.ai-editor/index-snapshot.json`

## Run (after toolchain/deps)
```bash
cd services/indexer-rs
cargo run -- --workspace /path/to/repo
```
