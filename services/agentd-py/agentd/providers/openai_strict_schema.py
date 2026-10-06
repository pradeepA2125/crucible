"""Adapt our JSON schemas to OpenAI Structured Outputs strict mode, and decode replies.

OpenAI strict mode accepts a subset of JSON Schema that our schemas don't fit:
the root must be an object (the controller's discriminated union is a root `anyOf`),
every object must list its properties with `additionalProperties: false` (the
controller's `tool_call.args` is a free-form object), and every property must be
required (optional fields are expressed as nullable). Rather than fork every schema
builder for one provider, the transport encodes on the way out and decodes on the
way back, so the engine sends and receives exactly what it does on every other
provider.

| ours                       | sent to OpenAI                         | decoded back            |
|----------------------------|----------------------------------------|-------------------------|
| root union                 | `{"action": <union>}`                  | `action` unwrapped      |
| free-form object / any     | string holding JSON                    | `json.loads`            |
| optional property          | required, nullable                     | `null` → key dropped    |
| `oneOf`                    | `anyOf` (our branches are disjoint)    | —                       |
| `const`                    | single-value `enum`                    | —                       |
| keywords outside the subset| dropped (validation stays in the loop) | —                       |
"""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ENVELOPE_KEY = "action"

# Keywords OpenAI strict mode accepts. Anything else (title, default, discriminator,
# minLength, …) is dropped: an unsupported keyword fails the whole request, while the
# constraint it carried is still checked by the loop that consumes the result.
_KEEP_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "anyOf", "enum",
    "description", "$ref", "$defs", "pattern", "format", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems",
})
_STRUCTURAL = frozenset({"type", "anyOf", "oneOf", "allOf", "$ref", "enum", "const"})
_MAX_NESTING = 10
_JSON_TYPES = {str: "string", bool: "boolean", int: "integer", float: "number"}


class StrictSchemaDecodeError(RuntimeError):
    """A reply that satisfied the encoded schema but can't be decoded back (a
    JSON-string field holding invalid JSON). A RuntimeError so every loop's existing
    malformed-output correction handles it."""


@dataclass(frozen=True)
class StrictSchemaCodec:
    schema: dict[str, object]
    decode: Callable[[dict[str, object]], dict[str, object]]


def encode_strict_schema(schema: dict[str, object]) -> StrictSchemaCodec:
    return _Encoder().encode(copy.deepcopy(schema))


