# Exact Context Accounting — Part 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the context window out of `CRUCIBLE_MEMORY_WINDOW_TOKENS` and into a field in the Settings panel's Provider section, beside the model it belongs with, with an opt-in Test button that verifies the declared size by passphrase recall rather than by HTTP status.

**Architecture:** The window becomes a runtime-settable value on the `Compactor`, reachable from `ProviderRuntime` (which already owns the hot-swap seam) via a list of duck-typed "window sinks" — the same shape as its existing `engines` list. `PUT /v1/config/provider` gains an optional `context_window`; `GET /v1/config` reports the effective one. A new `POST /v1/providers/context-test` sends one deliberately huge prompt with a passphrase at the front and judges the answer by whether the passphrase comes back. The extension persists the number in `globalState` alongside backend/model and injects it as `CRUCIBLE_MEMORY_WINDOW_TOKENS` on the next managed spawn, so the startup path and the hot path agree without the backend needing two sources of truth.

**Tech Stack:** Python 3.13 / FastAPI / pydantic / pytest-asyncio (backend); TypeScript / Zod (editor-client); TypeScript + React + Vite + vitest + @testing-library/react (extension host + settings webview).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-09-exact-context-accounting-design.md`, "Part 2 — Context window in the settings UI". Part 1 is complete and merged on this branch (`f8dbc6a`…`9930390`); do not modify it.
- **Resolution order for the window, most specific first:** the configured provider window, then `CRUCIBLE_MEMORY_WINDOW_TOKENS`, then the existing `128000` default. Deployments that set only the env var must keep working with no change.
- **The value is DECLARED, never detected.** The spec's non-goals establish there is nothing to detect it from: NIM's `/v1/models` returns only `id`/`object`/`created`/`owned_by`, and NIM accepts a 600,058-token prompt with HTTP 200. Do not add a capability lookup or an error-sniffing fallback.
- **Error asymmetry, quoted in the UI help text verbatim:** too small = compaction fires early, wasteful but safe and self-correcting. Too large = the prompt overruns the real window; on NVIDIA NIM, measured, a 600,058-token prompt returned HTTP 200, billed every token, and answered with `completion_tokens: 1` and empty content — no error, no warning.
- **The Test button is opt-in and never runs automatically on save.** It must warn about cost before running.
- **The test judges by capability, not HTTP status.** A test that checks for an error would report success at five times the real window.
- **Starter-table entries err LOW.** Where the true window is uncertain, declare the smaller number: too-small is self-correcting, too-large fails silently.
- **Build order:** after changing `apps/editor-client`, run `npm run -w @crucible/editor-client build` before any `vscode-extension` typecheck — the extension types off the compiled `dist/index.d.ts`, not source.
- **pytest:** never pass `-q` (the `pyproject.toml` `addopts` already sets it; a second one suppresses the summary). Never pipe pytest through `tail` — the pipe masks the exit code. Redirect to a file and check `$?`.
- Type-strict everywhere: no `any` in new TypeScript, no untyped returns in new Python.

## File Structure

**Backend (`services/agentd-py/`)**

| File | Responsibility |
|---|---|
| `agentd/memory/compactor.py` (modify) | `Compactor.set_window_tokens()` — the window stops being construction-frozen. |
| `agentd/memory/harness.py` (modify) | `MemoryHarness.set_window_tokens()` — forwards to the compactor; no-op without one. |
| `agentd/providers/runtime.py` (modify) | `ProviderRuntime` holds the effective `context_window` and a list of window sinks; `swap()` applies a new one after validation succeeds. |
| `agentd/api/routes.py` (modify) | `ProviderSwapRequest.context_window`; `GET /v1/config` reports it; new `POST /v1/providers/context-test`. |
| `agentd/providers/context_probe.py` (create) | The context test. Pure prompt-building + verdict, plus the one async runner. Kept out of `validate.py`: that module is a cheap ping, this one deliberately sends a megabyte. |
| `agentd/providers/openai_compatible_transport.py` (modify) | `generate_text` accepts `on_usage`, so the test can report the provider's own `prompt_tokens` instead of only its own estimate. |
| `agentd/main.py` (modify) | Pass both memory harnesses to `ProviderRuntime` as window sinks; seed its `context_window` from `MemoryConfig`. |

**editor-client (`apps/editor-client/`)**

| File | Responsibility |
|---|---|
| `src/contracts/task-contracts.ts` (modify) | `BackendConfigSchema.provider.contextWindow`; `ContextTestResultSchema`; `setProvider`/`testContextWindow` on `BackendTaskClient`. |
| `src/client/http-backend-client.ts` (modify) | snake↔camel mapping for both. |

**Extension host (`apps/vscode-extension/src/`)**

| File | Responsibility |
|---|---|
| `settings-data.ts` (modify) | `contextWindow` on state + `setProvider`; new `settings/testContextWindow` in-msg and `settings/contextTestResult` out-msg; `SettingsDeps.saveContextWindow`. |
| `settings-deps.ts` (modify) | Wire the new deps to `RuntimeManager` and the HTTP client. |
| `runtime/vscode-runtime.ts` (modify) | `saveContextWindow`/`contextWindow` in `globalState`; feed it into `getProviderSettings`. |
| `runtime/backend-process.ts` (modify) | `BackendSettings.contextWindow` → `CRUCIBLE_MEMORY_WINDOW_TOKENS` in `buildBackendEnv`. |

**Settings webview (`apps/vscode-extension/webview-ui/src/settings/`)**

| File | Responsibility |
|---|---|
| `contextWindows.ts` (create) | The starter table + `defaultContextWindow(model)`. Pure, its own file because it is the one piece of this feature that will be edited most often and by people who are not editing React. |
| `types.ts` (modify) | Local mirror of the message protocol changes. |
| `sections/ProviderSection.tsx` (modify) | The field, its help text, and the two-step Test button. |

**Tests**

| File | Covers |
|---|---|
| `services/agentd-py/tests/test_memory_window_setter.py` (create) | Task 1 |
| `services/agentd-py/tests/test_provider_hotswap.py` (modify) | Tasks 2, 3 |
| `services/agentd-py/tests/test_config_route.py` (modify) | Task 3 |
| `services/agentd-py/tests/test_context_probe.py` (create) | Tasks 4, 5 |
| `apps/editor-client/test/settings-client.test.ts` (modify) | Task 6 |
| `apps/vscode-extension/test/settings-data.test.ts` (modify) | Task 7 |
| `apps/vscode-extension/test/runtime-backend-process.test.ts` (modify) | Task 8 |
| `apps/vscode-extension/webview-ui/src/settings/contextWindows.test.ts` (create) | Task 9 |
| `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx` (modify) | Tasks 9, 10 |

---

### Task 1: The compaction window stops being construction-frozen

**Files:**
- Modify: `services/agentd-py/agentd/memory/compactor.py` (add a method after `__init__`, around line 143)
- Modify: `services/agentd-py/agentd/memory/harness.py` (add a method to `MemoryHarness`, after `prepare_turn`, around line 150)
- Test: `services/agentd-py/tests/test_memory_window_setter.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Compactor.set_window_tokens(window_tokens: int) -> None` and `MemoryHarness.set_window_tokens(window_tokens: int) -> None`. Task 2 calls the harness one through a duck-typed sink list.

**Context for the implementer:** `Compactor.__init__` currently takes `window_tokens` and stashes it as `self._window_tokens`. Two places read it — the trigger comparison in `maybe_compact` (line ~155) and `nominal_hot_budget` (line ~177) — and both read it *per call*, so there is no derived state to invalidate when it changes. `MemoryHarness` may hold `compactor=None` (that is exactly what `NO_OP_HARNESS` is), so the harness-level setter has to tolerate that: the caller in Task 2 keeps a list of sinks and must not special-case them.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_memory_window_setter.py`:

```python
import pytest

from agentd.memory.compactor import Compactor
from agentd.memory.harness import NO_OP_HARNESS, MemoryHarness
from agentd.memory.store import MemoryStore


async def _never(old: str, new: str) -> str:
    raise AssertionError("summarize called below threshold")


async def _merge(old: str, new: str) -> str:
    return "merged summary"


@pytest.mark.asyncio
async def test_shrinking_the_window_makes_the_same_history_trip_the_trigger(tmp_path):
    """The window is read per call, so a mid-process change takes effect immediately."""
    store = MemoryStore(tmp_path / "m.sqlite3")
    comp = Compactor(
        store, _never, window_tokens=100_000, trigger_frac=0.65,
        hot_token_frac=0.4, hot_turns=10,
    )
    history = [{"role": "user", "content": "q" * 300} for _ in range(6)]  # ~600 est tokens

    assert (await comp.maybe_compact(history, "r1")).compacted is False

    comp._summarize = _merge  # below-threshold guard no longer applies
    comp.set_window_tokens(500)  # 500 * 0.65 = 325 < ~600
    assert (await comp.maybe_compact(history, "r1")).compacted is True


@pytest.mark.asyncio
async def test_harness_forwards_the_window_to_its_compactor(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    comp = Compactor(
        store, _never, window_tokens=100_000, trigger_frac=0.65,
        hot_token_frac=0.4, hot_turns=10,
    )
    harness = MemoryHarness(enabled=True, compactor=comp)
    harness.set_window_tokens(4096)
    assert comp._window_tokens == 4096


def test_harness_without_a_compactor_ignores_the_window():
    """NO_OP_HARNESS is a legitimate sink — the caller holds a list and must not
    have to special-case a memory-disabled process."""
    NO_OP_HARNESS.set_window_tokens(4096)  # must not raise
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_memory_window_setter.py
```

Expected: FAIL — `AttributeError: 'Compactor' object has no attribute 'set_window_tokens'`.

- [ ] **Step 3: Add the compactor setter**

In `agentd/memory/compactor.py`, immediately after `__init__` (before `async def maybe_compact`):

```python
    def set_window_tokens(self, window_tokens: int) -> None:
        """Re-point the trigger at a new context window mid-process.

        Only two places read it — the trigger comparison and the eviction floor —
        and both read it per call, so there is no derived state to invalidate and
        the change lands on the next loop iteration. This exists so the settings
        panel's context-window field can hot-apply the same way the provider
        hot-swap does, instead of requiring a backend restart.
        """
        self._window_tokens = window_tokens
```

- [ ] **Step 4: Add the harness forwarder**

In `agentd/memory/harness.py`, in `MemoryHarness`, immediately after `prepare_turn`:

```python
    def set_window_tokens(self, window_tokens: int) -> None:
        """Forward a settings-panel window change to the compactor.

        A harness with no compactor (memory disabled — NO_OP_HARNESS) accepts and
        ignores it: ProviderRuntime holds a plain list of sinks and must not have
        to know which processes have memory switched on.
        """
        if self._compactor is not None:
            self._compactor.set_window_tokens(window_tokens)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_memory_window_setter.py tests/test_memory_compactor.py tests/test_memory_harness.py
```

Expected: PASS, all of them.

- [ ] **Step 6: Type-check**

```bash
cd services/agentd-py && source .venv/bin/activate && mypy agentd/memory/compactor.py agentd/memory/harness.py
```

