# P4 — Install, Managed Runtime & Settings UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user goes from zero → working chat turn via a VSIX install + first-run wizard: the extension provisions the whole runtime (uv-managed agentd, indexer, ripgrep, LSPs), spawns/supervises the backend per workspace, and a settings webview round-trips provider/MCP/skills/policy config.

**Architecture:** Backend grows four seams (provider factory + validate ping, engine hot-swap via `ProviderRuntime`, startup lockfile, MCP admin RMW routes over the existing `reconcile()` seam). The extension grows a vscode-free `src/runtime/` core (installer, backend process) plus thin vscode wiring, and two new webview entries (setup wizard, settings panel) following the proven `MemoryPanel` second-Vite-entry pattern. A tagged GitHub Release carries per-OS runtime assets + `manifest.json`; the VSIX goes to the Marketplace.

**Tech Stack:** Python 3.12/FastAPI/pytest · TypeScript/vitest · React + Vite (webview-ui) · GitHub Actions · uv · vsce.

**Spec:** `docs/superpowers/specs/2026-07-02-p4-install-runtime-settings-design.md`

## Global Constraints

- Secrets: provider keys live in VS Code **SecretStorage** only; sent to the backend via spawn env or the hot-swap request body; **never persisted by the backend, never logged**.
- MCP config writes preserve unknown keys and store `${VAR}` references **verbatim** — never resolved values.
- MCP/skills **enable-disable state is user-local** (extension `globalState`), never written to shareable files; passed to the backend per call (`disabled` list) or per spawn (`AI_EDITOR_SKILLS_DISABLED`).
- Hot-swap applies **from the next turn**; in-flight coroutines keep their local engine refs (single-process asyncio — no locks needed).
- Runtime install root: `~/.ai-editor/runtime/`. Per-OS targets: `darwin-arm64`, `darwin-x64`, `linux-x64`, `win32-x64`.
- Provider set (picker parity): openai, anthropic, gemini, groq, ollama, watsonx, openrouter, huggingface, turboquant. `scripted` is dev-only, hidden.
- `start-backend.sh` and the `.env` dev flow remain untouched.
- Python: run `pytest` plain (never `-q` — `addopts` already sets it); TS: `npm run build && npm run test && npm run typecheck` from repo root. After editor-client changes run `npm run -w @ai-editor/editor-client build` before extension typecheck.
- Commit format `type(scope): description`, one logical change per commit.

---

## Part A — Backend (agentd)

### Task 1: Provider factory extraction

Extract the `main.py` if/elif transport chain into a reusable factory so the validate route and hot-swap can build transports outside module import, with request-supplied credentials.

**Files:**
- Create: `services/agentd-py/agentd/providers/factory.py`
- Modify: `services/agentd-py/agentd/main.py:116-258` (replace chain with factory calls)
- Test: `services/agentd-py/tests/test_provider_factory.py`

(`OpenAIJsonTransport` already accepts `api_key=` — verified; no transport changes needed.)

**Interfaces:**
- Produces: `build_transport(backend: str, credentials: dict[str, str] | None = None) -> object` — raises `ValueError` on unknown backend. `default_model(backend: str) -> str`. `resolve_model(backend: str) -> str` (env override or default). `PROVIDER_KEY_ENV: dict[str, str]` (backend → key env-var name; local providers absent).
- Consumed by: Tasks 2, 3 (validate, hot-swap), and `main.py`.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_provider_factory.py
import pytest

from agentd.providers.factory import (
    PROVIDER_KEY_ENV,
    build_transport,
    default_model,
    resolve_model,
)


def test_default_model_known_backends() -> None:
    assert default_model("gemini") == "gemini-3-flash-preview"
    assert default_model("openai") == "gpt-5"
    assert default_model("turboquant") == "devstral-small-2:24b-q4_k_xl"


def test_resolve_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_EDITOR_GEMINI_MODEL", "gemini-flash-latest")
    assert resolve_model("gemini") == "gemini-flash-latest"


def test_build_transport_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported backend"):
        build_transport("nope")


def test_build_transport_credentials_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Behavioral: OpenAIJsonTransport raises RuntimeError without a key, so
    # construction succeeding proves the request credential reached it.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_transport("openai")
    transport = build_transport("openai", credentials={"OPENAI_API_KEY": "sk-req"})
    assert transport is not None


def test_provider_key_env_covers_cloud_backends() -> None:
    for backend in ("openai", "anthropic", "gemini", "groq", "openrouter",
                    "watsonx", "huggingface"):
        assert backend in PROVIDER_KEY_ENV
    for local in ("ollama", "turboquant"):
        assert local not in PROVIDER_KEY_ENV
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_factory.py`
Expected: FAIL — `ModuleNotFoundError: agentd.providers.factory`

- [ ] **Step 3: Implement the factory**

Move the chain from `main.py` verbatim, with `_env()` credential-aware lookup:

```python
# services/agentd-py/agentd/providers/factory.py
"""Provider transport factory — the one place a (backend, credentials) pair
becomes a transport. Used at app startup (main.py), by POST /v1/providers/validate,
and by the PUT /v1/config/provider hot-swap. Request-supplied credentials override
process env and are held in the transport object only (never persisted/logged)."""
from __future__ import annotations

import os

_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-3-flash-preview",
    "huggingface": "deepseek-ai/DeepSeek-R1:fastest",
    "groq": "openai/gpt-oss-120b",
    "openrouter": "stepfun/step-3.5-flash:free",
    "watsonx": "ibm/granite-3-8b-instruct",
    "ollama": "glm-4.7-flash:latest",
    "turboquant": "devstral-small-2:24b-q4_k_xl",
    "openai": "gpt-5",
}

MODEL_ENV_VAR: dict[str, str] = {
    "anthropic": "AI_EDITOR_ANTHROPIC_MODEL",
    "gemini": "AI_EDITOR_GEMINI_MODEL",
    "huggingface": "AI_EDITOR_HUGGINGFACE_MODEL",
    "groq": "AI_EDITOR_GROQ_MODEL",
    "openrouter": "AI_EDITOR_OPENROUTER_MODEL",
    "watsonx": "AI_EDITOR_WATSONX_MODEL",
    "ollama": "AI_EDITOR_OLLAMA_MODEL",
    "turboquant": "AI_EDITOR_TURBOQUANT_MODEL",
    "openai": "AI_EDITOR_OPENAI_MODEL",
}

PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "watsonx": "WATSONX_API_KEY",
    "huggingface": "HF_TOKEN",
}


def default_model(backend: str) -> str:
    try:
        return _DEFAULT_MODEL[backend]
    except KeyError:
        raise ValueError(f"Unsupported backend: {backend}") from None


def resolve_model(backend: str) -> str:
    env_var = MODEL_ENV_VAR.get(backend)
    return (os.getenv(env_var) if env_var else None) or default_model(backend)


