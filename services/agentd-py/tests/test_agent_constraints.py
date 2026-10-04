"""Per-dimension tightening (spec §3.12)."""
from dataclasses import replace

from agentd.subagents.constraints import inherited_for_children, resolve_constraints
from agentd.subagents.definitions import BUILTIN_AGENTS

GP = BUILTIN_AGENTS["general-purpose"]


def _d(permission: str, trust: str = "trusted"):  # type: ignore[no-untyped-def]
    return replace(GP, permission=permission, trust=trust)


def test_default_shares_the_review_control() -> None:
    c = resolve_constraints(GP, GP, {}, is_builtin=True)
    assert (c.permission, c.edit_review, c.no_ask, c.capped) == ("default", "shared", False, False)


def test_accept_edits_on_both_sides_auto_accepts() -> None:
    c = resolve_constraints(_d("acceptEdits"), _d("acceptEdits"), {}, is_builtin=False)
    assert c.edit_review == "auto"
    c = resolve_constraints(_d("acceptEdits"), _d("default"), {}, is_builtin=False)
    assert c.edit_review == "shared"  # the user tightened the file: tighter wins


def test_dont_ask_is_stricter_on_commands() -> None:
    c = resolve_constraints(_d("acceptEdits"), _d("dontAsk"), {}, is_builtin=False)
    assert c.no_ask and c.edit_review == "auto"


def test_capped_always_requires_review_and_loses_accept_edits() -> None:
    c = resolve_constraints(_d("acceptEdits", "capped"), _d("acceptEdits", "capped"), {},
                            is_builtin=False)
    assert (c.permission, c.edit_review, c.capped) == ("default", "required", True)


def test_inherited_flags_tighten_a_trusted_builtin() -> None:
    c = resolve_constraints(GP, GP, {"capped": True, "read_only": True, "no_ask": True},
                            is_builtin=True)
    assert (c.permission, c.edit_review, c.no_ask, c.capped) == ("plan", "required", True, True)


def test_a_deleted_definition_runs_read_only() -> None:
    c = resolve_constraints(_d("acceptEdits"), None, {}, is_builtin=False)
    assert c.permission == "plan" and not c.can_edit


def test_children_inherit_every_restriction() -> None:
    c = resolve_constraints(_d("dontAsk", "capped"), _d("dontAsk", "capped"), {}, is_builtin=False)
    assert inherited_for_children(c) == {"read_only": False, "no_ask": True, "capped": True}
