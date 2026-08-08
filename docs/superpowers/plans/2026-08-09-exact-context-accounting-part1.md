# Exact Context Accounting — Part 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trigger memory compaction on the provider's exact `prompt_tokens` instead of a character estimate that is wrong in both directions at once.

**Architecture:** The transport already reads the provider's usage. A new `on_usage` callback carries `prompt_tokens` out once per streamed call; the controller loop records it as an `ObservedPrompt` pinned to the history length it measured; the compactor adds an estimate of only the messages appended since. Everything measured is exact; only the short tail is estimated. Providers that report no usage fall back to today's behaviour untouched.

**Tech Stack:** Python 3.13, pytest + pytest-asyncio, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-09-exact-context-accounting-design.md` (Part 1 only)

## Global Constraints

- Backend only — `services/agentd-py`. No frontend, no editor-client, no route changes.
- Part 2 (the settings-UI window field and Test button) is **out of scope**. The window keeps coming from `CRUCIBLE_MEMORY_WINDOW_TOKENS`.
- The **task tool loop is out of scope**. `tools/loop.py` keeps today's estimate; `create_tool_step` has no progress callbacks and `CRUCIBLE_TASK_SUBSYSTEM` is default-OFF.
- Only `openai_compatible` reports usage. The other eight transports must not see a new kwarg — reuse the existing `supports_token_progress` capability gate.
- Memory must never break a loop iteration. Every new path is best-effort; a failure degrades to the estimate.
- Activate the venv first: `cd services/agentd-py && source .venv/bin/activate`.
- **Never pass `-q` to pytest** — `pyproject.toml` already sets it, and a CLI `-q` stacks to `-qq` and suppresses the pass/fail summary entirely. Run plain `pytest <paths>`.
- **Never pipe pytest** (`| tail`, `| head`) — the pipe's exit code masks pytest's. Redirect to a file and check `$?` if you need to capture output.
- Comments explain **why**, not what. English only.
- Never `git push`. Never `git add -A` or `git add .` — stage the exact paths named in each task.

## File Structure

| File | Responsibility |
|---|---|
| `agentd/memory/models.py` (modify) | Add `ObservedPrompt` beside the other memory dataclasses. |
| `agentd/memory/compactor.py` (modify) | Two pure accounting functions + use them in `maybe_compact`. |
| `agentd/memory/harness.py` (modify) | Thread `observed` from `prepare_turn` to `maybe_compact`. |
| `agentd/providers/openai_compatible_transport.py` (modify) | Fire `on_usage` once at stream end. |
| `agentd/reasoning/engine.py` (modify) | Pass `on_usage` through the existing capability gate. |
| `agentd/chat/controller_loop.py` (modify) | Record the observation; forward it to `prepare_turn`. |
| `tests/test_memory_accounting.py` (create) | The substantive tests — pure, no DB, no network. |
| `tests/test_memory_compactor.py` (modify) | Trigger behaviour with and without an observation. |
| `tests/test_openai_compatible_transport.py` (modify) | `on_usage` fires once, with the provider's number. |
| `tests/test_memory_controller_loop_wiring.py` (modify) | The loop records and forwards the observation. |

---

### Task 1: Pure accounting

**Files:**
- Modify: `services/agentd-py/agentd/memory/models.py`
- Modify: `services/agentd-py/agentd/memory/compactor.py`
- Test: `services/agentd-py/tests/test_memory_accounting.py` (create)

**Interfaces:**
- Consumes: `estimate_tokens(text: str) -> int` and `_history_tokens(history: History) -> int`, both already in `compactor.py`. `estimate_tokens` is `max(1, len(text) // 3)`.
- Produces:
  - `ObservedPrompt(tokens: int, message_count: int)` — frozen dataclass in `agentd/memory/models.py`
  - `input_tokens(history: History, observed: ObservedPrompt | None) -> int` in `compactor.py`
  - `fixed_overhead(history: History, observed: ObservedPrompt | None) -> int` in `compactor.py`

  Tasks 2, 3 and 5 all rely on these exact names.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_memory_accounting.py`:

```python
"""Pure token accounting for the compaction trigger.

No store, no network, no event loop — this is where the arithmetic that decides
when history gets evicted is actually pinned down.

estimate_tokens is `max(1, len(text) // 3)`, so the fixtures below use content
lengths that are exact multiples of 3 and the expected values are computed, not
guessed.
"""
from agentd.memory.compactor import fixed_overhead, input_tokens
from agentd.memory.models import ObservedPrompt

# 30 chars -> 10 estimated tokens; 60 chars -> 20. History estimate = 30.
HISTORY = [
    {"role": "user", "content": "a" * 30},
    {"role": "assistant", "content": "b" * 60},
]


class TestInputTokens:
    def test_falls_back_to_the_estimate_with_no_observation(self):
        # Every provider but one reports no usage at all; they keep today's behaviour.
        assert input_tokens(HISTORY, None) == 30

    def test_uses_the_exact_count_when_it_covers_the_whole_history(self):
        # Nothing appended since the measurement, so nothing is estimated.
        observed = ObservedPrompt(tokens=500, message_count=2)
        assert input_tokens(HISTORY, observed) == 500

    def test_adds_an_estimate_for_messages_appended_since(self):
        # Measured the first message only; the second arrived afterwards.
        observed = ObservedPrompt(tokens=500, message_count=1)
        assert input_tokens(HISTORY, observed) == 520      # 500 + 60//3

    def test_discards_an_observation_that_outruns_the_history(self):
        # Compaction rewrote history, so the pinned message_count is meaningless.
        observed = ObservedPrompt(tokens=500, message_count=5)
        assert input_tokens(HISTORY, observed) == 30

    def test_a_zero_count_observation_estimates_the_whole_history(self):
        # The call measured system+schema only. Coherent, not a special case.
        observed = ObservedPrompt(tokens=400, message_count=0)
        assert input_tokens(HISTORY, observed) == 430


class TestFixedOverhead:
    def test_is_zero_without_an_observation(self):
        assert fixed_overhead(HISTORY, None) == 0

    def test_is_the_measured_total_minus_the_measured_history(self):
        # What the system prompt and response schema occupy.
        observed = ObservedPrompt(tokens=500, message_count=2)
        assert fixed_overhead(HISTORY, observed) == 470

    def test_only_subtracts_the_slice_that_was_measured(self):
        observed = ObservedPrompt(tokens=500, message_count=1)
        assert fixed_overhead(HISTORY, observed) == 490    # 500 - 30//3

    def test_never_goes_negative(self):
        # The estimate can exceed the real count; overhead is a floor, not a signal.
        observed = ObservedPrompt(tokens=5, message_count=2)
        assert fixed_overhead(HISTORY, observed) == 0

    def test_is_zero_for_a_stale_observation(self):
        observed = ObservedPrompt(tokens=500, message_count=5)
        assert fixed_overhead(HISTORY, observed) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_accounting.py`
Expected: FAIL — `ImportError: cannot import name 'fixed_overhead'` (and `ObservedPrompt`).

- [ ] **Step 3a: Add the dataclass**

In `agentd/memory/models.py`, immediately after the `TurnPreparation` dataclass (it ends at the `recall_trace` field, around line 64), add:

```python
@dataclass(frozen=True)
class ObservedPrompt:
    """The provider's exact prompt_tokens for one call, pinned to the slice of
    history it measured.

    `prompt_tokens` describes the call already made, while compaction decides
    what to send next — so the count alone is not enough. `message_count` records
    how long `history` was when that call was built, which is what lets the
    compactor estimate only the messages appended since and take the rest exact.
    It is also how a stale observation is detected: after compaction rewrites
    history, the pinned count no longer refers to anything.
    """

    tokens: int
    message_count: int
```

`models.py` already imports `dataclass` (it decorates `TurnPreparation`); if it imports only `field`, add `dataclass` to that import.

- [ ] **Step 3b: Add the accounting functions**

In `agentd/memory/compactor.py`, directly after `_history_tokens` (around line 33), add:

```python
def _is_usable(history: History, observed: ObservedPrompt | None) -> bool:
    # A count longer than the history it claims to describe cannot be trusted —
    # that is exactly the shape a compaction leaves behind after rewriting it.
    return observed is not None and observed.message_count <= len(history)


def input_tokens(history: History, observed: ObservedPrompt | None) -> int:
    """Total input tokens the next call will send: system prompt, schemas and
    history together.

    The window holds all of it, so all of it counts against the trigger. The
    character estimate could only ever see message contents, missing the system
    prompt entirely — about 14.5k tokens on a real controller turn.
    """
    if not _is_usable(history, observed):
        return _history_tokens(history)
    assert observed is not None  # narrowed by _is_usable
    return observed.tokens + _history_tokens(history[observed.message_count:])


def fixed_overhead(history: History, observed: ObservedPrompt | None) -> int:
    """Tokens the input carries that are NOT history — system prompt and schemas.

    Needed because the eviction floor budgets retained HISTORY, while the trigger
    now measures total input. Derived by subtraction, so it inherits the
    chars-per-token error on the measured slice: the trigger is exact, this is
    not. Clamped at zero because the estimate can exceed the real count.
    """
    if not _is_usable(history, observed):
        return 0
    assert observed is not None  # narrowed by _is_usable
    return max(0, observed.tokens - _history_tokens(history[:observed.message_count]))
```

Add the import at the top of `compactor.py`, alongside the existing `from agentd.memory.models import ...`:

```python
from agentd.memory.models import ObservedPrompt
```

(If `compactor.py` imports several names from that module, add `ObservedPrompt` to the existing list rather than writing a second import line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_accounting.py`
Expected: PASS — 10 tests.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/models.py \
        services/agentd-py/agentd/memory/compactor.py \
        services/agentd-py/tests/test_memory_accounting.py
git commit -m "feat(memory): pure accounting for exact prompt tokens"
```

---

### Task 2: The compactor triggers on measured input

**Files:**
- Modify: `services/agentd-py/agentd/memory/compactor.py`
- Test: `services/agentd-py/tests/test_memory_compactor.py`

**Interfaces:**
- Consumes: `ObservedPrompt`, `input_tokens`, `fixed_overhead` from Task 1.
- Produces: `Compactor.maybe_compact(history, run_id, observed: ObservedPrompt | None = None)` — the keyword is optional and defaults to None, so every existing caller keeps working unchanged. Task 3 passes it.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_memory_compactor.py`:

```python
@pytest.mark.asyncio
async def test_observation_makes_the_trigger_fire_on_real_occupancy(tmp_path):
    """The estimate sees message contents only. A history that looks small can
    still be sitting behind a large system prompt, and the provider's count is
    the only thing that knows."""
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    # threshold = 100 * 0.65 = 65 tokens of input
    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.65, hot_token_frac=0.4, hot_turns=2
    )
    # Estimated at 30 tokens — well under the threshold on its own.
    history = [
        {"role": "user", "content": "a" * 30},
        {"role": "assistant", "content": "b" * 60},
    ]

    without = await comp.maybe_compact(list(history), "r-no-obs")
    assert without.compacted is False, "estimate alone should stay under threshold"

    # The provider says this same history really costs 80 tokens of input.
    observed = ObservedPrompt(tokens=80, message_count=2)
    with_obs = await comp.maybe_compact(list(history), "r-obs", observed=observed)
    assert with_obs.compacted is True, "measured input is over threshold and must compact"


@pytest.mark.asyncio
async def test_stale_observation_does_not_trigger_compaction(tmp_path):
    """After a compaction rewrites history the pinned count is meaningless; using
    it would compact again immediately, every turn, forever."""
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    comp = Compactor(
        store, summ, window_tokens=100, trigger_frac=0.65, hot_token_frac=0.4, hot_turns=2
    )
    history = [
        {"role": "user", "content": "a" * 30},
        {"role": "assistant", "content": "b" * 60},
    ]
    stale = ObservedPrompt(tokens=5000, message_count=99)
    result = await comp.maybe_compact(history, "r-stale", observed=stale)
    assert result.compacted is False


@pytest.mark.asyncio
async def test_eviction_floor_leaves_room_for_the_fixed_overhead(tmp_path):
    """hot_token_frac budgets retained HISTORY, but the trigger now measures total
    input. A large system prompt must shrink the history that survives, or
    compaction cannot actually get back under the window."""
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old, evicted):
        return "A"

    # hot floor = 1000 * 0.4 = 400 tokens of input.
    comp = Compactor(
        store, summ, window_tokens=1000, trigger_frac=0.1, hot_token_frac=0.4, hot_turns=50
    )
    history = [{"role": "user", "content": "q" * 300} for _ in range(10)]   # 100 tokens each

    # 300 tokens of that 400 floor is system prompt: measured 1300 total against
    # 1000 tokens of history.
    observed = ObservedPrompt(tokens=1300, message_count=10)
    result = await comp.maybe_compact(list(history), "r-floor", observed=observed)

    assert result.compacted is True
    retained = sum(len(str(m.get("content", ""))) // 3
                   for m in result.history if not str(m.get("content", "")).startswith("[MEMORY]"))
    assert retained <= 100, f"kept {retained} tokens of history against a 100-token budget"
```

Add to that file's imports if not already present:

```python
from agentd.memory.models import ObservedPrompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_compactor.py`
Expected: FAIL — `maybe_compact() got an unexpected keyword argument 'observed'`.

- [ ] **Step 3: Change the trigger and the floor**

In `agentd/memory/compactor.py`, replace the signature and the two lines that use the estimate:

```python
    async def maybe_compact(
        self, history: History, run_id: str, observed: ObservedPrompt | None = None
    ) -> CompactionResult:
        # Pure token-trigger check (no count short-circuit: a short history of oversized
        # turns can be over budget and must still compact). Below threshold is the common
        # per-iteration case — return without touching the store (no hot-path DB read).
        #
        # `observed` is the provider's exact count for the last call when there is
        # one; input_tokens degrades to the old estimate when there is not, so the
        # eight transports that report no usage are unaffected.
        if input_tokens(history, observed) < self._window_tokens * self._trigger_frac:
            return CompactionResult(compacted=False, history=history)
```

and, where `hot_budget` is computed:

```python
        # The floor budgets retained HISTORY, but the trigger measures total input,
        # so the system prompt's share has to come off the top — otherwise
        # compaction targets a number the input can never reach. Floored at 1 so a
        # pathological overhead cannot ask for a negative budget.
        hot_budget = max(1, int(self._window_tokens * self._hot_token_frac)
                         - fixed_overhead(history, observed))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_compactor.py tests/test_memory_accounting.py`
Expected: PASS, including every pre-existing test in `test_memory_compactor.py` — they call `maybe_compact` without `observed`, which is why the parameter defaults to None.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/compactor.py \
        services/agentd-py/tests/test_memory_compactor.py
git commit -m "feat(memory): trigger compaction on measured input tokens"
```

---

### Task 3: The harness threads the observation through

**Files:**
- Modify: `services/agentd-py/agentd/memory/harness.py:115-125`
- Test: `services/agentd-py/tests/test_memory_harness.py`

**Interfaces:**
- Consumes: `Compactor.maybe_compact(..., observed=...)` from Task 2.
- Produces: `MemoryHarness.prepare_turn(history, run_id, query="", observed: ObservedPrompt | None = None)`. Task 5 calls it with `observed=`.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_memory_harness.py`:

```python
@pytest.mark.asyncio
async def test_prepare_turn_forwards_the_observation_to_the_compactor(tmp_path):
    """The harness is the only path between the loop and the compactor, so an
    observation it drops is an observation that silently never applies."""
    seen: list[object] = []

    class _SpyCompactor:
        async def maybe_compact(self, history, run_id, observed=None):
            seen.append(observed)
            return CompactionResult(compacted=False, history=history)

    harness = MemoryHarness(enabled=True, compactor=_SpyCompactor())
    observed = ObservedPrompt(tokens=1234, message_count=2)
    await harness.prepare_turn([{"role": "user", "content": "hi"}], "r1", observed=observed)

    assert seen == [observed]


@pytest.mark.asyncio
async def test_no_op_harness_still_accepts_an_observation(tmp_path):
    """The disabled harness must stay a byte-identical passthrough — a caller
    that always passes the kwarg cannot be allowed to break it."""
    history = [{"role": "user", "content": "hi"}]
    prep = await NO_OP_HARNESS.prepare_turn(
        history, "r1", observed=ObservedPrompt(tokens=99, message_count=1)
    )
    assert prep.history is history
    assert prep.compacted is False
```

Add to that file's imports if not already present:

```python
from agentd.memory.models import CompactionResult, ObservedPrompt
```

(`CompactionResult` may already be imported; if it lives elsewhere in this codebase, import it from wherever `compactor.py` imports it from.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_harness.py`
Expected: FAIL — `prepare_turn() got an unexpected keyword argument 'observed'`.

- [ ] **Step 3: Thread it through**

In `agentd/memory/harness.py`, change the signature and the single call:

```python
    async def prepare_turn(
        self, history: History, run_id: str, query: str = "",
        observed: ObservedPrompt | None = None,
    ) -> TurnPreparation:
```

and:

```python
                result = await self._compactor.maybe_compact(history, run_id, observed=observed)
```

Add `ObservedPrompt` to the existing `from agentd.memory.models import ...` line.

Leave the `if not self._enabled:` early return exactly as it is — the disabled harness must remain a byte-identical passthrough.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_harness.py`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/harness.py \
        services/agentd-py/tests/test_memory_harness.py
git commit -m "feat(memory): thread the prompt observation through the harness"
```

---

### Task 4: The transport reports prompt tokens

**Files:**
- Modify: `services/agentd-py/agentd/providers/openai_compatible_transport.py`
- Modify: `services/agentd-py/agentd/reasoning/engine.py`
- Test: `services/agentd-py/tests/test_openai_compatible_transport.py`

**Interfaces:**
- Consumes: `_usage_token_counts(usage) -> tuple[int | None, int | None]`, already in the transport — it returns `(completion_tokens, reasoning_tokens)`. **It does not return prompt_tokens**; read that with the same defensive attribute-or-dict access.
- Produces: an `on_usage(prompt_tokens: int, completion_tokens: int) -> None` keyword accepted by `generate_json`, `_get_completion_output`, `_get_completion_text`, `_generate_json_once`, `_json_object_fallback` and `_stream_with_finish_reason`, and passed by `DefaultReasoningEngine.create_controller_step` behind the existing capability gate. Task 5 supplies the callback.

**Context you need:** `_stream_with_finish_reason` already accumulates `usage_payload` (the last usage object seen) for the token counters. `on_usage` reads `prompt_tokens` off that same object — no new request parameter, nothing else to negotiate.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_openai_compatible_transport.py`:

```python
@pytest.mark.asyncio
async def test_on_usage_reports_prompt_tokens_once() -> None:
    """The compaction trigger needs the size of what we SENT. It is on the same
    usage object the counters already read, and it fires exactly once — unlike
    on_progress, which is throttled and fires many times per call."""
    transport, _ = _transport([])
    seen: list[tuple[int, int]] = []
    stream = _StreamThenUsage(
        [_StreamDelta("hello there")],
        _Usage(completion_tokens=17),
    )
    stream._usage.prompt_tokens = 4242          # type: ignore[attr-defined]
    transport._completions = _FakeCompletions([stream])

    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_usage=lambda p, c: seen.append((p, c)),
    )

    assert seen == [(4242, 17)], seen


@pytest.mark.asyncio
async def test_on_usage_is_silent_when_the_endpoint_reports_nothing() -> None:
    """Most endpoints report no usage at all. A zero would be indistinguishable
    from a real measurement and would poison the trigger."""
    transport, _ = _transport([])
    seen: list[tuple[int, int]] = []
    transport._completions = _FakeCompletions([_DeltaStream([_StreamDelta("hi")])])

    await transport._stream_with_finish_reason(
        {"model": "m", "messages": []},
        on_thinking=lambda _c: None,
        on_usage=lambda p, c: seen.append((p, c)),
    )

    assert seen == [], seen
```

`_Usage` (defined earlier in this file) has no `prompt_tokens` attribute; add one so the fixture is honest rather than monkeypatched at the call site — change its `__init__` to accept `prompt_tokens: int = 0` and assign `self.prompt_tokens = prompt_tokens`, then construct it as `_Usage(completion_tokens=17, prompt_tokens=4242)` and drop the `# type: ignore` line above.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py`
Expected: FAIL — `_stream_with_finish_reason() got an unexpected keyword argument 'on_usage'`.

- [ ] **Step 3a: Read prompt tokens in the transport**

In `agentd/providers/openai_compatible_transport.py`, add a helper beside `_usage_token_counts`:

```python
def _usage_prompt_tokens(usage: Any) -> int | None:
    """prompt_tokens off a usage payload of either shape, or None when absent.

    Separate from _usage_token_counts because that one answers "what did the
    model generate"; this answers "how big was what we sent", which is the
    number the compaction trigger runs on.
    """
    value = usage.get("prompt_tokens") if isinstance(usage, dict) else getattr(usage, "prompt_tokens", None)
    return value if isinstance(value, int) else None
```

Thread an `on_usage: Any = None` keyword through, in the same positions the existing `on_progress` keyword occupies, on each of: `generate_json`, `_generate_json_once`, `_json_object_fallback`, `_get_completion_output`, `_get_completion_text`, `_stream_with_finish_reason`. Pass it down at every call between them exactly as `on_progress` is passed.

Then, in `_stream_with_finish_reason`, immediately after the final `on_progress` emit block, add:

```python
                # Accounting, not display: one call, unthrottled, and only when the
                # endpoint actually reported. A zero here would be indistinguishable
                # from a real measurement of an empty prompt.
                if on_usage is not None and usage_payload is not None:
                    prompt_n = _usage_prompt_tokens(usage_payload)
                    completion_n, _ = _usage_token_counts(usage_payload)
                    if prompt_n is not None:
                        on_usage(prompt_n, completion_n or 0)
```

- [ ] **Step 3b: Pass it through the capability gate**

In `agentd/reasoning/engine.py`, `create_controller_step` takes a new keyword beside `on_progress`:

```python
        on_usage: Callable[[int, int], None] | None = None,
```

and gains a matching gated spread in the `generate_json` call, directly after the `on_progress` one:

```python
            # Same capability gate as on_progress: only openai_compatible reports
            # usage, and the other eight transports must not see the kwarg.
            **({"on_usage": on_usage}
               if on_usage is not None
               and getattr(self._transport, "supports_token_progress", False)
               else {}),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_openai_compatible_transport.py`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/openai_compatible_transport.py \
        services/agentd-py/agentd/reasoning/engine.py \
        services/agentd-py/tests/test_openai_compatible_transport.py
git commit -m "feat(providers): report prompt tokens through an on_usage callback"
```

---

### Task 5: The controller loop closes the circuit

**Files:**
- Modify: `services/agentd-py/agentd/chat/controller_loop.py`
- Test: `services/agentd-py/tests/test_memory_controller_loop_wiring.py`

**Interfaces:**
- Consumes: `on_usage` from Task 4, `prepare_turn(..., observed=...)` from Task 3, `ObservedPrompt` from Task 1.
- Produces: nothing further. This is the last task.

**Context you need:** `_iterate` runs exactly once per turn (called from `run`), and already defines `_on_thinking`, `_on_progress`, `_on_retry` and `_on_salvage` before its iteration loop. `prepare_turn` is called near the top of that loop, around line 890, before `create_controller_step`. The observation must be recorded when a call finishes and read on the *next* iteration — that ordering is the whole point.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_memory_controller_loop_wiring.py`:

```python
@pytest.mark.asyncio
async def test_loop_feeds_the_measured_prompt_into_the_next_compaction(tmp_path: Path):
    """The circuit: the transport reports what the last call cost, and the next
    turn's compaction decision runs on that number instead of a guess."""
    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])

    observed_seen: list[object] = []

    class _SpyHarness:
        # prepare_turn is the ONLY thing the loop calls on the harness.
        async def prepare_turn(self, history, run_id, query="", observed=None):
            observed_seen.append(observed)
            return TurnPreparation(history=history)

    class _ReportingEngine:
        """Reports usage on call 1, then answers on call 2."""

        def __init__(self) -> None:
            self.calls = 0

        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            self.calls += 1
            if self.calls == 1:
                if on_usage is not None:
                    on_usage(7777, 20)
                return {"type": "tool_call", "thought": "t", "tool": "list_directory",
                        "args": {"path": "."}}
            return {"type": "answer", "thought": "t", "answer": "done"}

    loop = ControllerLoop(_ReportingEngine(), reg, EventBroadcaster(), channel_id="c",
                          phase_sm=ControllerPhaseSM(), memory_harness=_SpyHarness())
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=4,
                   auto_accept_edits=True)

    # First iteration has nothing measured yet; the second runs on the report.
    assert observed_seen[0] is None
    assert observed_seen[1] is not None
    assert observed_seen[1].tokens == 7777
