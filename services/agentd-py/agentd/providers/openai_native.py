"""Native function calling for the ChatGPT plan route: actions as tools, history as items.

The models on this route (Codex-tuned) work in a loop of their own: messages carry a
`phase` (`commentary` preambles, then a `final_answer`) and actions are meant to be
`function_call` items, after which the model stops and waits for results. Asking for
one action as schema-constrained *text* forces every message — preambles included — to
be an action, so one response held several made-up steps (measured live, 2026-10-07).
Every response requests one function call. The controller exposes tool_calls as
one function containing one or more members with identities and dependency metadata.
Earlier steps arrive as the model's own calls and matched outputs, so it recognises completed work.

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
from functools import lru_cache
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
    "namespace. The JSON examples describe controller actions: emit this action through "
    "its corresponding native function (named by type); pass all fields except type as "
    "arguments. Do not emit the examples as text. Your earlier actions appear as "
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
            if not isinstance(name, str):
                return None
            codec = self.codecs.get(name)
            if codec is None and name in {"tool_call", "tool_calls"}:
                codec = _historical_tool_codec(name)
            if codec is None:
                return None
            fields = {k: v for k, v in action.items() if k not in {"type", "provider_call_id"}}
            return name, json.dumps(codec.encode(fields))
        (name, codec), = self.codecs.items()
        return name, json.dumps(codec.encode(dict(action)))


@lru_cache(maxsize=1)
def _historical_tool_codec(name="tool_call"):
    # A terminal-only schema still has to replay earlier tool groups faithfully.
    from agentd.chat.controller_prompts import _CALL_SCHEDULING, _VARIANT_SPECS
    if name == "tool_calls":
        return encode_strict_schema({"type": "object", "properties": {
            "thought": {"type": "string"}, **copy.deepcopy(_VARIANT_SPECS[name]["properties"])},
            "required": ["thought", "calls"]})
    return encode_strict_schema({"type": "object", "properties": {
        "tool": {"type": "string"}, "args": {"type": "object"},
        "thought": {"type": "string"}, **copy.deepcopy(_CALL_SCHEDULING)},
        "required": ["tool", "args"]})


def native_actions(schema: Mapping[str, Any], schema_name: str) -> NativeActions:
    branches = _union_branches(schema)
    if branches is not None:
        functions: list[dict[str, Any]] = []
        codecs: dict[str, StrictSchemaCodec] = {}
        has_batch = any(name == "tool_calls" for name, _ in branches)
        for name, branch in branches:
            params = copy.deepcopy(branch)
            description = params.pop("description", None)
            params["properties"] = {k: v for k, v in params["properties"].items()
                                    if k != "type"}
            params["required"] = [r for r in params.get("required", []) if r != "type"]
            codec = encode_strict_schema(params)
            codecs[name] = codec
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
            tool_choice="required", instructions=UNION_INSTRUCTIONS + (
                " For a tool batch, call crucible.tool_calls once with thought and a calls array. "
                if has_batch else ""), union=True,
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
    pending: dict[str, str] = {}
    calls = 0
    batch_results: list[dict[str, Any]] | None = None

    def close_pending() -> None:
        nonlocal batch_results
        for call_id in pending:
            items.append({"type": "function_call_output", "call_id": call_id,
                          "output": "[batch results]\n" + json.dumps(batch_results)
                          if batch_results is not None else NO_RESULT})
        batch_results = None
        pending.clear()

    for entry in history:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = str(entry.get("content", "")) if isinstance(entry, dict) else str(entry)
        if role == "tool_result" and batch_results is not None:
            batch_results.append(entry)
            continue
        if role == "tool_result":
            label = str(entry.get("tool") or "")
            identity = entry.get("provider_call_id") or entry.get("call_id")
            if pending:
                call_id = str(identity) if identity in pending else next(iter(pending))
                # A grouped result must never attach to a different pending sibling.
                if entry.get("batch_id") and identity not in pending:
                    items.append({"role": "user", "content": _with_label(
                        _ORPHAN_LABEL_FMT, label, content)})
                    continue
                implied = pending.pop(call_id)
                output = (
                    "[batch result]\n" + json.dumps(entry)
                    if entry.get("batch_id") or entry.get("delivered_read")
                    else content
                    if label == implied
                    else _with_label(_RESULT_LABEL_FMT, label, content)
                )
                items.append({"type": "function_call_output", "call_id": call_id,
                              "output": output})
            else:
                items.append({"role": "user",
                              "content": _with_label(_ORPHAN_LABEL_FMT, label, content)})
            continue
        close_pending()
        if role == "assistant":
            action = _parse_action(content)
            if action is not None and action.get("type") == "tool_calls":
                # Keep the original entry for lossless replay, but expose only action fields
                # as native arguments; persisted batch/member metadata is not a parameter.
                items.append({"role": "user", "content": "[batch envelope]\n" + json.dumps(entry)})
                clean = {"type": "tool_calls", "thought": action.get("thought", ""),
                         "calls": [{k: v for k, v in node.items()
                                    if k not in {"provider_call_id", "thought", "type"}}
                                   for node in action.get("calls", [])]}
                encoded = actions.encode_action(clean)
                if encoded is None:
                    raise RuntimeError("native history cannot encode a tool batch")
                calls += 1
                call_id = str(action.get("provider_call_id") or f"call_{calls}")
                items.append({"type": "function_call", "call_id": call_id,
                              "name": encoded[0], "arguments": encoded[1],
                              **({"namespace": NAMESPACE} if actions.union else {})})
                pending[call_id] = "tool_calls"
                batch_results = []
                continue
            encoded = actions.encode_action(action) if action is not None else None
            if encoded is None:
                items.append({"role": "assistant", "content": content})
                continue
            if action.get("provider_call_id"):
                items.append({"role": "user", "content": "[action metadata]\n" + json.dumps({
                    "provider_call_id": action["provider_call_id"],
                    "entry": {k: v for k, v in entry.items() if k not in {"role", "content"}}})})
            calls += 1
            call_id = str(action.get("provider_call_id") or f"call_{calls}")
            call: dict[str, Any] = {"type": "function_call", "call_id": call_id,
                                    "name": encoded[0], "arguments": encoded[1]}
            if actions.union:
                call["namespace"] = NAMESPACE
            items.append(call)
            pending[call_id] = _implied_tool(action)
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
    batch_meta = None
    action_meta = None
    batch_members = []
    envelope_entry = None
    for item in body:
        kind = item.get("type")
        if kind == "function_call":
            if item["name"] in {"tool_call", "tool_calls"} and item["name"] not in actions.codecs:
                action = {"type": item["name"], **_historical_tool_codec(item["name"]).decode(
                    json.loads(item["arguments"]))}
            else:
                action = actions.decode_call(item["name"], item["arguments"])
            if not actions.union:
                action = dict(action)
            if envelope_entry is not None:
                history.append(envelope_entry)
                envelope_entry = None
            elif batch_meta is not None:
                member = batch_meta["members"][len(batch_members)]
                batch_members.append({**member, "tool": action["tool"], "args": action["args"]})
                if len(batch_members) == len(batch_meta["members"]):
                    history.append({"role": "assistant", "content": json.dumps(
                        {**batch_meta["action"], "calls": batch_members}), **batch_meta["entry"]})
                    batch_meta = None
                    batch_members = []
            else:
                metadata = {}
                if action_meta:
                    action["provider_call_id"] = action_meta["provider_call_id"]
                    metadata = action_meta["entry"]
                    action_meta = None
                history.append({"role": "assistant", "content": json.dumps(action), **metadata})
            implied[item["call_id"]] = _implied_tool(action)
        elif kind == "function_call_output":
            output = str(item["output"])
            if output.startswith("[batch results]\n"):
                history.extend(json.loads(output[len("[batch results]\n"):]))
                continue
            if output.startswith("[batch result]\n"):
                history.append(json.loads(output[len("[batch result]\n"):]))
                continue
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
            if content.startswith("[batch envelope]\n"):
                envelope_entry = json.loads(content[len("[batch envelope]\n"):])
                continue
            if content.startswith("[action metadata]\n"):
                action_meta = json.loads(content[len("[action metadata]\n"):])
                continue
            if content.startswith("[tool batch]\n"):
                batch_meta = json.loads(content[len("[tool batch]\n"):])
                continue
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
