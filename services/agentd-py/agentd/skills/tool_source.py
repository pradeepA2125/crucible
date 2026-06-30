from __future__ import annotations

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
    dict the controller loop injects into the dynamic tail each iteration."""

    name = "skills"

    def __init__(self, loader: object, active_skills: dict[str, str]) -> None:
        self._loader = loader
        self._active = active_skills

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
        self._active[name] = body
        return ToolOutput(output=body)
