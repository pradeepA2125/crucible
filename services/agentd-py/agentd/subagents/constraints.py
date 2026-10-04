"""Tighten-only permission resolution (spec §3.12).

There is no single privilege scale (v1: dontAsk is looser on edits but stricter on
commands), so each dimension is tightened on its own. Inputs: the snapshot taken at
dispatch, the definition as it is now (None when the file was deleted), and what the agent
inherited from its dispatcher."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentd.prompting.tagged import Permission
from agentd.subagents.definitions import AgentDefinition
from agentd.subagents.permissions import definition_allows_edit

_AUTO_EDIT_MODES = frozenset({"acceptEdits", "dontAsk"})


@dataclass(frozen=True)
class Constraints:
    permission: Permission
    no_ask: bool
    edit_review: Literal["shared", "auto", "required"]
    capped: bool
    tools: frozenset[str] | None
    can_edit: bool


def _intersect(a: frozenset[str] | None, b: frozenset[str] | None) -> frozenset[str] | None:
    if a is None:
        return b
    if b is None:
        return a
    return a & b


def resolve_constraints(
    snapshot: AgentDefinition, current: AgentDefinition | None, inherited: dict[str, bool],
    *, is_builtin: bool,
) -> Constraints:
    deleted = current is None and not is_builtin
    now = current or snapshot
    capped = (snapshot.trust == "capped" or now.trust == "capped"
              or bool(inherited.get("capped")))
    read_only = (deleted or "plan" in (snapshot.permission, now.permission)
                 or bool(inherited.get("read_only")))
    no_ask = ("dontAsk" in (snapshot.permission, now.permission)
              or bool(inherited.get("no_ask")))
    tools = _intersect(snapshot.tools, now.tools)
    can_edit = (not read_only
                and definition_allows_edit(snapshot.tools, snapshot.disallowed_tools)
                and definition_allows_edit(now.tools, now.disallowed_tools))
    if read_only:
        permission: Permission = "plan"
    elif no_ask:
        permission = "dontAsk"
    elif not capped and snapshot.permission == now.permission == "acceptEdits":
        permission = "acceptEdits"
    else:
        permission = "default"
    if capped:
        edit_review: Literal["shared", "auto", "required"] = "required"
    elif snapshot.permission in _AUTO_EDIT_MODES and now.permission in _AUTO_EDIT_MODES:
        edit_review = "auto"
    else:
        edit_review = "shared"
    return Constraints(permission=permission, no_ask=no_ask, edit_review=edit_review,
                       capped=capped, tools=tools, can_edit=can_edit)


def inherited_for_children(c: Constraints) -> dict[str, bool]:
    return {"read_only": c.permission == "plan", "no_ask": c.no_ask, "capped": c.capped}
