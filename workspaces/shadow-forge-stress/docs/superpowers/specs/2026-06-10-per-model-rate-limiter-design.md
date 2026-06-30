# Per-Model Rate Limiter — Design

**Date:** 2026-06-10
**Status:** Approved (architecture) — pending spec review

## Problem

The agentd backend makes LLM API calls through transport classes (`GeminiJsonTransport`, `OpenRouterJsonTransport`, etc.) that already implement retry logic on transient errors (429, 500, 503). However, there is no proactive rate limiting — if multiple tasks run concurrently or a single task issues rapid requests, the transports can overwhelm provider quotas before retries even kick in.

Current retry behavior is reactive: it waits *after* a 429 is received. This wastes time (the first N requests all fail before any backoff) and risks cascading failures across tasks sharing the same model quota.

## Requirements (confirmed)

1. **Per-model API call throttling** — each model key (e.g., `gemini/gemini-2.0-flash`, `openrouter/meta-llama/llama-3.1-70b`) has its own independent limit.
2. **Configurable via single env var** — `RATE_LIMITS` with JSON map: `{"gemini/gemini-2.0-flash": 60, "openrouter/meta-llama/llama-3.1-70b": 30}` (requests per 60-second window).
3. **Auto-delay transparently** — the limiter blocks until the window allows the request; callers (transport methods) do not need to handle rate-limit errors.
4. **No limit = no delay** — models not in `RATE_LIMITS` pass through immediately.

## Architecture

### Core Component: `ModelRateLimiter`

**Location:** `services/agentd-py/agentd/providers/rate_limiter.py` (new file)

#### Sliding Window Algorithm

Each model key maintains:
- An `asyncio.Lock` for asyncio-safe access
- A `collections.deque[float]` of request timestamps within the current window

When `acquire(model_key)` is called:
1. If `model_key` is not in the configured limits → return immediately (pass through).
2. Acquire the per-model lock.
3. Prune timestamps older than `now - 60s` from the deque.
4. If `len(deque) < max_requests` → append `now`, return (allowed).
5. Otherwise → calculate `delay = oldest_timestamp + 60 - now`, `await asyncio.sleep(delay)`.
6. After sleeping, re-prune and append `now`.

#### Env Var Parser: `parse_rate_limits()`

```python
def parse_rate_limits(raw: str | None) -> dict[str, int] | None:
    """Parse RATE_LIMITS env var into {model_key: max_requests}.

    Example value:
      '{"gemini/gemini-2.0-flash": 60, "openrouter/meta-llama/llama-3.1-70b": 30}'
    """
    if not raw:
        return None
    try:
        limits = json.loads(raw)
        if not isinstance(limits, dict):
            logger.warning("RATE_LIMITS: expected JSON object, got %s", type(limits).__name__)
            return None
        validated: dict[str, int] = {}
        for key, val in limits.items():
            if not isinstance(key, str) or not key.strip():
                logger.warning("RATE_LIMITS: skipping invalid key %r", key)
                continue
            if not isinstance(val, int) or val <= 0:
                logger.warning("RATE_LIMITS: skipping invalid value for key %r: %s", key, val)
                continue
            validated[key] = val
        return validated if validated else None
    except json.JSONDecodeError as exc:
        logger.warning("RATE_LIMITS: invalid JSON: %s", exc)
        return None
```

### Integration Points

#### 1. `GeminiJsonTransport.__init__` (`gemini_transport.py`, line 39)

Add parameter and store:
```python
class GeminiJsonTransport(ModelJsonTransport):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        thinking_enabled: bool = False,
        thinking_budget: int | None = None,
        thinking_level: str | None = None,
        include_thoughts: bool = False,
        timeout_sec: float = 120.0,
        max_retries: int = 4,
        models_client: Any | None = None,
        rate_limiter: ModelRateLimiter | None = None,  # NEW
    ) -> None:
        # ... existing init code ...
        self._rate_limiter = rate_limiter
```

Call `acquire` at the start of `generate_json` and `generate_text`:
```python
async def generate_json(self, *, model: str, ...) -> dict[str, object]:
    if self._rate_limiter:
        await self._rate_limiter.acquire(model)
    # ... rest of existing method ...
```

#### 2. `OpenRouterJsonTransport.__init__` (`openrouter_transport.py`, line 35)

Same pattern — add `rate_limiter` parameter, store it, call `acquire(model)` at the start of `generate_json` and `generate_text`.

#### 3. `main.py` (service startup)

After the existing helper functions (`_int_env`, `_float_env`, etc.), add:
```python
from agentd.providers.rate_limiter import ModelRateLimiter, parse_rate_limits

raw_limits = os.getenv("RATE_LIMITS")
rate_limiter: ModelRateLimiter | None = None
if raw_limits:
    parsed = parse_rate_limits(raw_limits)
    if parsed:
        rate_limiter = ModelRateLimiter(parsed)
```

Then pass `rate_limiter` to each transport that supports it:

**Gemini (line 174):**
```python
transport = GeminiJsonTransport(
    api_key=os.getenv("GEMINI_API_KEY"),
    thinking_enabled=thinking_enabled,
    thinking_budget=thinking_budget,
    thinking_level=thinking_level,
    include_thoughts=_bool_env("AI_EDITOR_GEMINI_INCLUDE_THOUGHTS", False),
    timeout_sec=_float_env("AI_EDITOR_GEMINI_TIMEOUT_SEC", 120.0),
    max_retries=_int_env("AI_EDITOR_GEMINI_MAX_RETRIES", 4),
    rate_limiter=rate_limiter,  # NEW
)
```

**OpenRouter (line 239):**
```python
transport = OpenRouterJsonTransport(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    max_tokens=_int_env("AI_EDITOR_OPENROUTER_MAX_TOKENS", 4096),
    timeout_sec=_float_env("AI_EDITOR_OPENROUTER_TIMEOUT_SEC", 120.0),
    max_retries=_int_env("AI_EDITOR_OPENROUTER_MAX_RETRIES", 4),
    rate_limiter=rate_limiter,  # NEW
)
```

## Trade-offs Considered

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Sliding window vs fixed window | Sliding window | More accurate; prevents burst at window boundaries |
| Per-model locks vs global lock | Per-model locks | Avoids contention between independent models |
| Transparent delay vs exception | Transparent delay | Keeps transport APIs clean; callers don't need rate-limit handling |
| 60-second default window | 60 seconds | Matches standard API rate limit conventions |

## Summary of Changes

| File | Change |
|------|--------|
| `agentd/providers/rate_limiter.py` | **NEW** — `ModelRateLimiter` class + `parse_rate_limits()` |
| `agentd/providers/gemini_transport.py` | Add `rate_limiter` param to `__init__`, call `acquire()` in `generate_json`/`generate_text` |
| `agentd/providers/openrouter_transport.py` | Same pattern as Gemini |
| `agentd/main.py` | Parse `RATE_LIMITS` env var, instantiate limiter, pass to transports |

## Next Steps

1. User reviews this spec document.
2. If approved, invoke writing-plans skill for implementation plan.
