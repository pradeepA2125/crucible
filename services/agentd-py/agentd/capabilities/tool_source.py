"""The `load_capabilities` tool: deliver a deferred MCP server's schemas as a result."""
from __future__ import annotations

from dataclasses import dataclass, field

from agentd.capabilities.bundle import (
    DEFAULT_LIMIT,
    MAX_BUNDLE_CHARS,
    MAX_LIMIT,
    render_bundle,
    select_tools,
)
from agentd.capabilities.catalog import LOADER_TOOL, short_name, tool_version
from agentd.capabilities.history import guidance_visible, loaded_versions
from agentd.tools.registry import ToolDefinition, ToolOutput

_MAX_QUERY_CHARS = 1600

LOAD_CAPABILITIES = ToolDefinition(
    name=LOADER_TOOL,
    description=(
        "Load the tool schemas of a deferred namespace from OPTIONAL CAPABILITIES. Give just "
        "`namespace`: that loads every tool of the server. The result is the schemas, not an "
        "execution: the loaded tools are callable from your next step. Call it alone in its "
        "step. Only when a whole-namespace load is refused as bundle_too_large, retry with a "
        "`query` (a few words naming the tools you need) or exact unprefixed `tool_names`. "
        "Loading grants no permission: every call still goes through the usual approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "description": 'For example "mcp.<server>".'},
            "query": {"type": "string", "minLength": 1, "maxLength": _MAX_QUERY_CHARS},
            "tool_names": {"type": "array", "items": {"type": "string"},
                           "maxItems": MAX_LIMIT},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
        },
        "required": ["namespace"],
        "additionalProperties": False,
    },
)


@dataclass
class CapabilitySession:
    """Shared by the controller loop and the source, like `active_skills`: the loop sets
    `namespaces` at run start and hands over its live history list."""

    namespaces: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    history: list[dict[str, object]] = field(default_factory=list)
    guidance: str = ""


def _error(text: str) -> ToolOutput:
    return ToolOutput(output=f"Error: {text}", is_error=True)


class CapabilityToolSource:
    name = "capabilities"

    def __init__(self, session: CapabilitySession) -> None:
        self._session = session

    def definitions(self) -> list[ToolDefinition]:
        return [LOAD_CAPABILITIES]

    def owns(self, tool: str) -> bool:
        return tool == LOADER_TOOL

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        if tool != LOADER_TOOL:
            return _error(f"unknown tool '{tool}'")
        namespace = str(args.get("namespace", "")).strip()
        definitions = self._session.namespaces.get(namespace)
        if definitions is None:
            known = ", ".join(sorted(self._session.namespaces)) or "(none)"
            return _error(f"no namespace '{namespace}'. Known namespaces: {known}")
        query = args.get("query")
        names = args.get("tool_names")
        if query is not None and names is not None:
            return _error("give either `query` or `tool_names`, not both")
        if query is not None and (not isinstance(query, str) or not query.strip()
                                  or len(query) > _MAX_QUERY_CHARS):
            return _error(f"`query` must be 1–{_MAX_QUERY_CHARS} characters")
        if names is not None and (not isinstance(names, list) or not names
                                  or len(names) > MAX_LIMIT
                                  or not all(isinstance(n, str) for n in names)):
            return _error(f"`tool_names` must be 1–{MAX_LIMIT} tool names")
        limit = args.get("limit", DEFAULT_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            return _error(f"`limit` must be an integer from 1 to {MAX_LIMIT}")
        try:
            selected = select_tools(definitions, query=query, tool_names=names, limit=limit)
        except KeyError as exc:
            return _error(f"{namespace} has no tool named {exc.args[0]}")
        if not selected:
            return ToolOutput(output=f"No tools in {namespace} match that query; try other "
                                     "words or tool_names.")
        loaded = loaded_versions(self._session.history)
        if all(loaded.get(str(d.get("name", ""))) == tool_version(d) for d in selected):
            shown = ", ".join(short_name(str(d.get("name", ""))) for d in selected)
            return ToolOutput(output=f"Already loaded: {shown}. Their schemas are earlier in "
                                     "this conversation.")
        guidance = None if guidance_visible(self._session.history) else self._session.guidance
        bundle = render_bundle(namespace, selected, guidance)
        if len(bundle) > MAX_BUNDLE_CHARS:
            return _error(f"bundle_too_large: {len(selected)} tools of {namespace} come to "
                          f"{len(bundle)} characters (limit {MAX_BUNDLE_CHARS}). Retry with a "
                          "`query` naming the tools you need, or exact unprefixed `tool_names` "
                          "from the namespace's directory line.")
        return ToolOutput(output=bundle, whole=True)
