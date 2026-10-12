from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.models import ThreadLiveState
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


class ObservingEngine(ScriptedReasoningEngine):
    async def create_controller_step(self, *, on_context_input=None, **kwargs):
        assert on_context_input is not None
        on_context_input.begin('m', {'input': 'request'})
        on_context_input.reported(15)
        on_context_input.finish()
        return {'type': 'answer', 'thought': 'done', 'answer': 'ok'}


@pytest.mark.asyncio
async def test_main_turn_observer_and_rewind(tmp_path: Path):
    store = ChatThreadStore(tmp_path / 'chat.sqlite3')
    thread = store.create_thread(str(tmp_path))
    ctrl = ChatController(workspace_path=str(tmp_path), reasoning_engine=ObservingEngine(None, []),
                          thread_store=store, orchestrator=None, broadcaster=EventBroadcaster())
    ctrl._context_usage.select(provider='openai', model='m', identity=('m',),
                               window=100, source='configured')
    await ctrl.handle_message(thread.thread_id, 'hi', channel_id='c')
    snap = ctrl.context_usage(thread.thread_id)
    assert snap.input_tokens == 15
    assert snap.status == 'last_request'
    live = ThreadLiveState(process_id=snap.process_id, context_usage=snap)
    assert live.model_dump()['context_usage'] == snap.model_dump()
    assert live.model_dump()['process_id'] == snap.process_id
    ctrl.forget_turn_caches(thread.thread_id)
    assert ctrl.context_usage(thread.thread_id).status == 'invalidated'
    assert ctrl.context_usage(thread.thread_id).input_tokens is None


@pytest.mark.asyncio
async def test_unsupported_engine_clears_previous_request(tmp_path: Path):
    class UnsupportedEngine:
        async def create_controller_step(self, *, plan_context, history, tool_definitions,
                                         phase, on_thinking, on_retry, on_progress,
                                         on_salvage, on_usage, unconstrained):
            assert ctrl.context_usage(thread.thread_id).input_tokens is None
            assert ctrl.context_usage(thread.thread_id).status == 'unavailable'
            return {'type': 'answer', 'thought': 'done', 'answer': 'ok'}

    store = ChatThreadStore(tmp_path / 'chat.sqlite3')
    thread = store.create_thread(str(tmp_path))
    ctrl = ChatController(workspace_path=str(tmp_path), reasoning_engine=UnsupportedEngine(),
                          thread_store=store, orchestrator=None, broadcaster=EventBroadcaster())
    ctrl._context_usage.select(provider='openai', model='m', identity=('m',),
                               window=100, source='configured')
    attempt = ctrl._context_usage.observer(thread.thread_id, None).begin('m', {'input': 'prior'})
    attempt.reported(80)
    attempt.finish()
    await ctrl.handle_message(thread.thread_id, 'hi', channel_id='c')
    assert ctrl.context_usage(thread.thread_id).input_tokens is None
    assert ctrl.context_usage(thread.thread_id).status == 'unavailable'


@pytest.mark.asyncio
@pytest.mark.parametrize('adaptive_reads', [False, True])
async def test_engine_forwards_optional_context_observer(monkeypatch, adaptive_reads):
    from agentd.chat.context_usage import ContextUsageTracker
    from agentd.reasoning.engine import DefaultReasoningEngine
    from agentd.tools.read_policy import ResultBudgetObserver, result_budget

    monkeypatch.setenv('CRUCIBLE_ADAPTIVE_READS_ENABLED', '1' if adaptive_reads else '0')
    plan_context = {'context_capacity': 10_000, 'capacity_model': 'm'}

    class Transport:
        async def generate_json(self, *, model, on_context_input=None, **kwargs):
            if adaptive_reads:
                assert isinstance(on_context_input, ResultBudgetObserver)
                assert on_context_input.observer is observer
                assert on_context_input.context is plan_context
            else:
                assert on_context_input is observer
            on_context_input.begin(model, {'input': 'request'})
            on_context_input.reported(15)
            on_context_input.finish()
            return {'type': 'answer', 'answer': 'ok'}

    tracker = ContextUsageTracker(lambda *_: None)
    tracker.select(provider='openai', model='m', identity=('m',),
                   window=10_000, source='configured')
    observer = tracker.observer('a', None)
    engine = DefaultReasoningEngine(transport=Transport(), model='m')
    await engine.create_controller_step(plan_context, [], [], phase='ACTIVE',
                                        on_context_input=observer)
    snapshot = tracker.snapshot('a')
    assert snapshot.input_tokens == 15
    assert snapshot.status == 'last_request'
    if adaptive_reads:
        assert plan_context['tool_result_budget'] == result_budget(10_000, 15)
    else:
        assert 'tool_result_budget' not in plan_context


