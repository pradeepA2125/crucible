# agentd-py

Python orchestration backend for AI Editor.

## Responsibilities
- Deterministic task lifecycle orchestration
- Plan/patch/repair execution loop
- Deterministic validation command pipeline (syntax/type/lint/test)
- Integration point for model providers (OpenAI, others)

Patch operations are validated and applied inside shadow workspaces, then promoted on accept.

Review lifecycle states are explicit:
- `READY_FOR_REVIEW` after validation passes in shadow workspace
- `PROMOTING` while applying accepted patch set to the real workspace
- `SUCCEEDED` after successful promotion

## API surface (scaffold)
- `POST /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `GET /v1/tasks/{task_id}/result`
- `GET /v1/tasks/{task_id}/artifacts`
- `POST /v1/tasks/{task_id}/cancel`
- `POST /v1/tasks/{task_id}/accept`
- `POST /v1/tasks/{task_id}/reject`

`accept`, `reject`, and `GET /result` return a `TaskResult` payload with `plan` and `patch` metadata.

## Runtime configuration
- `OPENAI_API_KEY`: required when `CRUCIBLE_REASONING_BACKEND=openai` (default)
- `CRUCIBLE_OPENAI_MODEL`: optional, default `gpt-5`
- `ANTHROPIC_API_KEY`: required when `CRUCIBLE_REASONING_BACKEND=anthropic`
- `CRUCIBLE_ANTHROPIC_MODEL`: optional, default `claude-3-5-sonnet-latest`
- `CRUCIBLE_ANTHROPIC_ENDPOINT`: optional, default `https://api.anthropic.com/v1/messages` (converted to SDK base URL internally)
- `CRUCIBLE_ANTHROPIC_VERSION`: optional, default `2023-06-01` (sent via SDK default headers)
- `CRUCIBLE_ANTHROPIC_MAX_TOKENS`: optional, default `4096`
- `CRUCIBLE_ANTHROPIC_TIMEOUT_SEC`: optional, default `60.0`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`: required when `CRUCIBLE_REASONING_BACKEND=gemini`
- `CRUCIBLE_GEMINI_MODEL`: optional, default `gemini-3-flash-preview`
- `CRUCIBLE_GEMINI_THINKING_ENABLED`: optional, default `1` (enables Gemini thinking mode)
- `CRUCIBLE_GEMINI_THINKING_BUDGET`: optional integer budget; default dynamic `-1` when thinking is enabled and no level is set
- `CRUCIBLE_GEMINI_THINKING_LEVEL`: optional thinking level hint (for models that support levels)
- `CRUCIBLE_GEMINI_INCLUDE_THOUGHTS`: optional (`1|0`), default `0`
- `HF_TOKEN` (or `HUGGING_FACE_HUB_TOKEN` / `HUGGINGFACEHUB_API_TOKEN`): required when `CRUCIBLE_REASONING_BACKEND=huggingface`
- `CRUCIBLE_HUGGINGFACE_MODEL`: optional, default `deepseek-ai/DeepSeek-R1:fastest` (set to a coding model such as `deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest` if preferred)
- `CRUCIBLE_HUGGINGFACE_MAX_NEW_TOKENS`: optional, default `4096`
- `CRUCIBLE_HUGGINGFACE_SEED`: optional integer seed for reproducibility
- `CRUCIBLE_HUGGINGFACE_TIMEOUT_SEC`: optional, default `60.0`
- `GROQ_API_KEY`: required when `CRUCIBLE_REASONING_BACKEND=groq`
- `CRUCIBLE_GROQ_MODEL`: optional, default `openai/gpt-oss-120b`
- `CRUCIBLE_GROQ_ENDPOINT`: optional custom base URL for Groq-compatible endpoint
- `CRUCIBLE_GROQ_MAX_TOKENS`: optional, default `4096`
- `CRUCIBLE_GROQ_TIMEOUT_SEC`: optional, default `60.0`
- `CRUCIBLE_REASONING_BACKEND`: `openai` (default), `anthropic`, `gemini`, `huggingface`, `groq`, or `scripted` (debug)
- `CRUCIBLE_VALIDATION_COMMANDS_JSON`: optional JSON array of commands; if unset, validator auto-detects defaults
- `CRUCIBLE_STEP_SCOPED_MODE`: optional (`1|0`), default `1`; enables step-scoped patching with preflight gates
- `CRUCIBLE_AST_CUTOVER_MODE`: optional, default `hard`; any value other than `hard` fails startup
- `CRUCIBLE_MAX_ATTEMPTS_PER_STEP`: optional, default `3`
- `CRUCIBLE_PATCH_CANDIDATE_COUNT`: optional, default `3`
- `CRUCIBLE_CHECKPOINT_RETENTION_TASKS`: optional, default `20`
- `CRUCIBLE_DB_PATH`: optional SQLite path, default `.agentd/agentd.sqlite3`
- `CRUCIBLE_SHADOW_ROOT`: optional shadow root, default `.agentd/shadows`

AST patching dependencies:
- Python AST/CST patching requires `libcst` (installed by default dependencies).
- TypeScript/Rust selector resolution uses `tree_sitter_languages` when available.
- If tree-sitter parsers are unavailable in runtime, candidate preflight fails deterministically with `parser_unavailable`.

## Run (after deps install)
```bash
cd services/agentd-py
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn agentd.main:app --reload --port 8000
```

## Development profile (Hugging Face serverless)
```bash
export CRUCIBLE_REASONING_BACKEND=huggingface
export HF_TOKEN=hf_xxx
export CRUCIBLE_HUGGINGFACE_MODEL=deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest
```

## Development profile (Groq Cloud)
```bash
export CRUCIBLE_REASONING_BACKEND=groq
export GROQ_API_KEY=gsk_xxx
export CRUCIBLE_GROQ_MODEL=openai/gpt-oss-120b
```

## Phase 0 evaluation commands
```bash
# 1) Seed/freeze benchmark corpus manifest
crucible-eval init-corpus-manifest \
  --workspace-root /path/to/workspaces \
  --output /path/to/repo/docs/benchmarks/benchmark-corpus.v1.json \
  --freeze

# 2) Export deterministic replay bundle for a task from SQLite
crucible-eval export-bundle \
  --db-path /path/to/repo/services/agentd-py/.agentd/agentd.sqlite3 \
  --task-id task-123 \
  --output /tmp/benchmarks/bundle.task-123.json

# 3) Replay/verify deterministic bundle fingerprint
crucible-eval replay-bundle \
  --bundle /tmp/benchmarks/bundle.task-123.json

# 4) Produce score + weekly report from bundles directory
crucible-eval score --bundles-root /tmp/benchmarks
crucible-eval weekly-report --bundles-root /tmp/benchmarks

# 5) Phase 1 reliability gate (baseline vs current)
crucible-eval phase1-gate-report \
  --baseline-bundles-root /tmp/benchmarks/baseline \
  --bundles-root /tmp/benchmarks/current
```
