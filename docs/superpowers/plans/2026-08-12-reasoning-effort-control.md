# Reasoning-Effort Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the user an `Off · Low · Medium · High · Max` reasoning-effort dial on a composer chip, mapped honestly onto each provider's own wire shape, so a nemotron-3 turn stops burning 17× more reasoning tokens than output tokens.

**Architecture:** A provider-neutral `ReasoningEffort` ladder plus an `EffortSupport` capability record (tri-state per rung: supported / unsupported-with-reason / unknown). Each transport declares its capability and owns its wire mapping; `ProviderRuntime` owns clamping and fan-out to live engines; the chip renders from the capability record. Effort is transport instance state, so no ReAct-loop call site changes.

**Tech Stack:** Python 3.13 / FastAPI / pytest-asyncio (backend), TypeScript / Zod (editor-client), React + Vite (webview), vitest (frontend tests).

**Spec:** `docs/superpowers/specs/2026-08-12-reasoning-effort-control-design.md`

## Global Constraints

- Ladder is exactly five rungs: `off`, `low`, `medium`, `high`, `max`. Wire values are lowercase strings matching these names except where a provider's own vocabulary differs (each transport's map is explicit).
- Clamping is **downward-biased**: an unsupported rung resolves to the nearest non-unsupported rung *below* it; only if none exists does it search upward.
- **UNKNOWN is not UNSUPPORTED.** A rung in neither `supported` nor `unsupported` is sent as-is and never triggers a clamp.
- A probative 400 marks **only the offending rung** unsupported — never the whole capability. Transient failures (429, 5xx, timeouts, connection errors) mark nothing.
- The two transport members are optional and accessed with `getattr` guards, matching the existing `supports_token_progress` convention. Transports that do not implement them are never asked.
- Backend tests: run `pytest` with **no `-q`** (`pyproject.toml` already sets it; a second one suppresses the summary). Never pipe pytest output — a pipe masks its exit code.
- **Every backend task must end with `ruff check <the files it touched>` clean** (line limit is 100 chars; unused imports are errors). Added after Task 2 shipped two lint errors that were transcribed straight out of this plan's own test code — if a code block here violates ruff, fix the code, not the linter.
- Never use `asyncio.get_event_loop().run_until_complete()` in tests; use `@pytest.mark.asyncio` + `async def`.
- After changing `apps/editor-client`, run `npm run -w @crucible/editor-client build` before typechecking `crucible-vscode-extension` — the extension types off compiled `dist/index.d.ts`, not source.
- Commit after every task. Work on branch `feat/reasoning-effort-control` (already created; the spec is its first commit).

---

## File Structure

**Create:**
- `services/agentd-py/agentd/providers/reasoning_effort.py` — the ladder, `EffortSupport`, clamping. No provider knowledge.
- `services/agentd-py/tests/test_reasoning_effort.py` — ladder + clamp unit tests.
- `services/agentd-py/tests/test_provider_effort_wire.py` — table-driven per-transport wire-body assertions.
- `services/agentd-py/tests/test_provider_effort_learning.py` — probative-400 learning tests.
- `apps/vscode-extension/webview-ui/src/components/EffortMenu.tsx` — the chip.
- `apps/vscode-extension/webview-ui/src/test/EffortMenu.test.tsx` — chip rendering tests.

**Modify:**
- `agentd/providers/openai_compatible_transport.py` — capability + wire map + learning path.
- `agentd/providers/openrouter_transport.py` — registry-backed capability override + `reasoning:{effort}` wire form.
- `agentd/providers/groq_transport.py`, `ollama_transport.py`, `gemini_transport.py`, `turboquant_transport.py` — capability + wire map each.
- `agentd/providers/runtime.py` — hold the level + transport, resolve/clamp, fan out.
- `agentd/api/routes.py` — `ProviderSwapRequest.reasoning_effort`, `GET /v1/config` provider block.
- `agentd/main.py` — seed `ProviderRuntime` from `CRUCIBLE_REASONING_EFFORT` and the startup transport.
- `apps/editor-client/src/contracts/task-contracts.ts`, `src/client/http-backend-client.ts` — schema + mapping.
- `apps/vscode-extension/src/composer-models.ts`, `src/chat-panel.ts`, `src/extension.ts`, `src/runtime/vscode-runtime.ts`, `src/runtime/backend-process.ts` — host plumbing + env.
- `apps/vscode-extension/webview-ui/src/types.ts`, `src/components/InputArea.tsx` — mirror types + chip placement.
- `CLAUDE.md`, `scripts/stress/start-backend.sh`, `.env` — docs + the two non-managed env sites.

---

### Task 1: The effort ladder and capability record

**Files:**
- Create: `services/agentd-py/agentd/providers/reasoning_effort.py`
- Test: `services/agentd-py/tests/test_reasoning_effort.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ReasoningEffort` (StrEnum: `OFF LOW MEDIUM HIGH MAX`, values `"off" "low" "medium" "high" "max"`); `LADDER: tuple[ReasoningEffort, ...]` cheapest-first; `EffortSupport(supported: frozenset[ReasoningEffort], unsupported: Mapping[ReasoningEffort, str])` with `.state(level) -> str` returning `"supported" | "unsupported" | "unknown"` and `.resolve(level) -> tuple[ReasoningEffort, str | None]`; `parse_effort(raw: str | None) -> ReasoningEffort | None`.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_reasoning_effort.py
import pytest

from agentd.providers.reasoning_effort import (
    LADDER,
    EffortSupport,
    ReasoningEffort,
    parse_effort,
)


def test_ladder_is_cheapest_first():
    assert LADDER == (
        ReasoningEffort.OFF,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.MAX,
    )


def test_state_is_tri_valued():
    support = EffortSupport(
        supported=frozenset({ReasoningEffort.LOW}),
        unsupported={ReasoningEffort.OFF: "no off here"},
    )
    assert support.state(ReasoningEffort.LOW) == "supported"
    assert support.state(ReasoningEffort.OFF) == "unsupported"
    # In neither set: we do not know, which is NOT the same as unsupported.
    assert support.state(ReasoningEffort.MAX) == "unknown"


def test_supported_rung_resolves_to_itself_with_no_note():
    support = EffortSupport(supported=frozenset({ReasoningEffort.HIGH}))
    assert support.resolve(ReasoningEffort.HIGH) == (ReasoningEffort.HIGH, None)


def test_unknown_rung_is_sent_as_is_never_clamped():
    # An unverifiable endpoint must not be silently downgraded — we send and learn.
    support = EffortSupport()
    assert support.resolve(ReasoningEffort.MAX) == (ReasoningEffort.MAX, None)


def test_unsupported_rung_clamps_downward():
    support = EffortSupport(
        supported=frozenset({ReasoningEffort.OFF, ReasoningEffort.LOW, ReasoningEffort.HIGH}),
        unsupported={ReasoningEffort.MAX: "tops out at high"},
    )
    effective, note = support.resolve(ReasoningEffort.MAX)
    assert effective == ReasoningEffort.HIGH
    assert note is not None and "tops out at high" in note


def test_clamp_target_may_be_unknown_not_only_supported():
    # openai_compatible's real shape: MAX explicitly unsupported, everything else
    # unverified. Clamping only to *supported* rungs would find nothing and leave
    # MAX in place, sending a value we know is wrong.
    support = EffortSupport(unsupported={ReasoningEffort.MAX: "tops out at high"})
    effective, note = support.resolve(ReasoningEffort.MAX)
    assert effective == ReasoningEffort.HIGH
    assert note is not None


def test_clamps_upward_only_when_nothing_below_is_available():
    # Groq: rejects "none", so OFF has to go UP to LOW. The one upward case.
    support = EffortSupport(
        supported=frozenset({ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH}),
        unsupported={ReasoningEffort.OFF: 'Groq rejects "none"'},
    )
    effective, note = support.resolve(ReasoningEffort.OFF)
    assert effective == ReasoningEffort.LOW
    assert note is not None and "rejects" in note


def test_degenerate_all_unsupported_keeps_the_request_and_still_notes_it():
    support = EffortSupport(unsupported={level: "nope" for level in LADDER})
    effective, note = support.resolve(ReasoningEffort.HIGH)
    assert effective == ReasoningEffort.HIGH
    assert note is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high", ReasoningEffort.HIGH),
        ("HIGH", ReasoningEffort.HIGH),
        ("  off ", ReasoningEffort.OFF),
        (None, None),
        ("", None),
        ("banana", None),
    ],
)
def test_parse_effort(raw, expected):
    assert parse_effort(raw) == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_reasoning_effort.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentd.providers.reasoning_effort'`

- [ ] **Step 3: Write the implementation**