Expected: no new errors in either file.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/memory/compactor.py services/agentd-py/agentd/memory/harness.py services/agentd-py/tests/test_memory_window_setter.py
git commit -m "feat(memory): let the compaction window change at runtime"
```

---

### Task 2: ProviderRuntime carries the context window and applies it

**Files:**
- Modify: `services/agentd-py/agentd/providers/runtime.py`
- Modify: `services/agentd-py/agentd/main.py` (the `ProviderRuntime` construction at lines ~302-315)
- Test: `services/agentd-py/tests/test_provider_hotswap.py` (append)

**Interfaces:**
- Consumes: `MemoryHarness.set_window_tokens(int)` from Task 1.
- Produces: `ProviderRuntime(backend, model, engines, window_sinks=(), context_window=None)` with a public `self.context_window: int | None`, and `swap(*, backend, model=None, credentials=None, context_window=None) -> dict[str, str | int]` whose returned dict carries `"context_window"` when one is in effect. Task 3 calls it from the route.

**Context for the implementer:** `ProviderRuntime` is the existing hot-swap seam — `main.py` builds exactly one, holding every live `DefaultReasoningEngine`. Its contract is *validate first, mutate second*: a failed swap must leave everything untouched. The context window follows the same rule, applied only after `ping_transport` returns, so a bad key does not silently change how compaction behaves. Window sinks are duck-typed (`set_window_tokens`) rather than typed as `MemoryHarness` for the same reason `engines` is `Sequence[object]`: importing the memory package into the providers package would be a new dependency edge for one method call.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_provider_hotswap.py`:

```python
class _WindowSink:
    def __init__(self) -> None:
        self.window: int | None = None

    def set_window_tokens(self, window_tokens: int) -> None:
        self.window = window_tokens


@pytest.mark.asyncio
async def test_swap_applies_the_context_window_to_every_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = DefaultReasoningEngine(model="old-model", transport=_Transport("old"))
    sink_a, sink_b = _WindowSink(), _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model", engines=[engine],
        window_sinks=[sink_a, sink_b], context_window=128_000,
    )
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport("new")
    )
    result = await rt.swap(backend="groq", model="m2", context_window=32_768)
    assert sink_a.window == 32_768 and sink_b.window == 32_768
    assert rt.context_window == 32_768
    assert result["context_window"] == 32_768


@pytest.mark.asyncio
async def test_swap_without_a_window_leaves_the_existing_one_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-only hot-swap (the composer's model menu) must not reset the window
    the user declared in Settings."""
    sink = _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
        window_sinks=[sink], context_window=200_000,
    )
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport("new")
    )
    result = await rt.swap(backend="groq", model="m2")
    assert sink.window is None  # never touched
    assert rt.context_window == 200_000
    assert result["context_window"] == 200_000


@pytest.mark.asyncio
async def test_failed_swap_does_not_apply_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate-then-mutate: a bad key must not silently change compaction."""
    from agentd.providers.validate import ProviderValidationError

    sink = _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
        window_sinks=[sink], context_window=128_000,
    )

    async def _boom(transport, model, timeout_sec=30.0):
        raise ProviderValidationError("bad key")

    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport()
    )
    monkeypatch.setattr(runtime_mod, "ping_transport", _boom)
    with pytest.raises(ProviderValidationError):
        await rt.swap(backend="groq", model="m2", context_window=8192)
    assert sink.window is None and rt.context_window == 128_000
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_provider_hotswap.py
```

Expected: FAIL — `TypeError: ProviderRuntime.__init__() got an unexpected keyword argument 'window_sinks'`.

- [ ] **Step 3: Widen ProviderRuntime**

Replace the body of `agentd/providers/runtime.py` below the imports with:

```python
class ProviderRuntime:
    def __init__(
        self,
        *,
        backend: str,
        model: str,
        engines: Sequence[object],
        window_sinks: Sequence[object] = (),
        context_window: int | None = None,
    ) -> None:
        self.backend = backend
        self.model = model
        # The window in effect right now. Seeded in main.py from MemoryConfig — i.e.
        # from CRUCIBLE_MEMORY_WINDOW_TOKENS or its 128000 default — so GET /v1/config
        # reports the real number from the first request, before anyone has saved
        # anything in the settings panel. That seeding IS the spec's resolution order:
        # provider window > env var > default, collapsed into one value.
        self.context_window = context_window
        self._engines = list(engines)
        # Duck-typed on set_window_tokens rather than typed as MemoryHarness: the
        # providers package should not grow a dependency on the memory package for
        # one method call, and the same reasoning already governs `engines`.
        self._window_sinks = list(window_sinks)

    async def swap(
        self,
        *,
        backend: str,
        model: str | None = None,
        credentials: dict[str, str] | None = None,
        context_window: int | None = None,
    ) -> dict[str, object]:
        try:
            transport = build_transport(backend, credentials=credentials)
            resolved = model or resolve_model(backend)
        except Exception as exc:
            # Identical reasoning to ping_provider's, and the identical error type
            # so the route needs no new handler: a transport that refuses to
            # construct ("CRUCIBLE_OPENAI_COMPAT_BASE_URL is required…") is a user
            # configuration mistake carrying its own actionable message. Raised as
            # a bare RuntimeError it escaped the route's handler entirely and came
            # back as a 500 with nothing the UI could show.
            raise ProviderValidationError(str(exc)) from exc
        await ping_transport(transport, resolved)  # raises ProviderValidationError
        for engine in self._engines:
            engine.set_provider(model=resolved, transport=transport)  # type: ignore[attr-defined]
        self.backend, self.model = backend, resolved
        # Applied only after validation succeeds, for the same reason the engines
        # are: a rejected swap must leave the process exactly as it was. Absent
        # means "unchanged", not "reset" — a model-only hot-swap from the composer
        # must not discard the window the user declared in Settings.
        if context_window is not None:
            self.context_window = context_window
            for sink in self._window_sinks:
                sink.set_window_tokens(context_window)  # type: ignore[attr-defined]
        result: dict[str, object] = {"backend": backend, "model": resolved}
        if self.context_window is not None:
            result["context_window"] = self.context_window
        return result
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_provider_hotswap.py
```

Expected: PASS, including the three pre-existing tests in that file.

- [ ] **Step 5: Wire the sinks in main.py**

In `agentd/main.py`, replace the `ProviderRuntime` construction block (currently lines ~307-315):

```python
provider_runtime: ProviderRuntime | None = None
if reasoning_backend != "scripted":
    _engines = [reasoning_engine]
    _ctrl_engine = getattr(_chat_agent, "_reasoning", None)
    if isinstance(_ctrl_engine, DefaultReasoningEngine) and _ctrl_engine is not reasoning_engine:
        _engines.append(_ctrl_engine)
    # Both harnesses in one process compact against the same window: the task loop's
    # (_task_memory_harness) and the chat controller's own (built inside
    # select_chat_handler). A NO_OP_HARNESS accepts set_window_tokens and ignores it,
    # so neither needs a guard here.
    _window_sinks: list[object] = [_task_memory_harness]
    _ctrl_harness = getattr(_chat_agent, "_memory_harness", None)
    if _ctrl_harness is not None and _ctrl_harness is not _task_memory_harness:
        _window_sinks.append(_ctrl_harness)
    provider_runtime = ProviderRuntime(
        backend=reasoning_backend,
        model=_chat_model,
        engines=_engines,
        window_sinks=_window_sinks,
        context_window=MemoryConfig.from_env(os.environ).window_tokens,
    )
```

`MemoryConfig` and `os` are already imported in `main.py`; do not re-import them.

- [ ] **Step 6: Verify the whole backend suite still passes and type-check**

```bash
cd services/agentd-py && source .venv/bin/activate
mypy agentd/providers/runtime.py agentd/main.py
pytest tests/test_provider_hotswap.py tests/test_config_route.py tests/test_provider_validate_route.py
```

Expected: mypy clean on `runtime.py`; `main.py` has pre-existing errors — confirm the count did not rise. Tests PASS.

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/providers/runtime.py services/agentd-py/agentd/main.py services/agentd-py/tests/test_provider_hotswap.py
git commit -m "feat(providers): carry the context window through the hot-swap seam"
```

---

### Task 3: The provider route accepts and reports the context window

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py` (`ProviderSwapRequest` at line 59; `get_config` at line 224; `put_config_provider` at line 252)
- Test: `services/agentd-py/tests/test_provider_hotswap.py` (append route-level tests)

**Interfaces:**
- Consumes: `ProviderRuntime.swap(..., context_window=...)` and `ProviderRuntime.context_window` from Task 2.
- Produces: `PUT /v1/config/provider` accepts `{"context_window": int | null}`; `GET /v1/config` returns `provider: {backend, model, context_window}`. Task 6 maps both in the editor-client.

**Context for the implementer:** the route returns `{"ok": True, **result}` today, so widening `swap`'s return dict is enough to expose the window on the response. The bounds check belongs in the pydantic model, not the route body: a nonsense window is a client bug and pydantic already turns a field constraint into a 422 with a usable message. `1024` as the floor and `10_000_000` as the ceiling are sanity rails, not judgements about real models — the point is to reject `0` (which would make every turn compact forever) and a fat-fingered extra digit.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_provider_hotswap.py`:

```python
@pytest.mark.asyncio
async def test_put_provider_applies_the_context_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _WindowSink()
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
        window_sinks=[sink], context_window=128_000,
    )
    monkeypatch.setattr(
        runtime_mod, "build_transport", lambda b, credentials=None: _Transport("new")
    )
    client = _client(tmp_path, rt)
    response = client.put(
        "/v1/config/provider",
        json={"backend": "groq", "model": "m2", "context_window": 32768},
    )
    assert response.status_code == 200
    assert response.json()["context_window"] == 32768
    assert sink.window == 32768


def test_put_provider_rejects_a_nonsense_window(tmp_path: Path) -> None:
    rt = ProviderRuntime(
        backend="openai", model="old-model",
        engines=[DefaultReasoningEngine(model="old-model", transport=_Transport("old"))],
    )
    client = _client(tmp_path, rt)
    assert client.put(
        "/v1/config/provider", json={"backend": "groq", "context_window": 0}
    ).status_code == 422


def test_config_reports_the_effective_context_window(tmp_path: Path) -> None:
    rt = ProviderRuntime(
        backend="openai", model="gpt-5",
        engines=[DefaultReasoningEngine(model="gpt-5", transport=_Transport())],
        context_window=200_000,
    )
    payload = _client(tmp_path, rt).get("/v1/config").json()
    assert payload["provider"] == {
        "backend": "openai", "model": "gpt-5", "context_window": 200_000
    }
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_provider_hotswap.py
```

Expected: FAIL — the window is ignored (`sink.window is None`), `0` is accepted with a 200, and `/v1/config`'s provider dict has no `context_window` key.

- [ ] **Step 3: Widen the request model**

In `agentd/api/routes.py`, replace `ProviderSwapRequest` (line 59):

```python
class ProviderSwapRequest(BaseModel):
    backend: str
    model: str | None = None
    credentials: dict[str, str] = {}
    # Absent means "leave the window as it is" — a model-only hot-swap must not
    # reset what the user declared in Settings. Bounds are sanity rails, not model
    # facts: 0 would make every turn compact forever, and the ceiling catches a
    # fat-fingered extra digit before it silently disables compaction entirely.
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
```

Add `Field` to the existing pydantic import at the top of the file (`from pydantic import BaseModel` → `from pydantic import BaseModel, Field`) if it is not already there.

- [ ] **Step 4: Pass it through the route and report it**

In `put_config_provider`, add the kwarg to the `swap` call:

```python
            result = await provider_runtime.swap(  # type: ignore[attr-defined]
                backend=body.backend,
                model=body.model,
                credentials=body.credentials or None,
                context_window=body.context_window,
            )
```

