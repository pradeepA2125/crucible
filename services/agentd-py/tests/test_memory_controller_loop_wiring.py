from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.memory.compactor import Compactor
from agentd.memory.harness import MemoryHarness
from agentd.memory.store import MemoryStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


@pytest.mark.asyncio
async def test_controller_loop_invokes_memory_harness_with_run_id(tmp_path: Path):
    calls: list[str] = []
    store = MemoryStore(tmp_path / "m.sqlite3")

    async def summ(old: str, evicted: str) -> str:
        return "A"

    class SpyCompactor(Compactor):
        async def maybe_compact(self, history, run_id):
            calls.append(run_id)
            return await super().maybe_compact(history, run_id)

    comp = SpyCompactor(
        store, summ, window_tokens=100000, trigger_frac=0.65, hot_token_frac=0.4, hot_turns=10
    )
    harness = MemoryHarness(enabled=True, compactor=comp)

    eng = ScriptedReasoningEngine(
        None,
        [],
        controller_step_responses=[{"type": "answer", "thought": "done", "answer": "hi"}],
    )
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)]
    )
    loop = ControllerLoop(
        eng,
        reg,
        EventBroadcaster(),
        channel_id="c1",
        phase_sm=ControllerPhaseSM(),
        memory_harness=harness,
    )
    await loop.run(
        {"goal": "hi", "workspace_path": str(tmp_path), "run_id": "thread-x"}, max_iters=4
    )
    assert calls and calls[0] == "thread-x"