```python
# services/agentd-py/agentd/providers/reasoning_effort.py
"""Provider-neutral reasoning-effort ladder.

Deliberately knows nothing about any provider: each transport declares its own
EffortSupport and owns its wire mapping, so a new provider is one file rather
than an edit to a shared table. See
docs/superpowers/specs/2026-08-12-reasoning-effort-control-design.md.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ReasoningEffort(StrEnum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


# Cheapest first. Index position IS the ordering clamping walks, so this tuple is
# the single definition of "below" and "above" for the whole feature.
LADDER: tuple[ReasoningEffort, ...] = (
    ReasoningEffort.OFF,
    ReasoningEffort.LOW,
    ReasoningEffort.MEDIUM,
    ReasoningEffort.HIGH,
    ReasoningEffort.MAX,
)


def parse_effort(raw: str | None) -> ReasoningEffort | None:
    """A user/env string to a rung, or None for absent-or-unrecognized.

    Unrecognized input is None (= "send nothing, provider default") rather than an
    exception: this parses env vars and request bodies, and a typo must not take
    the backend down.
    """
    if not raw:
        return None
    try:
        return ReasoningEffort(raw.strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True)
class EffortSupport:
    """What a (backend, model) pair can actually express.

    Tri-state on purpose. A rung listed in neither collection is UNKNOWN — we have
    no evidence either way, which is a different thing from knowing it is
    unsupported. A pasted openai_compatible endpoint is entirely unknown, and the
    UI must be able to say "unverified" rather than claim support it cannot vouch
    for. Same distinction the context-window verdict already draws between
    "couldn't tell" and "downgraded".
    """

    supported: frozenset[ReasoningEffort] = frozenset()
    unsupported: Mapping[ReasoningEffort, str] = field(default_factory=dict)

    def state(self, level: ReasoningEffort) -> str:
        if level in self.supported:
            return "supported"
        if level in self.unsupported:
            return "unsupported"
        return "unknown"

    def resolve(self, level: ReasoningEffort) -> tuple[ReasoningEffort, str | None]:
        """(effective rung, human note when it differs from what was asked).

        Downward-biased: never silently spend MORE thinking than requested — that
        is the failure mode this whole feature exists to fix. Upward is the last
        resort and exists for exactly one real case (Groq 400s on "none", so OFF
        has to become LOW).

        Clamp targets are rungs that are not KNOWN-bad, i.e. supported or unknown.
        Restricting targets to `supported` alone would strand the common
        openai_compatible shape, where MAX is known-unsupported and every other
        rung is merely unverified.
        """
        if self.state(level) != "unsupported":
            return level, None
        reason = self.unsupported[level]
        index = LADDER.index(level)
        for candidate in reversed(LADDER[:index]):
            if self.state(candidate) != "unsupported":
                return candidate, f"{level} unavailable here ({reason}); using {candidate}."
        for candidate in LADDER[index + 1 :]:
            if self.state(candidate) != "unsupported":
                return candidate, f"{level} unavailable here ({reason}); using {candidate}."
        # Degenerate: every rung is known-bad. Keep what was asked and let the
        # request fail loudly rather than inventing a rung that is equally wrong.
        return level, f"{level} unavailable here ({reason}); no alternative rung is available."
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_reasoning_effort.py`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/reasoning_effort.py services/agentd-py/tests/test_reasoning_effort.py
git commit -m "feat(providers): add the provider-neutral reasoning-effort ladder"
```

---

### Task 2: `openai_compatible` capability and wire mapping

**Files:**
- Modify: `services/agentd-py/agentd/providers/openai_compatible_transport.py` (constructor ~line 296; `_build_extra_body` at 382; new members after `_reasoning_config` at 400)
- Test: `services/agentd-py/tests/test_provider_effort_wire.py`

**Interfaces:**
- Consumes: `ReasoningEffort`, `EffortSupport`, `LADDER` from Task 1.
- Produces: on `OpenAICompatibleTransport` — `set_reasoning_effort(level: ReasoningEffort | None) -> None` (stores an **already-clamped** rung; `None` means send nothing) and `async reasoning_effort_support(model: str) -> EffortSupport`. Module constant `_OPENAI_COMPAT_EFFORT_WIRE: dict[ReasoningEffort, str]`.

Clamping is **not** done here — `ProviderRuntime` (Task 8) resolves before calling `set_reasoning_effort`, which keeps `_build_extra_body` synchronous while capability resolution stays async.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_provider_effort_wire.py
import pytest

from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort


def _transport() -> OpenAICompatibleTransport:
    # completions_client short-circuits real client construction (see __init__).
    return OpenAICompatibleTransport(
        base_url="http://localhost:9/v1", completions_client=object()
    )


@pytest.mark.parametrize(
    ("level", "wire"),
    [
        (ReasoningEffort.OFF, "none"),
        (ReasoningEffort.LOW, "low"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        # vLLM's ladder tops out at high; MAX is declared unsupported so the runtime
        # clamps before we get here, but the map must still be total.
        (ReasoningEffort.MAX, "high"),
    ],
)
def test_effort_rides_the_body_as_top_level_reasoning_effort(level, wire):
    t = _transport()
    t.set_reasoning_effort(level)
    body = t._build_extra_body("nvidia/nemotron-3", True, for_json=True)
    # extra_body is merged into the request body at top level by the OpenAI SDK,
    # which is exactly where vLLM reads reasoning_effort.
    assert body["reasoning_effort"] == wire


def test_no_effort_field_when_unset():
    t = _transport()
    body = t._build_extra_body("nvidia/nemotron-3", True, for_json=True)
    assert "reasoning_effort" not in body


def test_none_clears_a_previously_set_effort():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.LOW)
    t.set_reasoning_effort(None)
    body = t._build_extra_body("nvidia/nemotron-3", True, for_json=True)
    assert "reasoning_effort" not in body


def test_effort_is_sent_on_text_calls_too():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.LOW)
    assert t._build_extra_body("nvidia/nemotron-3", True, for_json=False)["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_support_marks_max_unsupported_and_leaves_the_rest_unknown():
    t = _transport()
    support = await t.reasoning_effort_support("nvidia/nemotron-3")
    assert support.state(ReasoningEffort.MAX) == "unsupported"
    # We cannot verify an arbitrary pasted endpoint — unknown, not supported.
    assert support.state(ReasoningEffort.HIGH) == "unknown"


@pytest.mark.asyncio
async def test_non_reasoning_model_supports_only_off():
    t = _transport()
    support = await t.reasoning_effort_support("some-plain-chat-model")
    assert support.state(ReasoningEffort.OFF) == "supported"
    assert support.state(ReasoningEffort.HIGH) == "unsupported"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py`
Expected: FAIL — `AttributeError: 'OpenAICompatibleTransport' object has no attribute 'set_reasoning_effort'`

- [ ] **Step 3: Add the import and module constant**

At the top of `agentd/providers/openai_compatible_transport.py`, alongside the existing imports:

```python
from agentd.providers.reasoning_effort import LADDER, EffortSupport, ReasoningEffort
```

Below `_CHAT_COMPLETIONS_SUFFIX`:

```python
# vLLM (which NVIDIA NIM is built on) reads a top-level `reasoning_effort` and
# auto-injects the low-level chat_template_kwargs.enable_thinking from it, so this
# is the front door for the whole OpenAI-compatible family. The ladder tops out at
# "high" there, which is why MAX maps onto "high" and is declared unsupported —
# the runtime clamps MAX to HIGH before it reaches this map, and the map stays
# total so a missed clamp still sends something valid rather than raising.
_OPENAI_COMPAT_EFFORT_WIRE: dict[ReasoningEffort, str] = {
    ReasoningEffort.OFF: "none",
    ReasoningEffort.LOW: "low",
    ReasoningEffort.MEDIUM: "medium",
    ReasoningEffort.HIGH: "high",
    ReasoningEffort.MAX: "high",
}
```

- [ ] **Step 4: Initialize the instance state**

In `__init__`, immediately after `self.supports_token_progress = True`:

```python
        # The EFFECTIVE rung (already clamped by ProviderRuntime), or None to send
        # no effort field at all — which is both the default and the escape hatch
        # back to pre-feature behavior.
        self._reasoning_effort: ReasoningEffort | None = None
        # Rungs this endpoint has PROVEN it rejects, learned from probative 400s
        # (Task 3). Per rung, never whole-capability: the sticky JSON-mode
        # downgrade's documented flaw is collapsing "cannot honor THIS" into
        # "cannot honor ANY", and the rungs here are independent.
        self._effort_rejected: dict[ReasoningEffort, str] = {}
```

- [ ] **Step 5: Add the two contract members**

Directly after `_reasoning_config` (currently ending at line 403):

```python
    def set_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        """Store the effective rung. None sends no effort field at all."""
        self._reasoning_effort = level

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        """What this endpoint can express for `model`.

        Deliberately pessimistic about knowledge, not about capability: an
        arbitrary base URL could be vLLM, LM Studio, Together, or DeepInfra, so
        every rung we have not disproven stays UNKNOWN and gets sent. MAX is the
        one rung we can rule out from the protocol alone.
        """
        is_reasoning, _ = await self._reasoning_config(model)
        if not is_reasoning:
            return EffortSupport(
                supported=frozenset({ReasoningEffort.OFF}),
                unsupported={
                    level: "this model does not expose reasoning"
                    for level in LADDER
                    if level is not ReasoningEffort.OFF
                },
            )
        unsupported = {
            ReasoningEffort.MAX: "OpenAI-compatible endpoints top out at 'high'",
            **self._effort_rejected,
        }
        return EffortSupport(unsupported=unsupported)
```

- [ ] **Step 6: Emit the wire value**

Replace the body of `_build_extra_body` (line 395-398) with:

```python
        extra_body: dict[str, Any] = {}
        if is_reasoning:
            extra_body["reasoning"] = {"enabled": True}
        if self._reasoning_effort is not None:
            extra_body["reasoning_effort"] = _OPENAI_COMPAT_EFFORT_WIRE[self._reasoning_effort]
        return extra_body
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py`
Expected: PASS, 9 passed

- [ ] **Step 8: Run the existing transport suite for regressions**

Run: `cd services/agentd-py && pytest tests/test_openai_compatible_transport.py`
Expected: PASS, no failures (the new field is absent unless set)

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/providers/openai_compatible_transport.py services/agentd-py/tests/test_provider_effort_wire.py
git commit -m "feat(providers): map reasoning effort onto the openai_compatible wire"
```

---

### Task 3: Learn from a probative rejection

**Files:**
- Modify: `services/agentd-py/agentd/providers/openai_compatible_transport.py`
- Test: `services/agentd-py/tests/test_provider_effort_learning.py`

**Interfaces:**
- Consumes: `_effort_rejected`, `_reasoning_effort` from Task 2.
- Produces: module function `_is_probative_effort_rejection(exc: Exception) -> bool`; method `_note_effort_rejection(exc: Exception) -> bool` on the transport, returning True when it recorded a rejection and cleared the effort (so the caller knows a bare retry is worth attempting).

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_provider_effort_learning.py
import pytest

from agentd.providers.openai_compatible_transport import (
    OpenAICompatibleTransport,
    _is_probative_effort_rejection,
)
from agentd.providers.reasoning_effort import ReasoningEffort


def _transport() -> OpenAICompatibleTransport:
    return OpenAICompatibleTransport(
        base_url="http://localhost:9/v1", completions_client=object()
    )


class _Err(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status


def test_400_naming_the_parameter_is_probative():
    assert _is_probative_effort_rejection(
        _Err("400: unsupported value 'none' for reasoning_effort", 400)
    )


def test_rate_limit_is_not_probative():
    # A 429 that happened to mention the field must never permanently pin the
    # session to a lower rung — same rule the JSON-mode downgrade already follows.
    assert not _is_probative_effort_rejection(_Err("429 rate limited reasoning_effort", 429))


def test_server_error_is_not_probative():
    assert not _is_probative_effort_rejection(_Err("503 overloaded reasoning_effort", 503))


def test_400_about_something_else_is_not_probative():
    assert not _is_probative_effort_rejection(_Err("400: context length exceeded", 400))


def test_timeout_is_not_probative():
    assert not _is_probative_effort_rejection(TimeoutError("timed out"))


@pytest.mark.asyncio
async def test_a_rejection_marks_only_that_rung():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    assert t._note_effort_rejection(_Err("400 invalid reasoning_effort 'none'", 400)) is True

    support = await t.reasoning_effort_support("nvidia/nemotron-3")
    assert support.state(ReasoningEffort.OFF) == "unsupported"
    # Every other rung is untouched — the capability as a whole survives.
    assert support.state(ReasoningEffort.LOW) == "unknown"
    assert support.state(ReasoningEffort.HIGH) == "unknown"


def test_a_rejection_clears_the_effort_so_the_retry_omits_it():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    t._note_effort_rejection(_Err("400 invalid reasoning_effort 'none'", 400))
    assert "reasoning_effort" not in t._build_extra_body("m", True, for_json=True)


def test_a_transient_failure_records_nothing():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    assert t._note_effort_rejection(_Err("429 slow down", 429)) is False
    assert t._build_extra_body("m", True, for_json=True)["reasoning_effort"] == "none"


def test_nothing_recorded_when_no_effort_was_set():
    t = _transport()
    assert t._note_effort_rejection(_Err("400 invalid reasoning_effort", 400)) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_learning.py`
Expected: FAIL — `ImportError: cannot import name '_is_probative_effort_rejection'`

- [ ] **Step 3: Add the classifier**

Below `_OPENAI_COMPAT_EFFORT_WIRE` in `openai_compatible_transport.py`:

```python
# Statuses that prove nothing about capability. Mirrors _RETRYABLE_STATUS_CODES in
# spirit and for the same reason the sticky JSON downgrade excludes them: a
# rate-limit blip that permanently pinned the session to a lower rung would be the
# same silent-degradation bug wearing a different hat.
_NON_PROBATIVE_STATUS: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504})


def _is_probative_effort_rejection(exc: Exception) -> bool:
    """True only when the endpoint PROVED it rejects the effort value we sent.

    Requires both a 4xx that is not in the transient set AND the parameter named
    in the message — a 400 about context length says nothing about effort.
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or status in _NON_PROBATIVE_STATUS or status >= 500:
        return False
    text = str(exc).lower()
    return "reasoning_effort" in text or "reasoning effort" in text
```