```

Import whatever this file does not already have — `TurnPreparation` from `agentd.memory.models`, and `Path` from `pathlib`. Follow the file's existing construction of `ControllerLoop`: check how its other tests pass `memory_harness`, and match that exactly.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_controller_loop_wiring.py`
Expected: FAIL — `observed_seen[1]` is None, because the loop neither requests nor forwards the observation.

- [ ] **Step 3: Record and forward**

In `agentd/chat/controller_loop.py`, beside the other `_on_*` callbacks in `_iterate`, add:

```python
        # The provider's exact size for the LAST call, pinned to the history length
        # it measured. Compaction decides before the next call is built, so this is
        # necessarily one call behind — `message_count` is what lets the compactor
        # take the measured part exact and estimate only what arrived since.
        observed_prompt: list[ObservedPrompt | None] = [None]

        def _on_usage(prompt_tokens: int, _completion_tokens: int) -> None:
            observed_prompt[0] = ObservedPrompt(
                tokens=prompt_tokens, message_count=len(history))
```

Import `ObservedPrompt` from `agentd.memory.models` at the top of the file.

Pass the observation into the harness at the existing `prepare_turn` call:

```python
            _prep = await self._memory_harness.prepare_turn(
                history, run_id, query=str(plan_context.get("goal", "")),
                observed=observed_prompt[0])
```

