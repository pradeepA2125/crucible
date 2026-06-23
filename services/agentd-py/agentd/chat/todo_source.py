"""TodoToolSource — exposes the write_todos tool over a shared TodoLedger.

A ToolSource (the tools/sources.py seam) so adding it never touches the loop's tool
plumbing. The controller passes the SAME TodoLedger here and into ControllerLoop, so
a write_todos call is immediately visible to the loop's gate.
"""
from __future__ import annotations

from agentd.chat.todo_ledger import _STATUSES, TodoItem, TodoLedger
from agentd.tools.registry import ToolDefinition, ToolOutput

_WRITE_TODOS_DEF = ToolDefinition(
    name="write_todos",
    description=(
        "Create or update the todo list for a LARGE / multi-part change. Send the FULL "
        "list every call (full-list rewrite): every item with its current status. Use it "
        "when the request decomposes into multiple distinct features/steps; SKIP it for a "
        "single small edit. To reshape (split/insert/reorder), just resend the list in the "
        "new shape. Mark an item 'done' ONLY with evidence (cite the tool/edit result in "
        "'note'); 'blocked' (put the unblock condition in 'note') if you cannot proceed; "
        "'cancelled' (say why in 'note') to abandon one — never silently drop it. "
        "submit_changes is BLOCKED while any item is pending or in_progress."
    ),
    parameters={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "status": {"type": "string", "enum": list(_STATUSES)},
                        "note": {"type": "string"},
                    },
                    "required": ["title", "status"],
                },
            }
        },
        "required": ["items"],
    },
)


class TodoToolSource:
    name = "todo"

    def __init__(self, ledger: TodoLedger) -> None:
        self._ledger = ledger

    def definitions(self) -> list[ToolDefinition]:
        return [_WRITE_TODOS_DEF]

    def owns(self, tool: str) -> bool:
        return tool == "write_todos"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != "write_todos":
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
        raw_items = args.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            return ToolOutput(
                output="write_todos needs a non-empty 'items' array.", is_error=True)
        new_items: list[TodoItem] = []
        for it in raw_items:
            if not isinstance(it, dict) or not str(it.get("title", "")).strip():
                return ToolOutput(
                    output="each todo item needs a non-empty 'title'.", is_error=True)
            status = str(it.get("status", "pending"))
            if status not in _STATUSES:
                return ToolOutput(
                    output=f"invalid status {status!r}; use one of {list(_STATUSES)}.",
                    is_error=True)
            new_items.append(TodoItem(
                title=str(it["title"]).strip(), status=status, note=str(it.get("note", ""))))
        self._ledger.replace(new_items)
        return ToolOutput(output="Todo list updated:\n" + self._ledger.render())
