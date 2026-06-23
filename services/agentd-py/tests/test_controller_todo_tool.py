import pytest

from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource


def test_source_owns_only_write_todos():
    src = TodoToolSource(TodoLedger())
    assert src.owns("write_todos") is True
    assert src.owns("read_file") is False
    assert [d.name for d in src.definitions()] == ["write_todos"]


def test_definition_status_enum_has_five_states():
    d = TodoToolSource(TodoLedger()).definitions()[0]
    enum = d.parameters["properties"]["items"]["items"]["properties"]["status"]["enum"]
    assert set(enum) == {"pending", "in_progress", "done", "blocked", "cancelled"}


@pytest.mark.asyncio
async def test_write_todos_mutates_ledger_and_returns_render():
    led = TodoLedger()
    out = await TodoToolSource(led).execute("write_todos", {"items": [
        {"title": "Enemies", "status": "done", "note": "added in last edit"},
        {"title": "Jump", "status": "pending"},
    ]})
    assert out.is_error is False
    assert [(i.title, i.status) for i in led.items] == [("Enemies", "done"), ("Jump", "pending")]
    assert "Enemies" in out.output and "Jump" in out.output


@pytest.mark.asyncio
async def test_write_todos_rejects_bad_status_without_mutating():
    led = TodoLedger()
    out = await TodoToolSource(led).execute(
        "write_todos", {"items": [{"title": "X", "status": "doing"}]})
    assert out.is_error is True
    assert led.items == []


@pytest.mark.asyncio
async def test_write_todos_rejects_empty_items():
    out = await TodoToolSource(TodoLedger()).execute("write_todos", {"items": []})
    assert out.is_error is True