- [ ] **Step 4: Add the recorder**

Directly after `reasoning_effort_support`:

```python
    def _note_effort_rejection(self, exc: Exception) -> bool:
        """Record a proven-bad rung and drop the field. True when it fired.

        Marks ONLY the rung that was in flight. The effort is cleared to None so
        the immediate retry (and every call until the next swap re-resolves) omits
        the field entirely rather than guessing at a replacement — the runtime owns
        clamping, and it will pick the right neighbour on the next resolve.
        """
        level = self._reasoning_effort
        if level is None or not _is_probative_effort_rejection(exc):
            return False
        self._effort_rejected[level] = f"this endpoint rejected '{level}'"
        self._reasoning_effort = None
        logger.warning(
            "[effort] %s rejected reasoning_effort=%s; dropping it for this process",
            self._label,
            level,
        )
        return True
```

- [ ] **Step 5: Retry once without the field**

In `generate_text`, replace the existing `except Exception as e:` block that wraps `_call_with_retry` (currently `raise RuntimeError(f"{self._label} API error: {e}") from e`) with:

```python
        except Exception as e:
            if self._note_effort_rejection(e):
                # One bare retry with the field gone. The rung is now recorded, so
                # this cannot loop: a second failure has no effort left to blame.
                create_kwargs.pop("extra_body", None)
                retry_extra = self._build_extra_body(model, is_reasoning, for_json=False)
                if retry_extra:
                    create_kwargs["extra_body"] = retry_extra
                response = await self._call_with_retry(create_kwargs)
                return self._extract_text(response)
            raise RuntimeError(f"{self._label} API error: {e}") from e
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_learning.py tests/test_provider_effort_wire.py`
Expected: PASS, 18 passed

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/providers/openai_compatible_transport.py services/agentd-py/tests/test_provider_effort_learning.py
git commit -m "feat(providers): learn a rejected effort rung without disabling the dial"
```

---

### Task 4: OpenRouter capability and wire form

**Files:**
- Modify: `services/agentd-py/agentd/providers/openrouter_transport.py` (`_build_extra_body` at ~128, `_reasoning_config` at ~146)
- Test: `services/agentd-py/tests/test_provider_effort_wire.py` (append)

**Interfaces:**
- Consumes: Task 2's members (inherited from the base class).
- Produces: overrides `_build_extra_body` and `reasoning_effort_support` on `OpenRouterJsonTransport`. Module constant `_OPENROUTER_EFFORT_WIRE: dict[ReasoningEffort, dict[str, object]]`.

OpenRouter takes `reasoning: {effort}` rather than a top-level `reasoning_effort`, and supports the full ladder including `max`/`xhigh`. It also normalizes effort onto a token budget for budget-only models, so every rung is genuinely expressible.

- [ ] **Step 1: Write the failing tests (append to `tests/test_provider_effort_wire.py`)**

```python
from agentd.providers.openrouter_transport import OpenRouterJsonTransport


def _openrouter() -> OpenRouterJsonTransport:
    return OpenRouterJsonTransport(api_key="k", completions_client=object())


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (ReasoningEffort.OFF, {"enabled": False}),
        (ReasoningEffort.LOW, {"effort": "low"}),
        (ReasoningEffort.MEDIUM, {"effort": "medium"}),
        (ReasoningEffort.HIGH, {"effort": "high"}),
        (ReasoningEffort.MAX, {"effort": "max"}),
    ],
)
def test_openrouter_uses_the_nested_reasoning_object(level, expected):
    t = _openrouter()
    t.set_reasoning_effort(level)
    body = t._build_extra_body("deepseek/deepseek-r1", True, for_json=True)
    assert body["reasoning"] == expected
    # The base class's blanket {"enabled": True} must not survive alongside it.
    assert "reasoning_effort" not in body


@pytest.mark.asyncio
async def test_openrouter_supports_the_whole_ladder_for_a_reasoning_model():
    t = _openrouter()
    support = await t.reasoning_effort_support("deepseek/deepseek-r1")
    for level in ReasoningEffort:
        assert support.state(level) == "supported"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py -k openrouter`
Expected: FAIL — `KeyError: 'reasoning'` is not the expected dict (the base sends `{"enabled": True}`)

- [ ] **Step 3: Write the implementation**

Add the import and constant near the top of `openrouter_transport.py`:

```python
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort

# OpenRouter takes a nested `reasoning` object and accepts the full ladder,
# normalizing effort onto a token budget for models that only expose one
# (max/xhigh ~95% of available tokens, high 80%, medium 50%, low 20%). That
# normalization is why every rung here is genuinely expressible.
_OPENROUTER_EFFORT_WIRE: dict[ReasoningEffort, dict[str, object]] = {
    ReasoningEffort.OFF: {"enabled": False},
    ReasoningEffort.LOW: {"effort": "low"},
    ReasoningEffort.MEDIUM: {"effort": "medium"},
    ReasoningEffort.HIGH: {"effort": "high"},
    ReasoningEffort.MAX: {"effort": "max"},
}
```

In `_build_extra_body`, after the existing `super()` call and before the `require_parameters` block:

```python
        # Replace both the base's blanket {"enabled": True} and its top-level
        # reasoning_effort: OpenRouter reads neither, and leaving the flat key in
        # place would be an unread parameter that require_parameters could route on.
        extra_body.pop("reasoning_effort", None)
        if self._reasoning_effort is not None:
            extra_body["reasoning"] = dict(_OPENROUTER_EFFORT_WIRE[self._reasoning_effort])
```

Add after `_reasoning_config`:

```python
    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        """Registry-first: the live model list is the authority on whether this
        model reasons at all. When it says yes, every rung is expressible, because
        OpenRouter normalizes effort onto a budget for budget-only models."""
        is_reasoning, _ = await self._reasoning_config(model)
        if not is_reasoning:
            return EffortSupport(
                supported=frozenset({ReasoningEffort.OFF}),
                unsupported={
                    level: "this model does not expose reasoning"
                    for level in ReasoningEffort
                    if level is not ReasoningEffort.OFF
                },
            )
        return EffortSupport(supported=frozenset(ReasoningEffort))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py`
Expected: PASS, 16 passed

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/openrouter_transport.py services/agentd-py/tests/test_provider_effort_wire.py
git commit -m "feat(providers): map reasoning effort onto OpenRouter's reasoning object"
```

---

### Task 5: Groq capability and wire mapping

**Files:**
- Modify: `services/agentd-py/agentd/providers/groq_transport.py` (constructor ~line 45, `generate_json` ~line 113, `generate_text` ~line 173)
- Test: `services/agentd-py/tests/test_provider_effort_wire.py` (append)

**Interfaces:**
- Consumes: `ReasoningEffort`, `EffortSupport`.
- Produces: `set_reasoning_effort` / `reasoning_effort_support` on `GroqJsonTransport`; module constant `_GROQ_EFFORT_WIRE`.

Groq accepts only `low | medium | high` and **400s on `"none"`** — the one place `OFF` must clamp upward. `MAX` also has no expression and clamps down to `HIGH`.

- [ ] **Step 1: Write the failing tests (append)**

```python
from agentd.providers.groq_transport import GroqJsonTransport


def _groq() -> GroqJsonTransport:
    return GroqJsonTransport(api_key="k", completions_client=object())


@pytest.mark.asyncio
async def test_groq_declares_off_unsupported_because_it_400s():
    t = _groq()
    support = await t.reasoning_effort_support("openai/gpt-oss-120b")
    assert support.state(ReasoningEffort.OFF) == "unsupported"
    assert "none" in support.unsupported[ReasoningEffort.OFF]
    assert support.state(ReasoningEffort.MAX) == "unsupported"
    for level in (ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH):
        assert support.state(level) == "supported"


@pytest.mark.asyncio
async def test_groq_off_clamps_upward_to_low():
    support = await _groq().reasoning_effort_support("openai/gpt-oss-120b")
    effective, note = support.resolve(ReasoningEffort.OFF)
    assert effective == ReasoningEffort.LOW
    assert note is not None


def test_groq_never_emits_none_on_the_wire():
    # Regression guard for a verified provider fact: Groq rejects
    # reasoning_effort="none" with a 400.
    t = _groq()
    for level in ReasoningEffort:
        t.set_reasoning_effort(level)
        assert t._effort_wire_value() != "none"


@pytest.mark.parametrize(
    ("level", "wire"),
    [
        (ReasoningEffort.LOW, "low"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        (ReasoningEffort.MAX, "high"),
        (ReasoningEffort.OFF, "low"),
    ],
)
def test_groq_wire_values(level, wire):
    t = _groq()
    t.set_reasoning_effort(level)
    assert t._effort_wire_value() == wire
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py -k groq`
Expected: FAIL — `AttributeError: 'GroqJsonTransport' object has no attribute 'reasoning_effort_support'`

- [ ] **Step 3: Write the implementation**

Add near the top of `groq_transport.py`:

```python
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort

# Groq accepts low|medium|high and REJECTS "none" with a 400 (verified against
# provider docs 2026-08-12). OFF therefore maps upward onto "low" and is declared
# unsupported so the user is told rather than silently given more thinking than
# they asked for; MAX has no expression and maps down onto "high".
_GROQ_EFFORT_WIRE: dict[ReasoningEffort, str] = {
    ReasoningEffort.OFF: "low",
    ReasoningEffort.LOW: "low",
    ReasoningEffort.MEDIUM: "medium",
    ReasoningEffort.HIGH: "high",
    ReasoningEffort.MAX: "high",
}
```

In `__init__`, after `self._reasoning_effort = reasoning_effort or os.getenv(...)`:

```python
        # The unified dial, when one is set. Kept separate from the legacy env
        # string above so an unset dial preserves today's env-driven behavior
        # exactly (see _effort_wire_value).
        self._effort: ReasoningEffort | None = None
```

Add these methods to the class:

```python
    def set_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        self._effort = level

    def _effort_wire_value(self) -> str | None:
        """The dial wins when set; otherwise the legacy env string stands."""
        if self._effort is not None:
            return _GROQ_EFFORT_WIRE[self._effort]
        return self._reasoning_effort

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        return EffortSupport(
            supported=frozenset(
                {ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH}
            ),
            unsupported={
                ReasoningEffort.OFF: 'Groq rejects reasoning_effort "none"',
                ReasoningEffort.MAX: "Groq's ladder tops out at 'high'",
            },
        )
```

In both `generate_json` (line ~113) and `generate_text` (line ~173), replace `if self._reasoning_effort:` / `create_kwargs["reasoning_effort"] = self._reasoning_effort` with:

```python
            effort = self._effort_wire_value()
            if effort:
                create_kwargs["reasoning_effort"] = effort
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py`
Expected: PASS, 25 passed

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/providers/groq_transport.py services/agentd-py/tests/test_provider_effort_wire.py
git commit -m "feat(providers): map reasoning effort onto Groq, which rejects 'none'"
```

---

### Task 6: Ollama capability and wire mapping

**Files:**
- Modify: `services/agentd-py/agentd/providers/ollama_transport.py` (constructor ~line 99, `_build_body` ~line 185)
- Test: `services/agentd-py/tests/test_provider_effort_wire.py` (append)

**Interfaces:**
- Consumes: `ReasoningEffort`, `EffortSupport`.
- Produces: `set_reasoning_effort` / `reasoning_effort_support` / `_effort_think_value() -> bool | str | None` on `OllamaJsonTransport`; module constant `_OLLAMA_EFFORT_WIRE`.

Ollama's `think` is a **top-level** request field (not inside `options`), accepting a bool or a level string.

- [ ] **Step 1: Write the failing tests (append)**

```python
from agentd.providers.ollama_transport import OllamaJsonTransport


