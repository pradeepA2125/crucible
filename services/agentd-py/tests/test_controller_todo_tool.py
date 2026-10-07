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


@pytest.mark.asyncio
async def test_on_mutate_fires_with_ledger_json_after_successful_write():
    # Mid-turn persistence hook: the controller wires this to set_controller_todos so /live
    # renders the checklist WHILE the EDIT turn is still running (the DB is /live's source —
    # without this the card only appears after the turn ends, by which point a terminal
    # outcome has already cleared it).
    led = TodoLedger()
    captured: list[str | None] = []

    async def _cb(raw: str | None) -> None:
        captured.append(raw)

    await TodoToolSource(led, on_mutate=_cb).execute(
        "write_todos", {"items": [{"title": "A", "status": "pending"}]})
    assert len(captured) == 1
    assert [i.title for i in TodoLedger.from_json(captured[0]).items] == ["A"]


@pytest.mark.asyncio
async def test_on_mutate_not_fired_on_rejected_write():
    captured: list[str | None] = []

    async def _cb(raw: str | None) -> None:
        captured.append(raw)

    await TodoToolSource(TodoLedger(), on_mutate=_cb).execute("write_todos", {"items": []})
    assert captured == []


@pytest.mark.asyncio
async def test_a_rewrite_that_changes_no_status_keeps_the_notes_but_says_so():
    # Live 2026-10-07 (gpt-5.6-terra): the main agent re-sent an unchanged list 26 times,
    # varying only `note`, while a team did the work — ~20% of the run's input tokens.
    led = TodoLedger()
    src = TodoToolSource(led)
    await src.execute("write_todos", {"items": [
        {"title": "Survey", "status": "in_progress"}, {"title": "Build", "status": "pending"}]})
    out = await src.execute("write_todos", {"items": [
        {"title": "Survey", "status": "in_progress", "note": "still surveying"},
        {"title": "Build", "status": "pending"}]})
    assert out.is_error is False
    assert led.items[0].note == "still surveying"          # the note is kept
    assert out.output.startswith("No status changed")
    assert "Do not call write_todos again" in out.output


@pytest.mark.asyncio
async def test_a_status_change_or_a_reshape_is_a_normal_update():
    led = TodoLedger()
    src = TodoToolSource(led)
    await src.execute("write_todos", {"items": [{"title": "A", "status": "in_progress"}]})
    flipped = await src.execute("write_todos", {"items": [{"title": "A", "status": "done"}]})
    reshaped = await src.execute("write_todos", {"items": [
        {"title": "A", "status": "done"}, {"title": "B", "status": "pending"}]})
    assert flipped.output.startswith("Todo list updated")
    assert reshaped.output.startswith("Todo list updated")