In `get_config`, replace the `"provider"` entry:

```python
            "provider": (
                {
                    "backend": provider_runtime.backend,  # type: ignore[attr-defined]
                    "model": provider_runtime.model,  # type: ignore[attr-defined]
                    # The effective window — seeded from CRUCIBLE_MEMORY_WINDOW_TOKENS
                    # at startup, overwritten by a settings-panel save. The panel
                    # pre-fills its field from this, so what it shows is what the
                    # running process is actually using.
                    "context_window": provider_runtime.context_window,  # type: ignore[attr-defined]
                }
                if provider_runtime is not None
                else None
            ),
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_provider_hotswap.py tests/test_config_route.py
```

Expected: PASS. If a pre-existing assertion in `test_config_route.py` compares the whole provider dict, update it to include `context_window` — that is a legitimate contract change, not a test to work around.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/tests/test_provider_hotswap.py services/agentd-py/tests/test_config_route.py
git commit -m "feat(api): accept and report the declared context window"
```

---

### Task 4: The context probe — pure prompt-building and verdict

**Files:**
- Create: `services/agentd-py/agentd/providers/context_probe.py`
- Test: `services/agentd-py/tests/test_context_probe.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `new_passphrase(rng: random.Random | None = None) -> str`
  - `build_probe(window_tokens: int, passphrase: str) -> tuple[str, dict[str, object]]` returning `(system_instructions, user_payload)`
  - `estimated_prompt_tokens(system_instructions: str, user_payload: dict[str, object]) -> int`
  - `recalled(answer: str, passphrase: str) -> bool`
  - `PROBE_CHARS_PER_TOKEN: float`
  - Task 5 composes all of these into the async runner.

**Context for the implementer:** this is the whole substance of the Test button, and it is pure — no network, no provider, no event loop. The verdict must come from *capability*, not from HTTP status: the spec records a 600,058-token prompt that returned HTTP 200 with `completion_tokens: 1` and empty content, so a test that checked for an error would report success at five times the real window.

Three details that are easy to get wrong:

1. **Where the passphrase goes.** At the very *start* of the filler, because the front of the prompt is what falls off when the window is overrun. A passphrase at the end proves nothing.
2. **Words, not hex.** A recall target of `9f3a71c2` invites tokenizer mangling and a wrong verdict for the wrong reason. Three ordinary words from a fixed list are reproduced reliably and still give ~110k combinations, which is far more than enough to defeat a cached response.
3. **The filler size is an estimate and must be labelled as one.** Part 1 measured the true ratio at 3.71–4.40 characters per token; `PROBE_CHARS_PER_TOKEN = 4.0` sits mid-range. Under-filling produces a false pass and over-filling a false fail, which is exactly why Task 5 reports the provider's own `prompt_tokens` alongside the verdict — the estimate sizes the request, the provider's count is what gets shown to the user.

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_context_probe.py`:

```python
import json
import random

from agentd.providers.context_probe import (
    PROBE_CHARS_PER_TOKEN,
    build_probe,
    estimated_prompt_tokens,
    new_passphrase,
    recalled,
)


def test_passphrase_is_three_reproducible_words():
    phrase = new_passphrase(random.Random(1))
    assert phrase.count("-") == 2
    assert phrase.replace("-", "").isalpha()
    assert new_passphrase(random.Random(1)) == phrase  # seeded => deterministic


def test_passphrases_differ_across_runs():
    """A fixed passphrase would let a cached or echoed response fake a pass."""
    seen = {new_passphrase() for _ in range(50)}
    assert len(seen) > 40


def test_probe_puts_the_passphrase_at_the_very_front_of_the_filler():
    """The FRONT of the prompt is what falls off when the window is overrun, so
    that is the only position that proves anything."""
    _system, payload = build_probe(4096, "velvet-harbor-quasar")
    document = str(payload["document"])
    assert document.index("velvet-harbor-quasar") < 200
    assert "velvet-harbor-quasar" not in document[len(document) // 2:]


def test_probe_is_sized_to_the_declared_window():
    _system, payload = build_probe(50_000, "a-b-c")
    estimated = estimated_prompt_tokens(_system, payload)
    assert 0.85 * 50_000 <= estimated <= 50_000


def test_probe_json_serializes():
    """The payload is sent as JSON by every transport; a non-serializable value
    would fail at the boundary, far from here."""
    _system, payload = build_probe(4096, "a-b-c")
    assert json.loads(json.dumps(payload))["document"]


def test_recall_is_case_and_whitespace_insensitive():
    assert recalled("The passphrase is  Velvet-Harbor-Quasar.", "velvet-harbor-quasar")
    assert recalled("velvet harbor quasar", "velvet-harbor-quasar")


def test_no_recall_when_the_answer_is_empty_or_wrong():
    """NVIDIA NIM answered an over-long prompt with completion_tokens:1 and empty
    content, HTTP 200 — an empty answer is a FAIL, not an inconclusive result."""
    assert not recalled("", "velvet-harbor-quasar")
    assert not recalled("   ", "velvet-harbor-quasar")
    assert not recalled("I don't see a passphrase.", "velvet-harbor-quasar")


def test_partial_recall_is_not_recall():
    assert not recalled("velvet harbor", "velvet-harbor-quasar")


def test_chars_per_token_sits_inside_the_measured_range():
    assert 3.71 <= PROBE_CHARS_PER_TOKEN <= 4.40
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_context_probe.py
```

Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.providers.context_probe'`.

- [ ] **Step 3: Write the module**

Create `services/agentd-py/agentd/providers/context_probe.py`:

```python
"""The context-window Test button.

Verifies a DECLARED context window by capability rather than by HTTP status.
That distinction is the whole point: measured against NVIDIA NIM, a 600,058-token
prompt returned HTTP 200, billed every token, and answered with
`completion_tokens: 1` and empty content. A test that looked for an error would
have reported success at roughly five times the real window.

So the probe puts a passphrase at the very FRONT of a prompt sized to the declared
window and asks for it back. The front is what falls off when the window is
overrun, so recall is evidence the model can genuinely use a context that size,
and silence is evidence it cannot.
"""
from __future__ import annotations

import json
import random
import re

# Mid-range of the 3.71-4.40 chars/token that Part 1 measured against real
# responses on the configured provider. Sizing the filler is unavoidably an
# estimate — under-filling produces a false pass, over-filling a false fail —
# which is why run_context_test reports the provider's OWN prompt_tokens next to
# the verdict wherever the transport supplies it. This number sizes the request;
# that number is what the user is shown.
PROBE_CHARS_PER_TOKEN = 4.0

# Ordinary words, not hex: a recall target like "9f3a71c2" invites tokenizer
# mangling and would produce a wrong verdict for a reason that has nothing to do
# with the context window. 48^3 = 110,592 combinations is far more than enough to
# stop a cached or echoed response from faking a pass.
_WORDS = (
    "velvet", "harbor", "quasar", "lantern", "cobalt", "meadow", "cinder", "harrow",
    "pewter", "willow", "basalt", "kestrel", "marlin", "nimbus", "orchard", "plover",
    "quarry", "ribbon", "saffron", "tundra", "umber", "verdant", "walnut", "yonder",
    "zephyr", "amber", "bramble", "citrine", "dapple", "ember", "fathom", "granite",
    "hollow", "indigo", "juniper", "kindle", "lichen", "mortar", "nectar", "opaline",
    "pumice", "quiver", "russet", "sable", "thicket", "upland", "vellum", "wicker",
)

_SYSTEM = (
    "You are checking whether a long document fits in your context window. "
    "The document begins with a line reading PASSPHRASE: followed by three "
    "hyphenated words. Reply with those three words and nothing else. If you "
    "cannot see that line, reply exactly: NOT VISIBLE."
)

# Filler that is cheap to build and hard to skim: a model cannot infer the
# passphrase from the body, so recall really does require having read the front.
_FILLER_LINE = (
    "{n:07d} reference record — inventory checksum, no semantic content, "
    "retained for context-length measurement only.\n"
)

# Room for the system prompt, the JSON envelope, the chat template's own tokens
# and the answer. Without it a probe sized exactly to the window would overrun it
# by construction and fail every time.
_HEADROOM_TOKENS = 2048


def new_passphrase(rng: random.Random | None = None) -> str:
    """Three hyphenated words, fresh per test run."""
    source = rng or random.Random()
    return "-".join(source.sample(_WORDS, 3))


def build_probe(
    window_tokens: int, passphrase: str
) -> tuple[str, dict[str, object]]:
    """(system_instructions, user_payload) for a prompt of the declared size."""
    budget_tokens = max(256, window_tokens - _HEADROOM_TOKENS)
    target_chars = int(budget_tokens * PROBE_CHARS_PER_TOKEN)
    head = f"PASSPHRASE: {passphrase}\n\n"
    parts = [head]
    filled = len(head)
    n = 0
    while filled < target_chars:
        line = _FILLER_LINE.format(n=n)
        parts.append(line)
        filled += len(line)
        n += 1
    document = "".join(parts)[:target_chars]
    return _SYSTEM, {"document": document, "question": "What is the passphrase?"}


def estimated_prompt_tokens(
    system_instructions: str, user_payload: dict[str, object]
) -> int:
    """Our own estimate of what this probe will cost, in the same units the filler
    was built in. Reported only when the transport gives us nothing better."""
    total_chars = len(system_instructions) + len(json.dumps(user_payload))
    return int(total_chars / PROBE_CHARS_PER_TOKEN)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def recalled(answer: str, passphrase: str) -> bool:
    """Did the model read the front of the prompt?

    Hyphen- and case-insensitive, because a model that reproduces the words has
    demonstrated recall whether or not it copies the punctuation. An empty answer
    is a definite FAIL, not an inconclusive result — that is precisely the shape
    of NIM's over-long-prompt response.
    """
    if not answer.strip():
        return False
    return _normalize(passphrase) in _normalize(answer)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_context_probe.py
```

Expected: PASS, all ten.

- [ ] **Step 5: Lint and type-check**

```bash
cd services/agentd-py && source .venv/bin/activate
ruff check agentd/providers/context_probe.py && mypy agentd/providers/context_probe.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/providers/context_probe.py services/agentd-py/tests/test_context_probe.py
git commit -m "feat(providers): pure passphrase probe for a declared context window"
```

---

### Task 5: The context test runner and its route

**Files:**
- Modify: `services/agentd-py/agentd/providers/context_probe.py` (append the runner)
- Modify: `services/agentd-py/agentd/providers/openai_compatible_transport.py` (`generate_text`, line 808)
- Modify: `services/agentd-py/agentd/api/routes.py` (new request model + route after `validate_provider`, line ~294)
- Test: `services/agentd-py/tests/test_context_probe.py` (append)

**Interfaces:**
- Consumes: `build_probe`, `new_passphrase`, `recalled`, `estimated_prompt_tokens` from Task 4.
- Produces:
  - `ContextTestResult(ok: bool, recalled: bool, prompt_tokens: int | None, exact: bool, error: str | None)`
  - `async run_context_test(*, backend, model, credentials, window_tokens, timeout_sec) -> ContextTestResult`
  - `POST /v1/providers/context-test` always returning 200.
  - `OpenAICompatibleTransport.generate_text(..., on_usage=None)`.
  - Task 6 maps the route payload in the editor-client.

**Context for the implementer:** mirror `POST /v1/providers/validate` exactly — always 200, `ok` is the signal, the provider's own error message is rendered verbatim by the UI, credentials are request-scoped and never persisted or logged. The one genuine addition is `on_usage` on `generate_text`: Part 1 put `on_usage` on `generate_json` and its streaming internals, but `generate_text`'s non-streaming path returns a response object carrying `.usage` and simply drops it. Reading it here is what makes the verdict auditable — the user sees the real token count next to the pass/fail, so a false pass caused by under-filling is visible rather than invisible. Gate the kwarg on `supports_token_progress`, exactly as `reasoning/engine.py` lines 314-330 already do; the other eight transports never see it.

`CRUCIBLE_CONTEXT_TEST_TIMEOUT_SEC` defaults to `300` — a full-window upload is multi-megabyte, and the spec says it may take a minute.

- [ ] **Step 1: Write the failing test**

Append to `services/agentd-py/tests/test_context_probe.py`. **Move every `import` shown below into the file's existing top-of-file import block** rather than leaving them mid-file — ruff's E402 flags module-level imports after code, and the appended blocks below are written as they read, not as they should be placed:

```python
import pytest

import agentd.providers.context_probe as probe_mod
from agentd.providers.context_probe import run_context_test


class _RecallingTransport:
    """Answers with whatever passphrase it was actually shown."""

    supports_token_progress = True

    def __init__(self, *, prompt_tokens: int | None = 31_500) -> None:
        self.prompt_tokens = prompt_tokens
        self.seen_chars = 0

    async def generate_text(
        self, *, model, system_instructions, user_payload, on_usage=None, **_kw
    ):
        document = str(user_payload["document"])
        self.seen_chars = len(document)
        if on_usage is not None and self.prompt_tokens is not None:
            on_usage(self.prompt_tokens, 8)
        return document.split("\n")[0].removeprefix("PASSPHRASE: ")


class _AmnesiacTransport:
    """The NIM shape: HTTP 200, empty content, no complaint."""

    supports_token_progress = False

    async def generate_text(self, *, model, system_instructions, user_payload, **_kw):
        return ""


class _FailingTransport:
    async def generate_text(self, *, model, system_instructions, user_payload, **_kw):
        raise RuntimeError("NIM API error: 400 context length exceeded")


@pytest.mark.asyncio
async def test_recall_passes_and_reports_the_providers_own_count(monkeypatch):
    transport = _RecallingTransport()
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: transport
    )
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None, window_tokens=32_768
    )
    assert result.ok is True and result.recalled is True
    assert result.prompt_tokens == 31_500 and result.exact is True
    assert result.error is None


@pytest.mark.asyncio
async def test_empty_answer_is_a_failed_window_not_an_error(monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _AmnesiacTransport()
    )
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None, window_tokens=600_000
    )
    assert result.ok is True  # the CALL succeeded
    assert result.recalled is False  # the WINDOW did not
    assert result.error is None
    assert result.exact is False and result.prompt_tokens is not None  # our estimate


@pytest.mark.asyncio
async def test_provider_error_is_surfaced_verbatim(monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _FailingTransport()
    )
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None, window_tokens=32_768
    )
    assert result.ok is False and result.recalled is False
    assert "context length exceeded" in (result.error or "")


@pytest.mark.asyncio
async def test_timeout_is_reported_as_a_timeout(monkeypatch):
    import asyncio

    class _Slow:
        async def generate_text(self, **_kw):
            await asyncio.sleep(5)
            return "never"

    monkeypatch.setattr(probe_mod, "build_transport", lambda b, credentials=None: _Slow())
    result = await run_context_test(
        backend="openai_compatible", model="m", credentials=None,
        window_tokens=32_768, timeout_sec=0.05,
    )
    assert result.ok is False and "did not respond" in (result.error or "")


@pytest.mark.asyncio
async def test_transport_without_token_progress_never_sees_on_usage(monkeypatch):
    """Gated exactly like reasoning/engine.py: the other eight transports must not
    receive a kwarg they do not declare."""
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _AmnesiacTransport()
    )
    result = await run_context_test(
        backend="gemini", model="m", credentials=None, window_tokens=32_768
    )
    assert result.ok is True  # no TypeError from an unexpected kwarg
```

And a route test in the same file:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.routes import build_router
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


def _route_client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_router(
            InMemoryTaskStore(), object(),
            ShadowWorkspaceManager(tmp_path / "shadows"), None, None,
        )
    )
    return TestClient(app)