def _ollama() -> OllamaJsonTransport:
    return OllamaJsonTransport(host="http://localhost:11434")


@pytest.mark.parametrize(
    ("level", "wire"),
    [
        (ReasoningEffort.OFF, False),
        (ReasoningEffort.LOW, "low"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        (ReasoningEffort.MAX, "high"),
    ],
)
def test_ollama_think_values(level, wire):
    t = _ollama()
    t.set_reasoning_effort(level)
    assert t._effort_think_value() == wire


def test_ollama_unset_dial_preserves_the_env_configured_think():
    t = OllamaJsonTransport(host="http://localhost:11434", think="medium")
    assert t._effort_think_value() == "medium"


def test_ollama_think_rides_the_body_top_level_not_options():
    t = _ollama()
    t.set_reasoning_effort(ReasoningEffort.LOW)
    # _build_body is KEYWORD-ONLY (verified against the real signature).
    body = t._build_body(
        model="qwen3", system="sys", user_content="user", json_format=None, num_predict=100
    )
    assert body["think"] == "low"
    assert "think" not in body["options"]


def test_ollama_omits_think_entirely_when_nothing_is_set():
    body = _ollama()._build_body(
        model="qwen3", system="sys", user_content="user", json_format=None, num_predict=100
    )
    assert "think" not in body


@pytest.mark.asyncio
async def test_ollama_supports_the_whole_ladder_except_max():
    support = await _ollama().reasoning_effort_support("qwen3")
    assert support.state(ReasoningEffort.OFF) == "supported"
    assert support.state(ReasoningEffort.MAX) == "unsupported"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py -k ollama`
Expected: FAIL — `AttributeError: 'OllamaJsonTransport' object has no attribute 'set_reasoning_effort'`

- [ ] **Step 3: Write the implementation**

Add near the top of `ollama_transport.py`:

```python
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort

# Ollama's `think` is a TOP-LEVEL request field (not an `options` entry) and takes
# either a bool or a level string, depending on the model. MAX has no distinct
# expression and maps onto "high".
_OLLAMA_EFFORT_WIRE: dict[ReasoningEffort, bool | str] = {
    ReasoningEffort.OFF: False,
    ReasoningEffort.LOW: "low",
    ReasoningEffort.MEDIUM: "medium",
    ReasoningEffort.HIGH: "high",
    ReasoningEffort.MAX: "high",
}
```

In `__init__`, immediately after `self._think = think`:

```python
        # The unified dial. Kept separate from _think so an unset dial leaves the
        # per-deployment CRUCIBLE_OLLAMA_THINK behavior exactly as it is today.
        self._effort: ReasoningEffort | None = None
```

Add to the class:

```python
    def set_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        self._effort = level

    def _effort_think_value(self) -> bool | str | None:
        """The dial wins when set; otherwise the constructor/env `think` stands.
        None means omit the field entirely — the model decides."""
        if self._effort is not None:
            return _OLLAMA_EFFORT_WIRE[self._effort]
        return self._think

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        return EffortSupport(
            supported=frozenset(
                {
                    ReasoningEffort.OFF,
                    ReasoningEffort.LOW,
                    ReasoningEffort.MEDIUM,
                    ReasoningEffort.HIGH,
                }
            ),
            unsupported={ReasoningEffort.MAX: "Ollama's think levels top out at 'high'"},
        )
```

In `_build_body`, immediately before `return body`:

```python
        think = self._effort_think_value()
        if think is not None:
            # Top level, NOT inside options — that is where Ollama reads it.
            body["think"] = think
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py`
Expected: PASS, 30 passed

- [ ] **Step 5: Run the existing Ollama suite for regressions**

Run: `cd services/agentd-py && pytest tests/ -k ollama`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/providers/ollama_transport.py services/agentd-py/tests/test_provider_effort_wire.py
git commit -m "feat(providers): map reasoning effort onto Ollama's think field"
```

---

### Task 7: Gemini wire mapping, and TurboQuant declared unsupported

> **Amended 2026-08-12 after a pre-flight dry run.** TurboQuant was originally
> scoped to a full budget-based ladder. Reading the real `_build_body` showed it
> applies the strict JSON grammar only when `thinking_budget == 0`, so any rung
> above Off would silently disable grammar enforcement; the profile is also a
> frozen dataclass. v1 therefore declares the dial unavailable there, with the
> reason surfaced on the chip.


**Files:**
- Modify: `services/agentd-py/agentd/providers/gemini_transport.py` (constructor ~line 52, `_build_thinking_config` at 289), `services/agentd-py/agentd/providers/turboquant_transport.py` (`_build_body` ~line 102)
- Test: `services/agentd-py/tests/test_provider_effort_wire.py` (append)

**Interfaces:**
- Consumes: `ReasoningEffort`, `EffortSupport`.
- Produces: `set_reasoning_effort` / `reasoning_effort_support` on both `GeminiJsonTransport` and the TurboQuant transport; module constants `_GEMINI_EFFORT_WIRE` and `_TURBOQUANT_EFFORT_BUDGET`.

These two are folded into one task because each is a single-hook change with no shared surface to review independently.

- [ ] **Step 1: Write the failing tests (append)**

```python
from agentd.providers.gemini_transport import GeminiJsonTransport


def _gemini() -> GeminiJsonTransport:
    return GeminiJsonTransport(api_key="k", thinking_enabled=True)


@pytest.mark.parametrize(
    ("level", "expected_level"),
    [
        (ReasoningEffort.LOW, "minimal"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
        (ReasoningEffort.MAX, "high"),
    ],
)
def test_gemini_sets_thinking_level(level, expected_level):
    t = _gemini()
    t.set_reasoning_effort(level)
    config = t._build_thinking_config()
    assert config["thinking_level"] == expected_level


def test_gemini_off_disables_thinking_outright():
    t = _gemini()
    t.set_reasoning_effort(ReasoningEffort.OFF)
    assert t._build_thinking_config() is None


def test_gemini_never_sends_level_and_budget_together():
    # Verified provider fact: Gemini 3 returns an error when both are present.
    t = GeminiJsonTransport(api_key="k", thinking_enabled=True, thinking_budget=8000)
    t.set_reasoning_effort(ReasoningEffort.HIGH)
    config = t._build_thinking_config()
    assert "thinking_level" in config
    assert "thinking_budget" not in config
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py -k gemini`
Expected: FAIL — `AttributeError: 'GeminiJsonTransport' object has no attribute 'set_reasoning_effort'`

- [ ] **Step 3: Implement Gemini**

Add near the top of `gemini_transport.py`:

```python
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort

# Gemini 3's thinking_level vocabulary is minimal|medium|high. LOW maps onto
# "minimal" (its close-to-zero tier) and MAX onto "high" (its ceiling). OFF is not
# a level at all — it is thinking_enabled=False, handled separately.
_GEMINI_EFFORT_WIRE: dict[ReasoningEffort, str] = {
    ReasoningEffort.LOW: "minimal",
    ReasoningEffort.MEDIUM: "medium",
    ReasoningEffort.HIGH: "high",
    ReasoningEffort.MAX: "high",
}
```

In `__init__`, after `self._thinking_level = normalize_thinking_level(thinking_level)`:

```python
        self._effort: ReasoningEffort | None = None
```

Add to the class:

```python
    def set_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        self._effort = level

    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        return EffortSupport(
            supported=frozenset(
                {
                    ReasoningEffort.OFF,
                    ReasoningEffort.LOW,
                    ReasoningEffort.MEDIUM,
                    ReasoningEffort.HIGH,
                }
            ),
            unsupported={ReasoningEffort.MAX: "Gemini thinking levels top out at 'high'"},
        )
```

At the very top of `_build_thinking_config` (before the existing `if not self._thinking_enabled:`):

```python
        if self._effort is ReasoningEffort.OFF:
            return None
        if self._effort is not None:
            # thinking_level and thinking_budget are mutually exclusive on Gemini 3 —
            # sending both is a documented error, so the dial replaces the budget
            # rather than joining it.
            config: dict[str, object] = {
                "thinking_level": _GEMINI_EFFORT_WIRE[self._effort]
            }
            if self._include_thoughts:
                config["include_thoughts"] = True
            return config
```

Note: if the attribute holding "should thoughts be surfaced" is named differently in this file, use the same expression the existing code at line ~298 uses to decide `include_thoughts`.

- [ ] **Step 4: Write the TurboQuant test (append)**

```python
from agentd.providers.turboquant_transport import PROFILES, TurboQuantTransport


@pytest.mark.asyncio
async def test_turboquant_declares_the_whole_ladder_unsupported():
    # TurboQuant applies its JSON-schema GBNF grammar ONLY when thinking is off
    # (_build_body gates on self._profile.thinking_budget == 0, working around
    # llama.cpp#20345). Raising effort there would silently disable grammar
    # enforcement on the one provider whose grammar is the main defense against
    # malformed edits, so v1 declares the dial unavailable and says why.
    t = TurboQuantTransport(profile=PROFILES["qwen3"])
    support = await t.reasoning_effort_support("qwen3")
    for level in ReasoningEffort:
        assert support.state(level) == "unsupported"
        assert "grammar" in support.unsupported[level]


def test_turboquant_has_no_effort_setter_so_nothing_is_ever_sent():
    # The runtime's setter call is getattr-guarded; absent means the transport is
    # never asked, which is exactly the intended no-op.
    assert not hasattr(TurboQuantTransport, "set_reasoning_effort")
```

If `PROFILES` has no `"qwen3"` key, use `sorted(PROFILES)[0]` — the profile choice is irrelevant to this assertion.

- [ ] **Step 5: Declare TurboQuant unsupported**

Add near the top of `turboquant_transport.py`:

```python
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort

# Why the dial is unavailable here rather than mapped onto thinking_budget:
# _build_body applies the strict json_schema GBNF grammar only when
# self._profile.thinking_budget == 0, because llama.cpp silently disables grammar
# enforcement once thinking is on (ggml-org/llama.cpp#20345). Any rung above Off
# would therefore trade the strongest malformed-output defense in the stack for a
# latency dial. The profile is also a frozen dataclass, so per-rung budgets would
# mean rebuilding it per call.
_TURBOQUANT_NO_EFFORT = "llama.cpp drops JSON grammar enforcement when thinking is on"
```

Add ONLY the capability member to the transport class — deliberately no
`set_reasoning_effort`, so the runtime's `getattr` guard skips it and no effort
field is ever emitted:

```python
    async def reasoning_effort_support(self, model: str) -> EffortSupport:
        return EffortSupport(
            unsupported={level: _TURBOQUANT_NO_EFFORT for level in ReasoningEffort}
        )
```

This lands in `EffortSupport.resolve`'s degenerate branch (Task 1), which keeps the
requested rung and returns a note — and with no setter, nothing reaches the wire.
The chip shows all five rungs disabled with the reason, which is the honest
rendering.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py`
Expected: PASS, 39 passed

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/providers/gemini_transport.py services/agentd-py/agentd/providers/turboquant_transport.py services/agentd-py/tests/test_provider_effort_wire.py
git commit -m "feat(providers): map effort onto Gemini levels; declare TurboQuant unsupported"
```

---

### Task 8: Runtime resolution and fan-out

**Files:**
- Modify: `services/agentd-py/agentd/providers/runtime.py`
- Test: `services/agentd-py/tests/test_provider_effort_runtime.py` (create)

**Interfaces:**
- Consumes: transports' `set_reasoning_effort` / `reasoning_effort_support`; `EffortSupport.resolve` from Task 1.
- Produces: `ProviderRuntime(..., transport: object | None = None, reasoning_effort: ReasoningEffort | None = None)`; attributes `.reasoning_effort` and `.reasoning_effort_note`; `async effort_support() -> EffortSupport`; `swap(..., reasoning_effort: ReasoningEffort | None = None)` returning a dict that additionally carries `reasoning_effort`, `reasoning_effort_note`, and `reasoning_effort_support` (the latter as `{"supported": [...], "unsupported": {...}}`).

`ProviderRuntime` must now hold a transport reference, because capability is a property of the transport and `swap` currently discards the one it builds.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_provider_effort_runtime.py
import pytest

from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort
from agentd.providers.runtime import ProviderRuntime


class _Transport:
    def __init__(self, support: EffortSupport | None = None) -> None:
        self.effort: ReasoningEffort | None = None
        self._support = support if support is not None else EffortSupport(
            supported=frozenset(ReasoningEffort)
        )

    def set_reasoning_effort(self, level):
        self.effort = level

    async def reasoning_effort_support(self, model):
        return self._support

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


class _PlainTransport:
    """A transport that never grew the optional members — must never be asked."""

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


@pytest.mark.asyncio
async def test_support_degrades_to_all_unknown_for_a_transport_without_the_members():
    rt = ProviderRuntime(
        backend="scripted", model="m", engines=[], transport=_PlainTransport()
    )
    support = await rt.effort_support()
    assert support.state(ReasoningEffort.HIGH) == "unknown"


@pytest.mark.asyncio
async def test_support_degrades_to_all_unknown_when_the_transport_raises():
    class _Boom(_Transport):
        async def reasoning_effort_support(self, model):
            raise RuntimeError("registry down")

    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=_Boom())
    support = await rt.effort_support()
    assert support.state(ReasoningEffort.HIGH) == "unknown"


