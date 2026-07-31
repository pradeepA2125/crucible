# Generic OpenAI-compatible Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `openai_compatible` backend with a user-configured base URL, model, and optional API key, so any OpenAI-compatible `/chat/completions` endpoint (NVIDIA NIM, vLLM, LM Studio, Together, DeepInfra) works without a new transport per vendor.

**Architecture:** `openrouter_transport.py` already *is* a generic OpenAI chat-completions client. Task 1 extracts its vendor-neutral logic into `OpenAICompatibleTransport`; OpenRouter becomes a thin subclass overriding three hooks. Tasks 2-4 add the sticky JSON-mode downgrade, the new backend, and a capability-reporting validate probe. Task 5 wires the UI.

**Tech Stack:** Python 3.13 · `openai` AsyncOpenAI SDK · pytest/pytest-asyncio · TypeScript · React · vitest

**Spec:** `docs/superpowers/specs/2026-07-31-openai-compatible-provider-design.md`

## Global Constraints

- **The 21 existing tests in `services/agentd-py/tests/test_openrouter_transport.py` MUST pass unchanged after every task.** They are the regression net for the extraction. Verified during planning: each uses a fresh transport and makes a single `generate_json` call, so the Task 2 sticky downgrade does not disturb any of them. If one fails, the extraction is wrong — do not edit the test to make it pass.
- Backend id string is exactly `openai_compatible` everywhere (Python, TypeScript, tests).
- Env var prefix is exactly `CRUCIBLE_OPENAI_COMPAT_`.
- Never log or persist the API key or the credentials dict.
- Run pytest **without** `-q` (`pyproject.toml` already sets it; a second `-q` suppresses the summary). Never pipe pytest through `tail` — it masks the exit code.
- Python: `cd services/agentd-py && source .venv/bin/activate` before running tests.
- Verified during planning against the live NVIDIA NIM endpoint, so do not re-litigate these: `max_completion_tokens` is accepted (no rename needed); strict `response_format: json_schema` works including `oneOf` discriminated unions; `nvext.guided_json` is **not** needed.

---

### Task 1: Extract `OpenAICompatibleTransport` base class

Pure refactor. No behavior change, no new features. Move the vendor-neutral code out of `openrouter_transport.py` and make OpenRouter a subclass.

**Files:**
- Create: `services/agentd-py/agentd/providers/openai_compatible_transport.py`
- Modify: `services/agentd-py/agentd/providers/openrouter_transport.py`
- Test: `services/agentd-py/tests/test_openai_compatible_transport.py`

**Interfaces:**
- Produces: `OpenAICompatibleTransport` with `__init__(*, api_key=None, base_url, vendor="openai_compatible", label="OpenAI-compatible", max_tokens=4096, json_max_tokens=16384, timeout_sec=120.0, max_retries=4, supports_oneof=False, completions_client=None)`; methods `generate_json`, `generate_text`, `aclose`; overridable hooks `_default_headers() -> dict[str,str] | None`, `_build_extra_body(model, is_reasoning) -> dict[str,Any]`, `async _reasoning_config(model) -> tuple[bool, float]`.
- Produces: `OpenRouterJsonTransport(OpenAICompatibleTransport)` — same public constructor signature as today.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_openai_compatible_transport.py`:

```python
import json

import pytest

from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport
from agentd.providers.openrouter_transport import OpenRouterJsonTransport


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Records every create() call and replays a scripted list of contents."""

    def __init__(self, contents: list[object]) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        item = self._contents.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def _transport(contents: list[object], **kw) -> tuple[OpenAICompatibleTransport, _FakeCompletions]:
    fake = _FakeCompletions(contents)
    kw.setdefault("base_url", "https://example.test/v1")
    return OpenAICompatibleTransport(completions_client=fake, **kw), fake


def test_openrouter_is_a_subclass() -> None:
    """The extraction's contract: OpenRouter reuses the base, it does not duplicate it."""
    assert issubclass(OpenRouterJsonTransport, OpenAICompatibleTransport)


