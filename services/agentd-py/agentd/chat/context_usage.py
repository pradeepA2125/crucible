"""Latest MAIN physical request input; never accounting or compaction state."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAX_SAFE = 2**53 - 1


def valid_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= _MAX_SAFE else None


class ContextUsage(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    thread_id: str
    provider: str
    model: str
    scope: Literal['main'] = 'main'
    process_id: str
    generation: int = Field(ge=0, le=_MAX_SAFE)
    revision: int = Field(ge=0, le=_MAX_SAFE)
    turn_id: str | None = None
    request_id: str | None = None
    status: Literal['awaiting_request', 'in_flight', 'last_request', 'invalidated', 'unavailable']
    input_tokens: int | None = Field(default=None, ge=0, le=_MAX_SAFE)
    input_quality: Literal['estimated', 'provider_reported', 'unavailable'] = 'unavailable'
    context_window: int | None = Field(default=None, gt=0, le=_MAX_SAFE)
    capacity_source: Literal['chatgpt_catalog', 'claude_catalog', 'configured', 'unknown'] = 'unknown'
    observed_at: str | None = None

    @model_validator(mode='after')
    def consistent(self) -> ContextUsage:
        if (self.input_tokens is None) != (self.input_quality == 'unavailable'):
            raise ValueError('input provenance mismatch')
        if self.status in ('awaiting_request', 'invalidated', 'unavailable') and self.input_tokens is not None:
            raise ValueError('inactive status cannot retain input')
        if (self.context_window is None) != (self.capacity_source == 'unknown'):
            raise ValueError('capacity provenance mismatch')
        if (self.observed_at is None) != (self.input_tokens is None):
            raise ValueError('observation time mismatch')
        if self.observed_at is not None:
            parsed = datetime.fromisoformat(self.observed_at.replace('Z', '+00:00'))
            if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
                raise ValueError('observation time must be UTC')
        return self


class ContextUsageTracker:
    def __init__(self, publish: Callable[[str, dict], None]) -> None:
        self.process_id = uuid4().hex
        self._publish = publish
        self._states: dict[str, ContextUsage] = {}
        self._epochs: dict[str, int] = {}
        self._identity: tuple = ()
        self._provider = 'unknown'
        self._model = 'unknown'
        self._window: int | None = None
        self._source = 'unknown'
        self._runtime = None
        self._closed = False
        self._unsubscribe: Callable[[], None] | None = None
        self._refreshing = False
        self._capacity_tasks: dict[tuple, asyncio.Task] = {}
        self._capacity_checked: dict[tuple, float] = {}

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = list(self._capacity_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._capacity_tasks.clear()
        for thread in list(self._states):
            self.invalidate(thread, status='unavailable')

    def attach_runtime(self, runtime) -> None:
        if self._closed:
            return
        self._runtime = runtime
        self._unsubscribe = runtime.subscribe_context(self.refresh_runtime)
        self.refresh_runtime()

    def refresh_runtime(self) -> None:
        runtime = self._runtime
        if runtime is None or self._refreshing or self._closed:
            return
        self._refreshing = True
        try:
            identity = runtime.context_identity()
            changed = identity != self._identity
            if changed:
                window = runtime.context_window if runtime.backend not in ('chatgpt', 'claude_subscription') else None
                self.select(provider=runtime.backend, model=runtime.model, identity=identity,
                            window=window, source='configured' if window else 'unknown')
            if runtime.backend not in ('chatgpt', 'claude_subscription') or identity in self._capacity_tasks:
                return
            if monotonic() - self._capacity_checked.get(identity, float('-inf')) < 60:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return

            async def resolve() -> None:
                try:
                    window, source = await runtime.context_capacity()
                    self.refresh_runtime()
                    self.enrich(identity, window,
                                'chatgpt_catalog' if source == 'catalog' else source)
                except Exception:
                    pass  # best-effort telemetry; inference never awaits this task
                finally:
                    self._capacity_tasks.pop(identity, None)
                    self._capacity_checked[identity] = monotonic()

            self._capacity_tasks[identity] = loop.create_task(resolve())
        finally:
            self._refreshing = False

    def capacity(self) -> tuple[int | None, str, str]:
        """Cached runtime capacity/provenance only; no UI occupancy or chat-path network await."""
        self.refresh_runtime()
        return self._window, self._source, self._model

    def snapshot(self, thread: str) -> ContextUsage:
        self.refresh_runtime()
        if thread not in self._states:
            self._states[thread] = ContextUsage(
                thread_id=thread, provider=self._provider, model=self._model,
                process_id=self.process_id, generation=0, revision=0,
                status='awaiting_request' if self._identity else 'unavailable',
                context_window=self._window, capacity_source=self._source)
        return self._states[thread]

    def _put(self, thread: str, **changes: object) -> None:
        previous = self.snapshot(thread)
        current = ContextUsage.model_validate({**previous.model_dump(), **changes,
                                               'revision': previous.revision + 1})
        self._states[thread] = current
        try:
            self._publish(thread, current.model_dump())
        except Exception:
            pass  # telemetry never fails a request

    def select(self, *, provider: str, model: str, identity: tuple,
               window: int | None, source: str) -> None:
        changed = identity != self._identity
        self._provider, self._model, self._identity = provider, model, identity
        self._window = window if valid_count(window) and window > 0 else None
        self._source = source if self._window is not None else 'unknown'
        if changed:
            for thread in list(self._states):
                self.invalidate(thread, status='awaiting_request')
        else:
            self.enrich(identity, self._window, self._source)

    def enrich(self, identity: tuple, window: int | None, source: str) -> None:
        if identity != self._identity:
            return
        self._window = window if valid_count(window) and window > 0 else None
        self._source = source if self._window is not None else 'unknown'
        for thread, snap in list(self._states.items()):
            if (snap.context_window, snap.capacity_source) != (self._window, self._source):
                self._put(thread, context_window=self._window, capacity_source=self._source)

    def invalidate(self, thread: str, *, status: str = 'invalidated') -> None:
        self._epochs[thread] = self._epochs.get(thread, 0) + 1
        previous = self.snapshot(thread)
        self._put(thread, generation=previous.generation + 1, status=status,
                  provider=self._provider, model=self._model,
                  input_tokens=None, input_quality='unavailable', observed_at=None,
                  turn_id=None, request_id=None, context_window=self._window,
                  capacity_source=self._source)

    def observer(self, thread: str, turn: str | None) -> RequestObserver:
        self.snapshot(thread)
        return RequestObserver(self, thread, turn)


class RequestObserver:
    """Logical call factory; transports must retain the returned physical handle."""
    def __init__(self, tracker: ContextUsageTracker, thread: str, turn: str | None) -> None:
        self._tracker, self._thread, self._turn = tracker, thread, turn
        self._epoch = tracker._epochs.get(thread, 0)
        self._identity = tracker._identity
        self._attempt: PhysicalObservation | None = None

    def _alive(self) -> bool:
        self._tracker.refresh_runtime()
        return (not self._tracker._closed and self._identity == self._tracker._identity
                and self._epoch == self._tracker._epochs.get(self._thread, 0))

    def unavailable(self) -> None:
        if self._alive():
            self._tracker.invalidate(self._thread, status='unavailable')

    def begin(self, model: str, body: dict) -> PhysicalObservation | None:
        if not self._alive() or model != self._tracker._model:
            return None
        from agentd.providers.context_observation import estimate_request_input
        try:
            estimate = valid_count(estimate_request_input(model, body))
        except (TypeError, ValueError):
            estimate = None
        generation = self._tracker.snapshot(self._thread).generation + 1
        self._tracker._put(self._thread, generation=generation, model=model,
                           turn_id=self._turn, request_id=uuid4().hex, status='in_flight',
                           input_tokens=estimate,
                           input_quality='estimated' if estimate is not None else 'unavailable',
                           observed_at=datetime.now(UTC).isoformat().replace('+00:00', 'Z') if estimate is not None else None)
        self._attempt = PhysicalObservation(self, generation)
        return self._attempt

    def reported(self, value: object) -> None:
        # Compatibility for single-attempt callers; transport callbacks use handles.
        if self._attempt is not None:
            self._attempt.reported(value)

    def finish(self) -> None:
        if self._attempt is not None:
            self._attempt.finish()


class PhysicalObservation:
    """A callback handle bound permanently to one physical dispatch generation."""
    def __init__(self, logical: RequestObserver, generation: int) -> None:
        self._logical = logical
        self._generation = generation
        self._finished = False

    def _current(self) -> bool:
        logical = self._logical
        return (not self._finished and logical._alive()
                and self._generation == logical._tracker.snapshot(logical._thread).generation)

    def reported(self, value: object) -> None:
        count = valid_count(value)
        if not self._current() or count is None:
            return
        logical = self._logical
        logical._tracker._put(logical._thread, input_tokens=count, input_quality='provider_reported',
                              observed_at=datetime.now(UTC).isoformat().replace('+00:00', 'Z'))

    def finish(self) -> None:
        if self._current():
            logical = self._logical
            snap = logical._tracker.snapshot(logical._thread)
            if snap.status == 'in_flight':
                logical._tracker._put(logical._thread, status='last_request' if snap.input_tokens is not None else 'unavailable')
        self._finished = True