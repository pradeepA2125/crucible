"""Child-channel events carry a monotonic seq (spec §5.5)."""
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.subagents.events import SequencedBroadcaster

CHILD = "chat:t1:agent:agent-1"


def test_stamps_only_its_own_channel() -> None:
    inner = EventBroadcaster()
    child_q, thread_q = inner.subscribe(CHILD), inner.subscribe("chat:t1")
    seq = SequencedBroadcaster(inner, CHILD)
    seq.broadcast(CHILD, {"type": "tool_call", "payload": {}})
    seq.broadcast("chat:t1", {"type": "agent_status", "payload": {}})
    seq.broadcast(CHILD, {"type": "tool_result", "payload": {}})
    assert [child_q.get_nowait()["seq"] for _ in range(2)] == [1, 2]
    assert "seq" not in thread_q.get_nowait()
    assert seq.last_seq == 2


def test_the_callers_event_is_not_mutated() -> None:
    seq = SequencedBroadcaster(EventBroadcaster(), CHILD)
    event = {"type": "progress", "payload": {}}
    seq.broadcast(CHILD, event)
    assert "seq" not in event


def test_subscribe_and_replay_are_the_inner_ones() -> None:
    inner = EventBroadcaster()
    seq = SequencedBroadcaster(inner, CHILD)
    seq.broadcast(CHILD, {"type": "a", "payload": {}})
    late = seq.subscribe(CHILD)  # replay comes from the shared buffer
    assert late.get_nowait()["seq"] == 1
    seq.clear_replay(CHILD)
    assert inner.subscribe(CHILD).empty()