def _int_env(env: dict, name: str, default: int) -> int:
    raw = env.get(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _float_env(env: dict, name: str, default: float) -> float:
    raw = env.get(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


def build_transport(backend: str, credentials: dict[str, str] | None = None) -> object:
    env: dict[str, str] = dict(os.environ)
    if credentials:
        env.update(credentials)  # request credential wins; process env untouched

    if backend == "anthropic":
        from agentd.providers.anthropic_transport import AnthropicJsonTransport
        return AnthropicJsonTransport(
            api_key=env.get("ANTHROPIC_API_KEY"),
            endpoint=env.get("AI_EDITOR_ANTHROPIC_ENDPOINT",
                             "https://api.anthropic.com/v1/messages"),
            anthropic_version=env.get("AI_EDITOR_ANTHROPIC_VERSION", "2023-06-01"),
            max_tokens=_int_env(env, "AI_EDITOR_ANTHROPIC_MAX_TOKENS", 4096),
            timeout_sec=_float_env(env, "AI_EDITOR_ANTHROPIC_TIMEOUT_SEC", 60.0),
        )
    if backend == "gemini":
        from agentd.providers.gemini_transport import GeminiJsonTransport
        # Thinking knobs: keep main.py's semantics (level defaults high for 3.x).
        thinking_level = env.get("AI_EDITOR_GEMINI_THINKING_LEVEL")
        raw_budget = env.get("AI_EDITOR_GEMINI_THINKING_BUDGET")
        thinking_budget = int(raw_budget) if raw_budget and raw_budget.lstrip("-").isdigit() else None
        thinking_enabled = env.get("AI_EDITOR_GEMINI_THINKING_ENABLED", "true").strip().lower() in {
            "1", "true", "yes", "on"}
        if thinking_enabled and thinking_budget is None and not thinking_level:
            thinking_level = "high"
        return GeminiJsonTransport(
            api_key=env.get("GEMINI_API_KEY"),
            thinking_enabled=thinking_enabled,
            thinking_budget=thinking_budget,
            thinking_level=thinking_level,
            include_thoughts=env.get("AI_EDITOR_GEMINI_INCLUDE_THOUGHTS", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            timeout_sec=_float_env(env, "AI_EDITOR_GEMINI_TIMEOUT_SEC", 120.0),
            max_retries=_int_env(env, "AI_EDITOR_GEMINI_MAX_RETRIES", 4),
        )
    if backend == "huggingface":
        from agentd.providers.huggingface_transport import HuggingFaceJsonTransport
        seed_raw = env.get("AI_EDITOR_HUGGINGFACE_SEED")
        return HuggingFaceJsonTransport(
            api_key=env.get("HF_TOKEN"),
            max_new_tokens=_int_env(env, "AI_EDITOR_HUGGINGFACE_MAX_NEW_TOKENS", 4096),
            seed=int(seed_raw) if seed_raw and seed_raw.isdigit() else None,
            timeout_sec=_float_env(env, "AI_EDITOR_HUGGINGFACE_TIMEOUT_SEC", 60.0),
        )
    if backend == "groq":
        from agentd.providers.groq_transport import GroqJsonTransport
        return GroqJsonTransport(
            api_key=env.get("GROQ_API_KEY"),
            endpoint=env.get("AI_EDITOR_GROQ_ENDPOINT"),
            max_tokens=_int_env(env, "AI_EDITOR_GROQ_MAX_TOKENS", 4096),
            timeout_sec=_float_env(env, "AI_EDITOR_GROQ_TIMEOUT_SEC", 60.0),
            max_retries=_int_env(env, "AI_EDITOR_GROQ_MAX_RETRIES", 4),
        )
    if backend == "openrouter":
        from agentd.providers.openrouter_transport import OpenRouterJsonTransport
        return OpenRouterJsonTransport(
            api_key=env.get("OPENROUTER_API_KEY"),
            max_tokens=_int_env(env, "AI_EDITOR_OPENROUTER_MAX_TOKENS", 4096),
            timeout_sec=_float_env(env, "AI_EDITOR_OPENROUTER_TIMEOUT_SEC", 120.0),
            max_retries=_int_env(env, "AI_EDITOR_OPENROUTER_MAX_RETRIES", 4),
        )
    if backend == "watsonx":
        from agentd.providers.watsonx_transport import WatsonxJsonTransport
        return WatsonxJsonTransport(
            api_key=env.get("WATSONX_API_KEY"),
            project_id=env.get("WATSONX_PROJECT_ID"),
            url=env.get("WATSONX_URL"),
            space_id=env.get("WATSONX_SPACE_ID"),
        )
    if backend == "ollama":
        from agentd.providers.ollama_transport import OllamaJsonTransport
        return OllamaJsonTransport(
            host=env.get("OLLAMA_HOST"),
            keep_alive=env.get("AI_EDITOR_OLLAMA_KEEP_ALIVE"),
            timeout_sec=_float_env(env, "AI_EDITOR_OLLAMA_TIMEOUT_SEC", 600.0),
            max_retries=_int_env(env, "AI_EDITOR_OLLAMA_MAX_RETRIES", 4),
        )
    if backend == "turboquant":
        from agentd.providers.turboquant_transport import TurboQuantTransport
        return TurboQuantTransport.from_env()
    if backend == "openai":
        from agentd.providers.openai_transport import OpenAIJsonTransport
        return OpenAIJsonTransport(api_key=env.get("OPENAI_API_KEY"))
    raise ValueError(f"Unsupported backend: {backend}")
```

Check each transport's real `__init__` while implementing — the kwargs above are
transcribed from `main.py:151-258`; the factory must match them exactly. Several
transports (openai, groq) raise `RuntimeError` at construction when their key is
missing — Task 2's `ping_provider` converts that into a clean validation error.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_factory.py`
Expected: PASS (5 tests)

- [ ] **Step 5: Refactor `main.py` to use the factory**

Replace `main.py:116-258` (keep the `scripted` branch as-is) with:

```python
reasoning_backend = os.getenv("AI_EDITOR_REASONING_BACKEND", "openai").strip().lower()
reasoning_engine: ReasoningEngine
if reasoning_backend == "scripted":
    reasoning_engine = ScriptedReasoningEngine(
        # ... existing scripted plan/patches block, unchanged ...
    )
else:
    from agentd.providers.factory import build_transport, resolve_model

    transport = build_transport(reasoning_backend)
    reasoning_engine = DefaultReasoningEngine(
        model=resolve_model(reasoning_backend), transport=transport
    )
```

Also replace the `_BACKEND_MODEL_ENVVAR` dict at `main.py:324-337` with
`from agentd.providers.factory import MODEL_ENV_VAR` and
`_chat_model = os.getenv(MODEL_ENV_VAR.get(reasoning_backend, "AI_EDITOR_OPENAI_MODEL"), "gpt-4o")`.
Delete the now-unused transport imports at the top of `main.py`.

- [ ] **Step 6: Run the full backend suite**

Run: `cd services/agentd-py && pytest`
Expected: no new failures (baseline ~1091 collected, 1 skip). Also: `ruff check . && mypy agentd`

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/providers/factory.py services/agentd-py/agentd/main.py \
        services/agentd-py/agentd/providers/openai_transport.py services/agentd-py/tests/test_provider_factory.py
git commit -m "refactor(providers): extract transport factory from main.py module chain"
```

---

### Task 2: `POST /v1/providers/validate`

**Files:**
- Create: `services/agentd-py/agentd/providers/validate.py`
- Modify: `services/agentd-py/agentd/api/routes.py` (new route inside `build_router`)
- Test: `services/agentd-py/tests/test_provider_validate_route.py`

**Interfaces:**
- Consumes: `build_transport`, `resolve_model` (Task 1).
- Produces: `ping_transport(transport, model, timeout_sec=30.0) -> None` (raises `ProviderValidationError` with an actionable message); `ping_provider(backend, model=None, credentials=None) -> str` (returns the resolved model). Route: `POST /v1/providers/validate` body `{"backend": str, "model": str|null, "credentials": {str: str}}` → `200 {"ok": true, "model": str}` or `200 {"ok": false, "error": str}` (always 200; `ok` is the signal — the wizard renders `error` verbatim).

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_provider_validate_route.py
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentd.providers.validate as validate_mod
from agentd.api.routes import build_router
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _OkTransport:
    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


class _BoomTransport:
    async def generate_text(self, *, model, system_instructions, user_payload):
        raise RuntimeError("401 invalid api key")


def _client(tmp_path: Path, transport) -> TestClient:
    app = FastAPI()

    def _fake_build_transport(backend, credentials=None):
        _fake_build_transport.last_credentials = credentials
        return transport

    validate_mod.build_transport = _fake_build_transport  # test seam
    app.include_router(build_router(
        InMemoryTaskStore(), object(), ShadowWorkspaceManager(tmp_path / "shadows"), None, None,
    ))
    _client.fake = _fake_build_transport
    return TestClient(app)


def test_validate_ok(tmp_path: Path) -> None:
    resp = _client(tmp_path, _OkTransport()).post(
        "/v1/providers/validate",
        json={"backend": "groq", "credentials": {"GROQ_API_KEY": "sk-x"}})
    body = resp.json()
    assert resp.status_code == 200 and body["ok"] is True
    assert body["model"]  # resolved default
    assert _client.fake.last_credentials == {"GROQ_API_KEY": "sk-x"}


def test_validate_provider_error_is_actionable(tmp_path: Path) -> None:
    body = _client(tmp_path, _BoomTransport()).post(
        "/v1/providers/validate", json={"backend": "groq"}).json()
    assert body["ok"] is False and "invalid api key" in body["error"]


def test_validate_unknown_backend(tmp_path: Path) -> None:
    body = _client(tmp_path, _OkTransport()).post(
        "/v1/providers/validate", json={"backend": "nope"}).json()
    assert body["ok"] is False and "Unsupported backend" in body["error"]


def test_validate_missing_key_is_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No factory stubbing here — the REAL factory + a deleted env key must come
    # back as ok:false with the transport's actionable message, not a 500.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.include_router(build_router(
        InMemoryTaskStore(), object(), ShadowWorkspaceManager(tmp_path / "s"), None, None))
    body = TestClient(app).post(
        "/v1/providers/validate", json={"backend": "openai"}).json()
    assert body["ok"] is False and "OPENAI_API_KEY" in body["error"]
```

> Monkeypatching the module attribute works only if `validate.py` calls
> `build_transport` through its own module namespace — implement it that way
> (plain `from ... import` then call; patch via `monkeypatch.setattr(validate_mod,
> "build_transport", ...)` is the cleaner pytest idiom — use `monkeypatch` in the
> real test, the shape above shows intent).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_validate_route.py`
Expected: FAIL — `ModuleNotFoundError` / 404

- [ ] **Step 3: Implement**

```python
# services/agentd-py/agentd/providers/validate.py
"""One cheap provider ping. Powers POST /v1/providers/validate and the hot-swap
pre-check. Credentials are request-scoped — used to build the transport, never
persisted, never logged."""
from __future__ import annotations

import asyncio

from agentd.providers.factory import build_transport, resolve_model


class ProviderValidationError(Exception):
    """Ping failed — message is user-facing and actionable."""


async def ping_transport(transport: object, model: str, timeout_sec: float = 30.0) -> None:
    try:
        await asyncio.wait_for(
            transport.generate_text(
                model=model,
                system_instructions="Reply with the single word OK.",
                user_payload={"ping": True},
            ),
            timeout=timeout_sec,
        )
    except TimeoutError:
        raise ProviderValidationError(
            f"Provider did not respond within {timeout_sec:.0f}s") from None
    except Exception as exc:  # surface the provider's own message — it names the fix
        raise ProviderValidationError(str(exc)) from exc


async def ping_provider(
    backend: str, model: str | None = None, credentials: dict[str, str] | None = None,
) -> str:
    try:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
    except Exception as exc:
        # Broad on purpose: transports raise RuntimeError at construction when a
        # key is missing ("OPENAI_API_KEY is required…") — that IS the actionable
        # message the wizard should show, not a 500.
        raise ProviderValidationError(str(exc)) from exc
    await ping_transport(transport, resolved)
    return resolved
```

Route, in `build_router` (place next to the `/config` route; module-level Pydantic body model near the other request models in `routes.py`):

```python
class ProviderValidateRequest(BaseModel):
    backend: str
    model: str | None = None
    credentials: dict[str, str] = {}
```

```python
    @router.post("/providers/validate")
    async def validate_provider(body: ProviderValidateRequest) -> dict:
        from agentd.providers.validate import ProviderValidationError, ping_provider

        try:
            resolved = await ping_provider(body.backend, body.model, body.credentials)
        except ProviderValidationError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "model": resolved}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_validate_route.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Full suite + commit**

Run: `cd services/agentd-py && pytest && ruff check . && mypy agentd`

```bash
git add services/agentd-py/agentd/providers/validate.py services/agentd-py/agentd/api/routes.py \
        services/agentd-py/tests/test_provider_validate_route.py
git commit -m "feat(api): POST /v1/providers/validate — one cheap provider ping"
```

---

### Task 3: Engine hot-swap (`ProviderRuntime` + `PUT /v1/config/provider`)

**Files:**
- Create: `services/agentd-py/agentd/providers/runtime.py`
- Modify: `services/agentd-py/agentd/reasoning/engine.py` (add `set_provider`)
- Modify: `services/agentd-py/agentd/api/routes.py` (`build_router` gains `provider_runtime=None`; new route; `/config` reports current provider)
- Modify: `services/agentd-py/agentd/main.py` (construct + pass `ProviderRuntime`)
- Test: `services/agentd-py/tests/test_provider_hotswap.py`

**Interfaces:**
- Consumes: `build_transport`/`resolve_model` (Task 1), `ping_transport` (Task 2), `DefaultReasoningEngine._model/_transport` internals.
- Produces: `DefaultReasoningEngine.set_provider(*, model: str, transport) -> None`. `ProviderRuntime(backend: str, model: str, engines: list)` with `async swap(*, backend, model=None, credentials=None) -> dict` and `.backend`/`.model` attrs. `build_router(..., provider_runtime=None)`. Route `PUT /v1/config/provider` body `{"backend", "model"?, "credentials"?}` → `200 {"ok": true, "backend", "model"}` | `400 {"detail": ...}` | `409` when no runtime (scripted). `GET /v1/config` gains `"provider": {"backend", "model"} | null`.

Known, documented v1 limitation: the memory-harness summarizer keeps its
construction-time transport until the next restart (it works — it's the previous
provider). Note this in the route docstring.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_provider_hotswap.py
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentd.providers.runtime as runtime_mod
from agentd.api.routes import build_router
from agentd.providers.runtime import ProviderRuntime
from agentd.reasoning.engine import DefaultReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _Transport:
    def __init__(self, name: str = "t") -> None:
        self.name = name

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


def _runtime() -> tuple[ProviderRuntime, DefaultReasoningEngine]:
    engine = DefaultReasoningEngine(model="old-model", transport=_Transport("old"))
    return ProviderRuntime(backend="openai", model="old-model", engines=[engine]), engine


def _client(tmp_path: Path, rt: ProviderRuntime | None) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(
        InMemoryTaskStore(), object(), ShadowWorkspaceManager(tmp_path / "shadows"),
        None, None, provider_runtime=rt))
    return TestClient(app)


@pytest.mark.asyncio
async def test_swap_mutates_every_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    rt, engine = _runtime()
    new_transport = _Transport("new")
    monkeypatch.setattr(runtime_mod, "build_transport", lambda b, credentials=None: new_transport)
    result = await rt.swap(backend="groq", model="m2")
    assert engine._model == "m2" and engine._transport is new_transport
    assert (rt.backend, rt.model) == ("groq", "m2")
    assert result == {"backend": "groq", "model": "m2"}


@pytest.mark.asyncio
async def test_swap_failure_leaves_engine_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentd.providers.validate import ProviderValidationError

    rt, engine = _runtime()

    async def _boom(transport, model, timeout_sec=30.0):
        raise ProviderValidationError("bad key")

    monkeypatch.setattr(runtime_mod, "build_transport", lambda b, credentials=None: _Transport())
    monkeypatch.setattr(runtime_mod, "ping_transport", _boom)
    with pytest.raises(ProviderValidationError):
        await rt.swap(backend="groq")
    assert engine._model == "old-model" and rt.backend == "openai"


def test_put_route_and_config_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt, _ = _runtime()
    monkeypatch.setattr(runtime_mod, "build_transport", lambda b, credentials=None: _Transport())
    client = _client(tmp_path, rt)
    resp = client.put("/v1/config/provider", json={"backend": "groq", "model": "m2"})
    assert resp.status_code == 200 and resp.json()["model"] == "m2"
    assert client.get("/v1/config").json()["provider"] == {"backend": "groq", "model": "m2"}


def test_put_route_409_when_no_runtime(tmp_path: Path) -> None:
    client = _client(tmp_path, None)
    assert client.put("/v1/config/provider", json={"backend": "groq"}).status_code == 409
    assert client.get("/v1/config").json()["provider"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_hotswap.py`
Expected: FAIL — `ModuleNotFoundError: agentd.providers.runtime`

- [ ] **Step 3: Implement**

`DefaultReasoningEngine.set_provider` (in `reasoning/engine.py`, right after `__init__`):

```python
    def set_provider(self, *, model: str, transport: ModelJsonTransport) -> None:
        """Hot-swap seam (PUT /v1/config/provider). Safe between turns: in-flight
        coroutines already hold self._transport locally; the next call reads the
        new pair. Loaders (instructions/skills) are untouched."""
        self._model = model
        self._transport = transport
```

```python
# services/agentd-py/agentd/providers/runtime.py
"""Mutable current-provider holder. main.py constructs one per process with every
live DefaultReasoningEngine (orchestrator's + the chat controller's); the hot-swap
route calls swap(), which validates first and only then mutates — a failed swap
leaves everything untouched. Known v1 limitation: the memory-harness summarizer
keeps its construction-time transport until restart."""
from __future__ import annotations

from agentd.providers.factory import build_transport, resolve_model
from agentd.providers.validate import ping_transport


class ProviderRuntime:
    def __init__(self, *, backend: str, model: str, engines: list) -> None:
        self.backend = backend
        self.model = model
        self._engines = list(engines)

    async def swap(
        self, *, backend: str, model: str | None = None,
        credentials: dict[str, str] | None = None,
    ) -> dict:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
        await ping_transport(transport, resolved)  # raises ProviderValidationError
        for engine in self._engines:
            engine.set_provider(model=resolved, transport=transport)
        self.backend, self.model = backend, resolved
        return {"backend": backend, "model": resolved}
```

`routes.py` — signature + route + config report:

```python
def build_router(
    store: TaskStore,
    orchestrator: AgentOrchestrator,
    workspace_manager: ShadowWorkspaceManager,
    retrieval_client: RetrievalArtifactClient | None = None,
    chat_agent: object | None = None,
    provider_runtime: object | None = None,
) -> APIRouter:
```

Inside `get_config`, add to the returned dict:

```python
            "provider": (
                {"backend": provider_runtime.backend, "model": provider_runtime.model}
                if provider_runtime is not None else None
            ),
```

(The return type annotation `dict[str, bool]` becomes `dict`.) New route + body model:

```python
class ProviderSwapRequest(BaseModel):
    backend: str
    model: str | None = None
    credentials: dict[str, str] = {}
```

```python
    @router.put("/config/provider")
    async def put_config_provider(body: ProviderSwapRequest) -> dict:
        """Hot-swap the reasoning provider/model in-process (applies next turn)."""
        from agentd.providers.validate import ProviderValidationError

        if provider_runtime is None:
            raise HTTPException(status_code=409, detail="provider hot-swap unavailable")
        try:
            result = await provider_runtime.swap(
                backend=body.backend, model=body.model,
                credentials=body.credentials or None)
        except (ProviderValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}
```

`main.py` wiring (after `_chat_agent` creation, before `build_router`):

```python
from agentd.providers.runtime import ProviderRuntime

provider_runtime: ProviderRuntime | None = None
if reasoning_backend != "scripted":
    _engines = [reasoning_engine]
    _ctrl_engine = getattr(_chat_agent, "_reasoning", None)
    if isinstance(_ctrl_engine, DefaultReasoningEngine) and _ctrl_engine is not reasoning_engine:
        _engines.append(_ctrl_engine)
    provider_runtime = ProviderRuntime(
        backend=reasoning_backend, model=_chat_model, engines=_engines)
```

and pass `provider_runtime=provider_runtime` to `build_router(...)` at `main.py:396`.
(The legacy `ChatAgent` holds a raw transport, not an engine — `getattr` returns
`None` there; controller path is the live one.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_hotswap.py`
Expected: PASS (4 tests)

- [ ] **Step 5: Full suite + commit**

Run: `cd services/agentd-py && pytest && ruff check . && mypy agentd`

```bash
git add services/agentd-py/agentd/providers/runtime.py services/agentd-py/agentd/reasoning/engine.py \
        services/agentd-py/agentd/api/routes.py services/agentd-py/agentd/main.py \
        services/agentd-py/tests/test_provider_hotswap.py
git commit -m "feat(api): provider/model hot-swap — ProviderRuntime + PUT /v1/config/provider"
```

---

### Task 4: Startup lockfile

**Files:**
- Create: `services/agentd-py/agentd/runtime_lock.py`
- Modify: `services/agentd-py/agentd/main.py` (startup/shutdown handlers)
- Test: `services/agentd-py/tests/test_runtime_lock.py`

**Interfaces:**
- Produces: `LockInfo` dataclass (`pid: int, port: int, started_at: float`); `write_lock(workspace, *, port, pid=None)`; `read_lock(workspace) -> LockInfo | None`; `clear_lock(workspace)`; `is_pid_alive(pid) -> bool`. Lock path: `<workspace>/.agentd/agentd.lock` (JSON). Extension (Task 10) reads/reaps the same file shape.
- Activation: main.py writes the lock at startup **only when `AI_EDITOR_PORT` is set** (the extension always sets it; the dev script doesn't — no behavior change for the script flow).

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_runtime_lock.py
import json
import os
from pathlib import Path

from agentd.runtime_lock import (
    LockInfo, clear_lock, is_pid_alive, read_lock, write_lock,
)


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    write_lock(tmp_path, port=8123)
    lock = read_lock(tmp_path)
    assert isinstance(lock, LockInfo)
    assert lock.port == 8123 and lock.pid == os.getpid() and lock.started_at > 0
    raw = json.loads((tmp_path / ".agentd" / "agentd.lock").read_text())
    assert set(raw) == {"pid", "port", "started_at"}


def test_read_missing_or_corrupt_returns_none(tmp_path: Path) -> None:
    assert read_lock(tmp_path) is None
    (tmp_path / ".agentd").mkdir()
    (tmp_path / ".agentd" / "agentd.lock").write_text("{not json")
    assert read_lock(tmp_path) is None


def test_clear_lock_is_idempotent(tmp_path: Path) -> None:
    write_lock(tmp_path, port=1)
    clear_lock(tmp_path)
    clear_lock(tmp_path)
    assert read_lock(tmp_path) is None


def test_is_pid_alive() -> None:
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(2**22 + 12345) is False  # exceeds default pid_max
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_runtime_lock.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# services/agentd-py/agentd/runtime_lock.py
"""Per-workspace backend lockfile: <workspace>/.agentd/agentd.lock (JSON pid/port/
started_at). The extension reuses a live backend and reaps stale locks — this file
is what makes one-workspace-one-backend hold by construction. Written only when
AI_EDITOR_PORT is set (managed spawns); the dev script flow is unaffected."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LockInfo:
    pid: int
    port: int
    started_at: float


def _lock_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".agentd" / "agentd.lock"


def write_lock(workspace: str | Path, *, port: int, pid: int | None = None) -> None:
    path = _lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"pid": pid or os.getpid(), "port": port, "started_at": time.time()}),
        encoding="utf-8")


def read_lock(workspace: str | Path) -> LockInfo | None:
    try:
        raw = json.loads(_lock_path(workspace).read_text(encoding="utf-8"))
        return LockInfo(pid=int(raw["pid"]), port=int(raw["port"]),
                        started_at=float(raw["started_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_lock(workspace: str | Path) -> None:
    try:
        _lock_path(workspace).unlink()
    except OSError:
        pass


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, ValueError):
        return True  # exists but not ours / unprobeable — treat as alive (conservative)
    return True
```

`main.py` (near the `_mcp_manager` startup handlers):

```python
_lock_port_raw = os.getenv("AI_EDITOR_PORT", "").strip()
if _lock_port_raw.isdigit():
    from agentd.runtime_lock import clear_lock, write_lock

    _lock_port = int(_lock_port_raw)

    def _write_runtime_lock() -> None:
        write_lock(_chat_workspace_path, port=_lock_port)

    def _clear_runtime_lock() -> None:
        clear_lock(_chat_workspace_path)

    app.add_event_handler("startup", _write_runtime_lock)
    app.add_event_handler("shutdown", _clear_runtime_lock)
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `cd services/agentd-py && pytest tests/test_runtime_lock.py && pytest`
Expected: PASS; no new failures.

```bash
git add services/agentd-py/agentd/runtime_lock.py services/agentd-py/agentd/main.py \
        services/agentd-py/tests/test_runtime_lock.py
git commit -m "feat(runtime): per-workspace agentd.lock written at startup when AI_EDITOR_PORT set"
```

---

### Task 5: MCP admin helpers + `reconcile(disabled=…)`

**Files:**
- Create: `services/agentd-py/agentd/mcp/admin.py`
- Modify: `services/agentd-py/agentd/mcp/client.py` (`reconcile` gains `disabled`; new `reconnect`; public `loader` property)
- Modify: `services/agentd-py/agentd/mcp/config.py` (public `config_path` property)
- Test: `services/agentd-py/tests/test_mcp_admin.py`

**Interfaces:**
- Produces: `admin.upsert_server(path: Path, name: str, entry: dict) -> None` (RMW, preserves unknown top-level keys AND unknown per-entry keys, `${VAR}` stays verbatim; raises `ValueError` on invalid name — reuse `_NAME_RE` from `config.py`); `admin.remove_server(path, name) -> bool`; `admin.read_raw_servers(path) -> dict[str, dict]` (all entries, including `enabled: false` ones — the GET route lists everything). `McpConnectionManager.reconcile(configs, disabled: frozenset[str] = frozenset())`; `McpConnectionManager.reconnect(name: str, disabled: frozenset[str] = frozenset())` (stop handle + reconcile from loader); `McpConnectionManager.loader` property; `McpConfigLoader.config_path` property.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_mcp_admin.py
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from agentd.mcp.admin import read_raw_servers, remove_server, upsert_server
from agentd.mcp.client import McpConnectionManager
from agentd.mcp.config import McpConfigLoader


def _seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "$schema": "https://example/schema.json",  # unknown top-level key
        "mcpServers": {
            "web": {"command": "uv", "args": ["run", "x.py"],
                    "env": {"OLLAMA_API_KEY": "${OLLAMA_API_KEY}"},
                    "enabled": True, "x-custom": 1},
        },
    }))


def test_upsert_preserves_unknown_keys_and_var_refs(tmp_path: Path) -> None:
    cfg = tmp_path / ".ai-editor" / "mcp.json"
    _seed(cfg)
    upsert_server(cfg, "gh", {"type": "http", "url": "https://x",
                              "headers": {"Authorization": "${GITHUB_PAT}"},
                              "enabled": True})
    raw = json.loads(cfg.read_text())
    assert raw["$schema"] == "https://example/schema.json"
    assert raw["mcpServers"]["web"]["x-custom"] == 1
    assert raw["mcpServers"]["web"]["env"]["OLLAMA_API_KEY"] == "${OLLAMA_API_KEY}"
    assert raw["mcpServers"]["gh"]["headers"]["Authorization"] == "${GITHUB_PAT}"


def test_upsert_creates_file_and_rejects_bad_name(tmp_path: Path) -> None:
    cfg = tmp_path / ".ai-editor" / "mcp.json"
    upsert_server(cfg, "a1", {"command": "x", "enabled": True})
    assert "a1" in read_raw_servers(cfg)
    with pytest.raises(ValueError):
        upsert_server(cfg, "bad__name", {"command": "x"})


def test_remove_server(tmp_path: Path) -> None:
    cfg = tmp_path / ".ai-editor" / "mcp.json"
    _seed(cfg)
    assert remove_server(cfg, "web") is True
    assert remove_server(cfg, "web") is False
    assert read_raw_servers(cfg) == {}


class _StubSession:
    async def list_tools(self):
        class _R:  # noqa: N801
            tools: list = []
        return _R()


@asynccontextmanager
async def _stub_factory(cfg):
    yield _StubSession()


@pytest.mark.asyncio
async def test_reconcile_disabled_filters_and_reconnect(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".ai-editor" / "mcp.json"
    _seed(cfg_path)
    upsert_server(cfg_path, "gh", {"type": "http", "url": "https://x", "enabled": True})
    loader = McpConfigLoader(tmp_path)
    manager = McpConnectionManager(loader, session_factory=_stub_factory)
    await manager.reconcile(loader.load(), disabled=frozenset({"gh"}))
    states = {s.name: s.state for s in manager.statuses()}
    assert "web" in states and "gh" not in states

    await manager.reconnect("web")
    assert {s.name for s in manager.statuses()} >= {"web"}
    await manager.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_mcp_admin.py`
Expected: FAIL — `ModuleNotFoundError: agentd.mcp.admin`

- [ ] **Step 3: Implement**

```python
# services/agentd-py/agentd/mcp/admin.py
"""Read-modify-write helpers over .ai-editor/mcp.json for the settings UI routes.
The file stays the source of truth (guided-writer pattern — see
docs/superpowers/2026-07-02-mcp-settings-ui-research.md §1). Unknown keys are
preserved; ${VAR} references are stored verbatim, never resolved."""
from __future__ import annotations

import json
from pathlib import Path

from agentd.mcp.config import _NAME_RE


def _read_raw(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_raw_servers(path: Path) -> dict[str, dict]:
    servers = _read_raw(path).get("mcpServers")
    return {k: v for k, v in servers.items() if isinstance(v, dict)} \
        if isinstance(servers, dict) else {}


def upsert_server(path: Path, name: str, entry: dict) -> None:
    if not _NAME_RE.match(name) or "__" in name:
        raise ValueError(
            f"invalid server name {name!r}: must match [A-Za-z0-9][A-Za-z0-9_-]* "
            "and not contain '__'")
    raw = _read_raw(path)
    servers = raw.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raw["mcpServers"] = servers = {}
    servers[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def remove_server(path: Path, name: str) -> bool:
    raw = _read_raw(path)
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return True
```

`client.py` changes:

```python
    @property
    def loader(self) -> McpConfigLoader:
        return self._loader

    async def reconcile(
        self, configs: list[McpServerConfig],
        disabled: frozenset[str] = frozenset(),
    ) -> None:
        desired = {c.name: c for c in configs if c.name not in disabled}
        # ... rest of the existing body unchanged ...

    async def reconnect(self, name: str, disabled: frozenset[str] = frozenset()) -> None:
        """Manual retry from the settings UI: drop the handle, re-reconcile."""
        await self._stop_handle(name)
        await self.reconcile(self._loader.load(), disabled=disabled)
```

`config.py`:

```python
    @property
    def config_path(self) -> Path:
        return self._path
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `cd services/agentd-py && pytest tests/test_mcp_admin.py && pytest && ruff check . && mypy agentd`

```bash
git add services/agentd-py/agentd/mcp/admin.py services/agentd-py/agentd/mcp/client.py \
        services/agentd-py/agentd/mcp/config.py services/agentd-py/tests/test_mcp_admin.py
git commit -m "feat(mcp): admin RMW helpers + reconcile(disabled) + manual reconnect"
```

---

### Task 6: MCP management routes

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py` (`build_router` gains `mcp_manager=None`; four routes)
- Modify: `services/agentd-py/agentd/main.py` (pass `_mcp_manager` to `build_router`)
- Test: `services/agentd-py/tests/test_mcp_routes.py`

**Interfaces:**
- Consumes: Task 5's `admin` helpers + manager methods; `manager.loader.config_path`.
- Produces (all soft-gated: manager `None` → `GET` returns `{"enabled": false, "servers": []}`, writes return 409):
  - `GET /v1/mcp/servers` → `{"enabled": true, "servers": [{"name", "transport", "enabled_in_file", "state", "detail", "tool_count"}]}` — every file entry (incl. `enabled:false`) merged with live status (`state: "connected"|"failed"|"connecting"|"disconnected"|"not_connected"`).
  - `PUT /v1/mcp/servers/{name}` body `{"entry": dict, "disabled": [str]}` → upsert + reconcile → the GET payload. (Spec said POST/PATCH/DELETE; PUT-upsert covers both POST and PATCH with one route — record as an intentional deviation.)
  - `DELETE /v1/mcp/servers/{name}` body `{"disabled": [str]}` → remove + reconcile → GET payload; 404 if absent.
  - `POST /v1/mcp/servers/{name}/reconnect` body `{"disabled": [str]}` → `manager.reconnect` → GET payload.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_mcp_routes.py
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.routes import build_router
from agentd.mcp.client import McpConnectionManager
from agentd.mcp.config import McpConfigLoader
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _StubSession:
    async def list_tools(self):
        class _R:
            tools = [type("T", (), {"name": "t1", "description": "", "inputSchema": {}})()]
        return _R()


@asynccontextmanager
async def _stub_factory(cfg):
    yield _StubSession()


def _client(tmp_path: Path, with_manager: bool = True) -> TestClient:
    manager = None
    if with_manager:
        manager = McpConnectionManager(
            McpConfigLoader(tmp_path), session_factory=_stub_factory)
    app = FastAPI()
    app.include_router(build_router(
        InMemoryTaskStore(), object(), ShadowWorkspaceManager(tmp_path / "shadows"),
        None, None, mcp_manager=manager))
    return TestClient(app)


def test_get_disabled_when_no_manager(tmp_path: Path) -> None:
    body = _client(tmp_path, with_manager=False).get("/v1/mcp/servers").json()
    assert body == {"enabled": False, "servers": []}
    resp = _client(tmp_path, with_manager=False).put(
        "/v1/mcp/servers/web", json={"entry": {"command": "x", "enabled": True}})
    assert resp.status_code == 409


def test_put_writes_file_connects_and_lists(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.put("/v1/mcp/servers/web", json={
        "entry": {"command": "uv", "args": ["run", "x.py"], "enabled": True},
        "disabled": []}).json()
    assert body["enabled"] is True
    (web,) = [s for s in body["servers"] if s["name"] == "web"]
    assert web["state"] == "connected" and web["tool_count"] == 1
    raw = json.loads((tmp_path / ".ai-editor" / "mcp.json").read_text())
    assert raw["mcpServers"]["web"]["command"] == "uv"


def test_disabled_entry_listed_but_not_connected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.put("/v1/mcp/servers/off", json={
        "entry": {"command": "x", "enabled": False}}).json()
    (off,) = [s for s in body["servers"] if s["name"] == "off"]
    assert off["enabled_in_file"] is False and off["state"] == "not_connected"


def test_delete_and_reconnect(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.put("/v1/mcp/servers/web", json={
        "entry": {"command": "uv", "enabled": True}})
    assert client.post("/v1/mcp/servers/web/reconnect", json={}).status_code == 200
    body = client.request("DELETE", "/v1/mcp/servers/web", json={}).json()
    assert all(s["name"] != "web" for s in body["servers"])
    assert client.request("DELETE", "/v1/mcp/servers/web", json={}).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_mcp_routes.py`
Expected: FAIL — `build_router` has no `mcp_manager` param / 404s

- [ ] **Step 3: Implement the routes**

In `routes.py` — signature `build_router(..., provider_runtime=None, mcp_manager=None)`, body models near the others:

```python
class McpUpsertRequest(BaseModel):
    entry: dict
    disabled: list[str] = []


class McpDisabledRequest(BaseModel):
    disabled: list[str] = []
```

```python
    def _mcp_server_listing() -> dict:
        from agentd.mcp.admin import read_raw_servers

        if mcp_manager is None:
            return {"enabled": False, "servers": []}
        statuses = {s.name: s for s in mcp_manager.statuses()}
        servers = []
        for name, entry in read_raw_servers(mcp_manager.loader.config_path).items():
            status = statuses.get(name)
            transport = str(entry.get("type") or
                            ("stdio" if entry.get("command") else "http"))
            servers.append({
                "name": name,
                "transport": transport,
                "enabled_in_file": entry.get("enabled") is True,
                "state": status.state if status else "not_connected",
                "detail": getattr(status, "detail", None) if status else None,
                "tool_count": getattr(status, "tool_count", 0) if status else 0,
            })
        return {"enabled": True, "servers": servers}

    @router.get("/mcp/servers")
    async def list_mcp_servers() -> dict:
        return _mcp_server_listing()

    @router.put("/mcp/servers/{name}")
    async def put_mcp_server(name: str, body: McpUpsertRequest) -> dict:
        from agentd.mcp.admin import upsert_server

        if mcp_manager is None:
            raise HTTPException(status_code=409, detail="MCP is disabled")
        try:
            upsert_server(mcp_manager.loader.config_path, name, body.entry)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await mcp_manager.reconcile(
            mcp_manager.loader.load(), disabled=frozenset(body.disabled))
        return _mcp_server_listing()

    @router.delete("/mcp/servers/{name}")
    async def delete_mcp_server(name: str, body: McpDisabledRequest) -> dict:
        from agentd.mcp.admin import remove_server

        if mcp_manager is None:
            raise HTTPException(status_code=409, detail="MCP is disabled")
        if not remove_server(mcp_manager.loader.config_path, name):
            raise HTTPException(status_code=404, detail=f"no MCP server named {name!r}")
        await mcp_manager.reconcile(
            mcp_manager.loader.load(), disabled=frozenset(body.disabled))
        return _mcp_server_listing()

    @router.post("/mcp/servers/{name}/reconnect")
    async def reconnect_mcp_server(name: str, body: McpDisabledRequest) -> dict:
        if mcp_manager is None:
            raise HTTPException(status_code=409, detail="MCP is disabled")
        await mcp_manager.reconnect(name, disabled=frozenset(body.disabled))
        return _mcp_server_listing()
```

`main.py:396`: `build_router(store, orchestrator, workspace_manager, retrieval_client, _chat_agent, provider_runtime=provider_runtime, mcp_manager=_mcp_manager)`.

- [ ] **Step 4: Run tests, full suite, commit**

Run: `cd services/agentd-py && pytest tests/test_mcp_routes.py && pytest && ruff check . && mypy agentd`

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/agentd/main.py \
        services/agentd-py/tests/test_mcp_routes.py
git commit -m "feat(api): MCP server management routes (list/upsert/delete/reconnect)"
```

---

### Task 6b: `AI_EDITOR_SKILLS_DISABLED` filter (the consumer side)

Task 10's spawn env and the Task 13 skill toggle emit this var — without a backend
consumer it does nothing (dry-run finding). Filter at the catalog loader so every
consumer (controller prompt, `read_skill`, `/v1/skills`, forced-load) sees the
same filtered set.

**Files:**
- Modify: `services/agentd-py/agentd/skills/loader.py` (`SkillCatalogLoader.load_catalog` filters)
- Test: `services/agentd-py/tests/test_skills_disabled_env.py`

**Interfaces:**
- Produces: `load_catalog()` drops manifests whose `name` is in the comma-separated
  `AI_EDITOR_SKILLS_DISABLED` env var (names stripped, empty entries ignored).
  Read per call — NOT cached with the mtime signature, so a restart isn't needed
  for tests but the env is process-stable in production anyway.

- [ ] **Step 1: Write the failing test**

```python
# services/agentd-py/tests/test_skills_disabled_env.py
from pathlib import Path

import pytest

from agentd.skills.loader import SkillCatalogLoader


def _write_skill(ws: Path, name: str) -> None:
    d = ws / ".ai-editor" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8")


def test_disabled_env_filters_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_skill(tmp_path, "keep-me")
    _write_skill(tmp_path, "drop-me")
    loader = SkillCatalogLoader(tmp_path)
    monkeypatch.setenv("AI_EDITOR_SKILLS_DISABLED", " drop-me , ,missing")
    assert [m.name for m in loader.load_catalog()] == ["keep-me"]
    monkeypatch.delenv("AI_EDITOR_SKILLS_DISABLED")
    assert sorted(m.name for m in loader.load_catalog()) == ["drop-me", "keep-me"]
```

- [ ] **Step 2: Run → FAIL, implement, run → PASS**

Run: `cd services/agentd-py && pytest tests/test_skills_disabled_env.py`
Implementation in `load_catalog` (apply the filter to the returned list AFTER the
mtime-cached scan, so the cache stays name-agnostic):

```python
def _disabled_names() -> frozenset[str]:
    raw = os.getenv("AI_EDITOR_SKILLS_DISABLED", "")
    return frozenset(n.strip() for n in raw.split(",") if n.strip())
```

and `return [m for m in catalog if m.name not in _disabled_names()]` at the
existing return site(s).

- [ ] **Step 3: Full suite + commit**

Run: `cd services/agentd-py && pytest`

```bash
git add services/agentd-py/agentd/skills/loader.py services/agentd-py/tests/test_skills_disabled_env.py
git commit -m "feat(skills): honor AI_EDITOR_SKILLS_DISABLED in catalog discovery"
```

---

## Part B — editor-client contracts

### Task 7: Client methods + Zod schemas

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`
- Modify: `apps/editor-client/src/client/http-backend-client.ts`
- Test: `apps/editor-client/test/settings-client.test.ts` (the package's tests live in `test/`, ESM imports with `.js` suffix, fetch mocked via the client's injected `fetchFn` option — verified against `test/http-backend-client.test.ts`)

**Interfaces (produced — Tasks 12–14 consume these exact names):**

```ts
export const ProviderValidateResultSchema = z.object({
  ok: z.boolean(), model: z.string().optional(), error: z.string().optional(),
});
export type ProviderValidateResult = z.infer<typeof ProviderValidateResultSchema>;

export const McpServerViewSchema = z.object({
  name: z.string(),
  transport: z.string(),
  enabledInFile: z.boolean(),
  state: z.string(),
  detail: z.string().nullable(),
  toolCount: z.number(),
});
export const McpServerListSchema = z.object({
  enabled: z.boolean(), servers: z.array(McpServerViewSchema),
});
export type McpServerView = z.infer<typeof McpServerViewSchema>;
export type McpServerList = z.infer<typeof McpServerListSchema>;

// BackendConfig gains:  provider: z.object({ backend: z.string(), model: z.string() }).nullable().optional()
```

Client methods on `HttpBackendClient` (+ the `BackendTaskClient` interface if the
existing config/skills methods are declared there — mirror whatever `getConfig`
does):

```ts
validateProvider(req: { backend: string; model?: string; credentials?: Record<string, string> }): Promise<ProviderValidateResult>
setProvider(req: { backend: string; model?: string; credentials?: Record<string, string> }): Promise<{ backend: string; model: string }>
listMcpServers(): Promise<McpServerList>
upsertMcpServer(name: string, entry: Record<string, unknown>, disabled: string[]): Promise<McpServerList>
deleteMcpServer(name: string, disabled: string[]): Promise<McpServerList>
reconnectMcpServer(name: string, disabled: string[]): Promise<McpServerList>
```

Snake↔camel mapping happens in the client (backend sends `enabled_in_file`/`tool_count`).

- [ ] **Step 1: Write the failing tests**

```ts
// apps/editor-client/test/settings-client.test.ts
import { describe, expect, test } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

interface Sent { url: string; method: string; body: unknown }

function clientWith(responseBody: unknown, sent: Sent[] = []) {
  return new HttpBackendClient({
    baseUrl: "http://localhost:8000",
    fetchFn: async (url, init) => {
      sent.push({
        url: String(url),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responseBody), {
        status: 200, headers: { "content-type": "application/json" },
      });
    },
  });
}

describe("settings client methods", () => {
  test("validateProvider posts body and parses result", async () => {
    const sent: Sent[] = [];
    const res = await clientWith({ ok: true, model: "m" }, sent)
      .validateProvider({ backend: "groq", credentials: { GROQ_API_KEY: "k" } });
    expect(res).toEqual({ ok: true, model: "m" });
    expect(sent[0].url).toContain("/v1/providers/validate");
    expect((sent[0].body as { backend: string }).backend).toBe("groq");
  });

  test("setProvider PUTs to /v1/config/provider", async () => {
    const sent: Sent[] = [];
    const res = await clientWith({ ok: true, backend: "groq", model: "m2" }, sent)
      .setProvider({ backend: "groq", model: "m2" });
    expect(res).toEqual({ backend: "groq", model: "m2" });
    expect(sent[0].method).toBe("PUT");
    expect(sent[0].url).toContain("/v1/config/provider");
  });

  test("listMcpServers maps snake_case to camelCase", async () => {
    const res = await clientWith({ enabled: true, servers: [{
      name: "web", transport: "stdio", enabled_in_file: true,
      state: "connected", detail: null, tool_count: 2 }] }).listMcpServers();
    expect(res.servers[0]).toEqual({
      name: "web", transport: "stdio", enabledInFile: true,
      state: "connected", detail: null, toolCount: 2 });
  });

  test("upsertMcpServer PUTs entry + disabled", async () => {
    const sent: Sent[] = [];
    await clientWith({ enabled: true, servers: [] }, sent)
      .upsertMcpServer("web", { command: "uv", enabled: true }, ["gh"]);
    expect(sent[0].url).toContain("/v1/mcp/servers/web");
    expect(sent[0].method).toBe("PUT");
    expect(sent[0].body).toEqual({
      entry: { command: "uv", enabled: true }, disabled: ["gh"] });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run -w @ai-editor/editor-client test`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Implement schemas + methods**

Add the schemas to `task-contracts.ts` (exported, near `SkillSummary`); extend
`BackendConfigSchema` with the optional nullable `provider` object. Implement the
six methods in `http-backend-client.ts` following the exact fetch/parse/map
pattern of `getConfig`/`listSkills` (they show the codebase's error handling and
snake→camel conventions). Mapping for MCP views:

```ts
private static mapMcpList(raw: unknown): McpServerList {
  const parsed = McpServerListWireSchema.parse(raw); // wire schema keeps snake_case
  return {
    enabled: parsed.enabled,
    servers: parsed.servers.map((s) => ({
      name: s.name, transport: s.transport, enabledInFile: s.enabled_in_file,
      state: s.state, detail: s.detail, toolCount: s.tool_count,
    })),
  };
}
```

- [ ] **Step 4: Run tests, build, typecheck**

Run: `npm run -w @ai-editor/editor-client test && npm run -w @ai-editor/editor-client build && npm run typecheck`
Expected: PASS / clean. (Build before extension typecheck — the extension types off `dist/index.d.ts`.)

- [ ] **Step 5: Commit**

```bash
git add apps/editor-client/src
git commit -m "feat(editor-client): validate/set provider + MCP server management client methods"
```

---

## Part C — Extension runtime (vscode-free core + wiring)

### Task 8: Manifest + platform helpers

**Files:**
- Create: `apps/vscode-extension/src/runtime/manifest.ts`
- Test: `apps/vscode-extension/test/runtime-manifest.test.ts` (the extension's tests live flat in `test/`, ESM imports with `.js` suffix — verified against `test/memory-data.test.ts`)

**Interfaces (produced):**

```ts
export type PlatformKey = "darwin-arm64" | "darwin-x64" | "linux-x64" | "win32-x64";
export type ComponentId = "uv" | "agentd" | "indexer" | "ripgrep" | "lsps";
export interface ComponentSpec {
  version: string;
  // binary components: per-platform url+sha256. agentd: single wheel url+sha256
  // under the "any" key. lsps: npm package specs, no url.
  urls?: Partial<Record<PlatformKey | "any", string>>;
  sha256?: Partial<Record<PlatformKey | "any", string>>;
  npmPackages?: string[];
}
export interface RuntimeManifest {
  manifestVersion: 1;
  releaseTag: string;
  components: Record<ComponentId, ComponentSpec>;
}
export function platformKey(platform?: NodeJS.Platform, arch?: string): PlatformKey; // throws on unsupported
export function sha256Hex(data: Buffer): string;
export function verifyChecksum(data: Buffer, expectedHex: string): void; // throws Error naming both digests
```

- [ ] **Step 1: Write the failing tests**

```ts
// apps/vscode-extension/test/runtime-manifest.test.ts
import { describe, expect, it } from "vitest";
import { platformKey, sha256Hex, verifyChecksum } from "../src/runtime/manifest.js";

describe("platformKey", () => {
  it("maps the four supported targets", () => {
    expect(platformKey("darwin", "arm64")).toBe("darwin-arm64");
    expect(platformKey("darwin", "x64")).toBe("darwin-x64");
    expect(platformKey("linux", "x64")).toBe("linux-x64");
    expect(platformKey("win32", "x64")).toBe("win32-x64");
  });
  it("throws on unsupported combos", () => {
    expect(() => platformKey("linux", "arm64")).toThrow(/unsupported/i);
  });
});

describe("checksums", () => {
  it("sha256Hex matches a known vector", () => {
    expect(sha256Hex(Buffer.from("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
  it("verifyChecksum throws with both digests in the message", () => {
    expect(() => verifyChecksum(Buffer.from("abc"), "00".repeat(32)))
      .toThrow(/ba7816bf/);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w @ai-editor/vscode-extension test`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```ts
// apps/vscode-extension/src/runtime/manifest.ts
// vscode-free: node crypto only. Types are the contract with scripts/release/make_manifest.py.
import { createHash } from "node:crypto";

export type PlatformKey = "darwin-arm64" | "darwin-x64" | "linux-x64" | "win32-x64";
export type ComponentId = "uv" | "agentd" | "indexer" | "ripgrep" | "lsps";

export interface ComponentSpec {
  version: string;
  urls?: Partial<Record<PlatformKey | "any", string>>;
  sha256?: Partial<Record<PlatformKey | "any", string>>;
  npmPackages?: string[];
}

export interface RuntimeManifest {
  manifestVersion: 1;
  releaseTag: string;
  components: Record<ComponentId, ComponentSpec>;
}

const SUPPORTED: Record<string, PlatformKey> = {
  "darwin-arm64": "darwin-arm64",
  "darwin-x64": "darwin-x64",
  "linux-x64": "linux-x64",
  "win32-x64": "win32-x64",
};

export function platformKey(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
): PlatformKey {
  const key = SUPPORTED[`${platform}-${arch}`];
  if (!key) throw new Error(`unsupported platform: ${platform}-${arch}`);
  return key;
}

export function sha256Hex(data: Buffer): string {
  return createHash("sha256").update(data).digest("hex");
}

export function verifyChecksum(data: Buffer, expectedHex: string): void {
  const actual = sha256Hex(data);
  if (actual !== expectedHex.toLowerCase()) {
    throw new Error(`checksum mismatch: expected ${expectedHex}, got ${actual}`);
  }
}
```

- [ ] **Step 4: Run tests to verify pass, commit**

Run: `npm run -w @ai-editor/vscode-extension test`

```bash
git add apps/vscode-extension/src/runtime
git commit -m "feat(runtime): manifest types + platform key + sha256 verification"
```

---

### Task 9: RuntimeInstaller

**Files:**
- Create: `apps/vscode-extension/src/runtime/installer.ts`
- Test: `apps/vscode-extension/test/runtime-installer.test.ts`

**Interfaces:**
- Consumes: Task 8 types + `verifyChecksum` + `platformKey`.
- Produces:

```ts
export interface ExecResult { code: number; stdout: string; stderr: string }
export interface InstallerDeps {
  runtimeDir: string;
  manifest: RuntimeManifest;
  download(url: string): Promise<Buffer>;
  exec(cmd: string, args: string[], opts?: { cwd?: string }): Promise<ExecResult>;
  hasNode(): Promise<boolean>;
  platform?: PlatformKey;               // default platformKey()
}
export type ComponentStatus = "pending" | "running" | "done" | "failed" | "skipped";
export interface ComponentProgress { id: ComponentId; status: ComponentStatus; detail?: string }
export interface InstallResult { ok: boolean; components: ComponentProgress[] }
export class RuntimeInstaller {
  constructor(deps: InstallerDeps);
  installAll(onProgress?: (p: ComponentProgress) => void): Promise<InstallResult>;
}
export function venvPython(runtimeDir: string, platform?: PlatformKey): string;
export function binPath(runtimeDir: string, name: string, platform?: PlatformKey): string;
```

Layout under `runtimeDir`: `bin/uv[.exe]`, `bin/ai-editor-indexer[.exe]`, `bin/rg[.exe]`,
`venv/` (created by uv), `node_modules/` (LSPs), `install-state.json`
(`Record<ComponentId, string>` — installed version per component, the resume seam),
`runtime.json` (`{ releaseTag, components: Record<ComponentId, string> }` written when
`installAll` finishes ok — the RuntimeState of spec §5.3).

Component behaviors (order matters — agentd needs uv):
1. **uv**: download platform url → verify → write `bin/uv`, chmod 0o755. Skip when state version matches and file exists.
2. **agentd**: `exec(uv, ["venv", "<runtimeDir>/venv", "--python", "3.12"])` then `exec(uv, ["pip", "install", "--python", venvPython(dir), "ai-editor-agentd==<version>"])` — the release wheel is on PyPI-or-release-URL; when `urls.any` is set, install that URL instead of the pinned name.
3. **indexer** / **ripgrep**: download → verify → write `bin/<name>`, chmod.
4. **lsps**: if `!hasNode()` → status `skipped`, detail `"Node.js not found — code-graph edges degraded"`; else `exec("npm", ["install", "--prefix", runtimeDir, ...npmPackages])`.

Failure isolation: a failed component marks `failed` with the error message and
**continues** to later components that don't depend on it (agentd depends on uv —
uv failure marks agentd `failed: "uv unavailable"` without running it); `ok` is
true only when nothing failed (skipped is not failed).

- [ ] **Step 1: Write the failing tests**

```ts
// apps/vscode-extension/test/runtime-installer.test.ts
import { mkdtempSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { RuntimeInstaller, venvPython, type InstallerDeps } from "../src/runtime/installer.js";
import { sha256Hex, type RuntimeManifest } from "../src/runtime/manifest.js";

const BIN = Buffer.from("#!/bin/sh\necho hi\n");

function manifest(): RuntimeManifest {
  const sha = sha256Hex(BIN);
  return {
    manifestVersion: 1,
    releaseTag: "v0.1.0",
    components: {
      uv: { version: "0.5.0", urls: { "darwin-arm64": "https://r/uv" }, sha256: { "darwin-arm64": sha } },
      agentd: { version: "0.1.0" },
      indexer: { version: "0.1.0", urls: { "darwin-arm64": "https://r/ix" }, sha256: { "darwin-arm64": sha } },
      ripgrep: { version: "14.1.0", urls: { "darwin-arm64": "https://r/rg" }, sha256: { "darwin-arm64": sha } },
      lsps: { version: "1", npmPackages: ["pyright@1.1.400", "typescript-language-server@4.3.3"] },
    },
  };
}

function deps(overrides: Partial<InstallerDeps> = {}): InstallerDeps & { calls: string[][] } {
  const calls: string[][] = [];
  return {
    runtimeDir: mkdtempSync(join(tmpdir(), "rt-")),
    manifest: manifest(),
    download: async () => BIN,
    exec: async (cmd, args) => { calls.push([cmd, ...args]); return { code: 0, stdout: "", stderr: "" }; },
    hasNode: async () => true,
    platform: "darwin-arm64",
    calls,
    ...overrides,
  };
}

describe("RuntimeInstaller", () => {
  it("happy path installs all five components and writes runtime.json", async () => {
    const d = deps();
    const result = await new RuntimeInstaller(d).installAll();
    expect(result.ok).toBe(true);
    expect(result.components.map((c) => c.status)).toEqual(
      ["done", "done", "done", "done", "done"]);
    expect(existsSync(join(d.runtimeDir, "bin", "uv"))).toBe(true);
    expect(d.calls.some(([c, a]) => c.endsWith("uv") && a === "venv")).toBe(true);
    const state = JSON.parse(readFileSync(join(d.runtimeDir, "runtime.json"), "utf8"));
    expect(state.releaseTag).toBe("v0.1.0");
  });

  it("checksum mismatch fails that component, uv failure cascades to agentd only", async () => {
    const d = deps({ download: async (url) => url.endsWith("uv") ? Buffer.from("evil") : BIN });
    const result = await new RuntimeInstaller(d).installAll();
    const byId = Object.fromEntries(result.components.map((c) => [c.id, c]));
    expect(result.ok).toBe(false);
    expect(byId.uv.status).toBe("failed");
    expect(byId.uv.detail).toMatch(/checksum/i);
    expect(byId.agentd.status).toBe("failed");
    expect(byId.indexer.status).toBe("done"); // independent components still run
  });

  it("node absent skips lsps with a degraded-consequence detail", async () => {
    const d = deps({ hasNode: async () => false });
    const result = await new RuntimeInstaller(d).installAll();
    const lsps = result.components.find((c) => c.id === "lsps")!;
    expect(result.ok).toBe(true);
    expect(lsps.status).toBe("skipped");
    expect(lsps.detail).toMatch(/degraded/i);
  });

  it("resume: matching install-state version skips the download", async () => {
    const d = deps();
    await new RuntimeInstaller(d).installAll();
    let downloads = 0;
    const d2 = { ...d, download: async () => { downloads++; return BIN; } };
    const result = await new RuntimeInstaller(d2).installAll();
    expect(result.ok).toBe(true);
    expect(downloads).toBe(0);
  });
});

describe("venvPython", () => {
  it("posix and windows layouts", () => {
    expect(venvPython("/r", "darwin-arm64")).toBe("/r/venv/bin/python");
    expect(venvPython("/r", "win32-x64")).toContain(join("venv", "Scripts", "python.exe"));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w @ai-editor/vscode-extension test`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `installer.ts`**

```ts
// apps/vscode-extension/src/runtime/installer.ts
// vscode-free: all effects behind InstallerDeps so tests inject fakes.
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  platformKey, verifyChecksum,
  type ComponentId, type PlatformKey, type RuntimeManifest,
} from "./manifest.js";

export interface ExecResult { code: number; stdout: string; stderr: string }
export interface InstallerDeps {
  runtimeDir: string;
  manifest: RuntimeManifest;
  download(url: string): Promise<Buffer>;
  exec(cmd: string, args: string[], opts?: { cwd?: string }): Promise<ExecResult>;
  hasNode(): Promise<boolean>;
  platform?: PlatformKey;
}
export type ComponentStatus = "pending" | "running" | "done" | "failed" | "skipped";
export interface ComponentProgress { id: ComponentId; status: ComponentStatus; detail?: string }
export interface InstallResult { ok: boolean; components: ComponentProgress[] }

const ORDER: ComponentId[] = ["uv", "agentd", "indexer", "ripgrep", "lsps"];
const BIN_NAME: Partial<Record<ComponentId, string>> = {
  uv: "uv", indexer: "ai-editor-indexer", ripgrep: "rg",
};

export function binPath(runtimeDir: string, name: string, platform: PlatformKey = platformKey()): string {
  const exe = platform === "win32-x64" ? `${name}.exe` : name;
  return join(runtimeDir, "bin", exe);
}

export function venvPython(runtimeDir: string, platform: PlatformKey = platformKey()): string {
  return platform === "win32-x64"
    ? join(runtimeDir, "venv", "Scripts", "python.exe")
    : join(runtimeDir, "venv", "bin", "python");
}

function readState(runtimeDir: string): Partial<Record<ComponentId, string>> {
  try {
    return JSON.parse(readFileSync(join(runtimeDir, "install-state.json"), "utf8"));
  } catch {
    return {};
  }
}

function writeState(runtimeDir: string, state: Partial<Record<ComponentId, string>>): void {
  writeFileSync(join(runtimeDir, "install-state.json"), JSON.stringify(state, null, 2));
}

export class RuntimeInstaller {
  private readonly platform: PlatformKey;

  constructor(private readonly deps: InstallerDeps) {
    this.platform = deps.platform ?? platformKey();
  }

  async installAll(onProgress?: (p: ComponentProgress) => void): Promise<InstallResult> {
    mkdirSync(join(this.deps.runtimeDir, "bin"), { recursive: true });
    const state = readState(this.deps.runtimeDir);
    const results: ComponentProgress[] = [];
    let uvOk = true;

    for (const id of ORDER) {
      const spec = this.deps.manifest.components[id];
      const emit = (p: ComponentProgress) => { onProgress?.(p); };
      emit({ id, status: "running" });
      let progress: ComponentProgress;
      try {
        if (id === "agentd" && !uvOk) {
          progress = { id, status: "failed", detail: "uv unavailable" };
        } else if (state[id] === spec.version && this.artifactPresent(id)) {
          progress = { id, status: "done", detail: "already installed" };
        } else {
          progress = await this.installOne(id);
          if (progress.status === "done") {
            state[id] = spec.version;
            writeState(this.deps.runtimeDir, state);
          }
        }
      } catch (err) {
        progress = { id, status: "failed", detail: err instanceof Error ? err.message : String(err) };
      }
      if (id === "uv" && progress.status !== "done") uvOk = false;
      emit(progress);
      results.push(progress);
    }

    const ok = results.every((c) => c.status !== "failed");
    if (ok) {
      const versions = Object.fromEntries(
        ORDER.map((id) => [id, this.deps.manifest.components[id].version]));
      writeFileSync(join(this.deps.runtimeDir, "runtime.json"), JSON.stringify(
        { releaseTag: this.deps.manifest.releaseTag, components: versions }, null, 2));
    }
    return { ok, components: results };
  }

  private artifactPresent(id: ComponentId): boolean {
    const bin = BIN_NAME[id];
    if (bin) return existsSync(binPath(this.deps.runtimeDir, bin, this.platform));
    if (id === "agentd") return existsSync(venvPython(this.deps.runtimeDir, this.platform));
    return existsSync(join(this.deps.runtimeDir, "node_modules"));
  }

  private async installOne(id: ComponentId): Promise<ComponentProgress> {
    const spec = this.deps.manifest.components[id];
    if (id === "lsps") {
      if (!(await this.deps.hasNode())) {
        return { id, status: "skipped", detail: "Node.js not found — code-graph edges degraded" };
      }
      const res = await this.deps.exec(
        "npm", ["install", "--prefix", this.deps.runtimeDir, ...(spec.npmPackages ?? [])]);
      if (res.code !== 0) throw new Error(`npm install failed: ${res.stderr.slice(0, 400)}`);
      return { id, status: "done" };
    }
    if (id === "agentd") {
      const uv = binPath(this.deps.runtimeDir, "uv", this.platform);
      const venv = await this.deps.exec(uv, ["venv", join(this.deps.runtimeDir, "venv"), "--python", "3.12"]);
      if (venv.code !== 0) throw new Error(`uv venv failed: ${venv.stderr.slice(0, 400)}`);
      const target = spec.urls?.any ?? `ai-editor-agentd==${spec.version}`;
      const pip = await this.deps.exec(
        uv, ["pip", "install", "--python", venvPython(this.deps.runtimeDir, this.platform), target]);
      if (pip.code !== 0) throw new Error(`uv pip install failed: ${pip.stderr.slice(0, 400)}`);
      return { id, status: "done" };
    }
    // binary components: uv / indexer / ripgrep
    const url = spec.urls?.[this.platform];
    const sha = spec.sha256?.[this.platform];
    if (!url || !sha) throw new Error(`manifest has no ${this.platform} artifact for ${id}`);
    const data = await this.deps.download(url);
    verifyChecksum(data, sha);
    const dest = binPath(this.deps.runtimeDir, BIN_NAME[id]!, this.platform);
    writeFileSync(dest, data);
    if (this.platform !== "win32-x64") chmodSync(dest, 0o755);
    return { id, status: "done" };
  }
}
```

- [ ] **Step 4: Run tests to verify pass, commit**

Run: `npm run -w @ai-editor/vscode-extension test && npm run -w @ai-editor/vscode-extension typecheck`

```bash
git add apps/vscode-extension/src/runtime
git commit -m "feat(runtime): RuntimeInstaller — provision uv/agentd/indexer/ripgrep/LSPs with resume + checksums"
```

---

### Task 10: BackendProcess

**Files:**
- Create: `apps/vscode-extension/src/runtime/backend-process.ts`
- Test: `apps/vscode-extension/test/runtime-backend-process.test.ts`

**Interfaces:**
- Consumes: `binPath`/`venvPython` (Task 9); lockfile JSON shape from Task 4 (`{pid, port, started_at}` at `<workspace>/.agentd/agentd.lock`).
- Produces:

```ts
export interface BackendSettings {
  backend: string;                     // "gemini" | "openai" | ... (never "scripted")
  model: string;
  apiKey?: { envVar: string; value: string };   // from SecretStorage, spawn-env only
  extraEnv?: Record<string, string>;   // policies/flags from VS Code settings
  skillsDisabled?: string[];           // → AI_EDITOR_SKILLS_DISABLED (comma-joined)
}
export interface ChildHandle { pid: number; kill(): void; onExit(cb: (code: number | null) => void): void }
export interface ProcessDeps {
  runtimeDir: string;
  spawn(cmd: string, args: string[], opts: { env: Record<string, string> }): ChildHandle;
  fetchJson(url: string, init?: { method?: string; body?: string }): Promise<unknown>; // throws on non-2xx
  pickPort(): Promise<number>;
  sleep(ms: number): Promise<void>;
  isPidAlive(pid: number): boolean;
  log(line: string): void;
  platform?: PlatformKey;
}
export const MODEL_ENV_VAR: Record<string, string>;  // same table as agentd/providers/factory.py
export function buildBackendEnv(
  workspace: string, settings: BackendSettings, runtimeDir: string, port: number,
  platform?: PlatformKey,
): Record<string, string>;
export class BackendProcess {
  constructor(deps: ProcessDeps);
  async start(workspace: string, settings: BackendSettings): Promise<{ port: number; reused: boolean }>;
  async stop(): Promise<void>;         // kills backend + watcher children
  get port(): number | undefined;
}
```

`buildBackendEnv` must produce (mirroring `start-backend.sh:361-392`):
`AI_EDITOR_REASONING_BACKEND`, `AI_EDITOR_WORKSPACE_PATH`, `AI_EDITOR_PORT` (→ lockfile),
`AI_EDITOR_DB_PATH`/`AI_EDITOR_CHAT_DB_PATH`/`AI_EDITOR_SHADOW_ROOT`/`AI_EDITOR_LOG_FILE`/
`AI_EDITOR_ARTIFACTS_ROOT` (all under `<workspace>/.agentd/`),
`AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH` (`<workspace>/.ai-editor/index-snapshot.json`),
`AI_EDITOR_RIPGREP_CMD` (Task 9 `binPath(runtimeDir, "rg")`),
`<MODEL_ENV_VAR[backend]>=model`, `settings.apiKey.envVar=value` when present,
default-on flags `AI_EDITOR_CHAT_CONTROLLER=1`, `AI_EDITOR_SKILLS_ENABLED=1`,
`AI_EDITOR_MCP_ENABLED=1`, `AI_EDITOR_DOC_WRITE_ENABLED=1`, `AI_EDITOR_SEMANTIC_RETRIEVAL=true`,
`AI_EDITOR_STEP_REVIEW_AUTO_ACCEPT=false`, `AI_EDITOR_SHELL_POLICY=ask`,
`AI_EDITOR_SCOPE_POLICY=ask`, `AI_EDITOR_SCOPE_TRIGGER=any`,
`AI_EDITOR_SKILLS_DISABLED` (comma-joined, only when non-empty) — then
`...settings.extraEnv` last so user settings override any default. Plus inherited
`PATH` etc. (`{ ...process.env, ...built }` — built wins).

`start()` logic:
1. Read `<workspace>/.agentd/agentd.lock`; if parseable AND `isPidAlive(pid)` AND
   `GET http://localhost:<port>/health` succeeds → `{ port, reused: true }` (no watcher spawn — a live managed backend already has one).
2. Otherwise delete the stale lock, `pickPort()`, spawn
   `venvPython(runtimeDir)` with args `["-m", "uvicorn", "agentd.main:app", "--port", String(port)]`
   (NO `--reload` — that's a dev-script concern) and the built env.
3. Poll `GET /health` once per second, 60 attempts; on timeout `stop()` and throw
   `Error("backend did not become healthy within 60s — see logs")`.
4. Fire `POST /v1/index/build` with `{workspace_path}` (non-fatal on error; log), then
   poll `GET /v1/index/status` up to 120×1s until `building === false` (non-fatal timeout).
5. Spawn the watcher: `binPath(runtimeDir, "ai-editor-indexer")` with args
   `["index", "--workspace", workspace, "--snapshot-path", "<ws>/.ai-editor/index-snapshot.json", "--watch", "true"]`
   and env `AI_EDITOR_BACKEND_URL=http://localhost:<port>`, `AI_EDITOR_LSP_ENABLED`
   `"true"` only when `<runtimeDir>/node_modules` exists, with
   `AI_EDITOR_LSP_PY_CMD="<runtimeDir>/node_modules/.bin/pyright-langserver --stdio"` and
   `AI_EDITOR_LSP_TS_CMD="<runtimeDir>/node_modules/.bin/typescript-language-server --stdio"`
   (`.cmd` suffix on win32); `AI_EDITOR_LSP_RS_CMD="rust-analyzer"` (detect-only — the
   indexer degrades gracefully when absent). Skip watcher entirely (with a log line)
   when the indexer binary is missing.
6. Return `{ port, reused: false }`.

- [ ] **Step 1: Write the failing tests**

```ts
// apps/vscode-extension/test/runtime-backend-process.test.ts
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { BackendProcess, buildBackendEnv, type ProcessDeps } from "../src/runtime/backend-process.js";

function deps(overrides: Partial<ProcessDeps> = {}) {
  const spawned: { cmd: string; args: string[]; env: Record<string, string> }[] = [];
  const d: ProcessDeps & { spawned: typeof spawned } = {
    runtimeDir: mkdtempSync(join(tmpdir(), "rt-")),
    spawn: (cmd, args, opts) => {
      spawned.push({ cmd, args, env: opts.env });
      return { pid: 4242, kill: () => {}, onExit: () => {} };
    },
    fetchJson: async () => ({ status: "ok", building: false }),
    pickPort: async () => 8123,
    sleep: async () => {},
    isPidAlive: () => false,
    log: () => {},
    platform: "darwin-arm64",
    spawned,
    ...overrides,
  };
  return d;
}

function ws(): string {
  return mkdtempSync(join(tmpdir(), "ws-"));
}

const SETTINGS = {
  backend: "gemini", model: "gemini-flash-latest",
  apiKey: { envVar: "GEMINI_API_KEY", value: "sk-secret" },
};

describe("buildBackendEnv", () => {
  it("assembles the full spawn env", () => {
    const env = buildBackendEnv("/ws", SETTINGS, "/rt", 8123, "darwin-arm64");
    expect(env.AI_EDITOR_REASONING_BACKEND).toBe("gemini");
    expect(env.AI_EDITOR_WORKSPACE_PATH).toBe("/ws");
    expect(env.AI_EDITOR_PORT).toBe("8123");
    expect(env.AI_EDITOR_GEMINI_MODEL).toBe("gemini-flash-latest");
    expect(env.GEMINI_API_KEY).toBe("sk-secret");
    expect(env.AI_EDITOR_RIPGREP_CMD).toBe("/rt/bin/rg");
    expect(env.AI_EDITOR_CHAT_CONTROLLER).toBe("1");
    expect(env.AI_EDITOR_DB_PATH).toBe(join("/ws", ".agentd", "agentd.sqlite3"));
  });
  it("extraEnv overrides defaults; skillsDisabled joins", () => {
    const env = buildBackendEnv("/ws", {
      ...SETTINGS, extraEnv: { AI_EDITOR_SHELL_POLICY: "allow_all" },
      skillsDisabled: ["a", "b"] }, "/rt", 1);
    expect(env.AI_EDITOR_SHELL_POLICY).toBe("allow_all");
    expect(env.AI_EDITOR_SKILLS_DISABLED).toBe("a,b");
  });
});

describe("BackendProcess.start", () => {
  it("reuses a live locked backend without spawning", async () => {
    const w = ws();
    mkdirSync(join(w, ".agentd"));
    writeFileSync(join(w, ".agentd", "agentd.lock"),
      JSON.stringify({ pid: 999, port: 8200, started_at: 1 }));
    const d = deps({ isPidAlive: () => true });
    const res = await new BackendProcess(d).start(w, SETTINGS);
    expect(res).toEqual({ port: 8200, reused: true });
    expect(d.spawned).toHaveLength(0);
  });

  it("reaps a stale lock and spawns backend + watcher", async () => {
    const w = ws();
    mkdirSync(join(w, ".agentd"));
    writeFileSync(join(w, ".agentd", "agentd.lock"),
      JSON.stringify({ pid: 999, port: 8200, started_at: 1 }));
    const d = deps();
    writeFileSync(join(d.runtimeDir, "bin-marker"), ""); // ensure runtimeDir exists
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "ai-editor-indexer"), "");
    const res = await new BackendProcess(d).start(w, SETTINGS);
    expect(res.reused).toBe(false);
    expect(res.port).toBe(8123);
    expect(d.spawned[0].args).toContain("agentd.main:app");
    expect(d.spawned[0].env.AI_EDITOR_PORT).toBe("8123");
    expect(d.spawned[1].args[0]).toBe("index"); // watcher
    expect(d.spawned[1].env.AI_EDITOR_BACKEND_URL).toBe("http://localhost:8123");
  });

  it("throws when health never comes up", async () => {
    const d = deps({ fetchJson: async () => { throw new Error("conn refused"); } });
    await expect(new BackendProcess(d).start(ws(), SETTINGS))
      .rejects.toThrow(/healthy within 60s/);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w @ai-editor/vscode-extension test`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `backend-process.ts`**

Implement exactly the interface + logic specified above. Structure:

```ts
// apps/vscode-extension/src/runtime/backend-process.ts
// vscode-free. One instance per workspace folder; owns agentd + watcher children.
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { binPath, venvPython } from "./installer.js";
import { platformKey, type PlatformKey } from "./manifest.js";

// ... interfaces from the block above ...

export const MODEL_ENV_VAR: Record<string, string> = {
  anthropic: "AI_EDITOR_ANTHROPIC_MODEL", gemini: "AI_EDITOR_GEMINI_MODEL",
  huggingface: "AI_EDITOR_HUGGINGFACE_MODEL", groq: "AI_EDITOR_GROQ_MODEL",
  openrouter: "AI_EDITOR_OPENROUTER_MODEL", watsonx: "AI_EDITOR_WATSONX_MODEL",
  ollama: "AI_EDITOR_OLLAMA_MODEL", turboquant: "AI_EDITOR_TURBOQUANT_MODEL",
  openai: "AI_EDITOR_OPENAI_MODEL",
};

export function buildBackendEnv(
  workspace: string, settings: BackendSettings, runtimeDir: string, port: number,
  platform: PlatformKey = platformKey(),
): Record<string, string> {
  const agentdDir = join(workspace, ".agentd");
  const built: Record<string, string> = {
    AI_EDITOR_REASONING_BACKEND: settings.backend,
    AI_EDITOR_WORKSPACE_PATH: workspace,
    AI_EDITOR_PORT: String(port),
    AI_EDITOR_DB_PATH: join(agentdDir, "agentd.sqlite3"),
    AI_EDITOR_CHAT_DB_PATH: join(agentdDir, "chat.sqlite3"),
    AI_EDITOR_SHADOW_ROOT: join(agentdDir, "shadows"),
    AI_EDITOR_LOG_FILE: join(agentdDir, "agentd.log"),
    AI_EDITOR_ARTIFACTS_ROOT: join(agentdDir, "artifacts"),
    AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH: join(workspace, ".ai-editor", "index-snapshot.json"),
    AI_EDITOR_RIPGREP_CMD: binPath(runtimeDir, "rg", platform),
    AI_EDITOR_CHAT_CONTROLLER: "1",
    AI_EDITOR_SKILLS_ENABLED: "1",
    AI_EDITOR_MCP_ENABLED: "1",
    AI_EDITOR_DOC_WRITE_ENABLED: "1",
    AI_EDITOR_SEMANTIC_RETRIEVAL: "true",
    AI_EDITOR_STEP_REVIEW_AUTO_ACCEPT: "false",
    AI_EDITOR_SHELL_POLICY: "ask",
    AI_EDITOR_SCOPE_POLICY: "ask",
    AI_EDITOR_SCOPE_TRIGGER: "any",
  };
  const modelVar = MODEL_ENV_VAR[settings.backend];
  if (modelVar) built[modelVar] = settings.model;
  if (settings.apiKey) built[settings.apiKey.envVar] = settings.apiKey.value;
  if (settings.skillsDisabled?.length) {
    built.AI_EDITOR_SKILLS_DISABLED = settings.skillsDisabled.join(",");
  }
  return { ...built, ...settings.extraEnv };
}
```

`BackendProcess.start/stop` per the numbered logic in the Interfaces block. Spawn env
is `{ ...process.env, ...buildBackendEnv(...) } as Record<string, string>`. Keep both
`ChildHandle`s; `stop()` kills watcher first, then backend.

- [ ] **Step 4: Run tests to verify pass, commit**

Run: `npm run -w @ai-editor/vscode-extension test && npm run -w @ai-editor/vscode-extension typecheck`

```bash
git add apps/vscode-extension/src/runtime
git commit -m "feat(runtime): BackendProcess — lockfile reuse/reap, spawn, health, pre-warm, watcher"
```

---

### Task 11: vscode wiring (activation, commands, SecretStorage, status bar)

Thin vscode layer — no unit tests beyond typecheck/build; behavior verified in the
Task 17 live smoke. Everything testable stayed in Tasks 8-10.

**Files:**
- Create: `apps/vscode-extension/src/runtime/vscode-runtime.ts`
- Modify: `apps/vscode-extension/src/extension.ts` (activation + command registration)

**Interfaces:**
- Consumes: `RuntimeInstaller`, `BackendProcess`, `platformKey` (Tasks 8-10).
- Produces: `RuntimeManager` class the panels (Tasks 12-13) and `extension.ts` use:

```ts
export class RuntimeManager {
  constructor(context: vscode.ExtensionContext, output: vscode.OutputChannel);
  readonly runtimeDir: string;                       // ~/.ai-editor/runtime
  isInstalled(): boolean;                            // runtime.json exists
  install(onProgress: (p: ComponentProgress) => void): Promise<InstallResult>;
  async getProviderSettings(): Promise<BackendSettings | undefined>;  // globalState + SecretStorage
  async saveProvider(backend: string, model: string, apiKey?: string): Promise<void>;
  async startForWorkspace(workspace: string): Promise<{ port: number; reused: boolean }>;
  async restart(workspace: string): Promise<void>;
  backendUrl(workspace: string): string | undefined; // http://localhost:<port> once started
  mcpDisabled(): string[];                           // globalState "aiEditor.mcpDisabledServers"
  setMcpDisabled(names: string[]): Thenable<void>;
  skillsDisabled(): string[];                        // globalState "aiEditor.skillsDisabled"
  setSkillsDisabled(names: string[]): Thenable<void>;
  dispose(): Promise<void>;
}
```

- [ ] **Step 1: Implement `vscode-runtime.ts`**

Key decisions (all mechanical):
- `runtimeDir = join(os.homedir(), ".ai-editor", "runtime")`.
- Real `InstallerDeps`: `download` = `Buffer.from(await (await fetch(url)).arrayBuffer())`
  (throw on `!res.ok`); `exec` = promisified `child_process.execFile` (never `shell: true`);
  `hasNode` = `execFile("node", ["--version"])` succeeding.
- Real `ProcessDeps`: `spawn` = `child_process.spawn(cmd, args, { env, stdio: ["ignore", "pipe", "pipe"] })`
  with stdout/stderr piped to the shared `vscode.OutputChannel` ("AI Editor Runtime");
  `fetchJson` = global fetch + `res.ok` check; `pickPort` = `net.createServer` listen-on-0 trick;
  `isPidAlive` = `process.kill(pid, 0)` in try/catch.
- Secrets: `context.secrets.store("aiEditor.providerKey." + backend, key)`; provider
  backend+model in `globalState` keys `aiEditor.provider.backend` / `aiEditor.provider.model`.
- Manifest: ship `resources/runtime-manifest.json` inside the VSIX (generated by Task 16
  at release time; a hand-written dev copy checked in now, clearly marked
  `"releaseTag": "dev-unpinned"`); `install()` reads it.
- `restart` = `stop()` + `startForWorkspace()` with fresh settings/env.

- [ ] **Step 2: Wire `extension.ts`**

In `activate()`: construct `RuntimeManager`; register commands
`aiEditor.runSetup` (opens the Task 12 wizard), `aiEditor.restartBackend`,
`aiEditor.openSettingsPanel` (Task 13). On activation with a workspace folder:
if `!manager.isInstalled()` → run `aiEditor.runSetup`; else
`manager.startForWorkspace(folder)` and point the existing backend-URL plumbing
(`aiEditor.backendBaseUrl` consumers / `controller.ts` client construction) at
`manager.backendUrl(folder)` when the setting is at its default — an explicit
user-set `aiEditor.backendBaseUrl` (the dev flow) always wins and skips managed
spawn entirely. Status bar item: `$(rocket) AI Editor: starting…` → `✓ :<port>` →
`$(error) failed (Open logs)`. `deactivate()` → `manager.dispose()`.

Two more spec §5.2/§5.3 behaviors live here:
- **Crash backoff:** `RuntimeManager` subscribes to the backend child's `onExit`;
  unexpected exit → restart after 2s, 4s, 8s (max 3 attempts, counter reset after
  5 minutes healthy), then give up with the `$(error)` status-bar state + an
  "Open logs" toast. Implemented in `vscode-runtime.ts` around `BackendProcess`
  (which stays restart-agnostic).
- **Upgrade prompt:** on activation, if `runtime.json`'s `releaseTag` differs from
  the bundled `resources/runtime-manifest.json`, show one info prompt
  ("Runtime v X available — install now?") → re-run `install()` (the resume/state
  logic makes it incremental) then restart backends.

Also assemble `BackendSettings.extraEnv` here from the Task 15 VS Code settings:
`aiEditor.policy.shell` → `AI_EDITOR_SHELL_POLICY`, `aiEditor.policy.scope` →
`AI_EDITOR_SCOPE_POLICY`, `aiEditor.memory.enabled` → `AI_EDITOR_MEMORY_ENABLED`,
`aiEditor.memory.reranker` → `AI_EDITOR_MEMORY_RERANKER` (only when the user set
them — otherwise the Task 10 defaults stand), plus `skillsDisabled` from
`manager.skillsDisabled()`.

- [ ] **Step 3: Build + typecheck + commit**

Run: `npm run build && npm run typecheck && npm run test`
Expected: clean; existing extension tests unaffected.

```bash
git add apps/vscode-extension/src apps/vscode-extension/resources
git commit -m "feat(extension): RuntimeManager wiring — managed install/spawn, secrets, status bar"
```

---

### Task 12: First-run setup wizard (webview)

**Files:**
- Create: `apps/vscode-extension/webview-ui/setup.html` (copy `memory.html`, retitle, point at `src/setup/main.tsx`)
- Create: `apps/vscode-extension/webview-ui/src/setup/main.tsx`, `SetupApp.tsx`, `types.ts`
- Create: `apps/vscode-extension/src/setup-data.ts` (vscode-free message handler)
- Create: `apps/vscode-extension/src/setup-panel.ts` (vscode panel class)
- Modify: `apps/vscode-extension/webview-ui/vite.config.ts` (add `setup` input)
- Modify: `apps/vscode-extension/src/extension.ts` (`aiEditor.runSetup` opens the panel)
- Test: `apps/vscode-extension/test/setup-data.test.ts`, plus a webview-ui component test colocated as `webview-ui/src/setup/SetupApp.test.tsx` if warranted (precedent: `src/memory/MemoryApp.test.tsx`)

**Interfaces:**
- Consumes: `RuntimeManager` (Task 11), `HttpBackendClient.validateProvider` (Task 7).
- Produces: message protocol (webview `types.ts` local mirror — the webview never imports editor-client):

```ts
// webview → host
type SetupInMsg =
  | { type: "setup/install" }
  | { type: "setup/validate"; backend: string; model: string; apiKey?: string }
  | { type: "setup/save"; backend: string; model: string; apiKey?: string }   // save + start backend
  | { type: "setup/openChat" };
// host → webview
type SetupOutMsg =
  | { type: "setup/progress"; component: string; status: string; detail?: string }
  | { type: "setup/installDone"; ok: boolean }
  | { type: "setup/validateResult"; ok: boolean; model?: string; error?: string }
  | { type: "setup/ready"; port: number }
  | { type: "setup/error"; message: string };
```

```ts
// src/setup-data.ts — vscode-free
export interface SetupDeps {
  install(onProgress: (p: { id: string; status: string; detail?: string }) => void): Promise<{ ok: boolean }>;
  validate(req: { backend: string; model?: string; credentials?: Record<string, string> }): Promise<{ ok: boolean; model?: string; error?: string }>;
  saveAndStart(backend: string, model: string, apiKey?: string): Promise<{ port: number }>;
  openChat(): void;
  keyEnvVar(backend: string): string | undefined;   // PROVIDER_KEY_ENV mirror
}
export function createSetupHandler(deps: SetupDeps, post: (msg: SetupOutMsg) => void):
  (msg: SetupInMsg) => Promise<void>;
export const PROVIDERS: { id: string; label: string; local: boolean; keyEnvVar?: string; defaultModel: string }[];
```

`PROVIDERS` lists all nine (spec decision 3): openai, anthropic, gemini, groq,
ollama (local), watsonx, openrouter, huggingface, turboquant (local) — defaults
mirroring `agentd/providers/factory.py::_DEFAULT_MODEL`. Local providers hide the
key field; `validate` for them pings reachability through the same backend route
once the backend is up — pre-backend, the wizard's validate for local providers
shows "will be checked at start" (the backend isn't running yet to proxy the ping;
cloud validation pre-backend is impossible for the same reason, so **validate
runs against a short-lived backend**: `saveAndStart` order is install → start →
validate-via-route → report. Reflect exactly that in `SetupApp` step order:
Install → Provider form → Start & validate → Done).

- [x] **Step 1: Write the failing handler tests**

```ts
// apps/vscode-extension/test/setup-data.test.ts
import { describe, expect, it } from "vitest";
import { createSetupHandler, PROVIDERS, type SetupDeps } from "../src/setup-data.js";

function deps(overrides: Partial<SetupDeps> = {}): SetupDeps {
  return {
    install: async (onProgress) => {
      onProgress({ id: "uv", status: "done" });
      return { ok: true };
    },
    validate: async () => ({ ok: true, model: "m" }),
    saveAndStart: async () => ({ port: 8123 }),
    openChat: () => {},
    keyEnvVar: (b) => (b === "ollama" ? undefined : "X_KEY"),
    ...overrides,
  };
}

describe("createSetupHandler", () => {
  it("install relays progress then installDone", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps(), (m) => posted.push(m));
    await handle({ type: "setup/install" });
    expect(posted).toEqual([
      { type: "setup/progress", component: "uv", status: "done", detail: undefined },
      { type: "setup/installDone", ok: true },
    ]);
  });

  it("validate maps apiKey to the provider env var", async () => {
    const posted: unknown[] = [];
    let seen: Record<string, string> | undefined;
    const handle = createSetupHandler(deps({
      validate: async (req) => { seen = req.credentials; return { ok: false, error: "bad" }; },
    }), (m) => posted.push(m));
    await handle({ type: "setup/validate", backend: "groq", model: "m", apiKey: "k" });
    expect(seen).toEqual({ X_KEY: "k" });
    expect(posted).toEqual([{ type: "setup/validateResult", ok: false, error: "bad" }]);
  });

  it("save starts the backend and posts ready; errors become setup/error", async () => {
    const posted: unknown[] = [];
    const handle = createSetupHandler(deps({
      saveAndStart: async () => { throw new Error("spawn failed"); },
    }), (m) => posted.push(m));
    await handle({ type: "setup/save", backend: "groq", model: "m", apiKey: "k" });
    expect(posted).toEqual([{ type: "setup/error", message: "spawn failed" }]);
  });

  it("PROVIDERS covers all nine, locals have no key var", () => {
    expect(PROVIDERS.map((p) => p.id).sort()).toEqual([
      "anthropic", "gemini", "groq", "huggingface", "ollama",
      "openai", "openrouter", "turboquant", "watsonx"]);
    expect(PROVIDERS.find((p) => p.id === "ollama")!.keyEnvVar).toBeUndefined();
  });
});
```

- [x] **Step 2: Run to verify failure, then implement `setup-data.ts`**

Run: `npm run -w @ai-editor/vscode-extension test` → FAIL. Implement the handler as a
plain switch over `msg.type` calling deps and posting the mapped results (every
deps call in try/catch → `setup/error`).

- [x] **Step 3: Implement the webview app**

`SetupApp.tsx`: four-step state machine (`welcome → install → provider → done`),
`useState` for step + per-component progress rows + provider form + validate state.
Install step renders a row per component (`spinner/✓/✗/skipped` + detail + a Retry
button that re-posts `setup/install` — the installer's resume makes retry cheap).
Provider step: `<select>` of `PROVIDERS`, model text input pre-filled with
`defaultModel`, password-type key input (hidden for `local`), "Install & Start"
button posting `setup/save`; on `setup/ready` advance to done with an
"Open chat" button (`setup/openChat`). Keep styling to the design tokens already
used by the chat webview (reuse its CSS variables; polish is deferred to C).
Message plumbing mirrors `src/memory/vscodeApi.tsx`.

- [x] **Step 4: Implement `setup-panel.ts` + wire vite/extension**

Copy `src/memory-panel.ts`, with exactly these deltas: entry `setup.html`,
viewType `aiEditor.setup`, title `"AI Editor Setup"`, message handler =
`createSetupHandler` bridged to `RuntimeManager` + a short-lived
`HttpBackendClient` built from `manager.backendUrl(workspace)` for `validate`.
`saveAndStart` = `manager.saveProvider(...)` → `manager.startForWorkspace(...)` →
`client.validateProvider(...)` → on `ok:false` post `setup/validateResult` and keep
the wizard on the provider step. Add the `setup` input to `vite.config.ts`
`rollupOptions.input`. `aiEditor.runSetup` opens the panel.

- [x] **Step 5: Build everything, run suites, commit**

Run: `npm run build && npm run test && npm run typecheck`
(Remember: webview-ui has its own build step inside `npm run build`; rebuild before
any live smoke — the dist is what the panel loads.)

```bash
git add apps/vscode-extension/webview-ui apps/vscode-extension/src
git commit -m "feat(setup): first-run wizard — install progress, provider picker, validate, start"
```

---

### Task 13: Settings panel (webview)

**Files:**
- Create: `apps/vscode-extension/webview-ui/settings.html` + `webview-ui/src/settings/{main.tsx,SettingsApp.tsx,types.ts}`
- Create: `apps/vscode-extension/src/settings-data.ts` (vscode-free) + `src/settings-panel.ts`
- Modify: `apps/vscode-extension/webview-ui/vite.config.ts` (add `settings` input)
- Modify: `apps/vscode-extension/src/extension.ts` (`aiEditor.openSettingsPanel`)
- Test: `apps/vscode-extension/test/settings-data.test.ts`

**Interfaces:**
- Consumes: Task 7 client methods; `RuntimeManager` (Task 11: `mcpDisabled`/`setMcpDisabled`/`skillsDisabled`/`setSkillsDisabled`/`restart`/`runtimeDir`); `GET /v1/config` provider report (Task 3); `GET /v1/skills`.
- Produces: message protocol + handler:

```ts
// webview → host
type SettingsInMsg =
  | { type: "settings/load" }
  | { type: "settings/setProvider"; backend: string; model: string; apiKey?: string }
  | { type: "settings/mcpUpsert"; name: string; entry: Record<string, unknown> }
  | { type: "settings/mcpDelete"; name: string }
  | { type: "settings/mcpToggle"; name: string; enabled: boolean }
  | { type: "settings/mcpReconnect"; name: string }
  | { type: "settings/skillToggle"; name: string; enabled: boolean }   // marks restart-required
  | { type: "settings/setEnvFlag"; key: string; value: string }        // policies + memory knobs
  | { type: "settings/restartBackend" };
// host → webview
type SettingsOutMsg =
  | { type: "settings/state"; state: SettingsState }   // full snapshot after every action
  | { type: "settings/error"; message: string };
interface SettingsState {
  provider: { backend: string; model: string } | null;
  runtime: { releaseTag: string; components: Record<string, string> } | null; // runtime.json
  mcp: { enabled: boolean; servers: McpServerRow[] };  // row = McpServerView + userEnabled
  skills: { name: string; description: string; enabled: boolean }[];
  envFlags: Record<string, string>;   // aiEditor.policy.shell / .policy.scope / .memory.enabled / .memory.reranker
  restartRequired: boolean;
}
```

`settings/setEnvFlag` writes the VS Code setting via an injected
`deps.updateSetting(key: string, value: string): Promise<void>` (host side:
`vscode.workspace.getConfiguration().update(...)`) and flags `restartRequired` —
these are env-at-spawn knobs, applied by the managed restart (spec §6.2 Policies +
Memory sections; the panel renders them as two small select/toggle groups).

```ts
```

```ts
// src/settings-data.ts
export interface SettingsDeps {
  client: {   // structural subset of HttpBackendClient — inject fakes in tests
    getConfig(): Promise<{ provider?: { backend: string; model: string } | null }>;
    listMcpServers(): Promise<McpServerList>;
    listSkills(workspace: string): Promise<{ name: string; description: string }[]>;
    validateProvider(req: object): Promise<{ ok: boolean; error?: string }>;
    setProvider(req: object): Promise<{ backend: string; model: string }>;
    upsertMcpServer(name: string, entry: object, disabled: string[]): Promise<McpServerList>;
    deleteMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
    reconnectMcpServer(name: string, disabled: string[]): Promise<McpServerList>;
  };
  workspace: string;
  readRuntimeJson(): { releaseTag: string; components: Record<string, string> } | null;
  mcpDisabled(): string[];
  setMcpDisabled(names: string[]): Promise<void> | Thenable<void>;
  skillsDisabled(): string[];
  setSkillsDisabled(names: string[]): Promise<void> | Thenable<void>;
  storeSecret(backend: string, key: string): Promise<void>;
  keyEnvVar(backend: string): string | undefined;
  readEnvFlags(): Record<string, string>;            // current values of the Task 15 settings
  updateSetting(key: string, value: string): Promise<void>;
  restartBackend(): Promise<void>;
}
export function createSettingsHandler(deps: SettingsDeps, post: (m: SettingsOutMsg) => void):
  (msg: SettingsInMsg) => Promise<void>;
```

Behavior pins (each is a test):
- `setProvider` = validate first (with credentials when apiKey given); on `ok:false`
  post `settings/error` with the provider's message and **do not** call `setProvider`;
  on success store the secret, call `setProvider`, post fresh state (hot-swap — no restart).
- `mcpToggle` = update the user-local disabled list, then `reconnectMcpServer(name, newDisabled)`
  (reconcile applies the new set — enabling connects, disabling disconnects; **the
  shareable file is never touched by toggle**).
- `skillToggle` = update user-local list + `restartRequired: true` in the next state
  (env-path change; applied by `settings/restartBackend`).
- Every action ends by posting a full rebuilt `settings/state` (except pure errors).

- [x] **Step 1: Write the failing handler tests**

```ts
// apps/vscode-extension/test/settings-data.test.ts
import { describe, expect, it, vi } from "vitest";
import { createSettingsHandler, type SettingsDeps } from "../src/settings-data.js";

function deps(overrides: Partial<SettingsDeps> = {}): SettingsDeps & { disabled: string[] } {
  const box = { disabled: [] as string[], skills: [] as string[] };
  return {
    client: {
      getConfig: async () => ({ provider: { backend: "openai", model: "gpt-5" } }),
      listMcpServers: async () => ({ enabled: true, servers: [{
        name: "web", transport: "stdio", enabledInFile: true,
        state: "connected", detail: null, toolCount: 2 }] }),
      listSkills: async () => [{ name: "s1", description: "d" }],
      validateProvider: async () => ({ ok: true }),
      setProvider: async () => ({ backend: "groq", model: "m2" }),
      upsertMcpServer: async () => ({ enabled: true, servers: [] }),
      deleteMcpServer: async () => ({ enabled: true, servers: [] }),
      reconnectMcpServer: vi.fn(async () => ({ enabled: true, servers: [] })),
    },
    workspace: "/ws",
    readRuntimeJson: () => ({ releaseTag: "v0.1.0", components: {} }),
    mcpDisabled: () => box.disabled,
    setMcpDisabled: async (n) => { box.disabled = n; },
    skillsDisabled: () => box.skills,
    setSkillsDisabled: async (n) => { box.skills = n; },
    storeSecret: async () => {},
    keyEnvVar: () => "X_KEY",
    readEnvFlags: () => ({ "aiEditor.policy.shell": "ask" }),
    updateSetting: async () => {},
    restartBackend: async () => {},
    disabled: box.disabled,
    ...overrides,
  };
}

describe("createSettingsHandler", () => {
  it("load posts a full state snapshot", async () => {
    const posted: any[] = [];
    await createSettingsHandler(deps(), (m) => posted.push(m))({ type: "settings/load" });
    expect(posted[0].type).toBe("settings/state");
    expect(posted[0].state.provider).toEqual({ backend: "openai", model: "gpt-5" });
    expect(posted[0].state.mcp.servers[0].userEnabled).toBe(true);
    expect(posted[0].state.skills).toEqual([{ name: "s1", description: "d", enabled: true }]);
  });

  it("setProvider validates first and aborts on failure", async () => {
    const posted: any[] = [];
    const setProvider = vi.fn();
    const d = deps();
    d.client.validateProvider = async () => ({ ok: false, error: "bad key" });
    d.client.setProvider = setProvider as any;
    await createSettingsHandler(d, (m) => posted.push(m))(
      { type: "settings/setProvider", backend: "groq", model: "m", apiKey: "k" });
    expect(setProvider).not.toHaveBeenCalled();
    expect(posted[0]).toEqual({ type: "settings/error", message: "bad key" });
  });

  it("mcpToggle updates user-local disabled list and reconnects with it", async () => {
    const d = deps();
    const posted: any[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({ type: "settings/mcpToggle", name: "web", enabled: false });
    expect(d.mcpDisabled()).toEqual(["web"]);
    expect(d.client.reconnectMcpServer).toHaveBeenCalledWith("web", ["web"]);
  });

  it("skillToggle flags restartRequired", async () => {
    const posted: any[] = [];
    await createSettingsHandler(deps(), (m) => posted.push(m))(
      { type: "settings/skillToggle", name: "s1", enabled: false });
    const state = posted.find((m) => m.type === "settings/state")!.state;
    expect(state.restartRequired).toBe(true);
    expect(state.skills[0].enabled).toBe(false);
  });
});
```

- [x] **Step 2: Run to verify failure, implement `settings-data.ts`**

Run → FAIL, then implement per the behavior pins. `buildState()` helper fans out
the four reads in `Promise.all` and merges user-local disabled sets into
`userEnabled`/`enabled` booleans; `restartRequired` is handler-instance state,
cleared after `settings/restartBackend`.

- [x] **Step 3: Implement the webview app + panel + wiring**

`SettingsApp.tsx` sections (one component each, rendered from the single
`SettingsState`): **Providers** (same form pieces as the wizard's provider step —
extract shared bits into `webview-ui/src/settings/ProviderForm.tsx` and import it
from both apps only if trivially shareable; otherwise duplicate the small form —
polish/dedup is a C concern), **Runtime** (versions table + Restart button +
restart-required banner), **MCP servers** (status-dot list + toggle + Reconnect +
Remove + an Add form: name / transport select / command-or-url / env-var-name
rows → assembles the entry object with `"enabled": true` and `${VAR}` references),
**Skills** (toggle list). `settings-panel.ts` = `memory-panel.ts` deltas: entry
`settings.html`, viewType `aiEditor.settings`, title `"AI Editor Settings"`.
Wire `aiEditor.openSettingsPanel`. Add the vite input.

- [x] **Step 4: Build, run all suites, commit**

Run: `npm run build && npm run test && npm run typecheck`

```bash
git add apps/vscode-extension/webview-ui apps/vscode-extension/src
git commit -m "feat(settings): settings panel — provider hot-swap, MCP management, skills, runtime"
```

---

### Task 14: MCP tier-1 QuickPick commands

**Files:**
- Create: `apps/vscode-extension/src/mcp-quickpick.ts` (pure helpers) + command wiring in `extension.ts`
- Test: `apps/vscode-extension/test/mcp-quickpick.test.ts`

**Interfaces:**
- Produces: `buildMcpEntry(input: { transport: "stdio" | "http" | "sse"; commandLine?: string; url?: string; envVarNames: string[] }): Record<string, unknown>` — pure, unit-tested; the vscode command (`aiEditor.mcpAddServer`) chains QuickPick(transport) → InputBox(command or URL) → InputBox(name) → InputBox(comma-separated env var names) → `client.upsertMcpServer(name, buildMcpEntry(...), disabled)` → info toast with resulting state. `aiEditor.mcpListServers`: QuickPick of servers (`$(check)/$(error)` + tool count) → per-server actions (Enable/Disable → same toggle path as Task 13, Reconnect, Remove).

- [ ] **Step 1: Write the failing tests**

```ts
// apps/vscode-extension/test/mcp-quickpick.test.ts
import { describe, expect, it } from "vitest";
import { buildMcpEntry } from "../src/mcp-quickpick.js";

describe("buildMcpEntry", () => {
  it("stdio: splits command line, env vars become ${VAR} refs, enabled true", () => {
    expect(buildMcpEntry({
      transport: "stdio", commandLine: "uv run server.py --x",
      envVarNames: ["API_KEY"] })).toEqual({
      command: "uv", args: ["run", "server.py", "--x"],
      env: { API_KEY: "${API_KEY}" }, enabled: true });
  });
  it("http: url + headers from env var names", () => {
    expect(buildMcpEntry({
      transport: "http", url: "https://x", envVarNames: ["GITHUB_PAT"] })).toEqual({
      type: "http", url: "https://x",
      headers: { Authorization: "Bearer ${GITHUB_PAT}" }, enabled: true });
  });
  it("no env vars: omits env/headers", () => {
    expect(buildMcpEntry({ transport: "sse", url: "https://y", envVarNames: [] }))
      .toEqual({ type: "sse", url: "https://y", enabled: true });
  });
});
```

- [ ] **Step 2: Run → FAIL, implement helper + commands, run → PASS**

`buildMcpEntry` is a ~25-line pure function matching the assertions exactly
(stdio env map `VAR → "${VAR}"`; http/sse first env var becomes the
`Authorization: Bearer ${VAR}` header — the GitHub-server convention from the
research doc; additional vars become bare `${VAR}` headers keyed by their name).
Command wiring in `extension.ts` per the Interfaces block.

- [ ] **Step 3: Build, typecheck, commit**

```bash
git add apps/vscode-extension/src
git commit -m "feat(mcp): tier-1 QuickPick add/list commands over the management routes"
```

---

### Task 15: package.json contributions

**Files:**
- Modify: `apps/vscode-extension/package.json`

- [ ] **Step 1: Add contributions**

`contributes.commands`: `aiEditor.runSetup` ("AI Editor: Run Setup"),
`aiEditor.openSettingsPanel` ("AI Editor: Open Settings"),
`aiEditor.restartBackend` ("AI Editor: Restart Backend"),
`aiEditor.mcpAddServer` ("AI Editor: Add MCP Server"),
`aiEditor.mcpListServers` ("AI Editor: List MCP Servers").
`contributes.configuration`: keep `aiEditor.backendBaseUrl` (its description now
documents "leave default for the managed backend; set explicitly to attach to a
dev backend"); add `aiEditor.managedRuntime.enabled` (boolean, default `true` —
kill-switch back to the pure dev flow); add the env-flag settings the panel and
`RuntimeManager` read: `aiEditor.policy.shell` (`ask`|`allow_all`, default `ask`),
`aiEditor.policy.scope` (`strict`|`ask`|`auto`, default `ask`),
`aiEditor.memory.enabled` (boolean, default `false`), `aiEditor.memory.reranker`
(boolean, default `false`). Marketplace fields: `publisher`, `icon`
(`resources/icon.png` — commit a simple placeholder; branding is C), `categories`
(`["AI", "Programming Languages"]`), `repository`.

- [ ] **Step 2: Verify + commit**

Run: `npm run build && npm run typecheck` and `npx vsce ls --tree` (from
`apps/vscode-extension`; requires `vsce` — `npm i -D @vscode/vsce` if absent) to
confirm the package manifest is valid and webview dists are included.

```bash
git add apps/vscode-extension/package.json apps/vscode-extension/resources
git commit -m "chore(extension): commands, configuration, and marketplace manifest fields"
```

---

## Part D — Release pipeline

### Task 16: Manifest generator

**Files:**
- Create: `scripts/release/make_manifest.py`
- Test: `scripts/release/test_make_manifest.py` (run by path: `cd services/agentd-py && pytest ../../scripts/release/test_make_manifest.py`)

**Interfaces:**
- Produces: `build_manifest(release_tag: str, dist_dir: Path, url_base: str) -> dict` scanning `dist_dir` for the conventional artifact names below and emitting the Task 8 `RuntimeManifest` JSON shape (camelCase keys). CLI: `python scripts/release/make_manifest.py --release-tag vX --dist DIR --url-base URL --out manifest.json`. Artifact naming convention (CI produces exactly these): `ai-editor-indexer-<platform>[.exe]`, `rg-<platform>[.exe]`, `uv-<platform>[.exe]` with `<platform>` ∈ the four keys; `ai_editor_agentd-<ver>-py3-none-any.whl`. Versions: binaries from `--component-version name=ver` repeatable flags; agentd version parsed from the wheel filename; `lsps` pinned via `--lsp-packages "pyright@X,typescript-language-server@Y"`.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/release/test_make_manifest.py
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_manifest import build_manifest  # noqa: E402


def _touch(d: Path, name: str, content: bytes = b"bin") -> None:
    (d / name).write_bytes(content)


def test_build_manifest_shape(tmp_path: Path) -> None:
    for plat in ("darwin-arm64", "darwin-x64", "linux-x64"):
        _touch(tmp_path, f"ai-editor-indexer-{plat}")
        _touch(tmp_path, f"rg-{plat}")
        _touch(tmp_path, f"uv-{plat}")
    _touch(tmp_path, "ai-editor-indexer-win32-x64.exe")
    _touch(tmp_path, "rg-win32-x64.exe")
    _touch(tmp_path, "uv-win32-x64.exe")
    _touch(tmp_path, "ai_editor_agentd-0.2.0-py3-none-any.whl")

    m = build_manifest(
        "v0.2.0", tmp_path, "https://gh/rel/v0.2.0",
        component_versions={"indexer": "0.2.0", "ripgrep": "14.1.0", "uv": "0.5.0"},
        lsp_packages=["pyright@1.1.400", "typescript-language-server@4.3.3"],
    )
    assert m["manifestVersion"] == 1 and m["releaseTag"] == "v0.2.0"
    ix = m["components"]["indexer"]
    assert ix["urls"]["darwin-arm64"] == "https://gh/rel/v0.2.0/ai-editor-indexer-darwin-arm64"
    assert ix["urls"]["win32-x64"].endswith(".exe")
    assert ix["sha256"]["darwin-arm64"] == hashlib.sha256(b"bin").hexdigest()
    agentd = m["components"]["agentd"]
    assert agentd["version"] == "0.2.0"
    assert agentd["urls"]["any"].endswith("ai_editor_agentd-0.2.0-py3-none-any.whl")
    assert m["components"]["lsps"]["npmPackages"] == [
        "pyright@1.1.400", "typescript-language-server@4.3.3"]


def test_missing_platform_artifact_raises(tmp_path: Path) -> None:
    _touch(tmp_path, "ai-editor-indexer-darwin-arm64")
    import pytest
    with pytest.raises(FileNotFoundError, match="rg-darwin-arm64"):
        build_manifest("v1", tmp_path, "u",
                       component_versions={"indexer": "1", "ripgrep": "1", "uv": "1"},
                       lsp_packages=[])
```

- [ ] **Step 2: Run → FAIL, implement, run → PASS**

Run: `cd services/agentd-py && pytest ../../scripts/release/test_make_manifest.py`
Implementation: ~90 lines — glob the conventions, `hashlib.sha256` each file,
assemble the dict, `argparse` main writing `--out`. Missing artifact for a
required platform → `FileNotFoundError` naming the file (release must fail loudly,
not ship a partial manifest). `lsps.version` = sha1 of the joined package list
(changes when pins change).

- [ ] **Step 3: Commit**

```bash
git add scripts/release
git commit -m "feat(release): manifest.json generator with sha256s over conventional artifact names"
```

---

### Task 17: Release workflow + exit smoke

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/release.yml
name: release
on:
  push:
    tags: ["v*"]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: npm install && npm run build && npm run test && npm run typecheck
      - run: |
          cd services/agentd-py
          python -m venv .venv && . .venv/bin/activate
          pip install -e .[dev] --no-build-isolation
          pytest
      - run: cd services/indexer-rs && cargo test

  indexer:
    needs: test
    strategy:
      matrix:
        include:
          - { os: macos-14, platform: darwin-arm64 }
          - { os: macos-13, platform: darwin-x64 }
          - { os: ubuntu-latest, platform: linux-x64 }
          - { os: windows-latest, platform: win32-x64 }
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - run: cd services/indexer-rs && cargo build --release
      - name: stage binary (posix)
        if: runner.os != 'Windows'
        run: |
          mkdir -p dist
          cp services/indexer-rs/target/release/ai-editor-indexer \
             dist/ai-editor-indexer-${{ matrix.platform }}
      - name: stage binary (windows)
        if: runner.os == 'Windows'
        run: |
          mkdir dist
          copy services\indexer-rs\target\release\ai-editor-indexer.exe dist\ai-editor-indexer-${{ matrix.platform }}.exe
      - uses: actions/upload-artifact@v4
        with: { name: "indexer-${{ matrix.platform }}", path: dist/ }

  fetch-tools:
    # uv + ripgrep: download official release binaries per platform, restage
    # under our naming convention. Pinned versions live in this job's env.
    needs: test
    runs-on: ubuntu-latest
    env: { UV_VERSION: "0.5.24", RG_VERSION: "14.1.1" }
    steps:
      - uses: actions/checkout@v4
      - run: python3 scripts/release/fetch_tools.py --uv "$UV_VERSION" --rg "$RG_VERSION" --out dist/
      - uses: actions/upload-artifact@v4
        with: { name: tools, path: dist/ }

  package:
    needs: [indexer, fetch-tools]
    runs-on: ubuntu-latest
    permissions: { contents: write }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: actions/download-artifact@v4
        with: { path: dist-artifacts, merge-multiple: true }
      - run: |
          cd services/agentd-py
          pip install build && python -m build --wheel --outdir ../../dist-artifacts
      - run: |
          python scripts/release/make_manifest.py \
            --release-tag "${GITHUB_REF_NAME}" --dist dist-artifacts \
            --url-base "https://github.com/${GITHUB_REPOSITORY}/releases/download/${GITHUB_REF_NAME}" \
            --component-version indexer=${GITHUB_REF_NAME#v} \
            --component-version ripgrep=14.1.1 --component-version uv=0.5.24 \
            --lsp-packages "pyright@1.1.400,typescript-language-server@4.3.3" \
            --out dist-artifacts/manifest.json
      - run: |
          npm install && npm run build
          cp dist-artifacts/manifest.json apps/vscode-extension/resources/runtime-manifest.json
          cd apps/vscode-extension && npx vsce package --out ../../dist-artifacts/
      - name: attach to release
        uses: softprops/action-gh-release@v2
        with: { files: dist-artifacts/* }

  publish:
    needs: package
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { path: dist-artifacts, merge-multiple: true }
      - run: npx vsce publish --packagePath dist-artifacts/*.vsix -p ${{ secrets.VSCE_PAT }}
```

Also create `scripts/release/fetch_tools.py`: downloads the pinned uv + ripgrep
official release archives per platform, extracts the single binary, restages as
`uv-<platform>[.exe]` / `rg-<platform>[.exe]` into `--out`. Same testing approach
as `make_manifest.py` (pure `stage(archive_bytes, kind, platform)` helper
unit-tested with small fixture archives built in the test via `tarfile`/`zipfile`;
network only in `main()`).

- [ ] **Step 2: Validate the workflow file**

Run: `python3 -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/release.yml').read_text()); print('yaml ok')"`
and `actionlint .github/workflows/release.yml` if `actionlint` is installed (skip otherwise — note it in the commit body).
The real proof is the first `v0.*` tag on the public repo; expect one or two
iterate-on-CI commits — that's normal, keep them `ci(release): fix …`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml scripts/release
git commit -m "ci(release): tag-triggered pipeline — per-OS binaries, wheel, VSIX, manifest, publish"
```

- [ ] **Step 4: Exit smoke (the roadmap's exit criterion — run before calling P4 done)**

On a machine/profile with **no `~/.ai-editor`** and a fresh VS Code profile
(`code --profile p4-smoke`):
1. Install the VSIX (from a local `vsce package` or the tagged release).
2. Open an empty folder → wizard auto-opens → Install completes (LSP row may be
   `skipped` if no node — verify the consequence text shows).
3. Pick a provider + key → backend starts → validate passes → chat opens → one
   real chat turn answers.
4. Settings panel: switch model → next turn uses it (**no restart**; verify via
   the panel's provider report). Add the vendored web-search MCP server via the
   Add form → status dot goes green **without restart**. Toggle it off → gone from
   the next turn's tools. Change shell policy → restart banner → Restart → applies.
5. Kill the extension host and reopen the folder: the lockfile makes the second
   activation **reuse** the still-running backend (status bar shows the same port).

---

## Verification (whole plan)

- `cd services/agentd-py && pytest && ruff check . && mypy agentd` — green.
- `npm run build && npm run test && npm run typecheck` (repo root) — green.
- `cd services/indexer-rs && cargo test` — untouched, still green.
- Task 17 step 4 exit smoke — performed and recorded (screenshots/notes in the PR).
- Update `CLAUDE.md` (new backend routes, `AI_EDITOR_PORT`/lockfile, `AI_EDITOR_SKILLS_DISABLED`, managed-runtime overview + the `aiEditor.managedRuntime.enabled` kill-switch) as the final commit: `docs(claude): P4 managed runtime + settings surfaces`.

