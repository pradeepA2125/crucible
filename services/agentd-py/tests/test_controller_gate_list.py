"""Multi-gate model + storage (spec §4.5). A thread holds a LIST of pending gates, each
with a stable gate_id and an optional agent tag."""
from pathlib import Path

from agentd.chat.live_state import resolve_live_state, resolve_thread_live
from agentd.chat.models import GateAgent, PendingGate
from agentd.chat.storage import ChatThreadStore
from agentd.domain.models import (
    CommandApprovalRequest,
    TaskExecutionState,
    TaskRecord,
    TaskStatus,
)


def _store(tmp_path: Path) -> tuple[ChatThreadStore, str]:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    return store, store.create_thread(str(tmp_path)).thread_id


def test_new_gates_get_distinct_stable_ids_and_optional_agent() -> None:
    a = PendingGate.new("command", {"command": "ls"})
    b = PendingGate.new("edit", {}, agent=GateAgent(id="agent-1", label="impl", name="general"))
    assert a.gate_id and b.gate_id and a.gate_id != b.gate_id
    assert a.agent is None and b.agent is not None and b.agent.label == "impl"


def test_gates_are_stored_as_an_ordered_list(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    first = PendingGate.new("command", {"command": "ls"})
    second = PendingGate.new("mcp_tool", {"server": "gh", "tool": "x", "args": {}})
    store.add_controller_gate(tid, first)
    store.add_controller_gate(tid, second)
    thread = store.get_thread(tid)
    assert thread is not None
    assert [g.gate_id for g in thread.pending_controller_gates] == [first.gate_id, second.gate_id]
    listed = store.list_threads(str(tmp_path))[0]
    assert [g.gate_id for g in listed.pending_controller_gates] == [first.gate_id, second.gate_id]


def test_remove_one_gate_by_id(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    keep, drop = PendingGate.new("command", {}), PendingGate.new("edit", {})
    store.add_controller_gate(tid, keep)
    store.add_controller_gate(tid, drop)
    assert store.remove_controller_gate(tid, drop.gate_id) is True
    assert store.remove_controller_gate(tid, drop.gate_id) is False
    thread = store.get_thread(tid)
    assert thread is not None and [g.gate_id for g in thread.pending_controller_gates] == [keep.gate_id]


def test_clear_all_or_one_agents_gates(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    agent = GateAgent(id="agent-1", label="impl", name="general")
    parent = PendingGate.new("command", {})
    child = PendingGate.new("command", {}, agent=agent)
    store.add_controller_gate(tid, parent)
    store.add_controller_gate(tid, child)
    store.clear_controller_gates(tid, agent_id="agent-1")
    thread = store.get_thread(tid)
    assert thread is not None and [g.gate_id for g in thread.pending_controller_gates] == [parent.gate_id]
    store.clear_controller_gates(tid)
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates == []


def test_a_legacy_single_gate_row_reads_as_a_one_item_list_with_a_stable_id(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    legacy = '{"kind": "edit", "payload": {"diff_entries": []}}'
    store._conn.execute("UPDATE chat_threads SET controller_gate_json = ? WHERE thread_id = ?",
                        (legacy, tid))
    store._conn.commit()
    first = store.get_thread(tid)
    second = store.get_thread(tid)
    assert first is not None and second is not None
    assert [g.gate_id for g in first.pending_controller_gates] == ["legacy-edit"]
    assert first.pending_controller_gates[0].gate_id == second.pending_controller_gates[0].gate_id
    assert first.pending_controller_gates[0].kind == "edit"


def test_a_gate_stored_without_an_id_gets_one(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    stored = store.add_controller_gate(tid, PendingGate(kind="mode", payload={}))
    assert stored.gate_id
    thread = store.get_thread(tid)
    assert thread is not None and thread.pending_controller_gates[0].gate_id == stored.gate_id


def _no_task(task_id: str) -> TaskRecord:
    raise KeyError(task_id)


def _command_gated_task() -> TaskRecord:
    es = TaskExecutionState()
    es.pending_command_request = CommandApprovalRequest(
        decision_id="d", command="ls", args=[], cwd="", step_id="s1")
    return TaskRecord(task_id="task-1", goal="g", workspace_path="/w",
                      status=TaskStatus.AWAITING_COMMAND_DECISION, execution_state=es)


def test_live_state_carries_the_gate_list_and_the_legacy_first_gate(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    first, second = PendingGate.new("command", {}), PendingGate.new("mcp_tool", {})
    store.add_controller_gate(tid, first)
    store.add_controller_gate(tid, second)
    live = resolve_thread_live(store.get_thread(tid), None, _no_task)
    assert [g.gate_id for g in live.pending_gates] == [first.gate_id, second.gate_id]
    assert live.pending_gate is not None and live.pending_gate.gate_id == first.gate_id
    dumped = live.model_dump(mode="json")
    assert dumped["pending_gates"][0]["gate_id"] == first.gate_id
    assert dumped["pending_gate"]["gate_id"] == first.gate_id


def test_no_gates_means_an_empty_list_and_no_legacy_gate(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    live = resolve_thread_live(store.get_thread(tid), None, _no_task)
    assert live.pending_gates == [] and live.pending_gate is None


def test_task_gates_carry_a_synthetic_task_id() -> None:
    task = _command_gated_task()
    live = resolve_live_state(task.task_id, lambda _tid: task)
    assert [g.gate_id for g in live.pending_gates] == ["task:task-1:command"]
    assert live.pending_gate is not None and live.pending_gate.gate_id == "task:task-1:command"


def test_controller_gates_come_first_then_the_task_gate(tmp_path: Path) -> None:
    store, tid = _store(tmp_path)
    ctrl = store.add_controller_gate(tid, PendingGate.new("mode", {}))
    task = _command_gated_task()
    live = resolve_thread_live(store.get_thread(tid), task.task_id, lambda _tid: task)
    assert [g.gate_id for g in live.pending_gates] == [ctrl.gate_id, "task:task-1:command"]
