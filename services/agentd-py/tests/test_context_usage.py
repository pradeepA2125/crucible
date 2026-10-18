import importlib

import pytest


def manager():
    module = importlib.import_module('agentd.chat.context_usage')
    events = []
    state = module.ContextUsageTracker(lambda thread, value: events.append((thread, value)))
    state.select(provider='openai', model='m', identity=('openai', 'm'), window=100, source='configured')
    return state, events


def test_restart_is_unavailable_and_process_unique():
    module = importlib.import_module('agentd.chat.context_usage')
    state = module.ContextUsageTracker(lambda *_: None)
    other = module.ContextUsageTracker(lambda *_: None)
    snap = state.snapshot('a')
    assert snap.input_tokens is None
    assert snap.input_quality == 'unavailable'
    assert snap.status == 'unavailable'
    assert snap.observed_at is None
    assert snap.process_id != other.snapshot('a').process_id


def test_latest_request_replaces_and_retired_callbacks_are_ignored():
    state, events = manager()
    first = state.observer('a', 'turn')
    first.begin('m', {'input': 'hello'})
    first.reported(81)
    first.finish()
    second = state.observer('a', 'turn')
    second.begin('m', {'input': 'new'})
    assert state.snapshot('a').input_quality == 'estimated'
    first.reported(999)
    first.finish()
    second.reported(0)
    second.finish()
    snap = state.snapshot('a')
    assert snap.input_tokens == 0
    assert snap.input_quality == 'provider_reported'
    assert snap.status == 'last_request'
    assert snap.generation == 2
    assert events[-1][1] == snap.model_dump()
    assert state.snapshot('b').input_tokens is None


@pytest.mark.parametrize('invalid', [None, True, -1, '12', 1.5, 2**53])
def test_invalid_report_keeps_estimate(invalid):
    state, _ = manager()
    obs = state.observer('a', None)
    obs.begin('m', {'input': 'abcdefgh'})
    estimated = state.snapshot('a').input_tokens
    obs.reported(invalid)
    obs.finish()
    assert state.snapshot('a').input_tokens == estimated
    assert state.snapshot('a').input_quality == 'estimated'


def test_invalidation_retires_entire_observer_and_preserves_capacity():
    state, _ = manager()
    obs = state.observer('a', 't')
    obs.begin('m', {'input': 'hello'})
    state.invalidate('a')
    tombstone = state.snapshot('a')
    obs.reported(82)
    obs.finish()
    obs.begin('m', {'input': 'late retry'})
    assert state.snapshot('a') == tombstone
    assert tombstone.status == 'invalidated'
    assert tombstone.input_tokens is None
    assert tombstone.context_window == 100


def test_selection_clears_old_input_and_does_not_fallback_catalog():
    state, _ = manager()
    obs = state.observer('a', None)
    obs.begin('m', {'input': 'hello'})
    obs.reported(20)
    state.select(provider='chatgpt', model='new', identity=('chatgpt', 'new', 'session'), window=None, source='unknown')
    snap = state.snapshot('a')
    assert snap.model == 'new'
    assert snap.status == 'awaiting_request'
    assert snap.context_window is None
    assert snap.input_tokens is None
    obs.reported(30)
    assert state.snapshot('a') == snap


def test_capacity_enrichment_does_not_rewrite_observation_or_resurrect_input():
    state, _ = manager()
    obs = state.observer('a', None)
    obs.begin('m', {'input': 'hello'})
    timestamp = state.snapshot('a').observed_at
    state.enrich(('openai', 'm'), 200, 'configured')
    assert state.snapshot('a').observed_at == timestamp
    assert state.snapshot('a').context_window == 200
    state.invalidate('a')
    state.enrich(('retired',), 999, 'configured')
    assert state.snapshot('a').context_window == 200
    assert state.snapshot('a').input_tokens is None


def test_full_body_estimate_and_snapshot_excludes_prompt_secrets():
    state, _ = manager()
    obs = state.observer('a', None)
    body = {'instructions': 'secret' * 20, 'input': 'history' * 20, 'tools': [{'schema': 'tool' * 200}], 'text': {'format': {'schema': 'strict' * 200}}}
    obs.begin('m', body)
    snap = state.snapshot('a')
    from agentd.providers.context_observation import count_request_input
    # Every model-visible field counts, not just the conversation input.
    assert snap.input_tokens > count_request_input('m', {'input': body['input']}) + 100
    assert 'secret' not in snap.model_dump_json()
    assert snap.scope == 'main'


def test_first_snapshot_after_selection_is_awaiting_request():
    state, _ = manager()
    assert state.snapshot('new').status == 'awaiting_request'