def test_context_test_route_is_always_200(tmp_path, monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _FailingTransport()
    )
    response = _route_client(tmp_path).post(
        "/v1/providers/context-test",
        json={"backend": "openai_compatible", "model": "m", "context_window": 32768},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False and "context length exceeded" in body["error"]


def test_context_test_route_returns_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(
        probe_mod, "build_transport", lambda b, credentials=None: _RecallingTransport()
    )
    body = _route_client(tmp_path).post(
        "/v1/providers/context-test",
        json={"backend": "openai_compatible", "model": "m", "context_window": 32768},
    ).json()
    assert body == {
        "ok": True, "recalled": True, "prompt_tokens": 31_500, "exact": True,
    }
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_context_probe.py
```

Expected: FAIL — `ImportError: cannot import name 'run_context_test'`.

- [ ] **Step 3: Append the runner to context_probe.py**

Add these imports at the top of `agentd/providers/context_probe.py`:

```python
import asyncio
import os
from dataclasses import dataclass

from agentd.providers.factory import build_transport, resolve_model
```

and append at the end of the file:

```python
@dataclass(frozen=True)
class ContextTestResult:
    """What one context test learned.

    `ok` and `recalled` answer different questions, and conflating them is the
    failure this whole module exists to avoid: `ok` is "the call completed",
    `recalled` is "the model could actually use a context that size". The NIM
    measurement in this module's docstring is ok=True, recalled=False.

    `exact` says whether `prompt_tokens` came from the provider or from our own
    chars-per-token estimate, so the UI can label it honestly rather than
    presenting a guess as a measurement.
    """

    ok: bool
    recalled: bool
    prompt_tokens: int | None = None
    exact: bool = False
    error: str | None = None


def context_test_timeout_sec() -> float:
    try:
        return float(os.getenv("CRUCIBLE_CONTEXT_TEST_TIMEOUT_SEC", "300"))
    except ValueError:
        return 300.0


async def run_context_test(
    *,
    backend: str,
    model: str | None,
    credentials: dict[str, str] | None,
    window_tokens: int,
    timeout_sec: float | None = None,
) -> ContextTestResult:
    """Send one prompt of the declared size and judge by passphrase recall.

    Deliberately expensive: it consumes approximately one full window of input
    tokens in a single request. It is opt-in from the UI and is never run as part
    of a save.
    """
    limit = timeout_sec if timeout_sec is not None else context_test_timeout_sec()
    try:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
    except Exception as exc:
        # Same reasoning as ping_provider's: a transport that refuses to construct
        # carries the actionable message ("…_BASE_URL is required"), and that is
        # what the user should read — not a stack trace.
        return ContextTestResult(ok=False, recalled=False, error=str(exc))

    passphrase = new_passphrase()
    system, payload = build_probe(window_tokens, passphrase)

    observed: list[int] = []
    kwargs: dict[str, object] = {}
    # Gated exactly as reasoning/engine.py gates its progress callbacks: the eight
    # transports that do not declare this capability must never see the kwarg.
    if getattr(transport, "supports_token_progress", False):
        kwargs["on_usage"] = lambda prompt_tokens, _completion: observed.append(
            prompt_tokens
        )

    try:
        answer = await asyncio.wait_for(
            transport.generate_text(  # type: ignore[attr-defined]
                model=resolved,
                system_instructions=system,
                user_payload=payload,
                **kwargs,
            ),
            timeout=limit,
        )
    except TimeoutError:
        return ContextTestResult(
            ok=False, recalled=False,
            error=f"Provider did not respond within {limit:.0f}s",
        )
    except Exception as exc:  # surface the provider's own message — it names the fix
        return ContextTestResult(ok=False, recalled=False, error=str(exc))

    return ContextTestResult(
        ok=True,
        recalled=recalled(answer, passphrase),
        prompt_tokens=observed[-1] if observed else estimated_prompt_tokens(system, payload),
        exact=bool(observed),
    )
```

- [ ] **Step 4: Let generate_text report usage**

In `agentd/providers/openai_compatible_transport.py`, change the `generate_text` signature (line 808) and its non-streaming return:

```python
    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
        on_thinking: object = None,
        on_usage: Any = None,
    ) -> str:
```

Then replace the streaming branch and the `try` block at the end of the method:

```python
        if callable(on_thinking):
            return await self._stream_with_thinking(
                create_kwargs, on_thinking=on_thinking, on_usage=on_usage
            )

        try:
            response = await self._call_with_retry(create_kwargs)
            # The non-streaming response already carries usage; it was simply being
            # dropped. Reading it is what lets the context test print the provider's
            # OWN prompt_tokens beside its verdict instead of only our estimate.
            if on_usage is not None:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    on_usage(
                        int(getattr(usage, "prompt_tokens", 0) or 0),
                        int(getattr(usage, "completion_tokens", 0) or 0),
                    )
            return self._extract_text(response)
        except Exception as e:
            raise RuntimeError(f"{self._label} API error: {e}") from e
```

And widen `_stream_with_thinking` (line 840) to forward it:

```python
    async def _stream_with_thinking(
        self,
        create_kwargs: dict[str, Any],
        *,
        on_thinking: Any,
        on_retry: Any = None,
        on_progress: Any = None,
        on_usage: Any = None,
    ) -> str:
        """Text-only view of _stream_with_finish_reason (generate_text's entry
        point, and the long-standing public-ish shape of this method).

        on_progress is forwarded rather than dropped: without it this path could
        never report a count no matter how long the generation ran. on_usage rides
        along for the same reason — the exact count exists, and dropping it here
        would make the caller re-derive an estimate it does not need to."""
        text, _finish_reason = await self._stream_with_finish_reason(
            create_kwargs, on_thinking=on_thinking, on_retry=on_retry,
            on_progress=on_progress, on_usage=on_usage,
        )
        return text
```

- [ ] **Step 5: Add the route**

In `agentd/api/routes.py`, add the request model beside `ProviderValidateRequest` (line 53):

```python
class ProviderContextTestRequest(BaseModel):
    backend: str
    model: str | None = None
    credentials: dict[str, str] = {}
    context_window: int = Field(ge=1024, le=10_000_000)