Clear it whenever compaction rewrote the history, directly after `history[:] = _prep.history`:

```python
            if _prep.compacted:
                # The pinned message_count no longer refers to this history.
                observed_prompt[0] = None
```

And request it on the model call, beside the other callbacks:

```python
                    on_progress=_on_progress, on_salvage=_on_salvage,
                    on_usage=_on_usage,
```

**A note on `len(history)`:** it is read at callback time, which is during the model call — after the payload was built and before anything new is appended. That is exactly the slice the provider measured.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_controller_loop_wiring.py`
Expected: PASS.

- [ ] **Step 5: Full verification sweep**

Run each and confirm the stated result before moving on:

```bash
cd services/agentd-py && source .venv/bin/activate && pytest
```
Expected: PASS. The baseline before this plan is **1538 passed, 1 skipped** — the count rises by the tests added here and nothing else changes.

```bash
cd services/agentd-py && source .venv/bin/activate && ruff check agentd tests
```
Expected: only the four pre-existing `E501` line-length errors in `agentd/chat/controller_loop.py`. Any other error is yours. Confirm the count is exactly four.

```bash
cd services/agentd-py && source .venv/bin/activate && mypy agentd/memory/compactor.py agentd/memory/harness.py agentd/chat/controller_loop.py
```
Expected: `controller_loop.py` has four pre-existing errors (one `dict` type-arg, three `create_controller_step` kwargs the Protocol does not declare). `compactor.py` and `harness.py` must have none.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/chat/controller_loop.py \
        services/agentd-py/tests/test_memory_controller_loop_wiring.py
git commit -m "feat(chat): feed the measured prompt size into compaction"
```

---

## Verifying it live

The unit tests prove the arithmetic; they cannot prove the number arriving from the wire is the one the trigger uses. Confirm against a real provider:

```bash
cd "$(git rev-parse --show-toplevel)"
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
bash scripts/stress/start-backend.sh --backend openai_compatible \
  --workspace "$PWD/workspaces/crucible-stress" --validation-profile none
tail -f .tmp/stress-*/logs/agentd.log | grep -i "memory\|compact"
```

Run a chat turn long enough to compact, and check that the `[memory] compacted` line appears at a plausible point rather than at roughly 45% of the window as it does today. A quick sanity number: with the default 128k window and a ~14.5k system prompt, the trigger should now fire at about 68k tokens of history rather than the ~83k it nominally targeted or the ~57k it actually hit.

Note `CRUCIBLE_MEMORY_ENABLED` defaults ON, so no flag is needed.