@pytest.mark.asyncio
async def test_apply_effort_clamps_and_records_the_note():
    transport = _Transport(
        EffortSupport(
            supported=frozenset({ReasoningEffort.LOW, ReasoningEffort.HIGH}),
            unsupported={ReasoningEffort.MAX: "tops out at high"},
        )
    )
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    await rt.apply_reasoning_effort(ReasoningEffort.MAX)
    assert transport.effort == ReasoningEffort.HIGH
    assert rt.reasoning_effort == ReasoningEffort.HIGH
    assert rt.reasoning_effort_note is not None


@pytest.mark.asyncio
async def test_apply_none_clears_the_transport_and_the_note():
    transport = _Transport()
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    await rt.apply_reasoning_effort(ReasoningEffort.HIGH)
    await rt.apply_reasoning_effort(None)
    assert transport.effort is None
    assert rt.reasoning_effort is None
    assert rt.reasoning_effort_note is None


@pytest.mark.asyncio
async def test_swap_carries_the_effort_onto_the_new_transport(monkeypatch):
    import agentd.providers.runtime as runtime_mod

    built = _Transport()
    monkeypatch.setattr(runtime_mod, "build_transport", lambda backend, credentials=None: built)
    monkeypatch.setattr(runtime_mod, "resolve_model", lambda backend: "new-model")

    async def _ok(transport, model):
        return None

    monkeypatch.setattr(runtime_mod, "ping_transport", _ok)

    class _Engine:
        def set_provider(self, *, model, transport):
            self.model = model

    rt = ProviderRuntime(
        backend="b",
        model="m",
        engines=[_Engine()],
        transport=_Transport(),
        reasoning_effort=ReasoningEffort.LOW,
    )
    result = await rt.swap(backend="b2")
    # A model-only swap must carry the existing level onto the freshly built
    # transport — absent means "unchanged", exactly like context_window.
    assert built.effort == ReasoningEffort.LOW
    assert result["reasoning_effort"] == "low"
    assert "supported" in result["reasoning_effort_support"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_runtime.py`
Expected: FAIL — `TypeError: ProviderRuntime.__init__() got an unexpected keyword argument 'transport'`

- [ ] **Step 3: Extend the constructor**

Add the import at the top of `runtime.py`:

```python
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort
```

Add two parameters to `__init__` (after `context_window`) and store them:

```python
        transport: object | None = None,
        reasoning_effort: ReasoningEffort | None = None,
```

```python
        # Capability is a property of the transport, and swap() previously threw
        # away the one it built. Held here so GET /v1/config can answer "which
        # rungs does the CURRENT provider actually expose".
        self._transport = transport
        # The effective (post-clamp) rung, and why it differs from the request when
        # it does. None means "send nothing" — the pre-feature default, which is
        # also what leaves each transport's own env-configured behavior in place.
        self.reasoning_effort = reasoning_effort
        self.reasoning_effort_note: str | None = None
```

- [ ] **Step 4: Add resolution and fan-out**

Add these methods to `ProviderRuntime`:

```python
    async def effort_support(self) -> EffortSupport:
        """What the current transport can express for the current model.

        Degrade-not-raise: a transport without the optional member, or one whose
        capability lookup fails (e.g. OpenRouter's registry fetch), yields
        all-UNKNOWN rather than blocking the config route.
        """
        resolver = getattr(self._transport, "reasoning_effort_support", None)
        if resolver is None:
            return EffortSupport()
        try:
            return await resolver(self.model)
        except Exception:
            return EffortSupport()

    async def apply_reasoning_effort(self, level: ReasoningEffort | None) -> None:
        """Clamp against the live capability, then push onto the transport."""
        setter = getattr(self._transport, "set_reasoning_effort", None)
        if level is None:
            self.reasoning_effort = None
            self.reasoning_effort_note = None
            if setter is not None:
                setter(None)
            return
        support = await self.effort_support()
        effective, note = support.resolve(level)
        self.reasoning_effort = effective
        self.reasoning_effort_note = note
        if setter is not None:
            setter(effective)
```

- [ ] **Step 5: Wire it into `swap`**

Add `reasoning_effort: ReasoningEffort | None = None` to the `swap` signature. After the existing `for engine in self._engines:` loop and the `self.backend, self.model = backend, resolved` line, insert:

```python
        # The freshly built transport starts with no effort. Re-apply after the
        # ping so an unsupported rung can never block a legitimate model swap —
        # the rung is clamped against the NEW model's capability, which is the
        # whole reason this is re-resolved rather than copied.
        self._transport = transport
        await self.apply_reasoning_effort(
            reasoning_effort if reasoning_effort is not None else self.reasoning_effort
        )
```

Then extend the returned dict, just before `return result`:

```python
        support = await self.effort_support()
        result["reasoning_effort"] = (
            self.reasoning_effort.value if self.reasoning_effort is not None else None
        )
        result["reasoning_effort_note"] = self.reasoning_effort_note
        result["reasoning_effort_support"] = {
            "supported": sorted(level.value for level in support.supported),
            "unsupported": {level.value: why for level, why in support.unsupported.items()},
        }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_runtime.py tests/test_provider_hotswap.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/agentd-py/agentd/providers/runtime.py services/agentd-py/tests/test_provider_effort_runtime.py
git commit -m "feat(providers): resolve and fan out reasoning effort from ProviderRuntime"
```

---

### Task 9: Config routes and env seed

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py` (`ProviderSwapRequest` at 66, `get_config` at 236, `put_config_provider` at 279), `services/agentd-py/agentd/main.py` (~line 345)
- Test: `services/agentd-py/tests/test_provider_effort_route.py` (create)

**Interfaces:**
- Consumes: `ProviderRuntime.apply_reasoning_effort`, `.effort_support()`, `.reasoning_effort` from Task 8; `parse_effort` from Task 1.
- Produces: `GET /v1/config` → `provider.reasoning_effort` (string or null) and `provider.reasoning_effort_support` (`{supported: string[], unsupported: {level: reason}}`); `PUT /v1/config/provider` accepts `reasoning_effort: str | null` and echoes `reasoning_effort`, `reasoning_effort_note`, `reasoning_effort_support`.

- [ ] **Step 1: Write the failing tests**

```python
# services/agentd-py/tests/test_provider_effort_route.py
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentd.providers.runtime as runtime_mod
from agentd.api.routes import build_router
from agentd.providers.reasoning_effort import EffortSupport, ReasoningEffort
from agentd.providers.runtime import ProviderRuntime
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _Transport:
    def __init__(self) -> None:
        self.effort = None

    def set_reasoning_effort(self, level):
        self.effort = level

    async def reasoning_effort_support(self, model):
        return EffortSupport(
            supported=frozenset({ReasoningEffort.LOW, ReasoningEffort.HIGH}),
            unsupported={ReasoningEffort.MAX: "tops out at high"},
        )

    async def generate_text(self, *, model, system_instructions, user_payload):
        return "OK"


def _client(tmp_path: Path, rt: ProviderRuntime) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_router(
            InMemoryTaskStore(),
            object(),
            ShadowWorkspaceManager(tmp_path / "shadows"),
            None,
            None,
            provider_runtime=rt,
        )
    )
    return TestClient(app)


def test_get_config_reports_the_level_and_the_support_map(tmp_path: Path) -> None:
    rt = ProviderRuntime(
        backend="openai_compatible",
        model="m",
        engines=[],
        transport=_Transport(),
        reasoning_effort=ReasoningEffort.HIGH,
    )
    body = _client(tmp_path, rt).get("/v1/config").json()
    provider = body["provider"]
    assert provider["reasoning_effort"] == "high"
    assert provider["reasoning_effort_support"]["supported"] == ["high", "low"]
    assert provider["reasoning_effort_support"]["unsupported"]["max"] == "tops out at high"


def test_put_clamps_and_returns_the_effective_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _Transport()
    monkeypatch.setattr(runtime_mod, "build_transport", lambda backend, credentials=None: transport)
    monkeypatch.setattr(runtime_mod, "resolve_model", lambda backend: "m")

    async def _ok(t, model):
        return None

    monkeypatch.setattr(runtime_mod, "ping_transport", _ok)

    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=transport)
    res = _client(tmp_path, rt).put(
        "/v1/config/provider", json={"backend": "b", "reasoning_effort": "max"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reasoning_effort"] == "high"
    assert "tops out at high" in body["reasoning_effort_note"]


def test_put_without_the_field_leaves_the_level_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _Transport()
    monkeypatch.setattr(runtime_mod, "build_transport", lambda backend, credentials=None: transport)
    monkeypatch.setattr(runtime_mod, "resolve_model", lambda backend: "m")

    async def _ok(t, model):
        return None

    monkeypatch.setattr(runtime_mod, "ping_transport", _ok)

    rt = ProviderRuntime(
        backend="b",
        model="m",
        engines=[],
        transport=transport,
        reasoning_effort=ReasoningEffort.LOW,
    )
    body = _client(tmp_path, rt).put("/v1/config/provider", json={"backend": "b"}).json()
    assert body["reasoning_effort"] == "low"


def test_an_unrecognized_level_is_rejected(tmp_path: Path) -> None:
    rt = ProviderRuntime(backend="b", model="m", engines=[], transport=_Transport())
    res = _client(tmp_path, rt).put(
        "/v1/config/provider", json={"backend": "b", "reasoning_effort": "banana"}
    )
    assert res.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_route.py`
Expected: FAIL — `KeyError: 'reasoning_effort'`

- [ ] **Step 3: Extend the request model**

In `routes.py`, add to `ProviderSwapRequest` (after `context_window`):

```python
    # Absent means "leave the rung as it is", matching context_window: a model-only
    # hot-swap from the composer must not reset the dial. An unrecognized value is
    # a 400 rather than a silent fallback — the user picked something, and quietly
    # ignoring it is how a dial becomes a placebo.
    reasoning_effort: str | None = None
```

- [ ] **Step 4: Report it from `get_config`**

Inside the `"provider"` dict in `get_config`, after `"context_window"`:

```python
                    "reasoning_effort": (
                        provider_runtime.reasoning_effort.value  # type: ignore[attr-defined]
                        if getattr(provider_runtime, "reasoning_effort", None) is not None
                        else None
                    ),
                    "reasoning_effort_support": _effort_support_payload(
                        await provider_runtime.effort_support()  # type: ignore[attr-defined]
                    ),
```

Add this helper at module scope in `routes.py`:

```python
def _effort_support_payload(support: object) -> dict[str, object]:
    """EffortSupport to JSON. Sorted so the payload is stable across restarts and
    the frontend's dedup/diffing never sees spurious churn."""
    return {
        "supported": sorted(level.value for level in support.supported),  # type: ignore[attr-defined]
        "unsupported": {
            level.value: why
            for level, why in support.unsupported.items()  # type: ignore[attr-defined]
        },
    }
```

- [ ] **Step 5: Accept it on the PUT**

In `put_config_provider`, before the `try:`:

```python
        from agentd.providers.reasoning_effort import parse_effort

        requested_effort = None
        if body.reasoning_effort is not None:
            requested_effort = parse_effort(body.reasoning_effort)
            if requested_effort is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown reasoning_effort {body.reasoning_effort!r}",
                )
```

and pass it into the swap call:

```python
                reasoning_effort=requested_effort,
```

- [ ] **Step 6: Seed from the environment in `main.py`**

In the `ProviderRuntime(...)` construction (~line 345), add:

```python
        transport=transport,
        reasoning_effort=parse_effort(os.getenv("CRUCIBLE_REASONING_EFFORT")),
```

and import `parse_effort` alongside the other provider imports at line ~144:

```python
    from agentd.providers.reasoning_effort import parse_effort
```

Leaving `CRUCIBLE_REASONING_EFFORT` unset yields `None`, so `set_reasoning_effort` is never called and each transport keeps its own env-configured behavior untouched. That is the spec's per-provider fallback, achieved with no extra code.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_route.py tests/test_provider_hotswap.py`
Expected: PASS

- [ ] **Step 8: Run the whole backend suite**

Run: `cd services/agentd-py && pytest --color=no > /tmp/effort-suite.txt 2>&1; echo exit=$?; tail -5 /tmp/effort-suite.txt`
Expected: `exit=0` and a summary line with 0 failures

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/agentd/main.py services/agentd-py/tests/test_provider_effort_route.py
git commit -m "feat(api): expose and accept reasoning effort on the provider config routes"
```

---

### Task 10: Live NIM probe and the nemotron capability

**Files:**
- Modify: `services/agentd-py/agentd/providers/openai_compatible_transport.py` (only if the probe says the generic form is rejected)
- Create: `scripts/verify/probe_reasoning_effort.py`

**Interfaces:**
- Consumes: the wire mapping from Task 2.
- Produces: a verified answer to "does NIM honor top-level `reasoning_effort` for nemotron-3", and — only if it does not — a model-specific `chat_template_kwargs` branch.

This is the one thing unit tests cannot answer. The repo's own history (the NIM `oneOf`/`json_schema` findings, the gopls unacked-request hang) is a list of cases where the reasonable assumption was wrong and only a live probe found it.

- [ ] **Step 1: Write the probe script**

```python
# scripts/verify/probe_reasoning_effort.py
"""Ask a live OpenAI-compatible endpoint which reasoning-effort form it honors.

Run:
  cd services/agentd-py && source .venv/bin/activate && cd -
  export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
  python scripts/verify/probe_reasoning_effort.py

Judges by REASONING TOKEN COUNT, not by HTTP status: an endpoint that ignores an
unknown body field returns 200 and simply reasons just as much, which is exactly
the silent no-op this probe exists to catch.
"""
import asyncio
import os

from openai import AsyncOpenAI

PROMPT = "What is 17 * 23? Answer with the number only."


async def _measure(client: AsyncOpenAI, model: str, extra: dict) -> tuple[int, int]:
    res = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT}],
        max_completion_tokens=2048,
        temperature=1.0,
        extra_body=extra,
    )
    usage = res.usage
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) or 0
    return usage.completion_tokens, reasoning


