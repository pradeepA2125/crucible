"""Only the main agent's gates are cleared at turn start (spec §3.8)."""
from pathlib import Path

from agentd.chat.models import GateAgent, GateTeam, PendingGate
from agentd.chat.storage import ChatThreadStore


def _gates(tmp_path: Path) -> tuple[ChatThreadStore, str]:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    store.add_controller_gate(tid, PendingGate.new("edit", {}))
    store.add_controller_gate(tid, PendingGate.new(
        "edit", {}, agent=GateAgent(id="agent-1", label="a", name="general-purpose")))
    store.add_controller_gate(tid, PendingGate(
        gate_id="", kind="mode", payload={}, team=GateTeam(id="team-1", name="checkout")))
    return store, tid


def test_is_main() -> None:
    assert PendingGate.new("edit", {}).is_main()
    assert not PendingGate.new(
        "edit", {}, agent=GateAgent(id="a", label="a", name="n")).is_main()
    assert not PendingGate(kind="mode", team=GateTeam(id="t", name="n")).is_main()


def test_clear_main_gates_keeps_agent_and_team_gates(tmp_path: Path) -> None:
    store, tid = _gates(tmp_path)
    store.clear_main_gates(tid)
    kept = store.get_thread(tid).pending_controller_gates  # type: ignore[union-attr]
    assert [(g.agent is not None, g.team is not None) for g in kept] == [
        (True, False), (False, True)]


def test_restart_reap_drops_agent_and_team_gates(tmp_path: Path) -> None:
    store, tid = _gates(tmp_path)
    assert store.remove_child_gates() == [tid]
    kept = store.get_thread(tid).pending_controller_gates  # type: ignore[union-attr]
    assert [g.is_main() for g in kept] == [True]