```

and the route immediately after `validate_provider` (line ~294):

```python
    @router.post("/providers/context-test")
    async def context_test(body: ProviderContextTestRequest) -> dict[str, object]:
        """Verify a DECLARED context window by passphrase recall.

        Always 200, like validate — `ok` says the call completed and `recalled`
        says the window is real. They are separate because a provider can return
        HTTP 200 with an empty answer for an over-long prompt (measured on NVIDIA
        NIM: a 600,058-token prompt, every token billed, completion_tokens: 1).

        Expensive and opt-in: one full window of input tokens per call. Never
        invoked as part of a save. Credentials are request-scoped, never persisted.
        """
        from agentd.providers.context_probe import run_context_test

        result = await run_context_test(
            backend=body.backend,
            model=body.model,
            credentials=body.credentials or None,
            window_tokens=body.context_window,
        )
        # Omit-when-nothing-to-say, mirroring validate_provider: an absent key reads
        # unambiguously as "no information" on the client.
        payload: dict[str, object] = {"ok": result.ok, "recalled": result.recalled}
        if result.prompt_tokens is not None:
            payload["prompt_tokens"] = result.prompt_tokens
            payload["exact"] = result.exact
        if result.error is not None:
            payload["error"] = result.error
        return payload
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_context_probe.py tests/test_provider_validate_route.py tests/test_provider_contracts_progress.py
```

Expected: PASS.

- [ ] **Step 7: Lint, type-check, and run the full backend suite**

```bash
cd services/agentd-py && source .venv/bin/activate
ruff check . && mypy agentd/providers/context_probe.py
pytest --color=no > /tmp/part2-task5.txt 2>&1; echo exit=$?; tail -5 /tmp/part2-task5.txt
```

Expected: `exit=0`, and the pass count is at least the 1560 that was green before this branch's Part 2 work started.

- [ ] **Step 8: Commit**

```bash
git add services/agentd-py/agentd/providers/context_probe.py services/agentd-py/agentd/providers/openai_compatible_transport.py services/agentd-py/agentd/api/routes.py services/agentd-py/tests/test_context_probe.py
git commit -m "feat(providers): context-window test route judged by passphrase recall"
```

---

### Task 6: The editor-client contract

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`BackendConfigSchema` line 324; `ProviderValidateResultSchema` line 342; `BackendTaskClient` lines 445-462)
- Modify: `apps/editor-client/src/client/http-backend-client.ts` (`getConfig` line 564; `setProvider` line 605)
- Test: `apps/editor-client/test/settings-client.test.ts` (append — this is where the existing `validateProvider`/`setProvider` tests live, NOT `http-backend-client.test.ts`)

**Interfaces:**
- Consumes: the route shapes from Tasks 3 and 5.
- Produces:
  - `BackendConfig["provider"]` gains `contextWindow: number | null | undefined`
  - `ContextTestResultSchema` / `ContextTestResult` = `{ ok, recalled, promptTokens?, exact?, error? }`
  - `setProvider(req: { backend; model?; credentials?; contextWindow?: number })`
  - `testContextWindow(req: { backend; model?; credentials?; contextWindow: number }): Promise<ContextTestResult>`
  - Task 7 calls both from `SettingsDeps`.

**Context for the implementer:** this package is the single source of truth for API shapes; the extension types off its compiled `dist/`, so it must be rebuilt before any extension typecheck. Routes speak snake_case and the client maps to camelCase — `context_window` → `contextWindow`, `prompt_tokens` → `promptTokens`. Follow `validateProvider`'s existing pattern for keys the route omits: spread the raw object and add the mapped key only when present, so an absent field stays absent rather than becoming an explicit `undefined`.

- [ ] **Step 1: Write the failing test**

Append to `apps/editor-client/test/settings-client.test.ts`, **reusing that file's existing `clientWith(responseBody, sent)` helper** — the constructor takes an options object (`new HttpBackendClient({ baseUrl, fetchFn })`), not positional arguments, and `clientWith` already records `{url, method, body}` for every request:

```typescript
  test("getConfig maps context_window to contextWindow", async () => {
    const res = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true,
      memory_enabled: true, skills_enabled: true, mcp_enabled: true,
      provider: { backend: "openai", model: "gpt-5", context_window: 200000 },
    }).getConfig();
    expect(res.provider?.contextWindow).toBe(200000);
  });

  test("getConfig tolerates a backend that reports no context_window", async () => {
    const res = await clientWith({
      task_subsystem_enabled: false, chat_controller_enabled: true,
      memory_enabled: true, skills_enabled: true, mcp_enabled: true,
      provider: { backend: "openai", model: "gpt-5" },
    }).getConfig();
    expect(res.provider?.contextWindow).toBeUndefined();
  });

  test("setProvider sends contextWindow as context_window", async () => {
    const sent: Sent[] = [];
    await clientWith({ ok: true, backend: "groq", model: "m2", context_window: 32768 }, sent)
      .setProvider({ backend: "groq", model: "m2", contextWindow: 32768 });
    expect((sent[0].body as { context_window: number }).context_window).toBe(32768);
  });

  test("setProvider omits context_window entirely when not supplied", async () => {
    /* Absent means "leave the window alone" on the route — a null would read as a
       value and is not the same thing. */
    const sent: Sent[] = [];
    await clientWith({ ok: true, backend: "groq", model: "m2" }, sent)
      .setProvider({ backend: "groq", model: "m2" });
    expect("context_window" in (sent[0].body as object)).toBe(false);
  });

  test("testContextWindow posts to the route and maps prompt_tokens", async () => {
    const sent: Sent[] = [];
    const res = await clientWith(
      { ok: true, recalled: false, prompt_tokens: 141234, exact: true }, sent,
    ).testContextWindow({ backend: "openai_compatible", contextWindow: 128000 });
    expect(sent[0].method).toBe("POST");
    expect(sent[0].url).toContain("/v1/providers/context-test");
    expect((sent[0].body as { context_window: number }).context_window).toBe(128000);
    expect(res).toEqual({ ok: true, recalled: false, promptTokens: 141234, exact: true });
  });

  test("testContextWindow keeps ok and recalled distinct on a failed call", async () => {
    const res = await clientWith({ ok: false, recalled: false, error: "429 rate limited" })
      .testContextWindow({ backend: "groq", contextWindow: 128000 });
    expect(res).toEqual({ ok: false, recalled: false, error: "429 rate limited" });
  });
```

Add these inside the existing `describe("settings client methods", ...)` block so they pick up the `clientWith` helper and the `Sent` interface already defined at the top of the file.

- [ ] **Step 2: Run the test to verify it fails**

```bash
npm run -w @crucible/editor-client test
```

Expected: FAIL — `client.testContextWindow is not a function`.

- [ ] **Step 3: Extend the contracts**

In `apps/editor-client/src/contracts/task-contracts.ts`, replace the `provider` line of `BackendConfigSchema`:

```typescript
  // Current reasoning provider (null when the backend runs scripted / pre-P4).
  // contextWindow is the window compaction is actually using right now — seeded
  // from CRUCIBLE_MEMORY_WINDOW_TOKENS at startup, overwritten by a settings save.
  provider: z.object({
    backend: z.string(),
    model: z.string(),
    contextWindow: z.number().nullable().optional(),
  }).nullable().optional(),
```

and add, after `ProviderValidateResultSchema`:

```typescript
// The context-window Test button's verdict. `ok` and `recalled` are separate on
// purpose: a provider can return HTTP 200 with an empty answer for an over-long
// prompt, so ok=true/recalled=false is the "your window is too big" case, not an
// error. `exact` says whether promptTokens came from the provider or from the
// backend's chars-per-token estimate.
export const ContextTestResultSchema = z.object({
  ok: z.boolean(),
  recalled: z.boolean(),
  promptTokens: z.number().optional(),
  exact: z.boolean().optional(),
  error: z.string().optional(),
});
export type ContextTestResult = z.infer<typeof ContextTestResultSchema>;
```

Then update the `BackendTaskClient` interface (line 457):

```typescript
  setProvider(req: { backend: string; model?: string; credentials?: Record<string, string>; contextWindow?: number }): Promise<{ backend: string; model: string }>;
  testContextWindow(req: { backend: string; model?: string; credentials?: Record<string, string>; contextWindow: number }): Promise<ContextTestResult>;
```

- [ ] **Step 4: Implement the client methods**

In `apps/editor-client/src/client/http-backend-client.ts`, in `getConfig`, replace the `provider` line:

```typescript
      provider: HttpBackendClient.mapProvider(raw["provider"]),
```

and add the static helper next to the existing `mapMcpList`:

```typescript
  private static mapProvider(raw: unknown): unknown {
    if (raw === null || typeof raw !== "object") return null;
    const p = raw as Record<string, unknown>;
    return {
      backend: p["backend"],
      model: p["model"],
      // Absent on an older backend; null when the process has no window configured.
      ...(p["context_window"] !== undefined ? { contextWindow: p["context_window"] } : {}),
    };
  }
```

Replace `setProvider`:

```typescript
  async setProvider(req: {
    backend: string;
    model?: string;
    credentials?: Record<string, string>;
    contextWindow?: number;
  }): Promise<{ backend: string; model: string }> {
    const raw = await this.fetchJson("/v1/config/provider", {
      method: "PUT",
      body: JSON.stringify({
        backend: req.backend,
        model: req.model ?? null,
        credentials: req.credentials ?? {},
        // Omitted, not nulled, when the caller has nothing to say: the route reads
        // absent as "leave the window alone", which is what a model-only hot-swap
        // from the composer needs.
        ...(req.contextWindow !== undefined ? { context_window: req.contextWindow } : {}),
      }),
    }) as Record<string, unknown>;
    return { backend: String(raw["backend"]), model: String(raw["model"]) };
  }

  async testContextWindow(req: {
    backend: string;
    model?: string;
    credentials?: Record<string, string>;
    contextWindow: number;
  }): Promise<ContextTestResult> {
    const raw = await this.fetchJson("/v1/providers/context-test", {
      method: "POST",
      body: JSON.stringify({
        backend: req.backend,
        model: req.model ?? null,
        credentials: req.credentials ?? {},
        context_window: req.contextWindow,
      }),
    }) as Record<string, unknown>;
    return ContextTestResultSchema.parse({
      ...raw,
      ...(raw["prompt_tokens"] !== undefined ? { promptTokens: raw["prompt_tokens"] } : {}),
    });
  }
```

Add `ContextTestResult` and `ContextTestResultSchema` to the imports at the top of `http-backend-client.ts`. **Do not touch `apps/editor-client/src/index.ts`** — it is five `export *` lines, so anything exported from `task-contracts.ts` is already public.

- [ ] **Step 5: Run the tests and build**

```bash
npm run -w @crucible/editor-client test
npm run -w @crucible/editor-client build
```

Expected: tests PASS; build succeeds. **The build is required** — the extension types off `dist/index.d.ts`, not source, and Task 7 will produce phantom errors without it.

- [ ] **Step 6: Commit**

```bash
git add apps/editor-client/src apps/editor-client/test
git commit -m "feat(client): context window on the provider contract"
```

---

### Task 7: The extension host plumbs the window and the test

**Files:**
- Modify: `apps/vscode-extension/src/settings-data.ts`
- Modify: `apps/vscode-extension/src/settings-deps.ts`
- Modify: `apps/vscode-extension/src/runtime/vscode-runtime.ts`
- Test: `apps/vscode-extension/test/settings-data.test.ts` (append)

**Interfaces:**
- Consumes: `client.setProvider({contextWindow})` and `client.testContextWindow(...)` from Task 6.
- Produces:
  - `SettingsState.provider` gains `contextWindow?: number | null`
  - in-msgs: `{ type: "settings/setProvider"; …; contextWindow?: number }` and `{ type: "settings/testContextWindow"; backend: string; model: string; contextWindow: number; apiKey?: string; extraCredentials?: Record<string, string> }`
  - out-msg: `{ type: "settings/contextTestResult"; result: { ok: boolean; recalled: boolean; promptTokens?: number; exact?: boolean; error?: string } }`
  - `SettingsDeps.client.testContextWindow`, `SettingsDeps.saveContextWindow(tokens: number): Promise<void>`
  - `RuntimeManager.saveContextWindow(tokens)` / `RuntimeManager.contextWindow()`
  - Task 8 reads the stored value; Tasks 9-10 send and render these messages.

**Context for the implementer:** the handler's discipline is that every mutating action ends with a rebuilt `SettingsState` snapshot (`postState()`), and errors go out as `settings/error`. The context test breaks that pattern in exactly one way and for a reason: its result is *not* part of the settings snapshot — it is a one-off verdict about a value, not a change to one — so it gets its own out-message and does **not** call `postState()`. Note also that a failed test must not be reported as `settings/error`: the route returns 200 with `ok:false`, and the panel renders the provider's message inline next to the field, not as the panel-wide red banner. Only a thrown exception (backend unreachable) reaches the catch-all.

Persisting the window is a separate write from `saveProvider`, mirroring how `storeProviderKey` is separate: the panel's `setProvider` wrapper already persists backend/model, and the window travels with the same save but is stored under its own key so a composer model-swap never disturbs it.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/test/settings-data.test.ts`:

```typescript
describe("context window", () => {
  it("forwards contextWindow to setProvider and persists it", async () => {
    const setProvider = vi.fn(async () => ({ backend: "groq", model: "m2" }));
    const saveContextWindow = vi.fn(async () => {});
    const d = deps({ saveContextWindow });
    d.client.setProvider = setProvider;
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/setProvider", backend: "groq", model: "m2", contextWindow: 32768,
    });
    expect(setProvider).toHaveBeenCalledWith(
      expect.objectContaining({ contextWindow: 32768 }),
    );
    expect(saveContextWindow).toHaveBeenCalledWith(32768);
  });

  it("does not persist the window when validation fails", async () => {
    const saveContextWindow = vi.fn(async () => {});
    const d = deps({ saveContextWindow });
    d.client.validateProvider = async () => ({ ok: false, error: "bad key" });
    const handle = createSettingsHandler(d, () => {});
    await handle({
      type: "settings/setProvider", backend: "groq", model: "m2", contextWindow: 32768,
    });
    expect(saveContextWindow).not.toHaveBeenCalled();
  });

  it("posts the context-test verdict on its own message", async () => {
    const d = deps();
    d.client.testContextWindow = async () => ({
      ok: true, recalled: false, promptTokens: 141234, exact: true,
    });
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/testContextWindow", backend: "groq", model: "m2", contextWindow: 128000,
    });
    const verdicts = posted.filter((m) => m.type === "settings/contextTestResult");
    expect(verdicts).toHaveLength(1);
    expect(verdicts[0]).toMatchObject({ result: { recalled: false, promptTokens: 141234 } });
  });

  it("reports a failed test on the verdict message, not the error banner", async () => {
    /* The route returns 200 with ok:false — that belongs next to the field, not in
       the panel-wide red banner reserved for things that actually broke. */
    const d = deps();
    d.client.testContextWindow = async () => ({ ok: false, recalled: false, error: "429 rate limited" });
    const posted: SettingsOutMsg[] = [];
    const handle = createSettingsHandler(d, (m) => posted.push(m));
    await handle({
      type: "settings/testContextWindow", backend: "groq", model: "m2", contextWindow: 128000,
    });
    expect(posted.some((m) => m.type === "settings/error")).toBe(false);
    expect(posted.find((m) => m.type === "settings/contextTestResult")).toMatchObject({
      result: { ok: false, error: "429 rate limited" },
    });
  });
});
```

Also extend the shared `deps()` factory in that file: add `testContextWindow: async () => ({ ok: true, recalled: true })` to the `client` object and `saveContextWindow: async () => {}` at the top level.

- [ ] **Step 2: Run the test to verify it fails**

```bash
npm run -w crucible-vscode-extension test
```

Expected: FAIL — `saveContextWindow` is not called and no `settings/contextTestResult` is posted.

- [ ] **Step 3: Extend the message protocol and deps in settings-data.ts**

Change `SettingsState.provider`:

```typescript
export interface SettingsState {
  // contextWindow is the window compaction is using right now, read back from
  // GET /v1/config so the field shows what the process actually has, not what the
  // panel last sent.
  provider: { backend: string; model: string; contextWindow?: number | null } | null;
```

Add to `SettingsInMsg`:

```typescript
  | { type: "settings/setProvider"; backend: string; model: string; apiKey?: string; extraCredentials?: Record<string, string>; contextWindow?: number }
  // Opt-in, expensive (~one full window of input tokens per call), never part of
  // a save. Carries the same credentials as a save so the user can test an
  // endpoint before committing to it.
  | { type: "settings/testContextWindow"; backend: string; model: string; contextWindow: number; apiKey?: string; extraCredentials?: Record<string, string> }
```

Add to `SettingsOutMsg`:

```typescript
  // Deliberately NOT folded into settings/state: a verdict about a value is not a
  // change to one, and it must not survive the next snapshot rebuild.
  | { type: "settings/contextTestResult"; result: { ok: boolean; recalled: boolean; promptTokens?: number; exact?: boolean; error?: string } }
```

Add to `SettingsDeps.client`:

```typescript
    testContextWindow(req: {
      backend: string;
      model?: string;
      credentials?: Record<string, string>;
      contextWindow: number;
    }): Promise<{ ok: boolean; recalled: boolean; promptTokens?: number | undefined; exact?: boolean | undefined; error?: string | undefined }>;
```

and to `SettingsDeps` itself:

```typescript
  /** Persist the declared context window for the next managed spawn. Separate from
   * saveProvider for the same reason storeSecret is: a composer model hot-swap
   * writes backend/model and must not disturb the window. */
  saveContextWindow(tokens: number): Promise<void>;
```

- [ ] **Step 4: Handle the two messages**

In `createSettingsHandler`, inside `case "settings/setProvider"`, after the existing `storeExtraCredentials` block and before `deps.client.setProvider`, nothing changes; extend the `setProvider` call and add the persistence:

```typescript
          await deps.client.setProvider({
            backend: msg.backend,
            model: msg.model,
            ...(credentials ? { credentials } : {}),
            ...(msg.contextWindow !== undefined ? { contextWindow: msg.contextWindow } : {}),
          });
          // After the hot-swap succeeds, so a rejected provider never leaves a
          // stale window persisted for the next managed spawn.
          if (msg.contextWindow !== undefined) {
            await deps.saveContextWindow(msg.contextWindow);
          }
          await postState();
          return;
```

Add a new case after `settings/clearProviderKey`:

```typescript
        case "settings/testContextWindow": {
          const envVar = deps.keyEnvVar(msg.backend);
          const primaryCred = envVar && msg.apiKey ? { [envVar]: msg.apiKey } : undefined;
          const credentials = (primaryCred || msg.extraCredentials)
            ? { ...primaryCred, ...msg.extraCredentials }
            : undefined;
          const result = await deps.client.testContextWindow({
            backend: msg.backend,
            model: msg.model,
            contextWindow: msg.contextWindow,
            ...(credentials ? { credentials } : {}),
          });
          // No postState(): the verdict is not part of the settings snapshot, and
          // a failed test (ok:false) is a 200 from the route — it belongs beside
          // the field, not in the panel-wide error banner.
          post({ type: "settings/contextTestResult", result });
          return;
        }
```

- [ ] **Step 5: Wire the real deps**

In `apps/vscode-extension/src/settings-deps.ts`, add to the `client` object:

```typescript
      testContextWindow: (req) => client().testContextWindow(req),
```

and at the top level of the returned object:

```typescript
    saveContextWindow: (tokens) => runtimeManager.saveContextWindow(tokens),
```

In `apps/vscode-extension/src/runtime/vscode-runtime.ts`, add next to `saveProvider`:

```typescript
  /** The declared context window, in tokens. Stored under its own key rather than
   * inside the provider record so a model-only hot-swap (composer model menu)
   * cannot clear it. Read back into the spawn env by getProviderSettings. */
  contextWindow(): number | undefined {
    return this.context.globalState.get<number>("crucible.provider.contextWindow");
  }

  async saveContextWindow(tokens: number): Promise<void> {
    await this.context.globalState.update("crucible.provider.contextWindow", tokens);
  }
```

and in `getProviderSettings`, after the `settings` object is built (before the `envVar` lookup):

```typescript
    const contextWindow = this.contextWindow();
    if (contextWindow !== undefined) settings.contextWindow = contextWindow;
```

- [ ] **Step 6: Run the tests**

```bash
npm run -w crucible-vscode-extension test
npm run -w crucible-vscode-extension typecheck
```

