# Unified Retry-Status Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface every retry (transport-level network/429/5xx backoff, and the controller's malformed-response corrective retry) as a distinct, self-overwriting "blinking" indicator in the chat transcript, instead of leaking "retrying…" text into the permanent thinking log.

**Architecture:** A new `on_retry(attempt, max_attempts, reason, message)` callback threads from the two provider transports (`ollama_transport.py`, `openrouter_transport.py`) up through `create_controller_step` (the only `ReasoningEngine` method the reactive chat controller calls) to `controller_loop.py`, which broadcasts a new `retry_status` SSE event on the chat channel. The VS Code extension relays it to the webview as an ephemeral, never-persisted `retryStatus` state slice, rendered as a pulsing bubble that takes rendering precedence over the thinking/streaming row until it clears.

**Tech Stack:** Python 3 / pytest-asyncio (backend), TypeScript / vitest / React (extension + webview).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-14-retry-status-indicator-design.md` — every task below implements a specific section of it.
- Controller-chat only (`CRUCIBLE_CHAT_CONTROLLER=1` path). Do not touch the legacy `ChatAgent` path or the task/planning pipeline (`create_plan`, `create_tool_step`, `create_planning_step` stay untouched).
- `retry_status` payload keys are **snake_case** on the wire (`max_attempts`, not `maxAttempts`) — matches the existing convention for chat SSE events (no rename step).
- Finding #18 (turn-failed message persistence) is explicitly out of scope — do not touch `_write_turn_message` or the `except Exception` catch-all shape in `chat/controller.py`.
- No new CSS design tokens — reuse the existing `pulse` keyframe (`webview-ui/src/index.css:99-102`) and `var(--color-accent)`, matching the existing `thinkingStatus` dot exactly.
- Never run tests with a trailing `-q` flag in `services/agentd-py` (already set via `addopts` in `pyproject.toml` — see CLAUDE.md).

---

### Task 1: Ollama transport — `on_retry` callback + status-code capture

**Files:**
- Modify: `services/agentd-py/agentd/providers/ollama_transport.py`
- Test: `services/agentd-py/tests/test_ollama_transport.py`

**Interfaces:**
- Produces: `OllamaJsonTransport.generate_json(..., on_retry: Callable[[int, int, str, str], None] | None = None)` — called as `on_retry(attempt, max_attempts, reason, message)` on each retry, where `reason` is one of `"rate_limited"`, `"server_error"`, `"network_error"`.
- Produces: `_RetryableHttpStatus.status_code: int` attribute.

- [ ] **Step 1: Write the failing tests**

Replace the existing `test_ollama_transport_broadcasts_retry_status_via_on_thinking` (lines 538-559) — it currently asserts retry text goes through `on_thinking`, which is the exact behavior this task removes — with two new tests that assert `on_thinking` is NOT used for retry text and `on_retry` IS:

```python
@pytest.mark.asyncio
async def test_ollama_transport_calls_on_retry_not_on_thinking() -> None:
    """Retry status must go through on_retry, not on_thinking — on_thinking is
    reserved for genuine model reasoning text, never retry noise."""
    flaky = [
        _FakeResponse(status_code=503, payload="busy"),
        _ok_response(json.dumps({"ok": True})),
    ]
    client = _FakeAsyncClient(flaky)
    transport = OllamaJsonTransport(http_client=client, max_retries=2)
    thinking_chunks: list[str] = []
    retries: list[tuple[int, int, str, str]] = []

    result = await transport.generate_json(
        model="m", schema_name="x", schema={"type": "object"},
        system_instructions="s", user_payload={},
        on_thinking=thinking_chunks.append,
        on_retry=lambda attempt, max_attempts, reason, message: retries.append(
            (attempt, max_attempts, reason, message)
        ),
    )

    assert result == {"ok": True}
    assert not any("retrying" in c for c in thinking_chunks), thinking_chunks
    assert len(retries) == 1, retries
    attempt, max_attempts, reason, message = retries[0]
    assert (attempt, max_attempts, reason) == (1, 2, "server_error")
    assert "attempt 1/2" in message


