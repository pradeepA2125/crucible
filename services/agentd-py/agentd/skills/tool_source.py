from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from agentd.skills.config import skills_body_max_chars
from agentd.tools.registry import ToolDefinition, ToolOutput

_READ_SKILL_DEF = ToolDefinition(
    name="read_skill",
    description=(
        "Load a skill's full SKILL.md instructions into context. Call with the skill "
        "name from the AVAILABLE SKILLS catalog when that skill is relevant to the task."
    ),
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
)


class SkillToolSource:
    """ToolSource exposing read_skill. Activated bodies land in the shared active_skills
    dict the controller loop injects into the dynamic tail each iteration.

    For the main agent exactly one skill is active at a time: a new read_skill REPLACES
    the previous entry rather than accumulating, and (when on_activate is given) persists
    the replacement immediately so it survives a mid-turn /stop, not just a clean
    turn-end. A sub-agent's source is `additive` instead (spec §5.2)."""

    name = "skills"

    def __init__(
        self,
        loader: object,
        active_skills: dict[str, str],
        on_activate: Callable[[str | None], Awaitable[None]] | None = None,
        *, additive: bool = False,
    ) -> None:
        self._loader = loader
        self._active = active_skills
        self._on_activate = on_activate
        self._additive = additive

    def definitions(self) -> list[ToolDefinition]:
        return [_READ_SKILL_DEF]

    def owns(self, tool: str) -> bool:
        return tool == "read_skill"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != "read_skill":
            return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)
        name = str(args.get("name", "")).strip()
        catalog = self._loader.load_catalog()  # type: ignore[attr-defined]
        manifest = next((m for m in catalog if m.name == name), None)
        if manifest is None:
            avail = ", ".join(m.name for m in catalog) or "(none)"
            return ToolOutput(
                output=f"Error: no skill named '{name}'. Available: {avail}", is_error=True
            )
        try:
            body = manifest.body_path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolOutput(output=f"Error: cannot read skill '{name}': {exc}", is_error=True)
        cap = skills_body_max_chars()
        if len(body) > cap:
            body = body[:cap] + f"\n\n[... skill '{name}' truncated at {cap} chars ...]"
        if not self._additive:
            # The parent keeps exactly one active skill; a sub-agent accumulates every
            # skill it reads (spec §5.2) and persists none of them.
            self._active.clear()
        self._active[name] = body
        if self._on_activate is not None:
            await self._on_activate(json.dumps({"name": name, "body": body}))
        return ToolOutput(output=body)