Expected: PASS. `settings.contextWindow` will not typecheck until Task 8 widens `BackendSettings` — if that error appears here, do Task 8's Step 3 now and note it; do not add a cast.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/src/settings-data.ts apps/vscode-extension/src/settings-deps.ts apps/vscode-extension/src/runtime/vscode-runtime.ts apps/vscode-extension/test/settings-data.test.ts
git commit -m "feat(extension): plumb the context window and its test through settings"
```

---

### Task 8: The managed spawn carries the declared window

**Files:**
- Modify: `apps/vscode-extension/src/runtime/backend-process.ts` (`BackendSettings` line 14; `buildBackendEnv` line 49)
- Test: `apps/vscode-extension/test/runtime-backend-process.test.ts` (append)

**Interfaces:**
- Consumes: `RuntimeManager.contextWindow()` from Task 7.
- Produces: `BackendSettings.contextWindow?: number`, mapped to `CRUCIBLE_MEMORY_WINDOW_TOKENS` in the spawn env.

**Context for the implementer:** this is what makes the spec's resolution order hold at startup rather than only after a hot-swap. The extension writes the user's declared window into `CRUCIBLE_MEMORY_WINDOW_TOKENS`, `MemoryConfig.from_env` reads it, and `ProviderRuntime` is seeded from it (Task 2) — so "provider window > env var > 128000 default" collapses to a single value the backend never has to arbitrate. A user who set only the env var and never opens the panel keeps exactly today's behaviour, because `settings.contextWindow` is then `undefined` and nothing is written.

Note `buildBackendEnv` ends with `{ ...built, ...settings.extraEnv }`, so anything a user sets through the VS Code settings `extraEnv` path still wins over the built-ins — that ordering is deliberate and must not change.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/test/runtime-backend-process.test.ts` (match the file's existing call shape for `buildBackendEnv`):

```typescript
describe("context window in the spawn env", () => {
  it("writes CRUCIBLE_MEMORY_WINDOW_TOKENS when a window is declared", () => {
    const env = buildBackendEnv(
      "/ws",
      { backend: "groq", model: "m", contextWindow: 32768 },
      "/rt", 8123, "darwin-arm64",
    );
    expect(env.CRUCIBLE_MEMORY_WINDOW_TOKENS).toBe("32768");
  });

  it("omits it entirely when none is declared", () => {
    /* A deployment that sets only CRUCIBLE_MEMORY_WINDOW_TOKENS by hand must keep
       working unchanged — writing a default here would silently override it. */
    const env = buildBackendEnv(
      "/ws", { backend: "groq", model: "m" }, "/rt", 8123, "darwin-arm64",
    );
    expect("CRUCIBLE_MEMORY_WINDOW_TOKENS" in env).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
npm run -w crucible-vscode-extension test
```

Expected: FAIL — `Object literal may only specify known properties` on `contextWindow`, and the env key is absent.

- [ ] **Step 3: Widen BackendSettings and buildBackendEnv**

In `apps/vscode-extension/src/runtime/backend-process.ts`:

```typescript
export interface BackendSettings {
  backend: string;                     // "gemini" | "openai" | ... (never "scripted")
  model: string;
  apiKey?: { envVar: string; value: string };   // from SecretStorage, spawn-env only
  extraEnv?: Record<string, string>;   // policies/flags from VS Code settings
  skillsDisabled?: string[];           // → CRUCIBLE_SKILLS_DISABLED (comma-joined)
  contextWindow?: number;              // → CRUCIBLE_MEMORY_WINDOW_TOKENS
}
```

and in `buildBackendEnv`, after the `skillsDisabled` block:

```typescript
  // The declared context window from the settings panel. Written into the SAME env
  // var a hand-configured deployment uses, which is what collapses the spec's
  // "provider window > env var > 128000" order into one value the backend never
  // has to arbitrate. Absent means absent: a user who set only the env var and
  // never opened the panel keeps exactly today's behaviour.
  if (settings.contextWindow !== undefined) {
    built.CRUCIBLE_MEMORY_WINDOW_TOKENS = String(settings.contextWindow);
  }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npm run -w crucible-vscode-extension test
npm run -w crucible-vscode-extension typecheck
```

Expected: PASS and clean.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/runtime/backend-process.ts apps/vscode-extension/test/runtime-backend-process.test.ts
git commit -m "feat(runtime): carry the declared context window into the spawn env"
```

---

### Task 9: The settings field and its starter table

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/contextWindows.ts`
- Create: `apps/vscode-extension/webview-ui/src/settings/contextWindows.test.ts`
- Modify: `apps/vscode-extension/webview-ui/src/settings/types.ts`
- Modify: `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx` (append)

**Interfaces:**
- Consumes: the message protocol from Task 7 (mirrored locally — the webview is a separate Vite bundle and never imports the extension's `src/`).
- Produces: `defaultContextWindow(model: string): number` and `DEFAULT_CONTEXT_WINDOW: number`; the Provider section sends `contextWindow` with `settings/setProvider`. Task 10 adds the Test button beside the same field.

**Context for the implementer — read this before writing the table.** The window is a *declared* value. The spec's non-goals rule out detecting it, so this table exists only to pre-fill a field the user is expected to correct. Two rules follow, and they are not negotiable:

1. **Every entry must be verifiable against the vendor's own model card or API documentation before it is committed.** A wrong entry is worse than an obvious default, because a user who sees a plausible number stops looking.
2. **Where the true value is uncertain, declare the SMALLER number.** The spec's error asymmetry says so directly: too small merely wastes a summarization call, while too large can make the agent silently produce nothing.

The table is matched by lowercase substring, mirroring `_is_reasoning_model` in `openai_compatible_transport.py` (which is `any(x in model.lower() for x in (...))`). Order matters — first match wins — so put more specific keys before more general ones.

- [ ] **Step 1: Write the failing test**

Create `apps/vscode-extension/webview-ui/src/settings/contextWindows.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { DEFAULT_CONTEXT_WINDOW, defaultContextWindow } from "./contextWindows";

describe("defaultContextWindow", () => {
  it("matches on a lowercase substring, like _is_reasoning_model does", () => {
    expect(defaultContextWindow("claude-3-5-sonnet-latest")).toBe(200_000);
    expect(defaultContextWindow("CLAUDE-3-5-SONNET-LATEST")).toBe(200_000);
  });

  it("falls back to the default for anything unrecognised", () => {
    expect(defaultContextWindow("some-model-nobody-has-heard-of")).toBe(DEFAULT_CONTEXT_WINDOW);
    expect(defaultContextWindow("")).toBe(DEFAULT_CONTEXT_WINDOW);
  });

  it("defaults to 128000, matching CRUCIBLE_MEMORY_WINDOW_TOKENS", () => {
    expect(DEFAULT_CONTEXT_WINDOW).toBe(128_000);
  });

  it("returns the first matching entry so specific keys can precede general ones", () => {
    // Both "qwen3" and a hypothetical longer key could match; the table is ordered.
    expect(defaultContextWindow("qwen3.6:35b-a3b-q4_K_M")).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/vscode-extension/webview-ui && npx vitest run src/settings/contextWindows.test.ts
```

Expected: FAIL — cannot resolve `./contextWindows`.

- [ ] **Step 3: Write the table**

Create `apps/vscode-extension/webview-ui/src/settings/contextWindows.ts`:

```typescript
/**
 * Starter context windows, keyed by lowercase model-name substring — the same
 * matching shape as `_is_reasoning_model` in openai_compatible_transport.py.
 *
 * This table PRE-FILLS a field the user is expected to correct. It is not a
 * capability lookup and must never grow into one: NVIDIA NIM's /v1/models returns
 * only id/object/created/owned_by, and NIM accepts a 600k-token prompt with HTTP
 * 200, so there is nothing to detect the real window from.
 *
 * Two rules for editing it:
 *   1. Verify every entry against the vendor's model card before committing it.
 *      A plausible wrong number is worse than an obvious default, because it
 *      stops the user looking.
 *   2. When unsure, declare the SMALLER number. Too small costs one early
 *      summarization call; too large can make the agent silently answer nothing
 *      (measured on NIM: 600,058 tokens in, completion_tokens: 1, no error).
 *
 * First match wins, so specific keys precede general ones.
 */
export const DEFAULT_CONTEXT_WINDOW = 128_000;

export const CONTEXT_WINDOWS: readonly (readonly [string, number])[] = [
  ["claude", 200_000],
  ["gemini", 1_000_000],
  ["gpt-4o", 128_000],
  // Qwen3's native window; deployments that enable YaRN extension should raise
  // this by hand. Erring low per rule 2.
  ["qwen3", 32_768],
];

export function defaultContextWindow(model: string): number {
  const name = model.toLowerCase();
  for (const [key, tokens] of CONTEXT_WINDOWS) {
    if (name.includes(key)) return tokens;
  }
  return DEFAULT_CONTEXT_WINDOW;
}
```

- [ ] **Step 4: Verify every table entry against its model card**

This is a real step, not a formality. For each of the four entries, open the vendor's own documentation and confirm the number. Delete any entry you cannot confirm — the default covers it. Record what you checked in the commit message.

- [ ] **Step 5: Run the table test**

```bash
cd apps/vscode-extension/webview-ui && npx vitest run src/settings/contextWindows.test.ts
```

Expected: PASS.

- [ ] **Step 6: Write the failing UI test**

Append to `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx`:

```typescript
describe("context window field", () => {
  it("prefills from the live backend value, not the table", () => {
    const withWindow: SettingsState = {
      ...state,
      provider: { backend: "gemini", model: "gemini-flash-latest", contextWindow: 262_144 },
    };
    render(<ProviderSection state={withWindow} busy={false} send={vi.fn()} />);
    expect((screen.getByLabelText(/Context window/) as HTMLInputElement).value).toBe("262144");
  });

  it("falls back to the starter table when the backend reports none", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    expect((screen.getByLabelText(/Context window/) as HTMLInputElement).value).toBe("1000000");
  });

  it("re-prefills from the table when the provider changes", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "anthropic" } });
    expect((screen.getByLabelText(/Context window/) as HTMLInputElement).value).toBe("200000");
  });

  it("sends the window with the save", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.change(screen.getByLabelText(/Context window/), { target: { value: "65536" } });
    fireEvent.click(screen.getByRole("button", { name: /Save & validate/ }));
    expect(send.mock.calls[0][0]).toMatchObject({ contextWindow: 65536 });
  });

  it("tells the user where to get the number and what each error costs", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    expect(screen.getByText(/model card/i)).toBeTruthy();
    expect(screen.getByText(/Too small/i)).toBeTruthy();
    expect(screen.getByText(/Too large/i)).toBeTruthy();
  });
});
```

- [ ] **Step 7: Mirror the protocol changes in the webview types**

In `apps/vscode-extension/webview-ui/src/settings/types.ts`, apply the same three changes Task 7 made to `src/settings-data.ts` — `provider.contextWindow?: number | null` on `SettingsState`, `contextWindow?: number` on the `settings/setProvider` in-msg, and the new `settings/testContextWindow` in-msg and `settings/contextTestResult` out-msg. Copy the comments too; this file is a deliberate mirror and drift between the two is the `.min(1)`-class footgun this codebase has been bitten by repeatedly.

- [ ] **Step 8: Add the field to ProviderSection**

In `ProviderSection.tsx`, add the import:

```typescript
import { defaultContextWindow } from "../contextWindows";
```

Add state beside the existing `model` state:

```typescript
  // The live backend value wins over the table: it is what compaction is actually
  // using, and showing the table's guess over the top of it would be a lie.
  const [contextWindow, setContextWindow] = useState(
    String(state.provider?.contextWindow ?? defaultContextWindow(state.provider?.model ?? "")),
  );
```

In the Provider `<select>`'s `onChange`, alongside `setModel(next.defaultModel)`:

```typescript
                setContextWindow(String(defaultContextWindow(next.defaultModel)));
```

Add the field after the Model input:

```tsx
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Context window (tokens)
            <input
              className={FIELD}
              inputMode="numeric"
              value={contextWindow}
              onChange={(e) => setContextWindow(e.target.value.replace(/[^0-9]/g, ""))}
              placeholder="128000"
            />
          </label>
          <p className="text-[11px] leading-relaxed text-text-3">
            Take this from the model's own model card or the provider's
            documentation — it cannot be detected, and a remembered number is
            usually wrong.{" "}
            <strong>Too small</strong> and history is evicted (and a summary paid
            for) while the window is still half empty — wasteful, but safe.{" "}
            <strong>Too large</strong> and the prompt overruns the real window: some
            providers return no error at all, bill every token, and answer with
            nothing. Use Test to confirm.
          </p>
```

In the Save button's `send({...})` call, add:

```typescript
                  ...(Number(contextWindow) > 0 ? { contextWindow: Number(contextWindow) } : {}),
```

- [ ] **Step 9: Run the UI tests**

```bash
cd apps/vscode-extension/webview-ui && npx vitest run src/settings/
```

Expected: PASS, including the three pre-existing ProviderSection tests. The first of those asserts an exact `send` payload (`toHaveBeenCalledWith`) — it will now also carry `contextWindow`, so update that assertion to match the real payload rather than loosening it to `objectContaining`.

- [ ] **Step 10: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings
git commit -m "feat(settings): declare the model's context window in the provider section"
```

---

### Task 10: The Test button

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.tsx`
- Test: `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx` (append)
- Modify: `CLAUDE.md` (env-var list + settings-panel description)

**Interfaces:**
- Consumes: `settings/testContextWindow` and `settings/contextTestResult` from Task 7, `contextWindow` state from Task 9.
- Produces: no new interfaces — this closes the feature.

**Context for the implementer:** two things drive the design here.

First, **never call `window.confirm`, `alert`, or any browser modal from a webview.** They block the message pipeline and can wedge the panel. The spec's "warns before running" requirement is met with a two-step inline control instead: the first click reveals a warning row naming the cost, and a second, differently-labelled click actually runs it. That is also far easier to test than a dialog.

Second, the verdict has three distinct outcomes and they must not be collapsed into pass/fail:

| Outcome | Meaning | Rendering |
|---|---|---|
| `ok && recalled` | The model genuinely used a context that size. | green |
| `ok && !recalled` | The call succeeded but the front of the prompt never reached the model — **the declared window is too large**. This is the silent-failure case the whole feature exists for. | amber, and say the window is too large |
| `!ok` | The call itself failed; `error` is the provider's own message. | red, verbatim |

The result is advisory: it never changes the stored value. The field is whatever the user declared.

- [ ] **Step 1: Write the failing test**

Append to `apps/vscode-extension/webview-ui/src/settings/sections/ProviderSection.test.tsx`:

```typescript
function postResult(result: Record<string, unknown>) {
  window.dispatchEvent(
    new MessageEvent("message", { data: { type: "settings/contextTestResult", result } }),
  );
}

describe("context window Test button", () => {
  it("warns with the cost before running anything", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: /^Test$/ }));
    expect(send).not.toHaveBeenCalled();  // first click only warns
    expect(screen.getByText(/1,000,000/)).toBeTruthy();  // the cost, spelled out
    expect(screen.getByText(/single request/i)).toBeTruthy();
  });

  it("runs only on the second, confirming click", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: /^Test$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Run test/ }));
    expect(send).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "settings/testContextWindow", backend: "gemini", contextWindow: 1_000_000,
      }),
    );
  });

  it("can be cancelled without sending", () => {
    const send = vi.fn();
    render(<ProviderSection state={state} busy={false} send={send} />);
    fireEvent.click(screen.getByRole("button", { name: /^Test$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Run test/ })).toBeNull();
  });

  it("reports recall as a pass, with the real token count", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: true, promptTokens: 998_123, exact: true });
    expect(screen.getByText(/998,123/)).toBeTruthy();
    expect(screen.getByText(/recalled/i)).toBeTruthy();
  });

  it("reports a successful call with no recall as a too-large window", () => {
    /* The silent-failure case: HTTP 200, tokens billed, nothing usable back. */
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: false, promptTokens: 998_123, exact: true });
    expect(screen.getByText(/too large/i)).toBeTruthy();
  });

  it("labels an estimated token count as an estimate", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: true, recalled: true, promptTokens: 990_000, exact: false });
    expect(screen.getByText(/estimated/i)).toBeTruthy();
  });

  it("shows the provider's own error verbatim", () => {
    render(<ProviderSection state={state} busy={false} send={vi.fn()} />);
    postResult({ ok: false, recalled: false, error: "429 Too Many Requests" });
    expect(screen.getByText(/429 Too Many Requests/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/vscode-extension/webview-ui && npx vitest run src/settings/sections/ProviderSection.test.tsx
```

Expected: FAIL — no `Test` button exists.

- [ ] **Step 3: Add the two-step button and the verdict**

In `ProviderSection.tsx`, add state:

```typescript
  // Two-step, not window.confirm: a browser modal blocks the webview's message
  // pipeline and can wedge the panel. The first click reveals the cost, the
  // second runs it.
  const [confirmingTest, setConfirmingTest] = useState(false);
  const [testResult, setTestResult] = useState<
    { ok: boolean; recalled: boolean; promptTokens?: number; exact?: boolean; error?: string } | null
  >(null);
```

Listen for the verdict (the section receives no props for it — it is not part of the settings snapshot, by design):

```typescript
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const msg = event.data;
      if (msg?.type === "settings/contextTestResult") {
        setConfirmingTest(false);
        setTestResult(msg.result);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
```

Clear a stale verdict whenever the number it described changes — in the `contextWindow` input's `onChange` and in the provider `<select>`'s `onChange`, add `setTestResult(null); setConfirmingTest(false);`.

Add the control after the help paragraph from Task 9:

```tsx
          <div className="flex flex-col gap-2">
            {!confirmingTest ? (
              <BtnGhost
                disabled={busy || !(Number(contextWindow) > 0)}
                onClick={() => { setTestResult(null); setConfirmingTest(true); }}
              >
                Test
              </BtnGhost>
            ) : (
              <div className="flex flex-col gap-2 rounded-md border border-border-strong p-2">
                <p className="text-[11px] leading-relaxed text-text-3">
                  <Icon name="warn" size={11} /> This sends about{" "}
                  {Number(contextWindow).toLocaleString()} input tokens in a single
                  request — a multi-megabyte upload that may take a minute, billed
                  in full by metered providers.
                </p>
                <div className="flex items-center gap-2">
                  <BtnPrimary
                    disabled={busy}
                    onClick={() =>
                      send({
                        type: "settings/testContextWindow",
                        backend,
                        model,
                        contextWindow: Number(contextWindow),
                        ...(provider.local || !apiKey ? {} : { apiKey }),
                        ...(extraCredentials ? { extraCredentials } : {}),
                      })
                    }
                  >
                    {busy ? "Testing…" : "Run test"}
                  </BtnPrimary>
                  <BtnGhost disabled={busy} onClick={() => setConfirmingTest(false)}>
                    Cancel
                  </BtnGhost>
                </div>
              </div>
            )}
            {testResult && <TestVerdict result={testResult} />}
          </div>
```

And the verdict renderer, as a small component at the bottom of the same file:

```tsx
/** Three outcomes, deliberately not collapsed into pass/fail: a call that
 * SUCCEEDS without recall is the silent-overflow case this whole feature exists
 * to expose, and it must not read as a green tick. Advisory only — nothing here
 * changes the stored value. */
function TestVerdict({ result }: {
  result: { ok: boolean; recalled: boolean; promptTokens?: number; exact?: boolean; error?: string };
}) {
  const count = result.promptTokens?.toLocaleString();
  const label = result.exact ? "sent" : "sent (estimated)";
  if (!result.ok) {
    return (
      <p className="text-xs" style={{ color: "var(--color-red)" }}>
        ✗ Test failed: {result.error ?? "unknown error"}
      </p>
    );
  }
  if (result.recalled) {
    return (
      <p className="text-xs" style={{ color: "var(--color-green)" }}>
        <Icon name="check" size={11} /> Passphrase recalled
        {count ? ` — ${count} tokens ${label}` : ""}. This window is usable.
      </p>
    );
  }
  return (
    <p className="text-xs leading-relaxed" style={{ color: "var(--color-amber)" }}>
      ⚠ The call succeeded but the passphrase came back wrong or empty
      {count ? ` — ${count} tokens ${label}` : ""}. The front of the prompt is not
      reaching the model, so this window is too large. Lower it and test again.
    </p>
  );
}
```

Add `useEffect` to the React import if it is not already there (it is — the file already uses it for the saved-flash).

- [ ] **Step 4: Run the UI tests**

```bash
cd apps/vscode-extension/webview-ui && npx vitest run src/settings/
```

Expected: PASS.

- [ ] **Step 5: Update CLAUDE.md**

Two edits. In the "Python backend env vars" → memory-harness list, replace the `CRUCIBLE_MEMORY_WINDOW_TOKENS` line:

```markdown
- `CRUCIBLE_MEMORY_WINDOW_TOKENS` — effective context window the fracs are taken against (default `128000`). Normally set for you: the settings panel's Provider section has a **Context window** field whose value the extension persists to `globalState` and injects here on the next managed spawn, and `PUT /v1/config/provider {context_window}` hot-applies it to every live compactor without a restart. A hand-set env var still works and is what a non-managed backend uses.
```

And in the "P4 — Install, managed runtime & settings UI" section, in the Settings-panel paragraph, after the sentence about the Provider section hot-swapping, add:

```markdown
  The Provider section also carries the **Context window** field (Part 2 of the
  exact-context-accounting spec): a DECLARED token count, pre-filled from a small
  substring-keyed starter table (`webview-ui/src/settings/contextWindows.ts`,
  default 128000) and never detected — NIM's `/v1/models` exposes no capability
  data and NIM accepts a 600k-token prompt with HTTP 200. It rides `setProvider`
  to `PUT /v1/config/provider` as `context_window`, which `ProviderRuntime` applies
  to every registered window sink (both memory harnesses) after validation
  succeeds; absent means "leave it alone", so a composer model-only swap never
  clears it. The opt-in **Test** button (`POST /v1/providers/context-test`,
  `agentd/providers/context_probe.py`) sends one prompt of the declared size with a
  passphrase at the FRONT and judges by recall, **not** by HTTP status — the
  measured NIM failure is HTTP 200 with `completion_tokens: 1` and empty content,
  so `ok` (the call completed) and `recalled` (the window is real) are separate
  fields all the way to the UI. `CRUCIBLE_CONTEXT_TEST_TIMEOUT_SEC` (default 300).
```

- [ ] **Step 6: Full verification across all three packages**

```bash
cd services/agentd-py && source .venv/bin/activate && ruff check . && pytest --color=no > /tmp/part2-final.txt 2>&1; echo exit=$?; tail -5 /tmp/part2-final.txt
cd - && npm run -w @crucible/editor-client build && npm run test && npm run typecheck
```

Expected: `exit=0` on pytest with a pass count no lower than before this branch's Part 2 work; TypeScript tests and typecheck clean across all workspaces.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/settings CLAUDE.md
git commit -m "feat(settings): opt-in context-window test judged by passphrase recall"
```

---

## Verifying it live

Unit tests cannot show that a real provider drops the front of an over-long prompt. Run this against a real backend once the tasks are done.

1. Start a backend against a real provider:

```bash
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
bash scripts/stress/start-backend.sh --backend openai_compatible \
  --workspace "$PWD/workspaces/crucible-stress" --validation-profile none
curl -s http://localhost:8000/v1/config | python3 -m json.tool   # provider.context_window present?
```

2. Hot-apply a window and confirm it reached the compactor:

```bash
curl -s -X PUT http://localhost:8000/v1/config/provider \
  -H 'content-type: application/json' \
  -d '{"backend":"openai_compatible","context_window":32768}' | python3 -m json.tool
curl -s http://localhost:8000/v1/config | python3 -m json.tool   # 32768 reported back
```

3. Run the test at a window you believe is right, then at one you know is absurd. The second is the one that matters — it should come back `ok: true, recalled: false`, which is the whole point of judging by capability:

```bash
curl -s -X POST http://localhost:8000/v1/providers/context-test \
  -H 'content-type: application/json' \
  -d '{"backend":"openai_compatible","context_window":32768}' | python3 -m json.tool

curl -s -X POST http://localhost:8000/v1/providers/context-test \
  -H 'content-type: application/json' \
  -d '{"backend":"openai_compatible","context_window":600000}' | python3 -m json.tool
```

Record the `prompt_tokens` each returns. If the reported count is far from the declared window, `PROBE_CHARS_PER_TOKEN` needs revisiting for that provider — note the measurement rather than silently tuning the constant.

4. In the dev host (`npm run build`, then `code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/crucible-stress"`, Developer: Reload Window), open the settings panel: confirm the field pre-fills from the live value, that saving hot-applies without a restart, that the Test button warns before running, and that a deliberately absurd window renders the amber "too large" verdict rather than a green tick.

5. Confirm the spawn path: quit and reopen the dev host so the managed backend restarts, then check the window survived —

```bash
curl -s http://localhost:8000/v1/config | python3 -m json.tool
```

## Known limitations to record, not fix

- **The composer's model menu does not carry a window.** Switching models there leaves the declared window in place, which is correct for a same-family switch and wrong for a cross-family one. The user corrects it in Settings. Widening the composer popover is a follow-on.
- **The setup wizard does not ask for a window.** A fresh install runs at the 128000 default until the user visits Settings. Deliberate: the wizard's job is to get to a working turn, and this value wants the model card open.
- **`fixed_overhead` remains estimate-contaminated.** Part 1 recorded this; nothing here changes it. The trigger is exact, the eviction floor is not.
- **The probe's filler size is an estimate.** `PROBE_CHARS_PER_TOKEN` sizes the request; the provider's reported `prompt_tokens` is what the user is shown, and the UI labels an estimated count as estimated. A provider whose real ratio is far from 4.0 will be tested at a somewhat different size than declared — visible in the reported count rather than hidden.
