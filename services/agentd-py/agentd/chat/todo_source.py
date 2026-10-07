"""TodoToolSource — exposes the write_todos tool over a shared TodoLedger.

A ToolSource (the tools/sources.py seam) so adding it never touches the loop's tool
plumbing. The controller passes the SAME TodoLedger here and into ControllerLoop, so
a write_todos call is immediately visible to the loop's gate.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agentd.chat.todo_ledger import _STATUSES, TodoItem, TodoLedger
from agentd.prompting.tagged import RenderContext, render_prompt, tagged
from agentd.tools.registry import ToolDefinition, ToolOutput

_WRITE_TODOS_DESCRIPTION = tagged("write_todos_description", (
    "Create or update the todo list for a LARGE / multi-part change. Send the FULL "
    "list every call (full-list rewrite): every item with its current status. Use it "
    "when the request decomposes into multiple distinct features/steps; SKIP it for a "
    "single small edit. To reshape (split/insert/reorder), just resend the list in the "
    "new shape. Mark an item 'done' ONLY with evidence (cite the tool/edit result in "
    "'note'); 'blocked' (put the unblock condition in 'note') if you cannot proceed; "
    "'cancelled' (say why in 'note') to abandon one — never silently drop it. "
    "A 'run tests/verify' step from <<main>>the user's plan<</main>><<child>>your task<</child>> "
    "is always its OWN item, separate "
    "from creating the file it tests; its 'done' evidence is the command output, never "
    "an edit result. "
    "<<main>>submit_changes<</main>><<child>>report<</child>> is BLOCKED while any item is "
    "pending or in_progress."
))
_WRITE_TODOS_PARAMETERS: dict[str, object] = {
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
}


def _write_todos_def(ctx: RenderContext) -> ToolDefinition:
    return ToolDefinition(
        name="write_todos",
        description=render_prompt(_WRITE_TODOS_DESCRIPTION, ctx),
        parameters=_WRITE_TODOS_PARAMETERS,
    )


# Main-rendered, kept for existing importers.
_WRITE_TODOS_DEF = _write_todos_def(RenderContext.main())


class TodoToolSource:
    name = "todo"

    def __init__(
        self,
        ledger: TodoLedger,
        on_mutate: Callable[[str | None], Awaitable[None]] | None = None,
        *,
        render_ctx: RenderContext | None = None,
    ) -> None:
        self._ledger = ledger
        # Awaited with ledger.to_json() right after a successful write_todos so the
        # controller can persist the in-flight ledger mid-turn (renders on /live while the
        # turn runs). None-safe: a source built without it (tests, no store) just no-ops.
        self._on_mutate = on_mutate
        self._render_ctx = render_ctx or RenderContext.main()

    def definitions(self) -> list[ToolDefinition]:
        return [_write_todos_def(self._render_ctx)]

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
        # A rewrite that changes no title or status is a no-op for the ledger's meaning.
        # Its notes are kept, but the reply says so: a model told to keep the list current
        # otherwise re-sends it every iteration (live 2026-10-07, gpt-5.6-terra: 26 calls
        # while a team did the work).
        unchanged = bool(self._ledger.items) and _shape(self._ledger.items) == _shape(new_items)
        self._ledger.replace(new_items)
        if self._on_mutate is not None:
            await self._on_mutate(self._ledger.to_json())
        if unchanged:
            return ToolOutput(output=(
                "No status changed — the list already matches. Notes saved. "
                "Do not call write_todos again until an item starts, finishes, is blocked "
                "or is cancelled; continue with the work, or end the turn if nothing is left "
                "for you to do.\n" + self._ledger.render()))
        return ToolOutput(output="Todo list updated:\n" + self._ledger.render())


def _shape(items: list[TodoItem]) -> list[tuple[str, str]]:
    return [(i.title, i.status) for i in items]