class _Encoder:
    def __init__(self) -> None:
        # Keyed by id() of nodes in the encoded tree, which the codec keeps alive.
        self._json_nodes: dict[int, str] = {}  # node id → "object" | "any"
        self._optional_keys: dict[int, frozenset[str]] = {}

    def encode(self, schema: dict[str, Any]) -> StrictSchemaCodec:
        defs = schema.pop("$defs", None)
        root = self._node(schema)
        wrapped = not _is_object(root)
        if wrapped:
            root = {
                "type": "object",
                "properties": {ENVELOPE_KEY: root},
                "required": [ENVELOPE_KEY],
                "additionalProperties": False,
            }
        if isinstance(defs, dict):
            root["$defs"] = {name: self._node(sub) for name, sub in defs.items()}
        decoder = _Decoder(root, self._json_nodes, self._optional_keys, wrapped)
        return StrictSchemaCodec(schema=root, decode=decoder.decode)

    def _node(self, node: Any) -> dict[str, Any]:
        if not isinstance(node, dict):
            return self._json_string("any", None)
        node = _collapse_single_all_of(node)
        if "$ref" in node:
            return {"$ref": node["$ref"]}
        if "const" in node:
            value = node.pop("const")
            node.setdefault("type", _JSON_TYPES.get(type(value), "string"))
            node["enum"] = [value]
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")
        if not (node.keys() & _STRUCTURAL):
            return self._json_string("any", node.get("description"))
        if _is_free_form_object(node):
            return self._json_string("object", node.get("description"))

        out: dict[str, Any] = {k: v for k, v in node.items() if k in _KEEP_KEYWORDS}
        if "anyOf" in out:
            out["anyOf"] = [self._node(branch) for branch in out["anyOf"]]
        if isinstance(out.get("items"), dict):
            out["items"] = self._node(out["items"])
        if isinstance(out.get("properties"), dict):
            self._object(out)
        return out

    def _object(self, out: dict[str, Any]) -> None:
        props: dict[str, Any] = out["properties"]
        required = set(out.get("required", []))
        encoded: dict[str, Any] = {}
        optional: set[str] = set()
        for name, sub in props.items():
            child = self._node(sub)
            if name not in required:
                child = self._nullable(child)
                optional.add(name)
            encoded[name] = child
        out["properties"] = encoded
        out["required"] = list(encoded)
        out["additionalProperties"] = False
        self._optional_keys[id(out)] = frozenset(optional)

    def _nullable(self, node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node or "type" not in node:
            if set(node) == {"anyOf"}:
                if {"type": "null"} not in node["anyOf"]:
                    node["anyOf"].append({"type": "null"})
                return node
            return {"anyOf": [node, {"type": "null"}]}
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if "null" not in types:
            node["type"] = [*types, "null"]
        if "enum" in node and None not in node["enum"]:
            node["enum"] = [*node["enum"], None]
        return node

    def _json_string(self, kind: str, description: object) -> dict[str, Any]:
        what = "a JSON object" if kind == "object" else "a JSON value"
        text = f"{what}, encoded as a string"
        if isinstance(description, str) and description:
            text = f"{description} ({text})"
        node: dict[str, Any] = {"type": "string", "description": text}
        self._json_nodes[id(node)] = kind
        return node


class _Decoder:
    def __init__(
        self,
        root: dict[str, Any],
        json_nodes: dict[int, str],
        optional_keys: dict[int, frozenset[str]],
        wrapped: bool,
    ) -> None:
        self._root = root
        self._defs: dict[str, Any] = root.get("$defs", {})
        self._json_nodes = json_nodes
        self._optional_keys = optional_keys
        self._wrapped = wrapped

    def decode(self, value: dict[str, object]) -> dict[str, object]:
        decoded = self._value(self._root, value, "")
        if self._wrapped:
            if not isinstance(decoded, dict) or ENVELOPE_KEY not in decoded:
                raise StrictSchemaDecodeError(f"reply has no '{ENVELOPE_KEY}' envelope")
            decoded = decoded[ENVELOPE_KEY]
        if not isinstance(decoded, dict):
            raise StrictSchemaDecodeError("reply must be a JSON object")
        return decoded

    def _resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        seen = 0
        while "$ref" in node and seen < 32:
            node = self._defs.get(str(node["$ref"]).rsplit("/", 1)[-1], {})
            seen += 1
        return node

    def _value(self, node: dict[str, Any], value: Any, path: str) -> Any:
        node = self._resolve(node)
        kind = self._json_nodes.get(id(node))
        if kind is not None:
            return _parse_json_field(value, kind, path)
        if value is None:
            return None
        if "anyOf" in node:
            branch = self._pick(node["anyOf"], value)
            return self._value(branch, value, path) if branch is not None else value
        props = node.get("properties")
        if isinstance(props, dict) and isinstance(value, dict):
            optional = self._optional_keys.get(id(node), frozenset())
            out: dict[str, Any] = {}
            for key, item in value.items():
                if item is None and key in optional:
                    continue
                sub = props.get(key)
                out[key] = self._value(sub, item, f"{path}.{key}" if path else key) \
                    if isinstance(sub, dict) else item
            return out
        items = node.get("items")
        if isinstance(items, dict) and isinstance(value, list):
            return [self._value(items, item, f"{path}[{i}]") for i, item in enumerate(value)]
        return value

    def _pick(self, branches: list[Any], value: Any) -> dict[str, Any] | None:
        for raw in branches:
            branch = self._resolve(raw)
            if branch == {"type": "null"}:
                continue
            if self._matches(branch, value):
                return branch
        return None

    def _matches(self, branch: dict[str, Any], value: Any) -> bool:
        if id(branch) in self._json_nodes:
            return isinstance(value, str)
        props = branch.get("properties")
        if isinstance(props, dict):
            if not isinstance(value, dict) or not set(value) <= set(props):
                return False
            for key, sub in props.items():
                enum = self._resolve(sub).get("enum") if isinstance(sub, dict) else None
                if isinstance(enum, list) and key in value and value[key] not in enum:
                    return False
            return True
        types = branch.get("type")
        allowed = types if isinstance(types, list) else [types]
        return _json_type(value) in allowed or (
            _json_type(value) == "integer" and "number" in allowed)


def strict_violations(schema: dict[str, object]) -> list[str]:
    """What OpenAI strict mode would reject in `schema`. Empty means it should pass."""
    problems: list[str] = []
    if not _is_object(schema):
        problems.append("root must be an object, not a union")
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        for name, sub in defs.items():
            _check(sub, f"$defs.{name}", 1, problems)
    _check(schema, "$", 0, problems)
    return problems


def _check(node: Any, path: str, depth: int, problems: list[str]) -> None:
    if not isinstance(node, dict):
        problems.append(f"{path}: not a schema object")
        return
    if depth > _MAX_NESTING:
        problems.append(f"{path}: nested deeper than {_MAX_NESTING}")
    unknown = set(node) - _KEEP_KEYWORDS
    if unknown:
        problems.append(f"{path}: unsupported keywords {sorted(unknown)}")
    if "$ref" in node:
        return
    if not ({"type", "anyOf"} & set(node)):
        problems.append(f"{path}: no type")
    for i, branch in enumerate(node.get("anyOf", []) or []):
        _check(branch, f"{path}.anyOf[{i}]", depth, problems)
    types = node.get("type")
    if types == "object" or (isinstance(types, list) and "object" in types):
        props = node.get("properties")
        if not isinstance(props, dict) or not props:
            problems.append(f"{path}: object without properties")
            props = {}
        if node.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties must be false")
        if set(node.get("required", [])) != set(props):
            problems.append(f"{path}: every property must be required")
        for name, sub in props.items():
            _check(sub, f"{path}.{name}", depth + 1, problems)
    if isinstance(node.get("items"), dict):
        _check(node["items"], f"{path}[]", depth + 1, problems)


def _is_object(node: dict[str, Any]) -> bool:
    return node.get("type") == "object" and "anyOf" not in node


def _is_free_form_object(node: dict[str, Any]) -> bool:
    types = node.get("type")
    is_object = types == "object" or (isinstance(types, list) and "object" in types)
    return is_object and not node.get("properties") and "anyOf" not in node


def _collapse_single_all_of(node: dict[str, Any]) -> dict[str, Any]:
    all_of = node.get("allOf")
    if isinstance(all_of, list) and len(all_of) == 1 and isinstance(all_of[0], dict):
        merged = {k: v for k, v in node.items() if k != "allOf"}
        merged.update(all_of[0])
        return merged
    return node


def _parse_json_field(value: Any, kind: str, path: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StrictSchemaDecodeError(f"'{path}' must be a string holding JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise StrictSchemaDecodeError(
            f"'{path}' must hold valid JSON ({exc.msg}): {value[:200]}") from exc
    if kind == "object" and not isinstance(parsed, dict):
        raise StrictSchemaDecodeError(f"'{path}' must hold a JSON object")
    return parsed


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"
