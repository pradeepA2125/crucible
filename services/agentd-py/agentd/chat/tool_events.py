"""Map an AgentToolTrace into the chat-persisted ``tool_events`` metadata shape.

The webview renders ``ChatMessage.metadata.tool_events`` as expandable tool
pills (see ``ToolEventView`` in webview-ui/src/types.ts) — the same component
the live SSE stream feeds. Persisting this shape makes pills survive thread
reloads; field names (``isError``, ``done``) are therefore frontend-camelCase,
not Python snake_case.
"""

from typing import Literal

from agentd.domain.models import AgentToolTrace

# Matches AI_EDITOR_TOOL_RESULT_MAX_CHARS' default — enough output to read what
# happened without bloating the chat DB (full output lives in artifacts).
TOOL_EVENT_MAX_OUTPUT_CHARS = 4000

ToolEventSource = Literal["explore", "execution", "planning"]


def trace_to_tool_events(
    trace: AgentToolTrace,
    source: ToolEventSource,
    max_output_chars: int = TOOL_EVENT_MAX_OUTPUT_CHARS,
) -> list[dict[str, object]]:
    """Join trace calls with their results by call_id and emit ToolEventView dicts."""
    results_by_call = {result.call_id: result for result in trace.results}
    events: list[dict[str, object]] = []
    for index, call in enumerate(trace.calls):
        event: dict[str, object] = {
            "id": index,
            "tool": call.tool_name,
            "args": call.arguments,
            "source": source,
            "done": True,
        }
        if call.thought:
            event["thought"] = call.thought
        result = results_by_call.get(call.call_id)
        if result is not None:
            output = result.output
            if len(output) > max_output_chars:
                output = output[:max_output_chars] + "\n… truncated"
            event["output"] = output
            event["isError"] = result.is_error
        events.append(event)
    return events
