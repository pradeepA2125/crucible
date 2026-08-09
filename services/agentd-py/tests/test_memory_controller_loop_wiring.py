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
        async def maybe_compact(self, history, run_id, observed=None):
            calls.append(run_id)
            return await super().maybe_compact(history, run_id, observed=observed)

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


@pytest.mark.asyncio
async def test_controller_loop_broadcasts_memory_compacted(tmp_path: Path):
    from agentd.memory.models import TurnPreparation

    class _CompactingHarness:
        """Returns a compacted prep so we can assert the loop broadcasts the event."""

        async def prepare_turn(self, history, run_id, query="", observed=None):
            return TurnPreparation(
                history=history, compacted=True, evicted_count=3, anchor_version=2
            )

        async def recall(self, query, run_id):
            return []

    class _RecordingBroadcaster(EventBroadcaster):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[tuple[str, dict]] = []

        def broadcast(self, channel_id, event):
            self.events.append((channel_id, event))
            return super().broadcast(channel_id, event)

    bc = _RecordingBroadcaster()
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
        bc,
        channel_id="c1",
        phase_sm=ControllerPhaseSM(),
        memory_harness=_CompactingHarness(),  # type: ignore[arg-type]
    )
    await loop.run(
        {"goal": "hi", "workspace_path": str(tmp_path), "run_id": "thread-x"}, max_iters=4
    )
    compacted = [e for _, e in bc.events if e.get("type") == "memory_compacted"]
    assert compacted, "expected a memory_compacted event to be broadcast"
    assert compacted[0]["payload"]["evicted"] == 3
    assert compacted[0]["payload"]["anchor_version"] == 2


@pytest.mark.asyncio
async def test_loop_feeds_the_measured_prompt_into_the_next_compaction(tmp_path: Path):
    """The circuit: the transport reports what the last call cost, and the next
    turn's compaction decision runs on that number instead of a guess."""
    from agentd.memory.models import TurnPreparation

    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])

    observed_seen: list[object] = []

    class _SpyHarness:
        # prepare_turn is the ONLY thing the loop calls on the harness.
        async def prepare_turn(self, history, run_id, query="", observed=None):
            observed_seen.append(observed)
            return TurnPreparation(history=history)

    class _ReportingEngine:
        """Reports usage on call 1, then answers on call 2."""

        def __init__(self) -> None:
            self.calls = 0

        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            self.calls += 1
            if self.calls == 1:
                if on_usage is not None:
                    on_usage(7777, 20)
                return {"type": "tool_call", "thought": "t", "tool": "list_directory",
                        "args": {"path": "."}}
            return {"type": "answer", "thought": "t", "answer": "done"}

    loop = ControllerLoop(_ReportingEngine(), reg, EventBroadcaster(), channel_id="c",
                          phase_sm=ControllerPhaseSM(), memory_harness=_SpyHarness())
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=4,
                   auto_accept_edits=True)

    # First iteration has nothing measured yet; the second runs on the report.
    assert observed_seen[0] is None
    assert observed_seen[1] is not None
    assert observed_seen[1].tokens == 7777
