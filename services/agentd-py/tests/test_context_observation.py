from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace as NS

import pytest

from agentd.providers.openai_compatible_transport import OpenAICompatibleTransport
from agentd.providers.openai_transport import OpenAIJsonTransport


class Observer:
    def __init__(self):
        self.events = []
        self.bodies = []

    def begin(self, model, body):
        self.events.append('begin')
        self.bodies.append(copy.deepcopy(body))

    def reported(self, value):
        self.events.append(('reported', value))

    def finish(self):
        self.events.append('finish')


class Stream:
    def __init__(self, events):
        self.events = events

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.events:
            raise StopAsyncIteration
        value = self.events.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self):
        pass


class Client:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def create(self, **body):
        self.calls.append(copy.deepcopy(body))
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def completed(value):
    return NS(type='response.completed', response=NS(usage=NS(input_tokens=value)))


def delta(text):
    return NS(type='response.output_text.delta', delta=text)


def compat_response(text, value):
    return NS(choices=[NS(message=NS(content=text), finish_reason='stop')],
              usage={'prompt_tokens': value})


SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
KWARGS = dict(model='m', schema_name='s', schema=SCHEMA,
              system_instructions='instructions', user_payload={'history': ['previous']})


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [0, 12, True, -1, '12', 1.5, None])
async def test_openai_input_is_independent_of_output_usage(value):
    observer = Observer()
    client = Client(Stream([delta('{"ok":true}'), completed(value)]))
    transport = OpenAIJsonTransport(responses_client=client, max_retries=0)
    assert await transport.generate_json(**KWARGS, on_context_input=observer) == {'ok': True}
    expected = [('reported', value)] if type(value) is int and value >= 0 else []
    assert observer.events == ['begin', *expected, 'finish']
    assert observer.bodies == client.calls
    assert observer.bodies[0]['text']['format']['schema']['additionalProperties'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [0, 12, True, -1, '12', 1.5, None])
async def test_compatible_nonstream_input_before_decode(value):
    observer = Observer()
    client = Client(compat_response('{"ok":true}', value))
    transport = OpenAICompatibleTransport(
        base_url='http://example.invalid', completions_client=client, max_retries=0,
    )
    assert await transport.generate_json(**KWARGS, on_context_input=observer) == {'ok': True}
    expected = [('reported', value)] if type(value) is int and value >= 0 else []
    assert observer.events == ['begin', *expected, 'finish']
    assert observer.bodies == client.calls


@pytest.mark.asyncio
async def test_compatible_fallback_replaces_previous_observation():
    observer = Observer()
    client = Client(compat_response('not json', 100), compat_response('{"ok":true}', None))
    transport = OpenAICompatibleTransport(
        base_url='http://example.invalid', completions_client=client, max_retries=0,
    )
    assert await transport.generate_json(**KWARGS, on_context_input=observer) == {'ok': True}
    assert observer.events == ['begin', ('reported', 100), 'finish', 'begin', 'finish']
    assert observer.bodies == client.calls
    assert 'matching this schema' in observer.bodies[-1]['messages'][0]['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', [RuntimeError('failure'), asyncio.CancelledError()])
async def test_openai_error_or_cancel_settles_estimate(outcome):
    observer = Observer()
    transport = OpenAIJsonTransport(responses_client=Client(outcome), max_retries=0)
    with pytest.raises(type(outcome)):
        await transport.generate_json(**KWARGS, on_context_input=observer)
    assert observer.events == ['begin', 'finish']


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [0, 12, True, -1, '12', 1.5, None])
async def test_compatible_stream_input_only_usage(value):
    observer = Observer()
    stream = Stream([
        NS(choices=[NS(delta=NS(content='{"ok":true}'), finish_reason='stop')]),
        NS(choices=[], usage=NS(prompt_tokens=value)),
    ])
    client = Client(stream)
    transport = OpenAICompatibleTransport(
        base_url='http://example.invalid', completions_client=client, max_retries=0,
    )
    assert await transport.generate_json(
        **KWARGS, on_context_input=observer, on_thinking=lambda _: None,
    ) == {'ok': True}
    expected = [('reported', value)] if type(value) is int and value >= 0 else []
    assert observer.events == ['begin', *expected, 'finish']
    assert observer.bodies == client.calls


