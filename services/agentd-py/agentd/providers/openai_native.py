"""Native function calling for the ChatGPT plan route: actions as tools, history as items.

The models on this route (Codex-tuned) work in a loop of their own: messages carry a
`phase` (`commentary` preambles, then a `final_answer`) and actions are meant to be
`function_call` items, after which the model stops and waits for results. Asking for
one action as schema-constrained *text* forces every message — preambles included — to
be an action, so one response held several made-up steps (measured live, 2026-10-07).
With function tools, `tool_choice` and `parallel_tool_calls: false`, a response holds
exactly one call. And when earlier steps arrive as the model's own calls and their
outputs, rather than role-labelled JSON inside one user message, it recognises work it
already did instead of redoing it.

The conversion is lossless: `from_native_input` rebuilds the engine's payload, and the
tests check the round trip for every history shape the loops write.
https://developers.openai.com/api/docs/guides/function-calling
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agentd.providers.openai_strict_schema import StrictSchemaCodec, encode_strict_schema

NAMESPACE = "crucible"
HISTORY_KEY = "conversation_history"
# A call the loop recorded no result for (a clarify or answer followed by the user's
# next message). The API requires an output for every call, so this is added, and
# dropped again on the way back.
NO_RESULT = "(no result recorded for this action)"
# A result whose label isn't the call's own tool — a correction or redirect from the
# loop (label ""), or e.g. an `edit` result — keeps its label in a header line.
_RESULT_LABEL = re.compile(r"\A\[from ([^\]\n]*)\]\n")
_ORPHAN_LABEL = re.compile(r"\A\[tool_result from ([^\]\n]*)\]\n")
_LOOP_LABEL = "controller"  # how the empty label (the loop itself) reads to the model
_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")

UNION_INSTRUCTIONS = (
    "\n\n# How you act\n"
    f"Take exactly one action per turn by calling one function from the `{NAMESPACE}` "
    "namespace; its arguments are that action's fields. Your earlier actions appear as "
    "your own function calls and their results as the function outputs."
)


def forced_instructions(name: str) -> str:
    return (f"\n\n# How you respond\nRespond by calling the `{name}` function; its "
            "arguments are your response.")


@dataclass
class NativeActions:
    """The function tools for one request, and how to map calls to and from actions."""

    tools: list[dict[str, Any]]
    tool_choice: str | dict[str, str]
    instructions: str
    union: bool
    # Union: one codec per action type. Flat: a single codec under the function name.
    codecs: dict[str, StrictSchemaCodec] = field(default_factory=dict)

    def decode_call(self, name: str, arguments: str) -> dict[str, object]:
        codec = self.codecs.get(name)
        if codec is None:
            raise RuntimeError(f"model called an unknown function {name!r}")
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"function {name!r} arguments are not valid JSON") from exc
        if not isinstance(args, dict):
            raise RuntimeError(f"function {name!r} arguments must be a JSON object")
        decoded = codec.decode(args)
        return {"type": name, **decoded} if self.union else decoded

    def encode_action(self, action: dict[str, object]) -> tuple[str, str] | None:
        """A past action as (function name, arguments). None: not an action of ours."""
        if self.union:
            name = action.get("type")
            if not isinstance(name, str) or name not in self.codecs:
                return None
            fields = {k: v for k, v in action.items() if k != "type"}
            return name, json.dumps(self.codecs[name].encode(fields))
        (name, codec), = self.codecs.items()
        return name, json.dumps(codec.encode(dict(action)))


def native_actions(schema: Mapping[str, Any], schema_name: str) -> NativeActions:
    branches = _union_branches(schema)
    if branches is not None:
        functions: list[dict[str, Any]] = []
        codecs: dict[str, StrictSchemaCodec] = {}
        for name, branch in branches:
            params = copy.deepcopy(branch)
            params["properties"] = {k: v for k, v in params["properties"].items()
                                    if k != "type"}
            params["required"] = [r for r in params.get("required", []) if r != "type"]
            codec = encode_strict_schema(params)
            codecs[name] = codec
            description = branch.get("description")
            functions.append({
                "type": "function", "name": name, "strict": True,
                "description": description if isinstance(description, str)
                else f"Take the `{name}` action.",
                "parameters": codec.schema,
            })
        namespace_description = schema.get("description")
        return NativeActions(
            tools=[{"type": "namespace", "name": NAMESPACE,
                    "description": namespace_description if isinstance(
                        namespace_description, str) else "Your available actions",
                    "tools": functions}],
            tool_choice="required", instructions=UNION_INSTRUCTIONS, union=True,
            codecs=codecs)
    name = _NAME_RE.sub("_", schema_name)[:64] or "respond"
    codec = encode_strict_schema(dict(schema))
    return NativeActions(
        tools=[{"type": "function", "name": name, "strict": True,
                "description": "Your response.", "parameters": codec.schema}],
        tool_choice={"type": "function", "name": name},
        instructions=forced_instructions(name), union=False, codecs={name: codec})


def _union_branches(schema: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]] | None:
    """[(action type, branch)] when every branch of a root union names its `type`."""
    union = schema.get("anyOf") or schema.get("oneOf")
    if not isinstance(union, list) or not union:
        return None
    branches = []
    for branch in union:
        props = branch.get("properties") if isinstance(branch, dict) else None
        tp = props.get("type") if isinstance(props, dict) else None
        if not isinstance(tp, dict):
            return None
        if "const" in tp:
            name = tp["const"]
        elif isinstance(tp.get("enum"), list) and len(tp["enum"]) == 1:
            name = tp["enum"][0]
        else:
            return None
        if not isinstance(name, str):
            return None
        branches.append((name, branch))
    return branches


# ---------------------------------------------------------------- history


def to_native_input(payload: dict[str, object], actions: NativeActions) -> list[dict[str, Any]]:
    """The engine's payload as native input items.

    Payload fields before the history (stable workspace context) open the input, the
    history follows as items, and every later field (goal, instruction, budget…) closes
    it — the same order as today, so the stable part still leads for the prompt cache.
    """
    history = payload.get(HISTORY_KEY)
    if not isinstance(history, list):
        return [{"role": "user", "content": json.dumps(payload)}]
    keys = list(payload)
    split = keys.index(HISTORY_KEY)
    head = {k: payload[k] for k in keys[:split]}
    tail = {k: payload[k] for k in keys[split + 1:]}
    items: list[dict[str, Any]] = []
    if head:
        items.append({"role": "user", "content": json.dumps(head)})
    items.extend(_history_items(history, actions))
    items.append({"role": "user", "content": json.dumps(tail)})
    return items


def _history_items(history: list[Any], actions: NativeActions) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pending: tuple[str, str] | None = None  # (call_id, the tool its result is labelled with)
    calls = 0

    def close_pending() -> None:
        nonlocal pending
        if pending is not None:
            items.append({"type": "function_call_output", "call_id": pending[0],
                          "output": NO_RESULT})
            pending = None

    for entry in history:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = str(entry.get("content", "")) if isinstance(entry, dict) else str(entry)
        if role == "tool_result":
            label = str(entry.get("tool") or "")
            if pending is not None:
                call_id, implied = pending
                output = (content if label == implied
                          else _with_label(_RESULT_LABEL_FMT, label, content))
                items.append({"type": "function_call_output", "call_id": call_id,
                              "output": output})
                pending = None
            else:
                items.append({"role": "user",
                              "content": _with_label(_ORPHAN_LABEL_FMT, label, content)})
            continue
        close_pending()
        if role == "assistant":
            action = _parse_action(content)
            encoded = actions.encode_action(action) if action is not None else None
            if encoded is None:
                items.append({"role": "assistant", "content": content})
                continue
            calls += 1
            call_id = f"call_{calls}"
            call: dict[str, Any] = {"type": "function_call", "call_id": call_id,
                                    "name": encoded[0], "arguments": encoded[1]}
            if actions.union:
                call["namespace"] = NAMESPACE
            items.append(call)
            pending = (call_id, _implied_tool(action))  # type: ignore[arg-type]
            continue
        items.append({"role": "user", "content": content})
    close_pending()
    return items


_RESULT_LABEL_FMT = "[from {}]\n"
_ORPHAN_LABEL_FMT = "[tool_result from {}]\n"


def _with_label(fmt: str, label: str, content: str) -> str:
    return fmt.format(label or _LOOP_LABEL) + content


def _implied_tool(action: dict[str, object]) -> str:
    """The tool label a result for this action carries when it is the action's own."""
    if action.get("type") == "tool_call":
        return str(action.get("tool") or "")
    return str(action.get("type") or "")


