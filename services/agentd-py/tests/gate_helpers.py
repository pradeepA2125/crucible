"""Test helper: the first pending controller gate (what pre-multi-gate tests asserted)."""
from agentd.chat.models import ChatThread, PendingGate


def first_gate(thread: ChatThread | None) -> PendingGate | None:
    if thread is None or not thread.pending_controller_gates:
        return None
    return thread.pending_controller_gates[0]