@pytest.mark.asyncio
async def test_compatible_usage_probe_replaces_each_physical_attempt():
    observer = Observer()
    client = Client(RuntimeError('continuous unsupported'), RuntimeError('usage unsupported'),
                    Stream([NS(choices=[NS(delta=NS(content='hello'))])]))
    transport = OpenAICompatibleTransport(
        base_url='http://example.invalid', completions_client=client, max_retries=0,
    )
    assert await transport.generate_text(
        model='m', system_instructions='system', user_payload={},
        on_context_input=observer, on_thinking=lambda _: None,
    ) == 'hello'
    assert observer.events == ['begin', 'finish'] * 3
    assert observer.bodies == client.calls
    assert client.calls[0]['stream_options']['continuous_usage_stats'] is True
    assert client.calls[1]['stream_options'] == {'include_usage': True}
    assert 'stream_options' not in client.calls[2]


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('opening', [False, True])
async def test_compatible_cancel_settles_estimate(streaming, opening):
    observer = Observer()
    outcome = asyncio.CancelledError()
    client = Client(outcome if opening or not streaming else Stream([outcome]))
    transport = OpenAICompatibleTransport(
        base_url='http://example.invalid', completions_client=client, max_retries=0,
    )
    with pytest.raises(asyncio.CancelledError):
        await transport.generate_text(
            model='m', system_instructions='system', user_payload={},
            on_context_input=observer, on_thinking=(lambda _: None) if streaming else None,
        )
    assert observer.events == ['begin', 'finish']


@pytest.mark.asyncio
@pytest.mark.parametrize('compatible', [False, True])
async def test_observer_exceptions_and_mutation_do_not_break_chat(compatible):
    class BrokenObserver:
        def begin(self, model, body):
            body.clear()
            raise RuntimeError('observer begin')

        def reported(self, value):
            raise RuntimeError('observer report')

        def finish(self):
            raise RuntimeError('observer finish')

    client = Client(compat_response('{"ok":true}', 10) if compatible
                    else Stream([delta('{"ok":true}'), completed(10)]))
    transport = (OpenAICompatibleTransport(
        base_url='http://example.invalid', completions_client=client, max_retries=0,
    ) if compatible else OpenAIJsonTransport(responses_client=client, max_retries=0))
    assert await transport.generate_json(
        **KWARGS, on_context_input=BrokenObserver(),
    ) == {'ok': True}
    assert client.calls[0]['model'] == 'm'
    assert 'on_context_input' not in client.calls[0]


@pytest.mark.asyncio
async def test_openai_pre_dispatch_failure_does_not_replace_observation(monkeypatch):
    observer = Observer()
    transport = OpenAIJsonTransport(responses_client=Client(), max_retries=0)

    async def unavailable():
        raise RuntimeError('no client')

    monkeypatch.setattr(transport, '_responses', unavailable)
    with pytest.raises(RuntimeError, match='no client'):
        await transport.generate_json(**KWARGS, on_context_input=observer)
    assert observer.events == []


@pytest.mark.asyncio
async def test_openai_early_action_settles_without_provider_accuracy():
    observer = Observer()
    client = Client(Stream([
        delta('{"ok":true}'),
        NS(type='response.output_item.done', item=NS(type='message')),
        completed(50),
    ]))
    transport = OpenAIJsonTransport(responses_client=client, max_retries=0)
    assert await transport.generate_json(
        **{**KWARGS, 'schema_name': 'controller_step_response'}, on_context_input=observer,
    ) == {'ok': True}
    assert observer.events == ['begin', 'finish']


def test_estimate_includes_all_model_visible_request_fields():
    from agentd.providers.context_observation import estimate_request_input
    body = {'instructions': 'system', 'input': [{'content': 'history and current'}]}
    base = estimate_request_input('m', body)
    for key in ['tools', 'text', 'response_format', 'messages']:
        assert estimate_request_input('m', {**body, key: {'schema': 'x' * 400}}) > base + 90
    assert estimate_request_input('m', {**body, 'max_output_tokens': 100000}) == base