@pytest.mark.asyncio
async def test_generate_json_returns_parsed_object() -> None:
    transport, fake = _transport([json.dumps({"ok": True})])
    result = await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="sys", user_payload={"k": "v"},
    )
    assert result == {"ok": True}
    assert len(fake.calls) == 1
    assert fake.calls[0]["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_base_sends_no_openrouter_specific_fields() -> None:
    """The base must be vendor-neutral: no provider.require_parameters, no site headers."""
    transport, fake = _transport([json.dumps({"ok": True})])
    await transport.generate_json(
        model="some/model", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert "provider" not in fake.calls[0].get("extra_body", {})


@pytest.mark.asyncio
async def test_generate_text_uses_max_tokens_not_json_max_tokens() -> None:
    transport, fake = _transport(["hello"], max_tokens=77, json_max_tokens=4242)
    await transport.generate_text(model="m", system_instructions="", user_payload={})
    assert fake.calls[0]["max_completion_tokens"] == 77


@pytest.mark.asyncio
async def test_generate_json_uses_json_max_tokens() -> None:
    transport, fake = _transport([json.dumps({"ok": True})], max_tokens=77, json_max_tokens=4242)
    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    assert fake.calls[0]["max_completion_tokens"] == 4242
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.providers.openai_compatible_transport'`

- [ ] **Step 3: Create the base module**

Create `services/agentd-py/agentd/providers/openai_compatible_transport.py`. Move these **verbatim** from `openrouter_transport.py`, changing only what is called out below:

Move as-is: `_RETRYABLE_STATUS_CODES`, `_is_retryable`, `_classify_retry_reason`, `_is_reasoning_model`, `_strip_json_code_fences`.

Move into the class: `generate_json`, `_get_completion_text`, `_generate_json_once`, `generate_text`, `_stream_with_thinking`, `_call_with_retry`, `_extract_text`, `_parse_output_object`, `aclose`.

Three mechanical changes while moving:

1. **Vendor strings.** Every hardcoded `"OpenRouter"` in an error message or log becomes `self._label`; `provider_debug_root("openrouter")` becomes `provider_debug_root(self._vendor)`. (Checked during planning: no existing test asserts these strings.)
2. **The three hooks** replace inline OpenRouter logic.
3. **`supports_oneof_grammar` is an instance attribute, not a class attribute** — see the note after the code.

```python
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from agentd.providers.contracts import ModelJsonTransport, narrow_schema_for_type
from agentd.runtime.artifacts import provider_debug_root

logger = logging.getLogger(__name__)


class OpenAICompatibleTransport(ModelJsonTransport):
    """Generic OpenAI `/chat/completions` client against a configurable base URL.

    Vendor-neutral: subclasses add their own headers, extra_body, and reasoning
    detection through the three hooks below. Used directly by the
    `openai_compatible` backend and subclassed by OpenRouter.
    """

    supports_anyof_grammar: bool = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str,
        vendor: str = "openai_compatible",
        label: str = "OpenAI-compatible",
        max_tokens: int = 4096,
        json_max_tokens: int = 16384,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        supports_oneof: bool = False,
        default_headers: dict[str, str] | None = None,
        completions_client: Any | None = None,
    ) -> None:
        self._vendor = vendor
        self._label = label
        self._max_tokens = max_tokens
        self._json_max_tokens = json_max_tokens
        self._timeout_sec = timeout_sec
        self._max_retries = max(0, max_retries)
        # Instance attribute, NOT a class attribute: OpenRouter must keep the
        # contracts default (False) while openai_compatible opts in to True.
        self.supports_oneof_grammar = supports_oneof

        if completions_client is not None:
            self._completions: Any = completions_client
            return

        client_kwargs: dict[str, Any] = {
            "base_url": base_url,
            "timeout": timeout_sec,
        }
        # Local endpoints (vLLM, LM Studio) legitimately have no key. The OpenAI
        # SDK requires *some* api_key value, so send a placeholder rather than
        # refusing to construct — the header is meaningless to a keyless server.
        client_kwargs["api_key"] = api_key or "not-required"
        headers = default_headers or self._default_headers()
        if headers:
            client_kwargs["default_headers"] = headers

        client = AsyncOpenAI(**client_kwargs)
        self._completions = client.chat.completions

    # ---------------------------------------------------------------- hooks

    def _default_headers(self) -> dict[str, str] | None:
        """Vendor-specific headers. Base sends none."""
        return None

    def _build_extra_body(self, model: str, is_reasoning: bool) -> dict[str, Any]:
        """Vendor-specific request body extras. Base sends only reasoning."""
        extra_body: dict[str, Any] = {}
        if is_reasoning:
            extra_body["reasoning"] = {"enabled": True}
        return extra_body

    async def _reasoning_config(self, model: str) -> tuple[bool, float]:
        """(is_reasoning, temperature). Base uses the name-substring heuristic."""
        is_reasoning = _is_reasoning_model(model)
        return is_reasoning, (1.0 if is_reasoning else 0.0)

    async def aclose(self) -> None:
        """Subclasses with owned resources override this."""
        return None
```

Then move the remaining methods verbatim, replacing the inline `extra_body` construction in `_generate_json_once` with `extra_body = self._build_extra_body(model, is_reasoning)` and in `generate_text` with the same call.

- [ ] **Step 4: Make OpenRouter a subclass**

Rewrite `openrouter_transport.py` to keep `_ModelCapabilityCache` (vendor-specific — it fetches openrouter.ai's registry) and reduce the transport to hooks:

```python
class OpenRouterJsonTransport(OpenAICompatibleTransport):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str | None = None,
        site_name: str | None = None,
        max_tokens: int = 4096,
        json_max_tokens: int = 16384,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        require_parameters: bool = True,
        completions_client: Any | None = None,
        model_capabilities: _ModelCapabilityCache | None = None,
    ) -> None:
        self._require_parameters = require_parameters
        self._model_caps = model_capabilities

        if completions_client is None:
            resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
            if not resolved_api_key:
                msg = "OPENROUTER_API_KEY is required for OpenRouterJsonTransport"
                raise RuntimeError(msg)
        else:
            resolved_api_key = "unused"

        self._site_url = site_url or os.getenv("CRUCIBLE_OPENROUTER_SITE_URL")
        self._site_name = site_name or os.getenv("CRUCIBLE_OPENROUTER_SITE_NAME")

        super().__init__(
            api_key=resolved_api_key,
            base_url=base_url,
            vendor="openrouter",
            label="OpenRouter",
            max_tokens=max_tokens,
            json_max_tokens=json_max_tokens,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            completions_client=completions_client,
        )
        if completions_client is None and self._model_caps is None:
            self._model_caps = _ModelCapabilityCache()

    def _default_headers(self) -> dict[str, str] | None:
        extra_headers: dict[str, str] = {}
        if self._site_url:
            extra_headers["HTTP-Referer"] = self._site_url
        if self._site_name:
            extra_headers["X-Title"] = self._site_name
        return extra_headers or None

    def _build_extra_body(self, model: str, is_reasoning: bool) -> dict[str, Any]:
        extra_body = super()._build_extra_body(model, is_reasoning)
        if self._require_parameters:
            extra_body["provider"] = {"require_parameters": True}
        return extra_body

    async def _reasoning_config(self, model: str) -> tuple[bool, float]:
        if self._model_caps is not None:
            caps = await self._model_caps.get(model)
            if caps is not None:
                supported = caps.get("supported_parameters") or []
                is_reasoning = "reasoning" in supported
                default_temp = (caps.get("default_parameters") or {}).get("temperature")
                temperature = (
                    float(default_temp) if isinstance(default_temp, int | float)
                    else (1.0 if is_reasoning else 0.0)
                )
                return is_reasoning, temperature
        return await super()._reasoning_config(model)

    async def aclose(self) -> None:
        if self._model_caps is not None:
            await self._model_caps.aclose()
```

Note: `_site_url`/`_site_name` must be assigned **before** `super().__init__()`, because the base constructor calls `_default_headers()`.

- [ ] **Step 5: Run both test files**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py tests/test_openrouter_transport.py`
Expected: PASS — all new tests plus **all 21 OpenRouter tests unchanged**.

If any OpenRouter test fails, the extraction is wrong. Fix the extraction, not the test.

- [ ] **Step 6: Run the full backend suite for regressions**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest`
Expected: same pass count as before the change.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/providers/openai_compatible_transport.py \
        services/agentd-py/agentd/providers/openrouter_transport.py \
        services/agentd-py/tests/test_openai_compatible_transport.py
git commit -m "refactor(providers): extract OpenAICompatibleTransport from openrouter"
```

---

### Task 2: Sticky JSON-mode downgrade

**Files:**
- Modify: `services/agentd-py/agentd/providers/openai_compatible_transport.py`
- Test: `services/agentd-py/tests/test_openai_compatible_transport.py`

**Interfaces:**
- Consumes: `OpenAICompatibleTransport` from Task 1.
- Produces: instance attribute `_json_mode: str` (`"strict"` | `"json_object"`), flipped permanently on first strict failure.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_openai_compatible_transport.py`:

```python
@pytest.mark.asyncio
async def test_downgrade_sticks_after_strict_failure() -> None:
    """Call 1 tries strict, fails, falls back. Call 2 must skip strict entirely —
    otherwise every LLM call in the system pays a guaranteed failed request."""
    transport, fake = _transport([
        RuntimeError("response_format not supported"),   # call 1 strict -> fail
        json.dumps({"ok": 1}),                            # call 1 fallback -> ok
        json.dumps({"ok": 2}),                            # call 2 -> straight to fallback
    ])

    first = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )
    second = await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )

    assert first == {"ok": 1}
    assert second == {"ok": 2}
    # 2 requests for call 1 (strict + fallback), only 1 for call 2.
    assert len(fake.calls) == 3
    assert fake.calls[0]["response_format"]["type"] == "json_schema"
    assert fake.calls[1]["response_format"]["type"] == "json_object"
    assert fake.calls[2]["response_format"]["type"] == "json_object"


@pytest.mark.asyncio
async def test_downgrade_clears_grammar_support_flags() -> None:
    """An endpoint that cannot honor json_schema must not be sent a tight oneOf schema."""
    transport, _ = _transport([
        RuntimeError("response_format not supported"),
        json.dumps({"ok": 1}),
    ], supports_oneof=True)

    assert transport.supports_oneof_grammar is True
    assert transport.supports_anyof_grammar is True

    await transport.generate_json(
        model="m", schema_name="s", schema={"type": "object"},
        system_instructions="", user_payload={},
    )

    assert transport.supports_oneof_grammar is False
    assert transport.supports_anyof_grammar is False


@pytest.mark.asyncio
async def test_no_downgrade_when_strict_succeeds() -> None:
    transport, fake = _transport([json.dumps({"ok": 1}), json.dumps({"ok": 2})],
                                 supports_oneof=True)
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    await transport.generate_json(model="m", schema_name="s", schema={"type": "object"},
                                  system_instructions="", user_payload={})
    assert len(fake.calls) == 2
    assert all(c["response_format"]["type"] == "json_schema" for c in fake.calls)
    assert transport.supports_oneof_grammar is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py -k downgrade`
Expected: FAIL — call 2 still attempts strict, so `len(fake.calls) == 4`.

- [ ] **Step 3: Implement the sticky flag**

In `OpenAICompatibleTransport.__init__`, add:

```python
        self._json_mode = "strict"
```

In `_generate_json_once`, guard the strict attempt and record the downgrade. Replace the single `try/except` around the strict call with:

```python
        if self._json_mode == "strict":
            create_kwargs = {
                **base_kwargs,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": safe_schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            self._dump_debug_request(create_kwargs, safe_schema_name)
            try:
                output_text = await self._get_completion_text(
                    create_kwargs, on_thinking, on_retry
                )
                return self._parse_output_object(output_text, schema_name)
            except Exception as e:
                logger.warning(
                    "%s: strict json_schema failed for %s — downgrading to json_object "
                    "for the rest of this process: %s",
                    self._label, schema_name, e,
                )
                self._downgrade_json_mode()

        return await self._json_object_fallback(
            base_kwargs=base_kwargs,
            schema=schema,
            schema_name=schema_name,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
            on_retry=on_retry,
        )
```

Add the downgrade helper:

```python
    def _downgrade_json_mode(self) -> None:
        """Permanent for this process. A restart re-probes, so an endpoint that
        gains strict support recovers with no cache to invalidate."""
        self._json_mode = "json_object"
        # An endpoint that ignores response_format cannot be trusted with a tight
        # union schema either — stop offering one.
        self.supports_oneof_grammar = False
        self.supports_anyof_grammar = False
```

Extract the existing fallback body (the `except` block from Task 1's moved code) into `_json_object_fallback(...)` unchanged, so it is reachable both from the downgrade path and directly once `_json_mode == "json_object"`. Extract the debug dump into `_dump_debug_request(create_kwargs, safe_schema_name)`.

Note: `supports_anyof_grammar` is a class attribute; assigning `self.supports_anyof_grammar = False` creates an instance attribute that shadows it. That is intended and per-instance.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py tests/test_openrouter_transport.py`
Expected: PASS — including all 21 OpenRouter tests.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/openai_compatible_transport.py \
        services/agentd-py/tests/test_openai_compatible_transport.py
git commit -m "feat(providers): sticky json_object downgrade after strict schema failure"
```

---

### Task 3: `openai_compatible` backend — config, URL normalization, factory

**Files:**
- Modify: `services/agentd-py/agentd/providers/openai_compatible_transport.py`
- Modify: `services/agentd-py/agentd/providers/factory.py`
- Test: `services/agentd-py/tests/test_openai_compatible_transport.py`
- Test: `services/agentd-py/tests/test_provider_factory.py`

**Interfaces:**
- Consumes: `OpenAICompatibleTransport` from Tasks 1-2.
- Produces: `normalize_base_url(raw: str | None) -> str | None`; `build_transport("openai_compatible")` returning a configured transport; `default_model("openai_compatible")` raising `ValueError`.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_openai_compatible_transport.py`:

```python
from agentd.providers.openai_compatible_transport import normalize_base_url


def test_normalize_base_url_strips_trailing_slash() -> None:
    assert normalize_base_url("https://x.test/v1/") == "https://x.test/v1"


def test_normalize_base_url_strips_pasted_chat_completions_suffix() -> None:
    """Vendors document the full endpoint URL; pasting it is the predictable mistake."""
    assert normalize_base_url("https://x.test/v1/chat/completions") == "https://x.test/v1"


def test_normalize_base_url_passes_through_a_plain_base() -> None:
    assert normalize_base_url("https://integrate.api.nvidia.com/v1") == \
        "https://integrate.api.nvidia.com/v1"


def test_normalize_base_url_handles_none_and_empty() -> None:
    assert normalize_base_url(None) is None
    assert normalize_base_url("   ") is None
```

Append to `services/agentd-py/tests/test_provider_factory.py`:

```python
def test_openai_compatible_requires_base_url(monkeypatch) -> None:
    monkeypatch.delenv("CRUCIBLE_OPENAI_COMPAT_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="CRUCIBLE_OPENAI_COMPAT_BASE_URL"):
        build_transport("openai_compatible")


def test_openai_compatible_has_no_default_model() -> None:
    """A guessed default would fail confusingly at the endpoint instead of clearly here."""
    with pytest.raises(ValueError, match="CRUCIBLE_OPENAI_COMPAT_MODEL"):
        default_model("openai_compatible")


def test_openai_compatible_builds_with_base_url_and_no_key(monkeypatch) -> None:
    """Local vLLM / LM Studio have no API key — construction must still succeed."""
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.delenv("CRUCIBLE_OPENAI_COMPAT_API_KEY", raising=False)
    transport = build_transport("openai_compatible")
    assert transport.supports_oneof_grammar is True


def test_openai_compatible_credentials_override_env(monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_BASE_URL", "http://from-env/v1")
    transport = build_transport(
        "openai_compatible",
        credentials={"CRUCIBLE_OPENAI_COMPAT_BASE_URL": "http://from-request/v1"},
    )
    assert transport is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py tests/test_provider_factory.py`
Expected: FAIL — `ImportError: cannot import name 'normalize_base_url'` and `ValueError: Unsupported backend: openai_compatible`.

- [ ] **Step 3: Add `normalize_base_url`**

In `openai_compatible_transport.py`, module level:

```python
def normalize_base_url(raw: str | None) -> str | None:
    """Accept what a user actually pastes. Vendors document the full
    `/chat/completions` URL, so strip it back to the base the SDK expects."""
    if raw is None:
        return None
    trimmed = raw.strip().rstrip("/")
    if not trimmed:
        return None
    suffix = "/chat/completions"
    if trimmed.endswith(suffix):
        trimmed = trimmed[: -len(suffix)]
    return trimmed or None
```

- [ ] **Step 4: Wire the factory**

In `factory.py`, add to the existing tables:

```python
MODEL_ENV_VAR["openai_compatible"] = "CRUCIBLE_OPENAI_COMPAT_MODEL"
PROVIDER_KEY_ENV["openai_compatible"] = "CRUCIBLE_OPENAI_COMPAT_API_KEY"
```

Add these entries inline in the existing dict literals (do not append at module level).

Deliberately add **no** `_DEFAULT_MODEL` entry, and make the error name the env var. Change `default_model` to:

```python
def default_model(backend: str) -> str:
    if backend == "openai_compatible":
        raise ValueError(
            "openai_compatible has no default model — set CRUCIBLE_OPENAI_COMPAT_MODEL "
            "(or pass an explicit model)"
        )
    try:
        return _DEFAULT_MODEL[backend]
    except KeyError:
        raise ValueError(f"Unsupported backend: {backend}") from None
```

Add the branch in `build_transport`, before the final `raise`:

```python
    if backend == "openai_compatible":
        from agentd.providers.openai_compatible_transport import (
            OpenAICompatibleTransport,
            normalize_base_url,
        )

        base_url = normalize_base_url(env.get("CRUCIBLE_OPENAI_COMPAT_BASE_URL"))
        if not base_url:
            msg = (
                "CRUCIBLE_OPENAI_COMPAT_BASE_URL is required for the OpenAI-compatible "
                "provider (e.g. https://integrate.api.nvidia.com/v1)"
            )
            raise RuntimeError(msg)
        return OpenAICompatibleTransport(
            api_key=env.get("CRUCIBLE_OPENAI_COMPAT_API_KEY"),
            base_url=base_url,
            max_tokens=_int_env(env, "CRUCIBLE_OPENAI_COMPAT_MAX_TOKENS", 4096),
            json_max_tokens=_int_env(env, "CRUCIBLE_OPENAI_COMPAT_JSON_MAX_TOKENS", 16384),
            timeout_sec=_float_env(env, "CRUCIBLE_OPENAI_COMPAT_TIMEOUT_SEC", 120.0),
            max_retries=_int_env(env, "CRUCIBLE_OPENAI_COMPAT_MAX_RETRIES", 4),
            # Verified live against NVIDIA NIM: strict json_schema honors oneOf
            # discriminated unions. The sticky downgrade covers endpoints that don't.
            supports_oneof=True,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py tests/test_provider_factory.py`
Expected: PASS

- [ ] **Step 6: Verify against the real endpoint**

This is the only step that makes a network call. It proves the wiring works end to end, not just against fakes.

```bash
cd services/agentd-py && source .venv/bin/activate
export CRUCIBLE_OPENAI_COMPAT_BASE_URL=https://integrate.api.nvidia.com/v1
export CRUCIBLE_OPENAI_COMPAT_MODEL=nvidia/nemotron-3-ultra-550b-a55b
export CRUCIBLE_OPENAI_COMPAT_API_KEY=$(grep '^NVIDIA_API_KEY=' ../../.env | cut -d= -f2- | tr -d '"')
python -c "
import asyncio
from agentd.providers.factory import build_transport, resolve_model
async def main():
    t = build_transport('openai_compatible')
    m = resolve_model('openai_compatible')
    out = await t.generate_json(
        model=m, schema_name='FlatStep',
        schema={'type':'object','properties':{'thought':{'type':'string'}},
                'required':['thought'],'additionalProperties':False},
        system_instructions='Return JSON only.', user_payload={'goal':'add a route'})
    print('OK', out)
    print('json_mode:', t._json_mode)
asyncio.run(main())
"
```

Expected: prints `OK {...}` and `json_mode: strict`.

Do **not** use `z-ai/glm-5.2` for this check. It is capable (strict json_schema with oneOf verified) but queue-bound on the free tier — ~225s TTFB per call, and one probe died with an HTTP 504 after 302s. Nemotron 3 Ultra answers in ~0.5s.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/providers/openai_compatible_transport.py \
        services/agentd-py/agentd/providers/factory.py \
        services/agentd-py/tests/test_openai_compatible_transport.py \
        services/agentd-py/tests/test_provider_factory.py
git commit -m "feat(providers): add openai_compatible backend with configurable base URL"
```

---

### Task 4: Validate probe reports JSON-mode support

**Files:**
- Modify: `services/agentd-py/agentd/providers/validate.py`
- Modify: `services/agentd-py/agentd/api/routes.py:273-286`
- Test: `services/agentd-py/tests/test_provider_validate_route.py`

**Interfaces:**
- Consumes: `build_transport` from Task 3.
- Produces: `ProviderPingResult` dataclass `{model: str, json_mode: str | None, warning: str | None}`; `ping_provider(...) -> ProviderPingResult`; route returns `{ok, model, json_mode?, warning?}`.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_provider_validate_route.py`:

```python
@pytest.mark.asyncio
async def test_validate_reports_strict_support_for_openai_compatible(monkeypatch) -> None:
    """The probe turns 'will this endpoint work with Crucible?' into a visible fact."""
    from agentd.providers import validate as validate_mod

    class _FakeTransport:
        supports_oneof_grammar = True

        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            return {"ok": True}

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _FakeTransport())
    result = await validate_mod.ping_provider(
        "openai_compatible", "some/model", {"CRUCIBLE_OPENAI_COMPAT_BASE_URL": "http://x/v1"}
    )
    assert result.model == "some/model"
    assert result.json_mode == "strict"
    assert result.warning is None


@pytest.mark.asyncio
async def test_validate_warns_when_strict_schema_unsupported(monkeypatch) -> None:
    from agentd.providers import validate as validate_mod

    class _NoSchemaTransport:
        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            raise RuntimeError("response_format not supported")

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _NoSchemaTransport())
    result = await validate_mod.ping_provider(
        "openai_compatible", "some/model", {"CRUCIBLE_OPENAI_COMPAT_BASE_URL": "http://x/v1"}
    )
    assert result.json_mode == "json_object"
    assert result.warning is not None
    assert "lower reliability" in result.warning


@pytest.mark.asyncio
async def test_validate_skips_probe_for_known_providers(monkeypatch) -> None:
    """Known providers must not pay an extra request to re-confirm what we know."""
    from agentd.providers import validate as validate_mod

    calls = {"json": 0}

    class _T:
        async def generate_text(self, **kwargs):
            return "OK"

        async def generate_json(self, **kwargs):
            calls["json"] += 1
            return {}

    monkeypatch.setattr(validate_mod, "build_transport", lambda *a, **k: _T())
    result = await validate_mod.ping_provider("openai", "gpt-5", None)
    assert calls["json"] == 0
    assert result.json_mode is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_provider_validate_route.py`
Expected: FAIL — `AttributeError: 'str' object has no attribute 'model'` (ping_provider still returns a str).

- [ ] **Step 3: Implement the probe**

Rewrite `validate.py`'s `ping_provider` and add the result type:

```python
from dataclasses import dataclass

_PROBE_BACKENDS = frozenset({"openai_compatible"})

_PROBE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

_DEGRADED_WARNING = (
    "No strict JSON schema support; will use json_object + schema-in-prompt. "
    "Expect lower reliability."
)


@dataclass(frozen=True)
class ProviderPingResult:
    model: str
    json_mode: str | None = None
    warning: str | None = None


async def probe_json_mode(transport: object, model: str, timeout_sec: float = 30.0) -> str:
    """One tiny strict json_schema call. Returns "strict" or "json_object".

    Informational only — it does NOT configure the transport. The transport
    rediscovers independently via its sticky downgrade, so there is no cached
    capability to go stale.
    """
    try:
        await asyncio.wait_for(
            transport.generate_json(  # type: ignore[attr-defined]
                model=model,
                schema_name="CapabilityProbe",
                schema=_PROBE_SCHEMA,
                system_instructions="Return JSON only.",
                user_payload={"probe": True},
            ),
            timeout=timeout_sec,
        )
    except Exception:
        return "json_object"
    return "strict"


async def ping_provider(
    backend: str, model: str | None = None, credentials: dict[str, str] | None = None
) -> ProviderPingResult:
    try:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
    except Exception as exc:
        raise ProviderValidationError(str(exc)) from exc
    await ping_transport(transport, resolved)

    if backend not in _PROBE_BACKENDS:
        return ProviderPingResult(model=resolved)

    json_mode = await probe_json_mode(transport, resolved)
    warning = _DEGRADED_WARNING if json_mode == "json_object" else None
    return ProviderPingResult(model=resolved, json_mode=json_mode, warning=warning)
```

Update the route in `routes.py:282-286`:

```python
        try:
            result = await ping_provider(body.backend, body.model, body.credentials)
        except ProviderValidationError as exc:
            return {"ok": False, "error": str(exc)}
        payload: dict[str, object] = {"ok": True, "model": result.model}
        if result.json_mode is not None:
            payload["json_mode"] = result.json_mode
        if result.warning is not None:
            payload["warning"] = result.warning
        return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_provider_validate_route.py tests/test_provider_hotswap.py`
Expected: PASS. `test_provider_hotswap.py` must still pass — `runtime.py` calls `ping_transport`, not `ping_provider`, so it is unaffected.

- [ ] **Step 5: Run the full backend suite**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest`
Expected: all pass. Any other caller of `ping_provider` expecting a `str` surfaces here.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/providers/validate.py \
        services/agentd-py/agentd/api/routes.py \
        services/agentd-py/tests/test_provider_validate_route.py
git commit -m "feat(providers): validate probes and reports strict-json-schema support"
```

---

### Task 5: Frontend wiring

**Files:**
- Modify: `apps/vscode-extension/src/setup-data.ts`
- Modify: `apps/vscode-extension/src/runtime/backend-process.ts:38-44`
- Modify: `apps/vscode-extension/src/runtime/vscode-runtime.ts:29`
- Modify: `apps/vscode-extension/webview-ui/src/setup/SetupApp.tsx:266`
- Test: `apps/vscode-extension/test/setup-data.test.ts`

**Interfaces:**
- Consumes: backend id `openai_compatible` and env var names from Task 3; `json_mode`/`warning` validate fields from Task 4.
- Produces: `ProviderInfo.keyOptional?: boolean`.

Note from planning: `ProviderSection.tsx` (settings) needs **no change** — it already renders `provider.extraFields` with placeholders, already omits an empty API key on save, and already gates only on `!model`. Only the setup wizard gates on the key.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/test/setup-data.test.ts`:

```ts
import { PROVIDERS } from "../src/setup-data";

describe("openai_compatible provider entry", () => {
  const provider = PROVIDERS.find((p) => p.id === "openai_compatible");

  it("is registered", () => {
    expect(provider).toBeDefined();
  });

  it("has an optional key so keyless local endpoints can be saved", () => {
    // vLLM / LM Studio have no API key; the wizard must not block save.
    expect(provider!.local).toBe(false);
    expect(provider!.keyOptional).toBe(true);
  });

  it("requires a base URL field with a usable placeholder", () => {
    const field = provider!.extraFields?.find(
      (f) => f.envVar === "CRUCIBLE_OPENAI_COMPAT_BASE_URL",
    );
    expect(field).toBeDefined();
    expect(field!.optional).toBeFalsy();
    expect(field!.placeholder).toContain("http");
  });

  it("has no default model so the user must supply one", () => {
    expect(provider!.defaultModel).toBe("");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w crucible-vscode-extension test -- setup-data`
Expected: FAIL — provider is `undefined`.

- [ ] **Step 3: Add the provider entry and the `keyOptional` flag**

In `apps/vscode-extension/src/setup-data.ts`, add to the `ProviderInfo` interface:

```ts
  /** Cloud provider whose API key is genuinely optional (self-hosted endpoints). */
  keyOptional?: boolean;
```

Append to `PROVIDERS`:

```ts
  {
    id: "openai_compatible",
    label: "OpenAI-compatible",
    local: false,
    keyOptional: true,
    keyEnvVar: "CRUCIBLE_OPENAI_COMPAT_API_KEY",
    defaultModel: "",
    extraFields: [
      {
        envVar: "CRUCIBLE_OPENAI_COMPAT_BASE_URL",
        label: "Base URL",
        placeholder: "https://integrate.api.nvidia.com/v1",
      },
    ],
  },
```

- [ ] **Step 4: Mirror the backend tables**

In `apps/vscode-extension/src/runtime/backend-process.ts`, add to `MODEL_ENV_VAR`:

```ts
  openai_compatible: "CRUCIBLE_OPENAI_COMPAT_MODEL",
```

In `apps/vscode-extension/src/runtime/vscode-runtime.ts`, add to `PROVIDER_KEY_ENV`:

```ts
  openai_compatible: "CRUCIBLE_OPENAI_COMPAT_API_KEY",
```

- [ ] **Step 5: Fix the setup wizard's save gate**

In `apps/vscode-extension/webview-ui/src/setup/SetupApp.tsx:266`, change:

```tsx
disabled={busy || !model || (!provider.local && !apiKey) || !extraFieldsFilled}
```

to:

```tsx
disabled={busy || !model || (!provider.local && !provider.keyOptional && !apiKey) || !extraFieldsFilled}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `npm run -w crucible-vscode-extension test -- setup-data`
Expected: PASS

- [ ] **Step 7: Render the validate warning**

The validate result may now carry `jsonMode`/`warning`. Add them to the result type in `apps/vscode-extension/src/setup-data.ts`:

```ts
  | { type: "setup/validateResult"; ok: boolean; model?: string; error?: string; jsonMode?: string; warning?: string }
```

and in `SetupDeps.validate`'s return type:

```ts
  ): Promise<{ ok: boolean; model?: string; error?: string; jsonMode?: string; warning?: string }>;
```

In `SetupApp.tsx`, under the existing validate status line, render the warning when present:

```tsx
{validateWarning && (
  <p className="text-xs text-warning">⚠ {validateWarning}</p>
)}
```

storing `warning` into `validateWarning` state in the `setup/validateResult` message handler alongside the existing fields.

- [ ] **Step 8: Typecheck and run the full frontend suite**

Run: `npm run -w @crucible/editor-client build && npm run -w crucible-vscode-extension typecheck && npm run test`
Expected: PASS. The editor-client build must run first — `vscode-extension` types off its compiled `dist/index.d.ts`, not source.

- [ ] **Step 9: Commit**

```bash
git add apps/vscode-extension/src/setup-data.ts \
        apps/vscode-extension/src/runtime/backend-process.ts \
        apps/vscode-extension/src/runtime/vscode-runtime.ts \
        apps/vscode-extension/webview-ui/src/setup/SetupApp.tsx \
        apps/vscode-extension/test/setup-data.test.ts
git commit -m "feat(vscode): OpenAI-compatible provider entry with optional API key"
```

---

### Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the backend and its env vars**

In `CLAUDE.md`, under "Python backend env vars" → "Core", add:

```markdown
- `CRUCIBLE_OPENAI_COMPAT_BASE_URL` / `_MODEL` / `_API_KEY` — the generic
  `openai_compatible` backend: any OpenAI `/chat/completions` endpoint (NVIDIA NIM,
  vLLM, LM Studio, Together, DeepInfra). Base URL and model are required (no defaults);
  the key is optional for self-hosted endpoints. Also `_MAX_TOKENS` (4096),
  `_JSON_MAX_TOKENS` (16384), `_TIMEOUT_SEC` (120), `_MAX_RETRIES` (4).
```

In the `providers/` bullet list under "Python backend (`agentd/`)", add:

```markdown
- `providers/openai_compatible_transport.py` — `OpenAICompatibleTransport`: the generic
  OpenAI chat-completions client (configurable base URL). Subclassed by
  `OpenRouterJsonTransport`, which adds site headers, `provider.require_parameters`,
  and the openrouter.ai model-capability registry via three hooks
  (`_default_headers`, `_build_extra_body`, `_reasoning_config`). Carries the **sticky
  JSON-mode downgrade**: strict `json_schema` is tried once, and on failure the
  instance falls back to `json_object` + schema-in-prompt for the rest of the process
  and clears `supports_{one,any}of_grammar`. Restart re-probes.
```

Add a gotcha near the other provider notes:

```markdown
- **GOTCHA — `openai_transport.py` cannot be pointed at a custom endpoint.** It uses the
  **Responses API** (`client.responses`), which OpenAI-compatible servers do not serve
  (NVIDIA NIM returns 404 for `/v1/responses`, 401 for `/v1/chat/completions`). Use the
  `openai_compatible` backend for any non-OpenAI endpoint.
- **NVIDIA NIM (`https://integrate.api.nvidia.com/v1`)**: verified 2026-07-31 to support
  strict `response_format: json_schema` including `oneOf` discriminated unions, and to
  accept `max_completion_tokens`. `nvext.guided_json` is not needed. Free-tier **capacity
  is per-model, and is the real constraint — not capability**:
  `nvidia/nemotron-3-ultra-550b-a55b` answers in ~0.5s, while `z-ai/glm-5.2` passed the
  same `oneOf` schema probe but only after a **225s queue** (0.4s of that was generation),
  and a second probe died with an HTTP 504 after 302s. GLM-5.2 is capable but not usable
  interactively on the free tier: one Crucible turn makes many calls. Use a paid endpoint
  (Z.ai's own API is OpenAI-compatible — a base-URL change) if you want that model.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document the openai_compatible provider backend"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 Architecture (credentials overlay) | 3 |
| §2 Transport extraction + 3 hooks | 1 |
| §3 Configuration + URL normalization + optional key | 3 |
| §4 Sticky downgrade + grammar-flag clearing | 2 |
| §5 Validate probe (informational only) | 4 |
| §6 Frontend + `keyOptional` | 5 |
| §7 Error handling | 3 (construction errors), 2 (downgrade), 4 (probe) |
| §8 Testing | 1-5, each task's test steps |
| §9 Out of scope | not implemented, by design |

**Deviation from the spec, recorded deliberately:** the spec allowed "one permitted exception" where an OpenRouter test asserting per-call strict retry might need updating. Planning verified no such test exists — all 21 use a fresh transport and a single `generate_json` call. The Global Constraints therefore require **all 21 unchanged**, with no exception.

**Addition beyond the spec:** the base constructor sends `api_key="not-required"` when no key is given, because the OpenAI SDK requires a non-empty `api_key`. The spec said only "sends Authorization only when a key is present"; refusing to construct would break the keyless local case the spec explicitly supports.