@pytest.mark.asyncio
async def test_engine_marks_unsupported_transport_unavailable():
    from agentd.chat.context_usage import ContextUsageTracker
    from agentd.reasoning.engine import DefaultReasoningEngine

    tracker = ContextUsageTracker(lambda *_: None)
    tracker.select(provider='openai', model='m', identity=('m',), window=100, source='configured')
    prior = tracker.observer('a', None).begin('m', {'input': 'prior'})
    prior.reported(80)
    prior.finish()

    class Transport:
        async def generate_json(self, *, model, schema_name, schema, system_instructions,
                                user_payload, on_thinking, on_retry):
            assert tracker.snapshot('a').input_tokens is None
            assert tracker.snapshot('a').status == 'unavailable'
            return {'type': 'answer', 'answer': 'ok'}

    engine = DefaultReasoningEngine(transport=Transport(), model='m')
    await engine.create_controller_step({}, [], [], phase='ACTIVE',
                                        on_context_input=tracker.observer('a', None))


@pytest.mark.asyncio
async def test_live_route_context_matches_publication(tmp_path: Path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agentd.api.routes import build_router
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.patch.engine import PatchEngine
    from agentd.storage.in_memory import InMemoryTaskStore
    from agentd.workspace.shadow import ShadowWorkspaceManager
    from tests.test_chat_live_route import _NoopReasoning, _Validator

    store = InMemoryTaskStore()
    chat = ChatThreadStore(tmp_path / 'chat.db')
    ws = ShadowWorkspaceManager(tmp_path / 'shadows')
    orch = AgentOrchestrator(store=store, reasoning_engine=_NoopReasoning(), validator=_Validator(),
                            patch_engine=PatchEngine(), workspace_manager=ws, chat_store=chat)
    events = []
    orch.broadcaster.broadcast = lambda channel, event: events.append((channel, event))
    ctrl = ChatController(workspace_path=str(tmp_path), reasoning_engine=ObservingEngine(None, []),
                          thread_store=chat, orchestrator=orch, broadcaster=orch.broadcaster)
    thread = chat.create_thread(str(tmp_path))
    ctrl._context_usage.select(provider='openai', model='m', identity=('m',),
                               window=100, source='configured')
    observer = ctrl._context_usage.observer(thread.thread_id, None)
    observer.begin('m', {'input': 'x'})
    observer.reported(0)
    observer.finish()
    app = FastAPI()
    app.include_router(build_router(store, orch, ws, None, ctrl))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        body = (await client.get(f'/v1/chat/threads/{thread.thread_id}/live')).json()
    assert body['context_usage'] == events[-1][1]['payload']
    assert body['process_id'] == body['context_usage']['process_id']
    assert events[-1][0] == f'chat:{thread.thread_id}'
    expected_process = ctrl._context_usage.process_id
    for broken in (lambda _: None, lambda _: {'invalid': True}):
        ctrl.context_usage = broken
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get(f'/v1/chat/threads/{thread.thread_id}/live')
        assert response.status_code == 200
        assert response.json()['process_id'] == expected_process
        assert response.json()['context_usage'] is None
        assert 'turn_active' in response.json()
    def failing_context(_):
        raise ValueError('telemetry unavailable')
    ctrl.context_usage = failing_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(f'/v1/chat/threads/{thread.thread_id}/live')
    assert response.status_code == 200
    assert response.json()['process_id'] == expected_process
    assert response.json()['context_usage'] is None
    await ctrl.close_context_usage()