def _parse_action(content: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("type") else None


def from_native_input(
    items: list[dict[str, Any]], actions: NativeActions, *, head_keys: int,
) -> dict[str, object]:
    """The inverse of to_native_input: proves the conversion loses nothing (tests) and
    lets debugging show what the engine sent. `head_keys` says whether a head message
    leads (0 when the payload had no fields before the history)."""
    if len(items) == 1:
        return json.loads(items[0]["content"])  # type: ignore[no-any-return]
    head = json.loads(items[0]["content"]) if head_keys else {}
    body = items[1:-1] if head_keys else items[:-1]
    tail = json.loads(items[-1]["content"])
    history: list[dict[str, object]] = []
    implied: dict[str, str] = {}
    for item in body:
        kind = item.get("type")
        if kind == "function_call":
            action = actions.decode_call(item["name"], item["arguments"])
            if not actions.union:
                action = dict(action)
            history.append({"role": "assistant", "content": json.dumps(action)})
            implied[item["call_id"]] = _implied_tool(action)
        elif kind == "function_call_output":
            output = str(item["output"])
            if output == NO_RESULT:
                continue
            match = _RESULT_LABEL.match(output)
            if match:
                label = "" if match.group(1) == _LOOP_LABEL else match.group(1)
                history.append({"role": "tool_result", "tool": label,
                                "content": output[match.end():]})
            else:
                history.append({"role": "tool_result", "tool": implied[item["call_id"]],
                                "content": output})
        elif item.get("role") == "user":
            content = str(item["content"])
            match = _ORPHAN_LABEL.match(content)
            if match:
                label = "" if match.group(1) == _LOOP_LABEL else match.group(1)
                history.append({"role": "tool_result", "tool": label,
                                "content": content[match.end():]})
            else:
                history.append({"role": "user", "content": content})
        else:
            history.append({"role": str(item.get("role")), "content": item.get("content")})
    return {**head, HISTORY_KEY: history, **tail}


# ---------------------------------------------------------------- prompt cache


# Marks the per-turn message (goal, instruction, budget…). Earlier ones stay in the input
# so each request starts with the whole previous one; this line says which is current.
CURRENT_STEP = "Current step (supersedes earlier step messages):\n"


def session_id_for(owner: str | None, instructions: str,
                   first_item: dict[str, Any] | None) -> str | None:
    """The plan route's cache key for one prompt stream. Measured live: the route takes its
    prompt_cache_key from the session_id header and gives a request without one a random
    key, so nothing is ever reused.

    One id per stream: the usage owner (thread:<id> for the main agent, the agent id for a
    sub-agent or member) plus a digest of what stays fixed while that stream's history
    grows — the instructions and the first input item. An owner's other calls (the memory
    consolidator under the thread, a member after it gains `edit`) send other instructions
    and get their own id; membrane#77 measured 1 in 20 misses with an id per stream against
    3 in 13 with one id shared across interleaved prompts. Stable across restarts."""
    if not owner:
        return None
    stream = _digest(instructions, first_item)[:16]
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"crucible:{owner}:{stream}"))


