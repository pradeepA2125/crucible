from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
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


def image_data(size=(800, 600), *, compress_level=6, format='PNG'):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', size, '#7856ab').save(
        buffer, format=format, compress_level=compress_level)
    return base64.b64encode(buffer.getvalue()).decode()


def image_body(part):
    return {'model': 'm', 'instructions': 'inspect the screenshot',
            'input': [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'What changed?'}, part]}]}


def test_image_estimate_is_independent_of_encoded_file_size_and_does_not_mutate_body():
    from agentd.providers.context_observation import estimate_request_input

    bodies = [image_body({'type': 'input_image', 'detail': 'high',
                         'image_url': 'data:image/png;base64,' + image_data(compress_level=c)})
              for c in (0, 9)]
    before = copy.deepcopy(bodies)
    assert len(json.dumps(bodies[0])) > 1_000_000
    assert len(json.dumps(bodies[1])) < 10_000
    assert estimate_request_input('m', bodies[0]) == estimate_request_input('m', bodies[1])
    assert 3_000 < estimate_request_input('m', bodies[0]) < 4_000
    assert bodies == before


@pytest.mark.parametrize('format', ['PNG', 'JPEG', 'WEBP'])
@pytest.mark.parametrize('serializer', ['responses', 'chat', 'anthropic', 'gemini'])
def test_native_image_serializers_use_dimensions(format, serializer):
    from agentd.images import estimate_image_tokens
    from agentd.providers.context_observation import estimate_request_input

    encoded = image_data(format=format)
    url = f'data:image/{format.lower()};base64,{encoded}'
    part = {
        'responses': {'type': 'input_image', 'image_url': url},
        'chat': {'type': 'image_url', 'image_url': {'url': url}},
        'anthropic': {'type': 'image', 'source': {'type': 'base64', 'data': encoded}},
        'gemini': {'inline_data': {'mime_type': 'image/png', 'data': encoded}},
    }[serializer]
    body = image_body(part)
    if serializer == 'gemini':
        body['contents'] = body.pop('input')
    placeholder = copy.deepcopy(body)
    placeholder['contents' if serializer == 'gemini' else 'input'][0]['content'][1] = {
        'image': '[image]'}
    assert estimate_request_input('m', body) == (
        estimate_request_input('m', placeholder) + estimate_image_tokens(800, 600))


def test_image_dimensions_and_repeated_occurrences_affect_the_estimate():
    from agentd.providers.context_observation import estimate_request_input

    small = {'type': 'input_image',
             'image_url': 'data:image/png;base64,' + image_data((512, 512))}
    large = {'type': 'input_image',
             'image_url': 'data:image/png;base64,' + image_data((2048, 1024))}
    assert estimate_request_input('m', image_body(large)) > estimate_request_input(
        'm', image_body(small))
    body = image_body(small)
    single = estimate_request_input('m', body)
    body['input'][0]['content'].append(small)
    assert estimate_request_input('m', body) >= single + 1536


@pytest.mark.parametrize('url', [
    'https://example.invalid/screenshot.png',
    'data:image/png;base64,not-base64!',
    'data:image/png;base64,' + base64.b64encode(b'not an image' * 100_000).decode(),
], ids=['remote', 'malformed', 'unreadable'])
def test_unknown_or_malformed_images_have_a_bounded_allowance(url, monkeypatch):
    import urllib.request

    from agentd.providers.context_observation import estimate_request_input

    def no_network(*args, **kwargs):
        pytest.fail('Image token estimation must not fetch URLs')

    monkeypatch.setattr(urllib.request, 'urlopen', no_network)
    body = image_body({'type': 'input_image', 'image_url': url})
    assert 16_384 <= estimate_request_input('m', body) < 16_500


def test_text_schemas_and_non_image_inline_data_keep_existing_text_estimation():
    from agentd.providers.context_observation import estimate_request_input

    body = {'input': [{'type': 'input_text', 'text': 'data:image/png;base64,' + 'a' * 400}],
            'tools': [{'type': ['string', 'null'], 'example': {'type': 'image_url',
                       'image_url': 'https://example.invalid/' + 'b' * 400}}],
            'messages': [{'content': [{'inline_data': {'mime_type': 'audio/wav',
                                                     'data': 'c' * 400}}]}]}
    expected = (len(json.dumps(body, ensure_ascii=False, separators=(',', ':'))) + 3) // 4
    assert estimate_request_input('m', body) == expected
    assert estimate_request_input('m', {'input': 'old', 'extra_body': body}) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('compatible', [False, True])
async def test_image_aware_workbar_and_context_estimates_then_provider_usage(compatible):
    from agentd.providers.context_observation import estimate_request_input

    part = {'type': 'input_image',
            'image_url': 'data:image/png;base64,' + image_data(compress_level=0)}
    body = image_body(part)
    progress = []
    observer = Observer()
    usage = []

    def tick(_thinking, _output, **metadata):
        progress.append(metadata)

    if compatible:
        body = {'model': 'm', 'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': part['image_url']}}]}]}
        stream = Stream([
            NS(choices=[NS(delta=NS(content='{"ok":true}'), finish_reason='stop')]),
            NS(choices=[], usage=NS(prompt_tokens=2222, completion_tokens=4)),
        ])
        client = Client(stream)
        transport = OpenAICompatibleTransport(
            base_url='http://example.invalid', completions_client=client, max_retries=0)
        await transport._stream_with_finish_reason(
            body, on_thinking=lambda _: None, on_progress=tick,
            on_usage=lambda i, o: usage.append((i, o)), on_context_input=observer)
    else:
        done = NS(type='response.completed', response=NS(usage=NS(
            input_tokens=2222, output_tokens=4)))
        client = Client(Stream([delta('{"ok":true}'), done]))
        transport = OpenAIJsonTransport(responses_client=client, max_retries=0)
        await transport._run(
            body, None, None, tick, lambda i, o: usage.append((i, o)),
            on_context_input=observer)
    assert progress[0]['input_n'] == estimate_request_input('m', observer.bodies[0])
    assert progress[0]['input_n'] < 4_000
    assert progress[-1]['input_n'] == 2222
    assert progress[-1]['exact'] is True
    assert observer.events == ['begin', ('reported', 2222), 'finish']
    assert usage == [(2222, 4)]
    assert part['image_url'] in json.dumps(client.calls[0])