async def main() -> None:
    client = AsyncOpenAI(
        base_url=os.environ["CRUCIBLE_OPENAI_COMPAT_BASE_URL"],
        api_key=os.environ.get("CRUCIBLE_OPENAI_COMPAT_API_KEY", "x"),
    )
    model = os.environ["CRUCIBLE_OPENAI_COMPAT_MODEL"]
    cases = {
        "baseline (no effort)": {},
        "generic reasoning_effort=low": {"reasoning_effort": "low"},
        "generic reasoning_effort=none": {"reasoning_effort": "none"},
        "nim chat_template_kwargs low_effort": {
            "chat_template_kwargs": {"enable_thinking": True, "low_effort": True}
        },
        "nim chat_template_kwargs off": {
            "chat_template_kwargs": {"enable_thinking": False}
        },
    }
    for label, extra in cases.items():
        try:
            total, reasoning = await _measure(client, model, extra)
            print(f"{label:42} completion={total:6}  reasoning={reasoning:6}")
        except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
            print(f"{label:42} ERROR: {exc}")


asyncio.run(main())
```

- [ ] **Step 2: Run the probe against the live endpoint**

Run:
```bash
cd services/agentd-py && source .venv/bin/activate && cd -
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
python scripts/verify/probe_reasoning_effort.py
```
Expected: five lines of token counts (or errors).

- [ ] **Step 3: Apply the decision rule**

- **`generic reasoning_effort=low` reasons measurably less than baseline** → the Task 2 mapping is correct. Record the numbers in the commit message and make **no code change**. Skip step 4.
- **generic form errors, or reasons the same as baseline, while the `chat_template_kwargs` cases do differ** → NIM does not expose the vLLM front door for this model. Do step 4.
- **Every case matches baseline** → this model ignores effort entirely. Do step 4 but with `unsupported` covering every rung except OFF, and say so in the commit message — the chip will then correctly show the dial as unavailable rather than pretending.

- [ ] **Step 4 (conditional): Add the nemotron branch**

Only if step 3 selected it. In `openai_compatible_transport.py`, add:

```python
# NIM exposes nemotron's reasoning through chat_template_kwargs rather than the
# vLLM front door — measured live, see scripts/verify/probe_reasoning_effort.py.
# Three real rungs only: off, low_effort, full.
_NEMOTRON_EFFORT_KWARGS: dict[ReasoningEffort, dict[str, object]] = {
    ReasoningEffort.OFF: {"enable_thinking": False},
    ReasoningEffort.LOW: {"enable_thinking": True, "low_effort": True},
    ReasoningEffort.MEDIUM: {"enable_thinking": True, "low_effort": True},
    ReasoningEffort.HIGH: {"enable_thinking": True},
    ReasoningEffort.MAX: {"enable_thinking": True},
}


def _uses_chat_template_effort(model: str) -> bool:
    return "nemotron" in model.lower()
```

In `_build_extra_body`, replace the effort branch with:

```python
        if self._reasoning_effort is not None:
            if _uses_chat_template_effort(model):
                extra_body["chat_template_kwargs"] = dict(
                    _NEMOTRON_EFFORT_KWARGS[self._reasoning_effort]
                )
            else:
                extra_body["reasoning_effort"] = _OPENAI_COMPAT_EFFORT_WIRE[
                    self._reasoning_effort
                ]
```

and in `reasoning_effort_support`, before the return:

```python
        if _uses_chat_template_effort(model):
            return EffortSupport(
                supported=frozenset(
                    {ReasoningEffort.OFF, ReasoningEffort.LOW, ReasoningEffort.HIGH}
                ),
                unsupported={
                    ReasoningEffort.MEDIUM: "nemotron exposes only low_effort and full",
                    ReasoningEffort.MAX: "nemotron exposes only low_effort and full",
                    **self._effort_rejected,
                },
            )
```

Add a test to `tests/test_provider_effort_wire.py`:

```python
def test_nemotron_uses_chat_template_kwargs_not_the_flat_field():
    t = _transport()
    t.set_reasoning_effort(ReasoningEffort.LOW)
    body = t._build_extra_body("nvidia/nemotron-3-ultra", True, for_json=True)
    assert body["chat_template_kwargs"] == {"enable_thinking": True, "low_effort": True}
    assert "reasoning_effort" not in body
```

- [ ] **Step 5: Run the tests**

Run: `cd services/agentd-py && pytest tests/test_provider_effort_wire.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/verify/probe_reasoning_effort.py services/agentd-py/agentd/providers/openai_compatible_transport.py services/agentd-py/tests/test_provider_effort_wire.py
git commit -m "test(providers): probe NIM live and pin the nemotron effort mapping"
```

---

### Task 11: editor-client contract

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`BackendConfigSchema` at 324, `BackendTaskClient.setProvider` at 477), `apps/editor-client/src/client/http-backend-client.ts` (`getConfig` at 566, `mapProvider` at 578, `setProvider` at 618)
- Test: `apps/editor-client/test/http-backend-client.test.ts` (append; if the file name differs, use the existing client test file)

**Interfaces:**
- Consumes: the route shapes from Task 9.
- Produces: `ReasoningEffortSchema` (`z.enum(["off","low","medium","high","max"])`), `EffortSupportSchema` (`{supported: ReasoningEffort[], unsupported: Record<string,string>}`), `BackendConfig.provider.reasoningEffort` / `.reasoningEffortSupport`; `setProvider` accepts `reasoningEffort?: ReasoningEffort` and returns `{backend, model, reasoningEffort, reasoningEffortNote, reasoningEffortSupport}`.

- [ ] **Step 1: Write the failing tests**

```ts
// apps/editor-client/test/http-backend-client.test.ts (append)
import { describe, expect, test } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