def _digest(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class SessionInputs:
    """Keeps each loop's input append-only, so the server's automatic cache breakpoint —
    the end of the previous request's input — is a prefix of the next request.

    The engine rebuilds its payload every iteration with a new per-turn message at the
    end. Sent as-is, the next request differs from the previous one at that message and
    only the instructions+tools block is reused (measured: 8.8k of 21k). Here, when the new
    history starts with the history sent last time, the input is the previous input plus
    the new history items plus the new per-turn message; anything else (a compaction, a
    call left without a result, another loop) starts over. Commit only after a request
    succeeds, so a failed one never leaves its per-turn message behind."""

    def __init__(self, limit: int = 64) -> None:
        self._limit = limit
        self._state: OrderedDict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = (
            OrderedDict())

    @staticmethod
    def key(session: str, schema_name: str, instructions: str,
            tools: list[dict[str, Any]]) -> str:
        return _digest(session, schema_name, instructions, tools)

    def build(self, key: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """`items` is to_native_input's output for a payload with history: everything but
        the last item is head + history, the last is the per-turn message."""
        body, tail = items[:-1], _labelled(items[-1])
        previous = self._state.get(key)
        if previous is not None:
            sent_body, sent = previous
            if body[:len(sent_body)] == sent_body:
                return sent + body[len(sent_body):] + [tail]
        return body + [tail]

    def commit(self, key: str, items: list[dict[str, Any]], sent: list[dict[str, Any]]) -> None:
        self._state[key] = (items[:-1], sent)
        self._state.move_to_end(key)
        while len(self._state) > self._limit:
            self._state.popitem(last=False)


def _labelled(tail: dict[str, Any]) -> dict[str, Any]:
    return {**tail, "content": CURRENT_STEP + str(tail.get("content", ""))}
