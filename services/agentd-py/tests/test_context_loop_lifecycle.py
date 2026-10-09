from pathlib import Path

import pytest

from agentd.chat.context_usage import ContextUsageTracker
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.memory.models import TurnPreparation
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


@pytest.mark.asyncio
async def test_compaction_retires_prior_attempt_before_next_dispatch(tmp_path: Path):
    events = []
    tracker = ContextUsageTracker(lambda _, value: events.append(value))
    tracker.select(provider='openai', model='m', identity=('m',), window=100, source='configured')
    prior = tracker.observer('a', 'old').begin('m', {'input': 'before compaction'})
    prior.reported(90)
    prior.finish()

    class Harness:
        async def prepare_turn(self, history, run_id, **kwargs):
            return TurnPreparation(history=history, compacted=True)

    class Engine:
        async def create_controller_step(self, *, on_context_input=None, **kwargs):
            snap = tracker.snapshot('a')
            assert snap.status == 'invalidated'
            assert snap.input_tokens is None
            prior.reported(99)
            assert tracker.snapshot('a') == snap
            physical = on_context_input.begin('m', {'input': 'compacted request'})
            physical.reported(12)
            physical.finish()
            return {'type': 'answer', 'thought': 'done', 'answer': 'ok'}

    registry = AggregatingToolRegistry([BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    loop = ControllerLoop(Engine(), registry, EventBroadcaster(), channel_id='c',
                          phase_sm=ControllerPhaseSM(), memory_harness=Harness(),
                          context_observer_factory=lambda: tracker.observer('a', 'new'),
                          context_invalidate=lambda: tracker.invalidate('a'))
    await loop.run({'goal': 'hi', 'workspace_path': str(tmp_path), 'run_id': 'a'}, max_iters=2)
    assert tracker.snapshot('a').input_tokens == 12
    assert tracker.snapshot('a').generation == 3
    assert any(value['status'] == 'invalidated' for value in events)


@pytest.mark.asyncio
async def test_loop_without_main_factory_never_observes_main_context(tmp_path: Path):
    class Engine:
        async def create_controller_step(self, *, on_context_input=None, **kwargs):
            assert on_context_input is None
            return {'type': 'answer', 'thought': 'done', 'answer': 'ok'}

    registry = AggregatingToolRegistry([BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)])
    loop = ControllerLoop(Engine(), registry, EventBroadcaster(), channel_id='child',
                          phase_sm=ControllerPhaseSM())
    await loop.run({'goal': 'hi', 'workspace_path': str(tmp_path)}, max_iters=2)
