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
- `POST /v1/tasks/{task_id}/cancel`
- `POST /v1/tasks/{task_id}/accept`
- `POST /v1/tasks/{task_id}/reject`

`accept`, `reject`, and `GET /result` return a `TaskResult` payload with `plan` and `patch` metadata.

## Runtime configuration
- `OPENAI_API_KEY`: required when `AI_EDITOR_REASONING_BACKEND=openai` (default)
- `AI_EDITOR_OPENAI_MODEL`: optional, default `gpt-5`
- `ANTHROPIC_API_KEY`: required when `AI_EDITOR_REASONING_BACKEND=anthropic`
- `AI_EDITOR_ANTHROPIC_MODEL`: optional, default `claude-3-5-sonnet-latest`
- `AI_EDITOR_ANTHROPIC_ENDPOINT`: optional, default `https://api.anthropic.com/v1/messages` (converted to SDK base URL internally)
- `AI_EDITOR_ANTHROPIC_VERSION`: optional, default `2023-06-01` (sent via SDK default headers)
- `AI_EDITOR_ANTHROPIC_MAX_TOKENS`: optional, default `4096`
- `AI_EDITOR_ANTHROPIC_TIMEOUT_SEC`: optional, default `60.0`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`: required when `AI_EDITOR_REASONING_BACKEND=gemini`
- `AI_EDITOR_GEMINI_MODEL`: optional, default `gemini-3-flash-preview`
- `AI_EDITOR_GEMINI_THINKING_ENABLED`: optional, default `1` (enables Gemini thinking mode)
- `AI_EDITOR_GEMINI_THINKING_BUDGET`: optional integer budget; default dynamic `-1` when thinking is enabled and no level is set
- `AI_EDITOR_GEMINI_THINKING_LEVEL`: optional thinking level hint (for models that support levels)
- `AI_EDITOR_GEMINI_INCLUDE_THOUGHTS`: optional (`1|0`), default `0`
- `HF_TOKEN` (or `HUGGING_FACE_HUB_TOKEN` / `HUGGINGFACEHUB_API_TOKEN`): required when `AI_EDITOR_REASONING_BACKEND=huggingface`
- `AI_EDITOR_HUGGINGFACE_MODEL`: optional, default `deepseek-ai/DeepSeek-R1:fastest` (set to a coding model such as `deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest` if preferred)
- `AI_EDITOR_HUGGINGFACE_MAX_NEW_TOKENS`: optional, default `4096`
- `AI_EDITOR_HUGGINGFACE_SEED`: optional integer seed for reproducibility
- `AI_EDITOR_HUGGINGFACE_TIMEOUT_SEC`: optional, default `60.0`
- `GROQ_API_KEY`: required when `AI_EDITOR_REASONING_BACKEND=groq`
- `AI_EDITOR_GROQ_MODEL`: optional, default `openai/gpt-oss-120b`
- `AI_EDITOR_GROQ_ENDPOINT`: optional custom base URL for Groq-compatible endpoint
- `AI_EDITOR_GROQ_MAX_TOKENS`: optional, default `4096`
- `AI_EDITOR_GROQ_TIMEOUT_SEC`: optional, default `60.0`
- `AI_EDITOR_REASONING_BACKEND`: `openai` (default), `anthropic`, `gemini`, `huggingface`, `groq`, or `scripted` (debug)
- `AI_EDITOR_VALIDATION_COMMANDS_JSON`: optional JSON array of commands; if unset, validator auto-detects defaults
- `AI_EDITOR_DB_PATH`: optional SQLite path, default `.agentd/agentd.sqlite3`
- `AI_EDITOR_SHADOW_ROOT`: optional shadow root, default `.agentd/shadows`

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
export AI_EDITOR_REASONING_BACKEND=huggingface
export HF_TOKEN=hf_xxx
export AI_EDITOR_HUGGINGFACE_MODEL=deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct:fastest
```

## Development profile (Groq Cloud)
```bash
export AI_EDITOR_REASONING_BACKEND=groq
export GROQ_API_KEY=gsk_xxx
export AI_EDITOR_GROQ_MODEL=openai/gpt-oss-120b
```
