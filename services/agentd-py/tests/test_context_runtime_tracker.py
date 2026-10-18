import asyncio

import pytest

from agentd.chat.context_usage import ContextUsageTracker


class Runtime:
    backend = 'chatgpt'
    model = 'm'
    context_window = 128000
    config_error = None

    def __init__(self):
        self.identity = ('chatgpt', 'm', 'session-a')
        self.listeners = []
        self.future = asyncio.get_running_loop().create_future()

    def context_identity(self):
        return self.identity

    def subscribe_context(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    async def context_capacity(self):
        return await self.future


@pytest.mark.asyncio
async def test_runtime_catalog_is_nonblocking_and_late_result_cannot_restore_input():
    runtime = Runtime()
    tracker = ContextUsageTracker(lambda *_: None)
    tracker.attach_runtime(runtime)
    snap = tracker.snapshot('a')
    assert snap.model == 'm'
    assert snap.status == 'awaiting_request'
    assert snap.context_window is None
    observer = tracker.observer('a', 't')
    observer.begin('m', {'input': 'hello'})
    # The meter's count lands from an executor thread; let it settle before taking
    # the timestamp the catalog result must not rewrite.
    for _ in range(200):
        if tracker.snapshot('a').input_quality == 'estimated':
            break
        await asyncio.sleep(0.005)
    timestamp = tracker.snapshot('a').observed_at
    assert timestamp is not None
    await asyncio.sleep(0)
    runtime.future.set_result((200000, 'catalog'))
    await asyncio.sleep(0)
    assert tracker.snapshot('a').capacity_source == 'chatgpt_catalog'
    assert tracker.snapshot('a').observed_at == timestamp
    runtime.identity = ('chatgpt', 'm', 'session-b')
    runtime.future = asyncio.get_running_loop().create_future()
    runtime.listeners[0]()
    assert tracker.snapshot('a').status == 'awaiting_request'
    observer.reported(999)
    assert tracker.snapshot('a').input_tokens is None
    await asyncio.sleep(0)
    runtime.future.set_result((None, 'unknown'))
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_runtime_external_identity_drift_retires_callback_and_configured_window_is_immediate():
    runtime = Runtime()
    runtime.backend = 'openai'
    tracker = ContextUsageTracker(lambda *_: None)
    tracker.attach_runtime(runtime)
    assert tracker.snapshot('a').context_window == 128000
    observer = tracker.observer('a', 't')
    observer.begin('m', {'input': 'hello'})
    runtime.identity = ('openai', 'new')
    runtime.model = 'new'
    observer.reported(99)
    assert tracker.snapshot('a').model == 'new'
    assert tracker.snapshot('a').input_tokens is None


@pytest.mark.asyncio
async def test_old_catalog_result_after_selection_change_is_discarded():
    runtime = Runtime()
    tracker = ContextUsageTracker(lambda *_: None)
    tracker.attach_runtime(runtime)
    await asyncio.sleep(0)
    old_future = runtime.future
    runtime.identity = ('chatgpt', 'new', 'session-a')
    runtime.model = 'new'
    runtime.future = asyncio.get_running_loop().create_future()
    runtime.listeners[0]()
    old_future.set_result((999, 'catalog'))
    await asyncio.sleep(0)
    assert tracker.snapshot('a').context_window is None
    await asyncio.sleep(0)
    runtime.future.set_result((321, 'catalog'))
    await asyncio.sleep(0)
    assert tracker.snapshot('a').context_window == 321


@pytest.mark.asyncio
async def test_close_cancels_lookup_unsubscribes_and_retires_callbacks():
    runtime = Runtime()
    tracker = ContextUsageTracker(lambda *_: None)
    tracker.attach_runtime(runtime)
    attempt = tracker.observer('a', 't').begin('m', {'input': 'hello'})
    await asyncio.sleep(0)
    await tracker.close()
    assert runtime.listeners == []
    assert tracker._capacity_tasks == {}
    snap = tracker.snapshot('a')
    attempt.reported(99)
    attempt.finish()
    assert tracker.snapshot('a') == snap
    assert snap.input_tokens is None
    assert snap.status == 'unavailable'
    await tracker.close()
