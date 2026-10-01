"""Test helper: the first pending controller gate (what pre-multi-gate tests asserted)."""
from agentd.chat.models import ChatThread, PendingGate, ThreadLiveState


def first_gate(thread: ChatThread | None) -> PendingGate | None:
    if thread is None or not thread.pending_controller_gates:
        return None
    return thread.pending_controller_gates[0]


def first_live_gate(live: ThreadLiveState) -> PendingGate | None:
    return live.pending_gates[0] if live.pending_gates else None