@pytest.mark.parametrize('changes', [
    {'input_tokens': True}, {'context_window': -1}, {'generation': True},
    {'input_tokens': 12, 'input_quality': 'unavailable'},
    {'context_window': 100, 'capacity_source': 'unknown'},
    {'observed_at': 'not-a-date'},
])
def test_wire_snapshot_rejects_invalid_or_inconsistent_fields(changes):
    from pydantic import ValidationError
    from agentd.chat.context_usage import ContextUsage
    state, _ = manager()
    payload = state.snapshot('new').model_dump()
    payload.update(changes)
    with pytest.raises(ValidationError):
        ContextUsage.model_validate(payload)


def test_same_logical_observer_returns_retired_physical_handles():
    state, events = manager()
    logical = state.observer('a', 't')
    first = logical.begin('m', {'input': 'first'})
    first.reported(8)
    first.finish()
    second = logical.begin('m', {'input': 'second'})
    current = state.snapshot('a')
    first.reported(999)
    first.finish()
    assert state.snapshot('a') == current
    second.reported(0)
    second.finish()
    state.invalidate('a')
    second.reported(99)
    third = state.observer('a', 't').begin('m', {'input': 'third'})
    third.finish()
    revisions = [value['revision'] for _, value in events]
    assert revisions == sorted(set(revisions))
    assert state.snapshot('a').generation == 4


def test_tracker_counts_full_request_using_shared_counter():
    from agentd.providers.context_observation import count_request_input
    state, _ = manager()
    body = {'input': 'history', 'tool_choice': {'schema': 'choice' * 200},
            'functions': [{'schema': 'function' * 200}], 'function_call': 'selected',
            'extra_body': {'response_format': {'schema': 'tail' * 200}}}
    state.observer('a', None).begin('m', body)
    assert state.snapshot('a').input_tokens == count_request_input('m', body)


def _gated_counter(monkeypatch):
    """Hold the meter's token count until the test releases it."""
    import threading

    from agentd.providers import context_observation
    release, started = threading.Event(), threading.Event()
    owner = threading.get_ident()
    threads = []

    def count(model, body):
        threads.append(threading.get_ident())
        started.set()
        release.wait(5)
        return 4242

    monkeypatch.setattr(context_observation, 'count_request_input', count)
    return release, started, threads, owner


async def _settle(started):
    import asyncio
    for _ in range(200):
        await asyncio.sleep(0.005)
        if started.is_set():
            return


@pytest.mark.asyncio
async def test_count_runs_off_the_request_path_and_the_old_value_stays_meanwhile(monkeypatch):
    # Counting may download the tokenizer's encoding; the request must never wait on it.
    import asyncio
    release, started, threads, owner = _gated_counter(monkeypatch)
    state, _ = manager()
    first = state.observer('a', 't').begin('m', {'input': 'x'})
    await _settle(started)
    release.set()
    for _ in range(200):
        await asyncio.sleep(0.005)
        if state.snapshot('a').input_tokens == 4242:
            break
    first.reported(81)
    first.finish()
    release.clear()
    started.clear()
    state.observer('a', 't').begin('m', {'input': 'next'})
    snap = state.snapshot('a')
    assert snap.status == 'in_flight'
    assert (snap.input_tokens, snap.input_quality) == (81, 'provider_reported')
    await _settle(started)
    release.set()
    for _ in range(200):
        await asyncio.sleep(0.005)
        if state.snapshot('a').input_quality == 'estimated':
            break
    assert (state.snapshot('a').input_tokens, state.snapshot('a').input_quality) == (
        4242, 'estimated')
    assert threads and all(t != owner for t in threads)


@pytest.mark.asyncio
async def test_provider_count_that_arrives_first_is_never_overwritten(monkeypatch):
    import asyncio
    release, started, _, _ = _gated_counter(monkeypatch)
    state, _ = manager()
    attempt = state.observer('a', 't').begin('m', {'input': 'x'})
    await _settle(started)
    attempt.reported(77)
    release.set()
    await asyncio.sleep(0.05)
    assert (state.snapshot('a').input_tokens, state.snapshot('a').input_quality) == (
        77, 'provider_reported')


@pytest.mark.asyncio
async def test_a_retired_request_count_is_dropped(monkeypatch):
    import asyncio
    release, started, _, _ = _gated_counter(monkeypatch)
    state, _ = manager()
    state.observer('a', 't').begin('m', {'input': 'x'})
    await _settle(started)
    state.invalidate('a')
    release.set()
    await asyncio.sleep(0.05)
    assert state.snapshot('a').input_tokens is None