@pytest.mark.asyncio
async def test_ollama_transport_classifies_429_as_rate_limited() -> None:
    flaky = [
        _FakeResponse(status_code=429, payload="rate limited"),
        _ok_response(json.dumps({"ok": True})),
    ]
    client = _FakeAsyncClient(flaky)
    transport = OllamaJsonTransport(http_client=client, max_retries=2)
    retries: list[tuple[int, int, str, str]] = []

    await transport.generate_json(
        model="m", schema_name="x", schema={"type": "object"},
        system_instructions="s", user_payload={},
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert retries[0][2] == "rate_limited"


@pytest.mark.asyncio
async def test_ollama_transport_classifies_connect_error_as_network_error() -> None:
    flaky: list[Any] = [
        httpx.ConnectError("daemon down"),
        _ok_response(json.dumps({"ok": True})),
    ]
    client = _FakeAsyncClient(flaky)
    transport = OllamaJsonTransport(http_client=client, max_retries=2)
    retries: list[tuple[int, int, str, str]] = []

    await transport.generate_json(
        model="m", schema_name="x", schema={"type": "object"},
        system_instructions="s", user_payload={},
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert retries[0][2] == "network_error"


@pytest.mark.asyncio
async def test_ollama_transport_on_retry_none_does_not_raise() -> None:
    """Existing callers that don't pass on_retry (e.g. generate_text call sites
    with no retry-status consumer) must keep working unchanged."""
    flaky = [
        _FakeResponse(status_code=503, payload="busy"),
        _ok_response(json.dumps({"ok": True})),
    ]
    client = _FakeAsyncClient(flaky)
    transport = OllamaJsonTransport(http_client=client, max_retries=2)

    result = await transport.generate_json(
        model="m", schema_name="x", schema={"type": "object"},
        system_instructions="s", user_payload={},
    )
    assert result == {"ok": True}
```

Delete the old `test_ollama_transport_broadcasts_retry_status_via_on_thinking` test (lines 538-559) entirely — it tests the behavior being replaced.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_ollama_transport.py -k "on_retry or rate_limited or network_error" -v`
Expected: FAIL — `TypeError: generate_json() got an unexpected keyword argument 'on_retry'`

- [ ] **Step 3: Implement**

In `ollama_transport.py`, update `_RetryableHttpStatus` (currently lines 40-46):

```python
class _RetryableHttpStatus(Exception):
    """Internal marker: an HTTP status Ollama treats as transiently retryable,
    raised from inside the streamed-response context manager and caught by
    _call_with_retry's loop (mirrors the pre-streaming code's plain status check,
    which can't be a simple if/continue anymore once the check lives inside a
    separate `async with self._client.stream(...)` coroutine)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
```

Update the raise site in `_stream_chat` (currently line 284):

```python
                raise _RetryableHttpStatus(
                    f"Ollama returned {response.status_code}: {text[:200]}",
                    response.status_code,
                )
```

Add a module-level classifier function, right after `_RetryableHttpStatus`:

```python
def _classify_retry_reason(exc: Exception | None) -> str:
    if isinstance(exc, _RetryableHttpStatus):
        return "rate_limited" if exc.status_code == 429 else "server_error"
    return "network_error"
```

Update `_call_with_retry` (currently lines 219-265) to accept and call `on_retry`:

```python
    async def _call_with_retry(
        self,
        body: dict[str, object],
        *,
        on_chunk: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, Any]:
        """POST /api/chat (streamed) with timeout + exponential backoff on transient
        errors. See _stream_chat for the line-parsing/merge; on_chunk is threaded
        through so callers (generate_json/generate_text) get live thinking deltas.
        on_retry (distinct from on_chunk) reports retry attempts as structured
        data — never injected into the thinking-chunk stream."""
        url = f"{self._host}/api/chat"
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "Ollama transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                if on_retry is not None:
                    reason = _classify_retry_reason(last_exc)
                    message = (
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                    on_retry(attempt, self._max_retries, reason, message)
                await asyncio.sleep(delay)

            try:
                return await asyncio.wait_for(
                    self._stream_chat(url, body, on_chunk),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                msg = f"Ollama request timed out after {self._timeout_sec}s (model={body.get('model')})"
                raise RuntimeError(msg) from exc
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                continue
            except _RetryableHttpStatus as exc:
                last_exc = exc
                continue
            except Exception:
                raise

        assert last_exc is not None
        raise RuntimeError(
            f"Ollama request failed after {self._max_retries} retries: {last_exc}"
        ) from last_exc
```

Update `generate_json` (currently lines 108-141) to accept and forward `on_retry`:

```python
    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, object]:
        contents = json.dumps(user_payload)
        body = self._build_body(
            model=model,
            system=system_instructions,
            user_content=contents,
            json_format=schema,
            num_predict=self._json_num_predict,  # cloud backends reject -1 (unlimited)
        )
        # on_chunk forwards each streamed `message.thinking` delta live, as it
        # arrives — this is what lets the UI show real progress during a call that
        # can take minutes, instead of a blank "Working…" wait (see _stream_chat).
        response = await self._call_with_retry(body, on_chunk=on_thinking, on_retry=on_retry)
        self._log_usage(model, schema_name, system_instructions, contents, response)
        output_text = self._extract_text(response)
        logger.warning("ollama raw output (%s): %s", schema_name, output_text[:600])

        # Strip <think> blocks that some models (e.g. Qwen3) may emit INLINE in
        # content rather than the structured message.thinking field streamed above
        # — a separate signal, so this can still fire in addition to on_chunk.
        thinking, output_text = _split_thinking(output_text)
        if thinking and on_thinking:
            on_thinking(thinking)

        return _parse_output_object(output_text, schema_name)
```

`generate_text` (lines 143-164) is unchanged — nothing in scope calls it with a retry-relevant need, and `_call_with_retry`'s new `on_retry` parameter defaults to `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_ollama_transport.py -v`
Expected: PASS (all tests, including the pre-existing ones — `test_ollama_transport_retries_on_503`, `test_ollama_transport_retries_on_connect_error` must still pass unchanged).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/ollama_transport.py services/agentd-py/tests/test_ollama_transport.py
git commit -m "feat(ollama-transport): add structured on_retry callback, drop retry text from on_thinking"
```

---

### Task 2: OpenRouter transport — `on_retry` across its three retry loops

**Files:**
- Modify: `services/agentd-py/agentd/providers/openrouter_transport.py`
- Test: `services/agentd-py/tests/test_openrouter_transport.py`

**Interfaces:**
- Produces: `OpenRouterJsonTransport.generate_json(..., on_retry: Any = None)` — same `(attempt, max_attempts, reason, message)` shape as Task 1.
- Consumes: nothing from Task 1 (independent transport).

- [ ] **Step 1: Write the failing tests**

Add to `test_openrouter_transport.py` (mirror whatever fixture/fake pattern the existing retry tests in that file use for a retryable exception — check the existing `_is_retryable`-triggering test first and reuse its fake):

```python
@pytest.mark.asyncio
async def test_openrouter_call_with_retry_calls_on_retry() -> None:
    """_call_with_retry (452-479) previously had no callback parameter at all —
    it retried silently. This is the one case in this file where on_retry is a
    genuinely new parameter, not a swap of an existing callback."""
    transport = _make_transport_with_flaky_completions(
        [_retryable_exception(500), _fake_completion("ok")]
    )
    retries: list[tuple[int, int, str, str]] = []

    result = await transport._call_with_retry(
        {"model": "m", "messages": []},
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert result is not None
    assert len(retries) == 1
    attempt, max_attempts, reason, message = retries[0]
    assert (attempt, reason) == (1, "server_error")


@pytest.mark.asyncio
async def test_openrouter_stream_with_thinking_calls_on_retry_not_on_thinking() -> None:
    transport = _make_transport_with_flaky_stream(
        [_retryable_exception(429), _fake_stream_chunks(["hello"])]
    )
    thinking_chunks: list[str] = []
    retries: list[tuple[int, int, str, str]] = []

    text = await transport._stream_with_thinking(
        {"model": "m", "messages": []},
        on_thinking=thinking_chunks.append,
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert text == "hello"
    assert not any("retrying" in c for c in thinking_chunks), thinking_chunks
    assert retries[0][2] == "rate_limited"


@pytest.mark.asyncio
async def test_openrouter_malformed_json_fallback_calls_on_retry() -> None:
    """The malformed-JSON fallback loop (325-358) is a transport-level retry on
    a JSON-parse failure — distinct from the controller-level corrective retry,
    but shares reason='malformed_response' per the spec's unified taxonomy."""
    transport = _make_transport_that_returns_malformed_json_once_then_valid()
    retries: list[tuple[int, int, str, str]] = []

    result = await transport.generate_json(
        model="m", schema_name="controller_step_response",
        schema={"type": "object"}, system_instructions="s", user_payload={},
        on_retry=lambda a, m, r, msg: retries.append((a, m, r, msg)),
    )

    assert result is not None
    assert any(r[2] == "malformed_response" for r in retries), retries
```

If no existing helper fakes a retryable exception / flaky completion stream in this test file, write minimal local fakes matching this file's existing style (check `_is_retryable`'s usage — it reads `exc.status_code`, so a fake exception just needs that attribute):

```python
class _FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _retryable_exception(status_code: int) -> Exception:
    return _FakeStatusError(status_code)
```

Adapt `_make_transport_with_flaky_completions`/`_make_transport_with_flaky_stream`/`_make_transport_that_returns_malformed_json_once_then_valid` to reuse whatever `_completions.create` fake object this test file already has (check the top of `test_openrouter_transport.py` for the existing fake `AsyncOpenAI`/`chat.completions` double before writing new ones — do not duplicate fixture machinery that already exists).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_openrouter_transport.py -k "on_retry" -v`
Expected: FAIL — `TypeError: _call_with_retry() got an unexpected keyword argument 'on_retry'`

- [ ] **Step 3: Implement**

Add the classifier near `_is_retryable` (line 76-78):

```python
def _classify_retry_reason(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "rate_limited"
    if isinstance(status_code, int):
        return "server_error"
    return "network_error"
```

Update `_call_with_retry` (currently lines 452-479) — it had NO callback before, so this adds the parameter, not a swap:

```python
    async def _call_with_retry(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_retry: Any = None,
    ) -> Any:
        """Call chat.completions.create with timeout and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "OpenRouter transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                if callable(on_retry):
                    reason = _classify_retry_reason(last_exc) if last_exc else "network_error"
                    message = (
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                    on_retry(attempt, self._max_retries, reason, message)
                await asyncio.sleep(delay)
            try:
                return await asyncio.wait_for(
                    self._completions.create(**create_kwargs),
                    timeout=self._timeout_sec,
                )
            except TimeoutError as exc:
                raise RuntimeError(
                    f"OpenRouter chat.completions timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc
```

Update `_stream_with_thinking` (currently lines 391-450) — swap the `on_thinking(...)` retry-text call for `on_retry(...)`:

```python
    async def _stream_with_thinking(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
        on_retry: Any = None,
    ) -> str:
        """Stream response forwarding reasoning chunks to on_thinking callback.

        OpenRouter surfaces reasoning in delta.reasoning (same field as Groq).
        """
        kwargs = {**create_kwargs, "stream": True}
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                logger.warning(
                    "OpenRouter transient error (attempt %d/%d), retrying in %.0fs",
                    attempt, self._max_retries, delay,
                )
                if callable(on_retry):
                    reason = _classify_retry_reason(last_exc) if last_exc else "network_error"
                    message = (
                        f"⏳ {last_exc.__class__.__name__ if last_exc else 'transient error'} "
                        f"— retrying in {delay:.0f}s (attempt {attempt}/{self._max_retries})…"
                    )
                    on_retry(attempt, self._max_retries, reason, message)
                await asyncio.sleep(delay)
            try:
                content_parts: list[str] = []
                stream = await asyncio.wait_for(
                    self._completions.create(**kwargs),
                    timeout=self._timeout_sec,
                )
                async for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    reasoning = getattr(delta, "reasoning", None)
                    if reasoning:
                        on_thinking(reasoning)
                    content = getattr(delta, "content", None) or ""
                    if content:
                        content_parts.append(content)
                return "".join(content_parts).strip()
            except TimeoutError as exc:
                raise RuntimeError(
                    f"OpenRouter streaming timed out after {self._timeout_sec}s"
                ) from exc
            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise

        assert last_exc is not None
        raise last_exc
```

Update `_get_completion_text` (currently lines 214-226) to thread `on_retry` through to whichever path it dispatches to:

```python
    async def _get_completion_text(
        self, create_kwargs: dict[str, Any], on_thinking: Any, on_retry: Any = None,
    ) -> str:
        """Route through the streaming path (forwarding reasoning deltas to
        on_thinking live, as they arrive) when a callback is given, else the plain
        non-streaming call. Previously on_thinking was accepted by generate_json
        but silently never used — every controller_step_response call (the one
        driving every turn of a live-driven session) rendered nothing until the
        whole call completed, identical to the gap fixed on the Ollama transport."""
        if callable(on_thinking):
            return await self._stream_with_thinking(
                create_kwargs, on_thinking=on_thinking, on_retry=on_retry
            )
        response = await self._call_with_retry(create_kwargs, on_retry=on_retry)
        return self._extract_text(response)
```

Update `_generate_json_once` (currently lines 228-358): add `on_retry: Any = None` to its signature, thread it to both `_get_completion_text` calls (line 291 and line 342), and swap the malformed-JSON fallback loop's `on_thinking(...)` call (lines 335-339) for `on_retry(...)` with `reason="malformed_response"`:

```python
    async def _generate_json_once(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Any = None,
        on_retry: Any = None,
    ) -> dict[str, object]:
        # ... unchanged body up through the `try:` block ...
        try:
            output_text = await self._get_completion_text(create_kwargs, on_thinking, on_retry)
            return self._parse_output_object(output_text, schema_name)
        except Exception as e:
            # ... unchanged fallback_kwargs construction ...
            last_parse_exc: Exception | None = None
            for attempt in range(self._max_retries + 1):
                if attempt > 0:
                    delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
                    logger.warning(
                        "OpenRouter malformed JSON for %s (attempt %d/%d), retrying in %.0fs",
                        schema_name, attempt, self._max_retries, delay,
                    )
                    if callable(on_retry):
                        on_retry(
                            attempt, self._max_retries, "malformed_response",
                            f"⏳ Malformed JSON response — retrying in {delay:.0f}s "
                            f"(attempt {attempt}/{self._max_retries})…",
                        )
                    await asyncio.sleep(delay)
                try:
                    output_text = await self._get_completion_text(fallback_kwargs, on_thinking, on_retry)
                    return self._parse_output_object(output_text, schema_name)
                except RuntimeError as e2:
                    if "not valid JSON" in str(e2) or "must be a JSON object" in str(e2):
                        last_parse_exc = e2
                        continue
                    raise RuntimeError(
                        f"OpenRouter API error for {schema_name} (fallback also failed): {e2}"
                    ) from e2
                except Exception as e2:
                    raise RuntimeError(
                        f"OpenRouter API error for {schema_name} (fallback also failed): {e2}"
                    ) from e2
            assert last_parse_exc is not None
            raise RuntimeError(
                f"OpenRouter API error for {schema_name} (fallback malformed JSON after retries): {last_parse_exc}"
            ) from last_parse_exc
```

(Leave every other line of `_generate_json_once`'s body — the reasoning config, `extra_body`, `base_kwargs`, debug artifact write — untouched; only the two call sites and the fallback loop's retry-text call change.)

Update `generate_json` (currently lines 173-212) to accept and forward `on_retry` to both `_generate_json_once` calls:

```python
    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Any = None,
        on_retry: Any = None,
    ) -> dict[str, object]:
        result = await self._generate_json_once(
            model=model, schema_name=schema_name, schema=schema,
            system_instructions=system_instructions, user_payload=user_payload,
            on_thinking=on_thinking, on_retry=on_retry,
        )
        if schema_name == "controller_step_response":
            narrowed = narrow_schema_for_type(schema, result)
            if narrowed is not None:
                logger.warning(
                    "openrouter: %s returned type=%r but missing action fields — "
                    "retrying with narrowed schema",
                    schema_name, result.get("type"),
                )
                result = await self._generate_json_once(
                    model=model, schema_name=schema_name, schema=narrowed,
                    system_instructions=system_instructions, user_payload=user_payload,
                    on_thinking=on_thinking, on_retry=on_retry,
                )
        return result
```

`generate_text` (lines 360-384) is unchanged — nothing in scope calls it with a retry-relevant need.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_openrouter_transport.py -v`
Expected: PASS (all tests, including pre-existing retry tests).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/openrouter_transport.py services/agentd-py/tests/test_openrouter_transport.py
git commit -m "feat(openrouter-transport): add on_retry across all three retry sites"
```

---

### Task 3: `ModelJsonTransport` Protocol — `on_retry` on `generate_json`

**Files:**
- Modify: `services/agentd-py/agentd/providers/contracts.py`

**Interfaces:**
- Consumes: nothing (pure Protocol/typing change).
- Produces: `ModelJsonTransport.generate_json(..., on_retry: Callable[[int, int, str, str], None] | None = None)`.

This is a `Protocol` (structural typing) — Tasks 1-2 already implement the matching runtime signature, so this task is documentation-of-contract only, no test needed (Protocols aren't runtime-checked here; `mypy agentd` is the verification).

- [ ] **Step 1: Update the Protocol**

In `contracts.py`, update `ModelJsonTransport.generate_json` (currently lines 51-60):

```python
    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, object]: ...
```

`generate_text` (lines 62-69) is unchanged.

- [ ] **Step 2: Run mypy to verify no regressions**

Run: `cd services/agentd-py && mypy agentd/providers/contracts.py agentd/providers/ollama_transport.py agentd/providers/openrouter_transport.py`
Expected: no new errors (both transports already implement this signature from Tasks 1-2).

- [ ] **Step 3: Commit**

```bash
git add services/agentd-py/agentd/providers/contracts.py
git commit -m "feat(providers): add on_retry to ModelJsonTransport.generate_json Protocol"
```

---

### Task 4: `create_controller_step` — `on_retry` through Protocol, engine, scripted engine

**Files:**
- Modify: `services/agentd-py/agentd/reasoning/contracts.py`
- Modify: `services/agentd-py/agentd/reasoning/engine.py`
- Modify: `services/agentd-py/agentd/orchestrator/scripted_engine.py`
- Test: `services/agentd-py/tests/test_create_controller_step.py` (existing file already covers `DefaultReasoningEngine.create_controller_step` — add to it)

**Interfaces:**
- Consumes: `ModelJsonTransport.generate_json(..., on_retry=...)` from Task 3.
- Produces: `ReasoningEngine.create_controller_step(..., on_retry: Callable[[int, int, str, str], None] | None = None)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_create_controller_step.py` (confirmed class name: `DefaultReasoningEngine` in `agentd/reasoning/engine.py`, keyword-only constructor `__init__(self, *, model: str, transport: ModelJsonTransport, project_instructions_loader=None, skill_catalog_loader=None)`):

```python
@pytest.mark.asyncio
async def test_create_controller_step_forwards_on_retry_to_transport() -> None:
    calls: list[tuple[int, int, str, str]] = []

    class _FakeTransport:
        supports_oneof_grammar = False

        async def generate_json(self, **kwargs):
            on_retry = kwargs.get("on_retry")
            if callable(on_retry):
                on_retry(1, 3, "network_error", "⏳ retrying…")
            return {"type": "answer", "thought": "t", "answer": "hi"}

    engine = DefaultReasoningEngine(model="m", transport=_FakeTransport())

    def _on_retry(attempt, max_attempts, reason, message):
        calls.append((attempt, max_attempts, reason, message))

    await engine.create_controller_step(
        plan_context={}, history=[], tool_definitions=[],
        phase="DECIDE", on_retry=_on_retry,
    )

    assert calls == [(1, 3, "network_error", "⏳ retrying…")]
```

Add the import if not already present: `from agentd.reasoning.engine import DefaultReasoningEngine`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_create_controller_step.py -k on_retry -v`
Expected: FAIL — `TypeError: create_controller_step() got an unexpected keyword argument 'on_retry'`

- [ ] **Step 3: Implement**

In `reasoning/contracts.py`, update `create_controller_step` (currently lines 78-93):

```python
    async def create_controller_step(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_definitions: list[dict[str, object]],
        *,
        phase: str,
        on_thinking: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, object]:
        """One turn of the agentic chat-controller ReAct loop.

        Returns a dict with at minimum {"type": ..., "thought": str} where type is one of
        tool_call | answer | clarify | propose_mode | edit | submit_changes (gated by `phase`:
        DECIDE allows the first four, EDIT allows tool_call/edit/submit_changes).

        on_retry reports transport-level or corrective-retry attempts as structured
        data (attempt, max_attempts, reason, message) — distinct from on_thinking,
        which carries only genuine model reasoning text.
        """
        ...
```

In `reasoning/engine.py`, update `create_controller_step` (currently lines 255-263 for the signature, line 296-303 for the transport call):

```python
    async def create_controller_step(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_definitions: list[dict[str, object]],
        *,
        phase: str,
        on_thinking: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, object]:
```

(all body up through the `result = await self._transport.generate_json(` call is unchanged), then:

```python
        result = await self._transport.generate_json(
            model=self._model,
            schema_name="controller_step_response",
            schema=schema,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
            on_retry=on_retry,
        )
```

(rest of the method body unchanged).

In `orchestrator/scripted_engine.py`, update `create_controller_step` (currently lines 123-137):

```python
    async def create_controller_step(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_definitions: list[dict[str, object]],
        *,
        phase: str,
        on_thinking: object = None,
        on_retry: object = None,
    ) -> dict[str, object]:
        _ = (plan_context, history, tool_definitions, phase, on_thinking, on_retry)
        if not self._controller_step_responses:
            raise RuntimeError("no controller_step_responses configured on ScriptedReasoningEngine")
        index = min(self._controller_step_index, len(self._controller_step_responses) - 1)
        self._controller_step_index += 1
        return self._controller_step_responses[index]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_create_controller_step.py -v`
Expected: PASS. Also run the FULL controller test suite to catch any test that constructs a `ScriptedReasoningEngine.create_controller_step` positionally in a way this signature change could break:

Run: `cd services/agentd-py && pytest tests/test_controller*.py -v`
Expected: PASS, no regressions (the new params are keyword-only with defaults, so no existing caller breaks).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/reasoning/contracts.py services/agentd-py/agentd/reasoning/engine.py services/agentd-py/agentd/orchestrator/scripted_engine.py services/agentd-py/tests/test_create_controller_step.py
git commit -m "feat(reasoning): thread on_retry through create_controller_step only"
```

---

### Task 5: `controller_loop.py` — broadcast `retry_status`, replace corrective-retry text

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py`
- Test: `services/agentd-py/tests/test_controller_loop_generic_exception.py` (extend — already covers the exception-catch retry path) and the malformed-response corrective test (search `grep -n "Invalid response\|Response failed" services/agentd-py/tests/test_controller*.py` for the existing test to extend)

**Interfaces:**
- Consumes: `ReasoningEngine.create_controller_step(..., on_retry=...)` from Task 4.
- Produces: SSE event `{"type": "retry_status", "payload": {"attempt": int, "max_attempts": int, "reason": str, "message": str}}` broadcast on the chat channel.

- [ ] **Step 1: Write the failing tests**

Add to `test_controller_loop_generic_exception.py` (which already drives the `except Exception` path per its filename — check its existing fixture for a `_FailingThenSucceedingEngine` or similar, and extend it to also emit `on_retry`):

```python
@pytest.mark.asyncio
async def test_controller_loop_broadcasts_retry_status_not_tool_thinking_chunk_on_transport_exception() -> None:
    """A create_controller_step exception is caught by the consecutive_malformed
    handler and now reports via retry_status, not the old tool_thinking_chunk
    '⚠️ Response failed' text."""
    # Reuse this file's existing pattern for an engine whose create_controller_step
    # raises once then returns a valid answer — check the top of this file for the
    # exact fixture class name and constructor shape before writing this test.
    broadcaster = _RecordingBroadcaster()  # or this file's existing broadcaster fake
    loop = _build_controller_loop(broadcaster=broadcaster)  # match this file's existing builder helper

    await loop.run(plan_context={"goal": "g"}, seed_history=[], max_iters=5, auto_accept_edits=True)

    retry_events = [e for e in broadcaster.events if e["type"] == "retry_status"]
    thinking_chunk_events = [
        e for e in broadcaster.events
        if e["type"] == "tool_thinking_chunk" and "Response failed" in e["payload"].get("chunk", "")
    ]
    assert retry_events, broadcaster.events
    assert retry_events[0]["payload"]["reason"] == "malformed_response"
    assert not thinking_chunk_events, thinking_chunk_events
```

Find the existing malformed-response (invalid-type) corrective test — likely named something with `invalid_response`/`malformed` in `test_controller_loop.py` or similar (`grep -rn "Invalid response" services/agentd-py/tests/`) — and add an equivalent assertion there: that a malformed action-type response now broadcasts `retry_status` with `reason="malformed_response"`, not `tool_thinking_chunk` with `"⚠️ Invalid response"` text.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_controller_loop_generic_exception.py -v`
Expected: FAIL — the assertion `retry_events` is empty because `_on_retry`/the `retry_status` event type don't exist yet.

- [ ] **Step 3: Implement**

In `controller_loop.py`'s `_iterate` method, add an `_on_retry` closure next to the existing `_on_thinking` closure (currently lines 299-304):

```python
        def _on_thinking(chunk: str) -> None:
            # Stream the model's reasoning live so the chat thinking pane updates
            # during a model call (the FE maps tool_thinking_chunk). Raw token
            # chunks are live-only; durable thinking_log gets compact tool labels.
            self._broadcaster.broadcast(self._channel_id, {
                "type": "tool_thinking_chunk", "payload": {"chunk": chunk}})

        def _on_retry(attempt: int, max_attempts: int, reason: str, message: str) -> None:
            # Distinct channel from _on_thinking — a retry is not model reasoning
            # and must never be baked into the permanent thinking log (see design
            # spec docs/superpowers/specs/2026-07-14-retry-status-indicator-design.md).
            self._broadcaster.broadcast(self._channel_id, {
                "type": "retry_status",
                "payload": {
                    "attempt": attempt, "max_attempts": max_attempts,
                    "reason": reason, "message": message,
                },
            })
```

Update the `create_controller_step` call (currently lines 373-377) to pass `on_retry=_on_retry`:

```python
                resp = await self._reasoning.create_controller_step(
                    plan_context=plan_context, history=history,
                    tool_definitions=tool_defs, phase=self._sm.phase,
                    on_thinking=_on_thinking, on_retry=_on_retry,
                )
```

Replace the exception-branch `tool_thinking_chunk` broadcast (currently lines 395-400) with an `_on_retry` call:

```python
                _on_retry(
                    consecutive_malformed, _MAX_MALFORMED, "malformed_response",
                    f"⚠️ Response failed ({consecutive_malformed}/{_MAX_MALFORMED}): "
                    f"{cap_event_output(str(exc), 200)} — retrying…",
                )
```

(this replaces the `self._broadcaster.broadcast(self._channel_id, {"type": "tool_thinking_chunk", ...})` block exactly — same position in the `except Exception as exc:` branch, right before `history.append(...)`).

Replace the invalid-response-type branch's `tool_thinking_chunk` broadcast (currently lines 443-448) the same way:

```python
                _on_retry(
                    consecutive_malformed, _MAX_MALFORMED, "malformed_response",
                    f"⚠️ Invalid response ({consecutive_malformed}/{_MAX_MALFORMED}): "
                    f"{cap_event_output(correction, 200)} — retrying…",
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_controller_loop_generic_exception.py tests/test_controller*.py -v`
Expected: PASS, including all pre-existing controller tests (no regression — `_on_retry` is additive, `_on_thinking`'s own behavior is untouched).

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py services/agentd-py/tests/test_controller_loop_generic_exception.py
git commit -m "feat(controller-loop): broadcast retry_status instead of tool_thinking_chunk for retries"
```

---

### Task 6: `editor-client` — `retry_status` in `StreamEvent`

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`

**Interfaces:**
- Produces: `StreamEvent` union member `{ type: "retry_status"; payload: { attempt: number; max_attempts: number; reason: string; message: string } }`.

No test needed — `ChatEventSchema` (line 252-256) is a loose `{type: string, payload: record}` Zod schema with no per-type validation; the union member is a compile-time-only addition, verified by `npm run typecheck`.

- [ ] **Step 1: Add the union member**

In `task-contracts.ts`, add to the `StreamEvent` union, right after `memory_compacted` (currently line 213):

```typescript
  | { type: "memory_compacted"; payload: { evicted: number; anchor_version: number } }
  | { type: "retry_status"; payload: { attempt: number; max_attempts: number; reason: string; message: string } };
```

- [ ] **Step 2: Typecheck**

Run: `cd apps/editor-client && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Build editor-client (vscode-extension types off its compiled dist)**

Run: `npm run -w @crucible/editor-client build`
Expected: build succeeds (per CLAUDE.md's build-order note — vscode-extension typechecks against `dist/index.d.ts`, not source).

- [ ] **Step 4: Commit**

```bash
git add apps/editor-client/src/contracts/task-contracts.ts
git commit -m "feat(editor-client): add retry_status to StreamEvent union"
```

---

### Task 7: Webview — `RetryStatusView` type + `ExtensionMessage`/`AppState`

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/types.ts`

**Interfaces:**
- Produces: `RetryStatusView { attempt: number; max_attempts: number; reason: string; message: string }`, `ExtensionMessage` member `{ type: "updateRetryStatus"; status: RetryStatusView | null }`, `AppState.retryStatus: RetryStatusView | null`.

No test needed — pure type additions, verified in Task 8's reducer tests and by typecheck.

- [ ] **Step 1: Add the type and wire it in**

In `types.ts`, add near `WorkbarInfo` (currently lines 122-127):

```typescript
export interface RetryStatusView {
  attempt: number;
  max_attempts: number;
  reason: string;
  message: string;
}
```

Add to `ExtensionMessage` (currently lines 130-164), right after `updateWorkbar`:

```typescript
  | { type: "updateWorkbar"; info: WorkbarInfo | null }
  | { type: "updateRetryStatus"; status: RetryStatusView | null }
```

Add to `AppState` (currently lines 224-246), right after `workbar`:

```typescript
  workbar: WorkbarInfo | null;
  retryStatus: RetryStatusView | null;
```

- [ ] **Step 2: Typecheck**

Run: `cd apps/vscode-extension/webview-ui && npx tsc --noEmit`
Expected: errors at every place `AppState` is constructed without `retryStatus` (at minimum `useAppState.ts`'s `INITIAL`) — expected, fixed in Task 8.

- [ ] **Step 3: Commit**

Commit together with Task 8 (the type change alone doesn't compile standalone — see Task 8's commit step).

---

### Task 8: Webview reducer — `retryStatus` state + clearing rules

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/hooks/useAppState.ts`
- Test: `apps/vscode-extension/webview-ui/src/test/useAppState.test.ts`

**Interfaces:**
- Consumes: `RetryStatusView`, `ExtensionMessage["updateRetryStatus"]` from Task 7.
- Produces: `AppState.retryStatus` — set on `updateRetryStatus`, cleared on `clearThread`, `appendChunk`, `appendThinkingChunk`, `appendThinkingEntry`, `showThinking`, `updateThinking`, `appendToolEvent`, and inside the existing `liveStatus` `controllerTurnEnded` branch.

- [ ] **Step 1: Write the failing tests**

Add to `useAppState.test.ts`:

```typescript
  it("updateRetryStatus sets retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…" },
      });
    });

    expect(result.current.state.retryStatus).toEqual({
      attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…",
    });
  });

  it("retryStatus never lands in thinkingEntries", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "appendChunk", chunk: "real answer" });
      fireMessage({ type: "finalizeAgentMessage" });
    });

    const last = result.current.state.messages[result.current.state.messages.length - 1];
    const thinkingLog = (last.metadata?.thinking_log as string[] | undefined) ?? [];
    expect(thinkingLog.some((t) => t.includes("retrying"))).toBe(false);
  });

  it("appendChunk (real content) clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "appendChunk", chunk: "hi" });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });

  it("appendThinkingChunk (real progress resuming) clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "appendThinkingChunk", chunk: "real reasoning" });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });

  it("clearThread clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "clearThread" });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });

  it("liveStatus controllerTurnEnded clears retryStatus", () => {
    const { result } = renderHook(() => useAppState());

    act(() => {
      fireMessage({ type: "setInputEnabled", enabled: false });
      fireMessage({
        type: "updateRetryStatus",
        status: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
      });
      fireMessage({ type: "liveStatus", status: null, turnActive: false });
    });

    expect(result.current.state.retryStatus).toBeNull();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/useAppState.test.ts`
Expected: FAIL — `retryStatus` is `undefined` (case not handled, `INITIAL` missing the field).

Also run: `npx tsc --noEmit` — Task 7 already surfaced that `src/components/ThreadView.test.tsx`'s `base: AppState` object literal (lines 17-35) is now missing the required `retryStatus` field too (it's the only other full `AppState` literal in the codebase besides `INITIAL`). Add `retryStatus: null,` to that literal (right after `workbar: null,`) as part of this step, so the codebase typechecks cleanly at the end of this task rather than staying broken until Task 10.

- [ ] **Step 3: Implement**

Add `retryStatus: null` to `INITIAL` (currently lines 24-42), right after `workbar`:

```typescript
const INITIAL: AppState = {
  view: "history",
  threads: [],
  activeThreadId: "",
  messages: [],
  streaming: null,
  thinkingStatus: null,
  inputEnabled: true,
  liveGate: null,
  livePlan: null,
  liveReview: null,
  liveError: null,
  liveTodos: null,
  liveSessions: null,
  sessionTranscripts: {},
  workbar: null,
  retryStatus: null,
  liveStatus: null,
  turnActive: false,
};
```

Add a `case "updateRetryStatus"` (place it near `updateWorkbar`, currently line 353-354):

```typescript
    case "updateWorkbar":
      return { ...state, workbar: msg.info };

    case "updateRetryStatus":
      return { ...state, retryStatus: msg.status };
```

Add `retryStatus: null` to the `clearThread` case (currently lines 117-124):

```typescript
    case "clearThread":
      return {
        ...state,
        messages: [],
        streaming: null,
        thinkingStatus: null,
        workbar: null,
        retryStatus: null,
      };
```

Add `retryStatus: null` to the `appendChunk` case's returned object (currently lines 136-156):

```typescript
    case "appendChunk": {
      const prev = ensureStreaming(state);
      const updatedEntries =
        prev.text === "" && prev.activeThinkingChunk
          ? [...prev.thinkingEntries, prev.activeThinkingChunk]
          : prev.thinkingEntries;
      const sealedChunk = prev.text === "" && prev.activeThinkingChunk ? "" : prev.activeThinkingChunk;
      return {
        ...state,
        thinkingStatus: null,
        retryStatus: null,
        streaming: {
          ...prev,
          text: prev.text + msg.chunk,
          thinkingEntries: updatedEntries,
          activeThinkingChunk: sealedChunk,
        },
      };
    }
```

Add `retryStatus: null` to `appendThinkingEntry` (currently lines 158-172):

```typescript
    case "appendThinkingEntry": {
      const prev = ensureStreaming(state);
      const entries: string[] = prev.activeThinkingChunk
        ? [...prev.thinkingEntries, prev.activeThinkingChunk]
        : [...prev.thinkingEntries];
      return {
        ...state,
        retryStatus: null,
        streaming: {
          ...prev,
          thinkingEntries: [...entries, msg.text],
          activeThinkingChunk: "",
        },
      };
    }
```

Add `retryStatus: null` to `appendThinkingChunk` (currently lines 174-183):

```typescript
    case "appendThinkingChunk": {
      const prev = ensureStreaming(state);
      return {
        ...state,
        retryStatus: null,
        streaming: {
          ...prev,
          activeThinkingChunk: prev.activeThinkingChunk + msg.chunk,
        },
      };
    }
```

Add `retryStatus: null` to `showThinking`/`updateThinking` (currently lines 129-131):

```typescript
    case "showThinking":
    case "updateThinking":
      return { ...state, thinkingStatus: msg.message, retryStatus: null };
```

Add `retryStatus: null` to `appendToolEvent`'s returned object (currently lines 185-207, only the final `return` needs the field — the early `if (alreadyPersisted) return state;` stays as-is since nothing changed):

```typescript
    case "appendToolEvent": {
      const alreadyPersisted = state.messages.some(
        (m) =>
          (m.metadata?.inflight_turn_id as string | undefined) !== undefined &&
          ((m.metadata?.tool_events as ToolEventView[] | undefined) ?? []).some(
            (t) => t.id === msg.event.id,
          ),
      );
      if (alreadyPersisted) return state;
      const prev = ensureStreaming(state);
      return {
        ...state,
        retryStatus: null,
        streaming: {
          ...prev,
          toolEvents: [...prev.toolEvents, { ...msg.event, done: false }],
        },
      };
    }
```

Update the `liveStatus` case's `controllerTurnEnded` branch (currently lines 356-377) to also clear `retryStatus`:

```typescript
    case "liveStatus": {
      const turnActive = msg.turnActive ?? false;
      const controllerTurnEnded =
        !turnActive && msg.status == null && (state.streaming != null || !state.inputEnabled);
      if (controllerTurnEnded) {
        const sealed = state.streaming ? sealStreaming(state, at) : state;
        return { ...sealed, liveStatus: msg.status, turnActive, inputEnabled: true, retryStatus: null };
      }
      return { ...state, liveStatus: msg.status, turnActive };
    }
```

(the plain non-`controllerTurnEnded` return is left as-is — a routine liveStatus poll tick during a real in-flight retry must NOT clear it, since retry_status events arrive far less often than the 1s poll).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/test/useAppState.test.ts`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 5: Commit (Tasks 7+8 together — the type addition doesn't compile standalone)**

```bash
git add apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/webview-ui/src/hooks/useAppState.ts apps/vscode-extension/webview-ui/src/test/useAppState.test.ts apps/vscode-extension/webview-ui/src/components/ThreadView.test.tsx
git commit -m "feat(webview): add ephemeral retryStatus state with full clearing coverage"
```

---

### Task 9: Extension relay — `ControllerUI.updateRetryStatus`, `streamTurn` branch, `chat-panel.ts`, `extension.ts`

**Files:**
- Modify: `apps/vscode-extension/src/controller.ts`
- Modify: `apps/vscode-extension/src/chat-panel.ts`
- Modify: `apps/vscode-extension/src/extension.ts`
- Test: `apps/vscode-extension/test/controller.test.ts` (check exact filename via `find apps/vscode-extension/test -iname "*controller*"`)

**Interfaces:**
- Consumes: `StreamEvent["retry_status"]` from Task 6, `AppState`/`ExtensionMessage` wiring implicit (webview-side already done in Tasks 7-8).
- Produces: `ControllerUI.updateRetryStatus(status: { attempt: number; max_attempts: number; reason: string; message: string } | null): void`.

- [ ] **Step 1: Write the failing test**

Add to the `describe("CrucibleController — chat", ...)` block, reusing the exact fixture shape the existing `"sendChatMessage appends user message and streams agent response"` test in that block already establishes (`createStubBackend({...}) spread + createChatThread/listChatThreads/getChatThread + sendChatMessage: async function* (...)`, `createUi({...overrides})`, `new CrucibleController(() => chatBackend, store, createSettings(), ui, { openDiff: async () => {} }, () => "timestamp")`):

```typescript
it("streamTurn relays retry_status to ui.updateRetryStatus", async () => {
  const retryCalls: Array<{ attempt: number; max_attempts: number; reason: string; message: string } | null> = [];

  const chatBackend: BackendTaskClient = {
    ...createStubBackend({
      submitPayloads: [], getTaskCalls: [], acceptCalls: [],
      rejectCalls: [], getResultCalls: [], planFeedbackCalls: [],
    }),
    createChatThread: async (workspacePath) => ({
      threadId: "chat-new", workspacePath, title: "New Chat", createdAt: "2026-07-14T00:00:00Z",
    }),
    listChatThreads: async () => [],
    getChatThread: async (threadId) => ({
      threadId, workspacePath: "/tmp/workspace", title: "New Chat", messages: [], touchedFiles: [],
    }),
    sendChatMessage: async function* (_threadId: string, _message: string, _signal?: AbortSignal) {
      yield {
        type: "retry_status" as const,
        payload: { attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…" },
      };
      yield { type: "chat_response" as const, payload: { chunk: "hi" } };
      yield { type: "chat_done" as const, payload: {} as Record<string, never> };
    },
  };

  const controller = new CrucibleController(
    () => chatBackend, new MemorySessionStore(), createSettings(),
    createUi({ updateRetryStatus: (status) => retryCalls.push(status) }),
    { openDiff: async () => {} },
    () => "2026-07-14T00:00:00.000Z"
  );

  await controller.sendChatMessage("hello");
  controller.dispose();

  expect(retryCalls).toContainEqual({
    attempt: 1, max_attempts: 4, reason: "rate_limited", message: "⏳ retrying…",
  });
  // finally-block cleanup: the last call must clear it back to null
  expect(retryCalls[retryCalls.length - 1]).toBeNull();
});
```

Also add `updateRetryStatus: () => {},` to `createUi`'s default stub object (currently around line 237, alongside the existing `updateWorkbar: () => {},`) so every OTHER existing test in this file that constructs `createUi()` with no overrides keeps compiling once the `ControllerUI` interface grows the new method.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/vscode-extension && npx vitest run test/controller.test.ts -t "retry_status"`
Expected: FAIL — `ui.updateRetryStatus is not a function` or the property doesn't exist on the `ControllerUI` interface yet.

- [ ] **Step 3: Implement**

In `controller.ts`, add to the `ControllerUI` interface (currently lines 60-97), right after `updateWorkbar` (line 87):

```typescript
  updateWorkbar(info: { stepIndex?: number; totalSteps?: number; stepTitle?: string; phaseLabel?: string } | null): void;
  updateRetryStatus(status: { attempt: number; max_attempts: number; reason: string; message: string } | null): void;
```

In `streamTurn()` (the loop starting at line 711), add a branch right after the existing `tool_thinking_chunk` branch (currently lines 722-724):

```typescript
        } else if (event.type === "tool_thinking_chunk") {
          const chunk = (event.payload["chunk"] as string) ?? "";
          if (chunk) this.ui.appendChatThinkingChunk(chunk);
        } else if (event.type === "retry_status") {
          const p = event.payload as {
            attempt?: number; max_attempts?: number; reason?: string; message?: string;
          };
          this.ui.updateRetryStatus({
            attempt: p.attempt ?? 0,
            max_attempts: p.max_attempts ?? 0,
            reason: p.reason ?? "",
            message: p.message ?? "",
          });
```

Update the `finally` block (currently lines 871-878) to also clear it, right after `this.ui.hideChatThinking()`:

```typescript
    } finally {
      this.openToolEvent = {}; // clear cross-turn stale ids
      this.ui.updateWorkbar(null);
      this.turnAbort = null;
      this.ui.hideChatThinking();
      this.ui.updateRetryStatus(null);
      this.ui.finalizeAgentMessage();
      this.ui.setChatInputEnabled(true);
    }
```

In `chat-panel.ts`, add a method right after `updateWorkbar` (search for its exact location — it's the extension-side implementer that `updateWorkbar`'s postMessage call lives next to; mirror `appendThinkingChunk`'s style at lines 384-386):

```typescript
  updateRetryStatus(status: { attempt: number; max_attempts: number; reason: string; message: string } | null): void {
    this.panel?.webview.postMessage({ type: "updateRetryStatus", status });
  }
```

In `extension.ts`, add the wiring in the `ui: ControllerUI` object literal (currently around lines 295-297, right after `updateWorkbar`):

```typescript
    updateWorkbar: (info) => {
      chatPanel.updateWorkbar(info);
    },
    updateRetryStatus: (status) => {
      chatPanel.updateRetryStatus(status);
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/vscode-extension && npx vitest run test/controller.test.ts`
Expected: PASS, all tests.

Run: `npm run -w crucible-vscode-extension typecheck`
Expected: no errors (confirms `extension.ts`'s `ui` object literal satisfies the widened `ControllerUI` interface).

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/controller.ts apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/extension.ts apps/vscode-extension/test/controller.test.ts
git commit -m "feat(extension): relay retry_status SSE event to the webview"
```

---

### Task 10: `ThreadView.tsx` — render the blinking retry bubble

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx`
- Test: `apps/vscode-extension/webview-ui/src/components/ThreadView.test.tsx` (existing file, lives alongside the component, not in `src/test/`)

**Interfaces:**
- Consumes: `AppState.retryStatus` from Task 8.

- [ ] **Step 1: Write the failing test**

Add to `ThreadView.test.tsx`, reusing its existing `base: AppState` fixture (lines 17-35) via object-spread — this file has no separate state-builder helper, every existing test constructs `{ ...base, ...overrides }` inline or renders `base` directly via `renderView()`:

```typescript
it("renders the retry bubble with the retry message when retryStatus is set", () => {
  const state: AppState = {
    ...base,
    retryStatus: { attempt: 2, max_attempts: 4, reason: "rate_limited", message: "⏳ Rate limited — retrying in 8s (attempt 2/4)…" },
  };

  render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);

  expect(screen.getByText(/Rate limited — retrying in 8s \(attempt 2\/4\)/)).toBeInTheDocument();
});

it("retry bubble takes precedence over thinkingStatus and the streaming bubble", () => {
  const state: AppState = {
    ...base,
    retryStatus: { attempt: 1, max_attempts: 4, reason: "network_error", message: "⏳ retrying…" },
    thinkingStatus: "Thinking…",
    streaming: { text: "partial answer", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [] },
  };

  render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);

  expect(screen.getByText(/retrying/)).toBeInTheDocument();
  expect(screen.queryByText("partial answer")).not.toBeInTheDocument();
});

it("renders normally (no retry bubble) once retryStatus clears", () => {
  const state: AppState = {
    ...base,
    retryStatus: null,
    streaming: { text: "partial answer", thinkingEntries: [], activeThinkingChunk: "", toolEvents: [] },
  };

  render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);

  expect(screen.getByText("partial answer")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/ThreadView.test.tsx -t retry`
Expected: FAIL — the retry message text is not found (nothing renders it yet).

- [ ] **Step 3: Implement**

In `ThreadView.tsx`, update the `isEmpty` check (currently lines 93-96):

```typescript
  const isEmpty =
    state.messages.length === 0 &&
    !state.streaming &&
    !state.thinkingStatus &&
    !state.retryStatus;
```

Add `state.retryStatus?.message` to the auto-scroll effect's dependency array (currently lines 80-86):

```typescript
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [
    state.messages.length,
    state.streaming?.text,
    state.streaming?.toolEvents.length,
    state.streaming?.thinkingEntries.length,
    state.thinkingStatus,
    state.retryStatus?.message,
  ]);
```

Replace the "Thinking status line" + "Streaming bubble" block (currently lines 288-313) so the retry bubble takes precedence over both, per the design's "own independent slot, full precedence" decision:

```typescript
            {/* Retry-status bubble — takes precedence over both the thinking
                status line and the streaming bubble below while a retry is
                in flight. Ephemeral: never appended to thinkingEntries, no
                trace left once it clears (see design spec). */}
            {state.retryStatus && (
              <div
                className="flex items-center gap-2 text-[11px]"
                style={{ color: "var(--color-text-3)", animation: "pulse 1.5s ease-in-out infinite" }}
              >
                <span
                  className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                  style={{ background: "var(--color-accent)" }}
                  aria-hidden="true"
                />
                {state.retryStatus.message}
              </div>
            )}

            {/* Thinking status line (when no streaming bubble yet, no retry in flight) */}
            {!state.retryStatus && state.thinkingStatus && !state.streaming && (
              <div className="flex items-center gap-2 text-[11px]"
                style={{ color: "var(--color-text-3)" }}>
                <span
                  className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                  style={{
                    background: "var(--color-accent)",
                    animation: "pulse 1.5s ease-in-out infinite",
                  }}
                  aria-hidden="true"
                />
                {state.thinkingStatus}
              </div>
            )}

            {/* Streaming bubble (hidden while a retry is in flight — resumes once it clears) */}
            {!state.retryStatus && state.streaming && (
              <AgentRow
                content={state.streaming.text}
                streaming
                streamingThinkingEntries={state.streaming.thinkingEntries}
                streamingThinkingChunk={state.streaming.activeThinkingChunk}
                toolEvents={state.streaming.toolEvents}
              />
            )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/ThreadView.test.tsx`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/ThreadView.tsx apps/vscode-extension/webview-ui/src/components/ThreadView.test.tsx
git commit -m "feat(webview): render blinking retry-status bubble with precedence over thinking/streaming"
```

---

### Task 11: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Full Python suite**

Run: `cd services/agentd-py && pytest > /tmp/retry-status-pytest.txt 2>&1; echo exit=$?; tail -20 /tmp/retry-status-pytest.txt`
Expected: all pass (or only the two pre-existing unrelated PTY-timing flakes noted in CLAUDE.md — no new failures). Do NOT pass `-q`.

- [ ] **Step 2: mypy**

Run: `cd services/agentd-py && mypy agentd`
Expected: no new errors introduced by this feature.

- [ ] **Step 3: Full TS suite + typecheck across workspaces**

Run: `npm run build && npm run test && npm run typecheck`
Expected: all green.

- [ ] **Step 4: Manual smoke (optional but recommended before merge)**

Start the backend with `CRUCIBLE_CHAT_CONTROLLER=1` against a workspace with an unreachable/misconfigured Ollama or OpenRouter endpoint (e.g. wrong port) to force real retries, open the VS Code dev host, send a chat message, and confirm: the bubble appears and updates per attempt, never appears in the persisted thinking log after the turn ends, and the transcript looks normal on the next real answer.

- [ ] **Step 5: No commit** (verification task only — if any step fails, fix in the relevant task above and re-commit there, don't accumulate fixup commits here).
