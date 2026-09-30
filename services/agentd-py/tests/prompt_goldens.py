"""Golden capture of every model-facing string the MAIN agent sees (spec §4.7.4).

Commit 1 of the tagged-template conversion runs `python -m tests.prompt_goldens` on
UNCHANGED code to write tests/goldens/controller_prompt_main.json. From then on
test_prompt_goldens.py asserts the live outputs still equal it byte-for-byte.
Every function here uses only call signatures that exist both before and after the
conversion (new params all default to the main agent), so the same collector runs
unmodified on both sides.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

GOLDEN_PATH = Path(__file__).parent / "goldens" / "controller_prompt_main.json"

# AGENTS.md sample deliberately contains `{ }` (breaks str.format) and a tag marker
# (must render verbatim — injected content is never parsed for tags, §4.7.2).
SAMPLE_INSTRUCTIONS = "Use {braces} freely.\nLiteral marker: <<main>>keep<</main>>\n"
_NOWHERE = Path("/nonexistent-crucible-golden-root")


def _skill_catalog() -> list[object]:
    from agentd.skills.models import SkillManifest
    return [SkillManifest(name="brainstorming", description="Use before creative work.",
                          body_path=_NOWHERE / "SKILL.md", dir=_NOWHERE)]


def _builtin_defs(phase: str) -> list[dict[str, object]]:
    from agentd.tools.registry import ToolRegistry
    return [d.model_dump() for d in ToolRegistry(_NOWHERE, _NOWHERE).definitions(phase)]


def _todo_defs() -> list[dict[str, object]]:
    from agentd.chat.todo_ledger import TodoLedger
    from agentd.chat.todo_source import TodoToolSource
    return [d.model_dump() for d in TodoToolSource(TodoLedger()).definitions()]


_MCP_DEF = {"name": "mcp__gh__create_issue", "description": "Create an issue.",
            "parameters": {"type": "object", "properties": {}}}
_SESSION_DEF = {"name": "start_session", "description": "Start a PTY session.",
                "parameters": {"type": "object", "properties": {}}}


# Each appended block is independent of the others, so all-off, all-on (both tool sets)
# and every flag toggled alone from all-off covers every block and the propose_mode swap
# without the full 32-way product.
_SYSTEM_CASES: dict[str, dict[str, object]] = {
    "all_off": {},
    "all_on/builtin": {"task": True, "memory": True, "instr": True, "skills": True},
    "all_on/extended": {"task": True, "memory": True, "instr": True, "skills": True,
                        "extended": True},
    "task_only": {"task": True},
    "memory_only": {"memory": True},
    "instructions_only": {"instr": True},
    "skills_only": {"skills": True},
    "extended_tools_only": {"extended": True},
}


def _system_prompts(out: dict[str, str]) -> None:
    from agentd.chat.controller_prompts import format_controller_system_prompt
    base = _builtin_defs("explore") + _todo_defs()
    for name, flags in _SYSTEM_CASES.items():
        tools = base + [_MCP_DEF, _SESSION_DEF] if flags.get("extended") else base
        out[f"system/{name}"] = format_controller_system_prompt(
            tools,
            task_subsystem_enabled=bool(flags.get("task")),
            memory_enabled=bool(flags.get("memory")),
            project_instructions=SAMPLE_INSTRUCTIONS if flags.get("instr") else None,
            skills_catalog=_skill_catalog() if flags.get("skills") else None)


def _payloads(out: dict[str, str]) -> None:
    from agentd.chat.controller_prompts import build_controller_step_payload
    tools = _builtin_defs("explore") + [{"name": "query_graph"}]
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "{}"}]
    base = {"workspace_path": "/w", "goal": "g", "max_iters": 10, "retrieval_seed": {"s": 1}}
    cases = {
        "ACTIVE/entry/skills": ("ACTIVE", {**base, "active_entry": True, "skill_check_due": True,
                                           "iteration": 0}, hist, True),
        "ACTIVE/entry/noskills": ("ACTIVE", {**base, "active_entry": True, "iteration": 0},
                                  hist, False),
        "ACTIVE/final": ("ACTIVE", {**base, "iteration": 9}, hist, False),
        "ACTIVE/mid": ("ACTIVE", {**base, "iteration": 3}, hist, False),
        "ACTIVE/mid/reconcile": ("ACTIVE", {
            **base, "iteration": 3, "todo_status": "[ ] a", "pending_reconcile_files": ["a.py"],
            "reconcile_item": {"title": "a", "status": "in_progress"}}, hist, False),
        "PLAN/entry/skills": ("PLAN", {**base, "plan_entry": True, "iteration": 0}, hist, True),
        "PLAN/final": ("PLAN", {**base, "iteration": 9}, hist, False),
        "PLAN/mid/skills": ("PLAN", {**base, "iteration": 3}, hist, True),
    }
    for name, (phase, ctx, h, skills) in cases.items():
        payload = build_controller_step_payload(ctx, h, tools, phase=phase, skills_available=skills)
        out[f"payload/{name}"] = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False)


def _corrections(out: dict[str, str]) -> None:
    from agentd.chat import controller_loop as cl
    from agentd.reasoning.react_common import MALFORMED_CORRECTION
    for tool in ("edit", "answer"):
        out[f"correction/reserved/{tool}"] = cl._reserved_tool_name_correction(
            {"type": "tool_call", "tool": tool}, "tool_call") or ""
    out["correction/progress_repeat"] = cl._progress_repeat_correction(
        {"type": "progress", "note": "x"}, "progress", True) or ""
    out["correction/progress_dedup"] = cl._progress_dedup_correction(
        {"type": "progress", "note": "x"}, "progress", {"x"}) or ""
    out["correction/malformed"] = MALFORMED_CORRECTION


class _NeverAppliedSession:
    """The empty-ops redirect fires before apply(); the loop only closes the session."""

    async def close(self) -> None:
        return None


def _empty_edit_redirect(out: dict[str, str]) -> None:
    """Drive a real loop: an empty-patch_ops edit, then an answer. Read the redirect text
    the loop appended to history (public behavior — identical before/after conversion)."""
    from agentd.chat.controller_loop import ControllerLoop
    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
    from agentd.tools.sources import AggregatingToolRegistry

    steps = [{"type": "edit", "thought": "t", "patch_ops": []},
             {"type": "answer", "thought": "t", "answer": "done"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        AggregatingToolRegistry([]), EventBroadcaster(), channel_id="c",
        phase_sm=ControllerPhaseSM(), edit_session_factory=_NeverAppliedSession)
    outcome = asyncio.run(loop.run({"goal": "g", "workspace_path": "/w"}, max_iters=4))
    redirect = next(m["content"] for m in outcome.history or []
                    if m.get("role") == "tool_result" and m.get("tool") == "edit")
    out["history/empty_edit_redirect"] = str(redirect)


def _tool_texts(out: dict[str, str]) -> None:
    from agentd.chat.edit_session import _validate_patch_ops
    from agentd.mcp.tool_source import McpToolSource

    out["tools/builtin/explore"] = json.dumps(_builtin_defs("explore"), indent=1, sort_keys=True)
    out["tools/builtin/verify"] = json.dumps(_builtin_defs("verify"), indent=1, sort_keys=True)
    out["tools/write_todos"] = json.dumps(_todo_defs(), indent=1, sort_keys=True)

    async def _deny(server: str, tool: str, args: dict[str, object]) -> bool:
        return False

    mcp = McpToolSource(object(), _deny)
    out["history/mcp_rejected"] = asyncio.run(mcp.execute("mcp__gh__create_issue", {})).output
    try:
        _validate_patch_ops([])
    except ValueError as exc:
        out["history/edit_session_empty_ops"] = str(exc)


def collect() -> dict[str, str]:
    out: dict[str, str] = {}
    _system_prompts(out)
    _payloads(out)
    _corrections(out)
    _empty_edit_redirect(out)
    _tool_texts(out)
    return out


def main() -> None:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(collect(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    GOLDEN_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {GOLDEN_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