// The constructor takes an options OBJECT ({baseUrl, fetchFn}) and fetchFn must
// return a real Response — this is the idiom the existing tests in this file use.
function jsonClient(payload: unknown, captured: { body?: string }) {
  return new HttpBackendClient({
    baseUrl: "http://localhost:8000",
    fetchFn: async (_url, init) => {
      captured.body = (init?.body as string) ?? "";
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });
}

describe("HttpBackendClient reasoning effort", () => {
  test("maps the snake_case config payload onto camelCase", async () => {
    const captured: { body?: string } = {};
    const client = jsonClient(
      {
        task_subsystem_enabled: false,
        chat_controller_enabled: true,
        memory_enabled: true,
        skills_enabled: false,
        mcp_enabled: false,
        provider: {
          backend: "openai_compatible",
          model: "nvidia/nemotron-3",
          reasoning_effort: "high",
          reasoning_effort_support: {
            supported: ["off", "low", "high"],
            unsupported: { max: "tops out at high" },
          },
        },
      },
      captured
    );
    const config = await client.getConfig();
    expect(config.provider?.reasoningEffort).toBe("high");
    expect(config.provider?.reasoningEffortSupport?.supported).toEqual(["off", "low", "high"]);
    expect(config.provider?.reasoningEffortSupport?.unsupported["max"]).toBe("tops out at high");
  });

  test("omits reasoning_effort from the PUT body when the caller has nothing to say", async () => {
    const captured: { body?: string } = {};
    const client = jsonClient({ backend: "b", model: "m" }, captured);
    await client.setProvider({ backend: "b" });
    expect("reasoning_effort" in JSON.parse(captured.body ?? "{}")).toBe(false);
  });

  test("sends the level and reads back the effective one", async () => {
    const captured: { body?: string } = {};
    const client = jsonClient(
      {
        backend: "b",
        model: "m",
        reasoning_effort: "high",
        reasoning_effort_note: "max unavailable here",
        reasoning_effort_support: { supported: ["high"], unsupported: {} },
      },
      captured
    );
    const res = await client.setProvider({ backend: "b", reasoningEffort: "max" });
    expect(JSON.parse(captured.body ?? "{}").reasoning_effort).toBe("max");
    expect(res.reasoningEffort).toBe("high");
    expect(res.reasoningEffortNote).toContain("unavailable");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm run -w @crucible/editor-client test`
Expected: FAIL — `reasoningEffort` is undefined

- [ ] **Step 3: Add the schemas**

In `task-contracts.ts`, above `BackendConfigSchema`:

```ts
export const ReasoningEffortSchema = z.enum(["off", "low", "medium", "high", "max"]);
export type ReasoningEffort = z.infer<typeof ReasoningEffortSchema>;

// Tri-state by omission: a rung in neither collection is UNKNOWN — the endpoint's
// capability is unverified, which the chip renders differently from "unsupported".
export const EffortSupportSchema = z.object({
  supported: z.array(ReasoningEffortSchema),
  unsupported: z.record(z.string(), z.string()),
});
export type EffortSupport = z.infer<typeof EffortSupportSchema>;
```

Extend the `provider` object inside `BackendConfigSchema`:

```ts
    reasoningEffort: ReasoningEffortSchema.nullable().optional(),
    reasoningEffortSupport: EffortSupportSchema.optional(),
```

Update the `setProvider` signature in `BackendTaskClient`:

```ts
  setProvider(req: { backend: string; model?: string; credentials?: Record<string, string>; contextWindow?: number; reasoningEffort?: ReasoningEffort }): Promise<{ backend: string; model: string; reasoningEffort?: ReasoningEffort | null; reasoningEffortNote?: string | null; reasoningEffortSupport?: EffortSupport }>;
```

- [ ] **Step 4: Map the payloads**

In `http-backend-client.ts`, extend `mapProvider`:

```ts
      ...(p["reasoning_effort"] !== undefined ? { reasoningEffort: p["reasoning_effort"] } : {}),
      ...(p["reasoning_effort_support"] !== undefined
        ? { reasoningEffortSupport: p["reasoning_effort_support"] }
        : {}),
```

Extend `setProvider`'s request body (alongside the `context_window` spread):

```ts
        ...(req.reasoningEffort !== undefined ? { reasoning_effort: req.reasoningEffort } : {}),
```

and its return:

```ts
    return {
      backend: String(raw["backend"]),
      model: String(raw["model"]),
      ...(raw["reasoning_effort"] !== undefined
        ? { reasoningEffort: raw["reasoning_effort"] as ReasoningEffort | null }
        : {}),
      ...(raw["reasoning_effort_note"] !== undefined
        ? { reasoningEffortNote: raw["reasoning_effort_note"] as string | null }
        : {}),
      ...(raw["reasoning_effort_support"] !== undefined
        ? { reasoningEffortSupport: EffortSupportSchema.parse(raw["reasoning_effort_support"]) }
        : {}),
    };
```

Add `ReasoningEffort`, `EffortSupportSchema`, and `EffortSupport` to the imports at the top of the file, and export the new schemas from the package index if `task-contracts` re-exports are explicit there.

- [ ] **Step 5: Run the tests and build**

Run: `npm run -w @crucible/editor-client test && npm run -w @crucible/editor-client build`
Expected: PASS, then a clean build (the extension types off `dist/`)

- [ ] **Step 6: Commit**

```bash
git add apps/editor-client/src apps/editor-client/test
git commit -m "feat(editor-client): carry reasoning effort and its support map"
```

---

### Task 12: Host plumbing and managed-spawn env

**Files:**
- Modify: `apps/vscode-extension/src/composer-models.ts`, `src/extension.ts` (~line 88 `composerModelState`), `src/chat-panel.ts` (~line 334 message routing), `src/runtime/vscode-runtime.ts`, `src/runtime/backend-process.ts` (`buildBackendEnv`)
- Also modify: `scripts/stress/start-backend.sh`, `.env`
- Test: `apps/vscode-extension/test/composer-models.test.ts` (append)

**Interfaces:**
- Consumes: `getConfig()` / `setProvider()` from Task 11.
- Produces: `composerModelState()` additionally returns `effort: { level: ReasoningEffort | null; support: EffortSupport | null; note?: string | null }`, carried on the existing `modelList` webview message; a new inbound `setReasoningEffort` webview message; `RuntimeManager.saveReasoningEffort(level)` / `.getReasoningEffort()`; `CRUCIBLE_REASONING_EFFORT` in the managed spawn env.

Reusing the existing `modelList` round-trip rather than adding a route keeps the chip's data on the same refresh as the model list, which is required anyway since capability is per-`(backend, model)`.

- [ ] **Step 1: Write the failing test**

```ts
// apps/vscode-extension/test/composer-models.test.ts (append)
import { describe, expect, it } from "vitest";
import { buildEffortRows } from "../src/composer-models.js";

describe("buildEffortRows", () => {
  it("marks each rung supported, unsupported, or unknown", () => {
    const rows = buildEffortRows({
      supported: ["off", "low", "high"],
      unsupported: { max: "tops out at high" },
    });
    expect(rows.map((r) => r.level)).toEqual(["off", "low", "medium", "high", "max"]);
    expect(rows.find((r) => r.level === "low")?.state).toBe("supported");
    expect(rows.find((r) => r.level === "max")?.state).toBe("unsupported");
    expect(rows.find((r) => r.level === "max")?.reason).toBe("tops out at high");
    // Not listed either way: unverified, still selectable.
    expect(rows.find((r) => r.level === "medium")?.state).toBe("unknown");
  });

  it("treats a null support map as entirely unknown rather than unsupported", () => {
    const rows = buildEffortRows(null);
    expect(rows.every((r) => r.state === "unknown")).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run -w crucible-vscode-extension test`
Expected: FAIL — `buildEffortRows` is not exported

- [ ] **Step 3: Add the pure row builder**

Append to `apps/vscode-extension/src/composer-models.ts`:

```ts
import type { EffortSupport, ReasoningEffort } from "@crucible/editor-client";

export const EFFORT_LADDER: ReasoningEffort[] = ["off", "low", "medium", "high", "max"];

export interface EffortRow {
  level: ReasoningEffort;
  state: "supported" | "unsupported" | "unknown";
  reason?: string;
}

/** All five rungs, always, tagged with what we know about each.
 *
 * A null support map means the backend told us nothing (older backend, or a
 * capability lookup that failed) — that is UNKNOWN across the board, never
 * unsupported, so the chip stays usable instead of appearing broken. */
export function buildEffortRows(support: EffortSupport | null): EffortRow[] {
  return EFFORT_LADDER.map((level) => {
    if (support?.supported.includes(level)) return { level, state: "supported" as const };
    const reason = support?.unsupported[level];
    if (reason !== undefined) return { level, state: "unsupported" as const, reason };
    return { level, state: "unknown" as const };
  });
}
```

- [ ] **Step 4: Extend the host state and message routing**

In `extension.ts`, extend `composerModelState`'s return (it already awaits `getConfig()`):

```ts
    return {
      current,
      options: buildModelOptions(current, keyed, PROVIDERS),
      effort: {
        level: config.provider?.reasoningEffort ?? null,
        support: config.provider?.reasoningEffortSupport ?? null,
      },
    };
```

Add a host handler beside the existing `onSetModel` closure:

```ts
  const setReasoningEffort = async (level: ReasoningEffort) => {
    const config = await controller.configClient().getConfig();
    const backend = config.provider?.backend;
    if (!backend) throw new Error("no provider configured");
    const res = await controller.configClient().setProvider({ backend, reasoningEffort: level });
    // Persist what the backend ACTUALLY applied, not what was asked — a clamped
    // rung must not come back on the next managed spawn as the unclamped one.
    await runtimeManager.saveReasoningEffort(res.reasoningEffort ?? level);
    return { ...(await composerModelState()), effortNote: res.reasoningEffortNote ?? null };
  };
```

Pass it into the `ChatPanel` constructor alongside `onSetModel`, and in `chat-panel.ts` add a branch mirroring the `setModel` one (which already handles its own errors so a failure never re-enables the composer):

```ts
      } else if (m["type"] === "setReasoningEffort") {
        p = (async () => {
          try {
            const result = await this.onSetReasoningEffort(m["level"] as ReasoningEffort);
            this.panel?.webview.postMessage({ type: "modelList", ...result });
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.panel?.webview.postMessage({ type: "effortSwapError", message });
          }
        })();
```

- [ ] **Step 5: Persist and inject the env**

In `src/runtime/vscode-runtime.ts`, beside the existing provider-model persistence:

```ts
  saveReasoningEffort(level: string): Thenable<void> {
    return this.context.globalState.update("crucible.reasoningEffort", level);
  }

  getReasoningEffort(): string | undefined {
    return this.context.globalState.get<string>("crucible.reasoningEffort");
  }
```

In `buildBackendEnv` in `src/runtime/backend-process.ts`, alongside the other optional env entries:

```ts
    // Third of the three env sites a new backend flag needs (start-backend.sh and
    // the repo-root .env are the other two); the managed spawn reads neither of
    // those, so omitting this here is how a persisted dial silently does nothing.
    ...(reasoningEffort ? { CRUCIBLE_REASONING_EFFORT: reasoningEffort } : {}),
```

threading `reasoningEffort` in from `runtimeManager.getReasoningEffort()` at the call site.

Add to `scripts/stress/start-backend.sh` beside the other exports:

```bash
export CRUCIBLE_REASONING_EFFORT="${CRUCIBLE_REASONING_EFFORT:-}"
```

and a commented line to the repo-root `.env`:

```
# off | low | medium | high | max — unset leaves each provider's own dial alone
# CRUCIBLE_REASONING_EFFORT=medium
```

- [ ] **Step 6: Run the tests and typecheck**

Run: `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS both

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/src apps/vscode-extension/test scripts/stress/start-backend.sh .env
git commit -m "feat(vscode-extension): plumb the reasoning-effort dial through the host"
```

---

### Task 13: The composer chip

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/EffortMenu.tsx`, `webview-ui/src/test/EffortMenu.test.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (mirror types + `setReasoningEffort` in the outbound union at ~line 228), `webview-ui/src/components/InputArea.tsx` (place the chip next to `<ModelMenu />`)

**Interfaces:**
- Consumes: the `modelList` message's `effort` field and `effortSwapError` from Task 12; `buildEffortRows`' row shape (mirrored locally — the webview never imports `src/`).
- Produces: `<EffortMenu />`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/vscode-extension/webview-ui/src/test/EffortMenu.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EffortMenu } from "../components/EffortMenu";

function sendModelList(effort: unknown) {
  window.dispatchEvent(new MessageEvent("message", { data: { type: "modelList", effort } }));
}

describe("EffortMenu", () => {
  it("shows the current rung on the chip", () => {
    render(<EffortMenu />);
    sendModelList({ level: "high", support: { supported: ["high"], unsupported: {} } });
    expect(screen.getByRole("button", { name: /high/i })).toBeTruthy();
  });

  it("disables an unsupported rung and shows why", () => {
    render(<EffortMenu />);
    sendModelList({
      level: "high",
      support: { supported: ["off", "low", "high"], unsupported: { max: "tops out at high" } },
    });
    fireEvent.click(screen.getByRole("button", { name: /high/i }));
    const max = screen.getByRole("menuitem", { name: /max/i });
    expect(max.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/tops out at high/i)).toBeTruthy();
  });

  it("marks an unverified rung selectable but flagged", () => {
    render(<EffortMenu />);
    sendModelList({ level: null, support: { supported: [], unsupported: {} } });
    fireEvent.click(screen.getByRole("button", { name: /effort/i }));
    const medium = screen.getByRole("menuitem", { name: /medium/i });
    expect(medium.hasAttribute("disabled")).toBe(false);
    expect(screen.getAllByText(/unverified/i).length).toBeGreaterThan(0);
  });

  it("surfaces a clamp note on the chip", () => {
    render(<EffortMenu />);
    window.dispatchEvent(
      new MessageEvent("message", {
        data: {
          type: "modelList",
          effort: { level: "high", support: { supported: ["high"], unsupported: { max: "x" } } },
          effortNote: "max unavailable here; using high.",
        },
      })
    );
    expect(screen.getByRole("button", { name: /no max/i })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run -w crucible-vscode-extension test -- EffortMenu`
Expected: FAIL — cannot resolve `../components/EffortMenu`

- [ ] **Step 3: Write the component**

```tsx
// apps/vscode-extension/webview-ui/src/components/EffortMenu.tsx
import { useEffect, useRef, useState } from "react";
import { vscode } from "../vscodeApi";

type Level = "off" | "low" | "medium" | "high" | "max";
type RowState = "supported" | "unsupported" | "unknown";

const LADDER: Level[] = ["off", "low", "medium", "high", "max"];
const LABEL: Record<Level, string> = {
  off: "Off",
  low: "Low",
  medium: "Medium",
  high: "High",
  max: "Max",
};

interface Support {
  supported: Level[];
  unsupported: Record<string, string>;
}

/**
 * EffortMenu — composer reasoning-effort chip + upward popover.
 *
 * All five rungs are always listed. Ones the active (backend, model) cannot
 * express are disabled with the provider's own reason; ones we have no evidence
 * about are selectable but flagged unverified — an arbitrary OpenAI-compatible
 * endpoint genuinely cannot be vouched for, and claiming support we don't have is
 * the silent-degradation failure this control exists to avoid.
 *
 * State arrives on the existing `modelList` message rather than a channel of its
 * own, because capability is per-(backend, model) and must refresh on model swap
 * anyway.
 */
export function EffortMenu() {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<Level | null>(null);
  const [support, setSupport] = useState<Support | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "modelList") {
        const effort = m["effort"] as { level: Level | null; support: Support | null } | undefined;
        if (effort) {
          setLevel(effort.level);
          setSupport(effort.support);
        }
        setNote((m["effortNote"] as string | null) ?? null);
        setError(null);
      } else if (m?.["type"] === "effortSwapError") {
        setError(m["message"] as string);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function stateOf(candidate: Level): RowState {
    if (support?.supported.includes(candidate)) return "supported";
    if (support?.unsupported[candidate] !== undefined) return "unsupported";
    return "unknown";
  }

  function choose(candidate: Level) {
    if (stateOf(candidate) === "unsupported") return;
    setOpen(false);
    vscode.postMessage({ type: "setReasoningEffort", level: candidate });
  }

  // "max unavailable here; using high." collapses to a chip-sized "no max".
  const clampHint = note ? note.split(" ")[0] : null;
  const chipLabel = level ? LABEL[level] : "Effort";

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={
          clampHint ? `Reasoning effort: ${chipLabel}, no ${clampHint}` : `Reasoning effort: ${chipLabel}`
        }
        className="menu-item flex items-center gap-1 text-[11px]"
      >
        <span>{chipLabel}</span>
        {clampHint ? (
          <span style={{ color: "var(--color-text-4)" }}>· no {clampHint}</span>
        ) : null}
      </button>

      {open ? (
        <div className="surface-card absolute bottom-full mb-1 right-0 z-10 min-w-[220px]" role="menu">
          {LADDER.map((candidate) => {
            const state = stateOf(candidate);
            return (
              <button
                key={candidate}
                type="button"
                role="menuitem"
                disabled={state === "unsupported"}
                onClick={() => choose(candidate)}
                className="menu-item w-full text-left text-[11px]"
              >
                <span>{LABEL[candidate]}</span>
                {state === "unsupported" ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>
                    {support?.unsupported[candidate]}
                  </span>
                ) : null}
                {state === "unknown" ? (
                  <span className="block" style={{ color: "var(--color-text-4)" }}>
                    unverified for this endpoint
                  </span>
                ) : null}
              </button>
            );
          })}
          {error ? (
            <div className="text-[11px]" style={{ color: "var(--color-red)" }}>
              {error}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Mirror the outbound message type and place the chip**

In `webview-ui/src/types.ts`, add to the outbound message union (beside `setModel` at ~line 228):

```ts
  | { type: "setReasoningEffort"; level: "off" | "low" | "medium" | "high" | "max" }
```

In `InputArea.tsx`, import `EffortMenu` and render it immediately before `<ModelMenu />` in the same flex row.

- [ ] **Step 5: Run the tests**

Run: `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS both

- [ ] **Step 6: Rebuild the webview bundle**

Run: `npm run build`
Expected: clean build. The webview is a separate Vite bundle — a stale `webview-ui/dist` will pin the old composer in the dev host.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/webview-ui/src
git commit -m "feat(webview): add the reasoning-effort chip to the composer"
```

---

### Task 14: Live measurement and documentation

**Files:**
- Modify: `CLAUDE.md` (the "Key Configuration → Python backend env vars" section and the provider notes)
- Test: manual, in a real dev host

**Interfaces:**
- Consumes: everything above.
- Produces: a measured before/after ratio, and the documentation a future session needs.

- [ ] **Step 1: Start a real backend**

Run:
```bash
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
bash scripts/stress/start-backend.sh --backend openai_compatible \
  --workspace "$PWD/workspaces/crucible-stress" --validation-profile none
curl -s http://localhost:8000/health
```
Expected: a healthy response.

- [ ] **Step 2: Confirm the capability map is real**

Run: `curl -s http://localhost:8000/v1/config | python3 -m json.tool`
Expected: `provider.reasoning_effort_support` present, with `unsupported` naming at least one rung and a human-readable reason.

- [ ] **Step 3: Open the dev host and measure both ends of the ladder**

Run: `code --extensionDevelopmentPath="$PWD/apps/vscode-extension" "$PWD/workspaces/crucible-stress"`

Then, in the chat panel: set the chip to **High**, send a task with several tool calls, and record the WorkBar's 🧠 / ↓ counts at the end of the turn. Start a new thread, set the chip to **Low**, send the *same* prompt, and record again.

- [ ] **Step 4: Judge the result**

The acceptance criterion is that the thinking count drops materially at `Low` while the output count stays in the same range. If the two runs are indistinguishable, the wire mapping is not being honored — re-run `scripts/verify/probe_reasoning_effort.py` and revisit Task 10's decision rule rather than shipping a placebo dial.

- [ ] **Step 5: Document it**

Add to `CLAUDE.md` under "Python backend env vars → Core":

```markdown
- `CRUCIBLE_REASONING_EFFORT` — unified reasoning-effort dial: `off | low | medium | high | max`.
  Unset (default) sends no effort parameter at all, which leaves each provider's own
  legacy dial (`CRUCIBLE_GEMINI_THINKING_LEVEL`, `CRUCIBLE_GROQ_REASONING_EFFORT`,
  `CRUCIBLE_OLLAMA_THINK`, TurboQuant's `thinking_budget`) exactly as it is today.
  Normally set from the composer chip, which persists to `globalState` and injects it
  on the next managed spawn; `PUT /v1/config/provider {reasoning_effort}` hot-applies
  it to every live transport without a restart. Rungs the active (backend, model)
  cannot express are clamped **downward** and the substitution is reported on the chip;
  the one upward case is Groq, which 400s on `"none"` so `off` becomes `low`. See
  `docs/superpowers/specs/2026-08-12-reasoning-effort-control-design.md`.
```

And add to the provider-specific notes:

```markdown
- **Reasoning effort is per-`(backend, model)`, tri-state.** `EffortSupport`
  (`agentd/providers/reasoning_effort.py`) distinguishes supported / unsupported-with-reason
  / **unknown** — a pasted `openai_compatible` endpoint is genuinely unverifiable, and
  unknown rungs are sent as-is rather than clamped. A probative 400 marks **only the
  offending rung** unsupported for the process; transient failures (429/5xx/timeout) mark
  nothing. This is deliberately narrower than the sticky JSON-mode downgrade, whose
  documented flaw is collapsing "cannot honor THIS" into "cannot honor ANY".
```

- [ ] **Step 6: Run the full suite one last time**

Run:
```bash
cd services/agentd-py && pytest --color=no > /tmp/effort-final.txt 2>&1; echo exit=$?; tail -5 /tmp/effort-final.txt
cd - && npm run test && npm run typecheck
```
Expected: `exit=0` for pytest; all TypeScript suites pass.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): document the unified reasoning-effort dial"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: the ladder and `EffortSupport` → Task 1; the transport contract and per-transport translation table → Tasks 2, 4, 5, 6, 7; capability resolution (registry-first) → Tasks 2 and 4; failure behavior → Task 3; config plumbing and the env fallback → Tasks 8 and 9; the three env sites → Task 12; the frontend contract, chip, and rendering states → Tasks 11, 12, 13; live probe and the acceptance criterion → Tasks 10 and 14. The spec's build order is preserved, with one deviation: the live NIM probe is Task 10 rather than gating Task 2, so the five other transports are not blocked behind access to a live key — Task 10's decision rule amends Task 2's mapping if the probe contradicts it, and Task 14 will not pass its acceptance criterion if that amendment was needed and skipped.

**Placeholder scan.** No TBD/TODO, no "add error handling", no "similar to Task N". Two steps are conditional on a measured result (Task 10 step 4, gated by an explicit decision rule) and two note that a signature should be checked against the real file before adapting (Task 7's TurboQuant `_build_body`, Task 11's `HttpBackendClient` constructor) — both name exactly what to check and what to preserve.

**Type consistency.** `ReasoningEffort` / `EffortSupport` / `LADDER` / `parse_effort` are defined in Task 1 and used unchanged throughout. `set_reasoning_effort` and `reasoning_effort_support` keep identical signatures across all six transports. `_effort_wire_value` (Groq) and `_effort_think_value` (Ollama) are deliberately distinct names for distinct return types (`str | None` vs `bool | str | None`) and each is defined in the task that uses it. The runtime's `apply_reasoning_effort` / `effort_support` are defined in Task 8 and consumed in Task 9. Frontend `reasoningEffort` / `reasoningEffortSupport` / `reasoningEffortNote` are defined in Task 11 and consumed in Tasks 12 and 13.

**One gap found and closed during review:** Task 8 originally had no way to reach a transport, because `swap` discards the one it builds and `ProviderRuntime` never held one. The `transport=` constructor parameter and the `self._transport = transport` assignment inside `swap` were added for that reason, and `main.py` (Task 9 step 6) passes the startup transport.
