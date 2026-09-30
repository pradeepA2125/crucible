# Sub-agents Plan 1A — Prompt Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put every prompt/string the controller shows a model onto one tagged-template mechanism that renders the main agent's text byte-identical to today while producing correct child (sub-agent) text, and add the `AGENT` phase, `report` action, allowed-types schema, AGENT payload branch and the `ReasoningEngine` seams that Phase 2's child runtime will call.

**Architecture:** A pure resolver (`agentd/prompting/tagged.py`) renders `<<tag>>…<</tag>>` regions against a hashable `RenderContext`. Goldens of every main-agent string are captured on unchanged code first; the conversion must keep them byte-identical. Child text lives in `child`/`perm:*`/`shell:*`/`type:report` regions that never render for main. No child agent runs yet — Phase 2 builds the runtime on these seams.

**Tech Stack:** Python 3.13, pytest + pytest-asyncio, the existing `agentd` package (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-09-29-subagents-design.md` (rev 10, approved). This plan implements spec §16 phase 1's **prompt half**: §4.1–§4.4, §4.6 (prompt surface), §4.7. The gates/approvals half of phase 1 (§4.5 multi-gate, `ApprovalOutcome`, `StaleWriteError`, `TurnEditSession` fixes) is **Plan 1B**, written after this plan lands.

## Global Constraints

- **Main-agent byte identity:** for the parent (`RenderContext.main()`), every system prompt, payload, correction string, tool description and history string is byte-identical to `tests/goldens/controller_prompt_main.json` as captured in Task 1 — the only deliberate exceptions are the two fixes in Task 11 (spec §4.7.4 step 3).
- **Tag vocabulary is closed** (spec §4.7.1): `main`, `child`, `child:edit`, `child:readonly`, `perm:{default|acceptEdits|dontAsk|plan}`, `shell:{ask|allow_all}`, `tool:<name>`, `type:{tool_call|answer|clarify|propose_mode|edit|submit_changes|progress|report}`. No `else`, no negation, no expressions.
- **Main is never gated by capability tags:** `tool:*`/`type:*` always render for main (except `type:report`); `child*`/`perm:*`/`shell:*` never do.
- **`tool:`/`type:` tags only on text that is unconditional for main today** — presence-based appends (`_MEMORY_BLOCK` iff memory, `_MCP_BLOCK` iff `mcp__` tools, `_SESSIONS_BLOCK` iff `start_session`, skills/instructions iff present) stay code-level.
- **Tags resolve before placeholder substitution** (`{tools_json}`, `{propose_mode_modes}`, `{instructions}`, `{persona}`, `{label}`, `{tool}`, …); injected content is never parsed for tags.
- **`<<` appears in templates only as a tag** — the validator rejects anything else at import time.
- **Test hygiene (repo rules):** never pass `-q` to pytest (pyproject already sets it); never pipe pytest (it masks the exit code) — redirect to a file and check `$?`; use `asyncio.run` / `@pytest.mark.asyncio`, never `get_event_loop().run_until_complete`.
- **Pre-existing failure:** `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies` fails on `main` before this work (verified 2026-09-30). It is not yours; don't fix it here, and don't let it hide new failures — compare failure sets.
- Run every command from `services/agentd-py` with `.venv/bin/python`.

## Review Focus

1. **Injected text containing tag syntax** — an AGENTS.md, persona body, or skill body that contains `<<main>>` or `{braces}` must render verbatim for both main and child (never parsed, never `str.format`-ed). Pinned in Task 1 (golden sample) and Task 4.
2. **A future edit to shared prompt text** that mentions `submit_changes` / Plan Mode / the retrieval seed outside a `main` region must fail CI, not silently leak to children. Pinned in Task 10 (structural lint).
3. **Strict-grammar providers and an empty report** — the AGENT tight schema must require a non-empty-by-schema `summary` on `report`, and the flat path must correct an empty one. Pinned in Task 7.
4. **Test/third-party engines with fixed `create_controller_step` signatures** must keep working when the loop starts passing `allowed_types`/`render_ctx`. Pinned in Task 9.
5. **Main's ACTIVE + task-subsystem `propose_mode`** must now actually appear in the response schema (latent bug, spec §4.1) without any other schema change for main. Pinned in Task 9.

---

## File Structure

| File | Responsibility |
|---|---|
| `agentd/prompting/__init__.py` (create) | package marker |
| `agentd/prompting/tagged.py` (create) | `RenderContext`, `render_prompt`, `validate_template`, `tagged`, `PromptTemplateError` — pure, no agentd imports |
| `tests/prompt_goldens.py` (create) | collector of every main-agent string; `python -m tests.prompt_goldens` writes the golden |
| `tests/goldens/controller_prompt_main.json` (create, generated) | the byte-identity golden |
| `agentd/chat/controller_prompts.py` (modify) | tagged constants; `_AGENT_ROLE_BLOCK`; `format_controller_system_prompt(render_ctx, persona)`; `AGENT` phase + `report` variant; `controller_response_schema(allowed_types)`; AGENT payload branch |
| `agentd/chat/controller_phase.py` (modify) | `start="AGENT"` |
| `agentd/chat/controller_loop.py` (modify) | tagged correction strings; `render_ctx` param; passes `allowed_types`/`render_ctx` to the engine when accepted; `report` empty-summary correction; `report` reserved for children |
| `agentd/reasoning/react_common.py` (modify) | tagged `MALFORMED` + `malformed_correction()`; `accepts_kwarg()` |
| `agentd/reasoning/contracts.py`, `agentd/reasoning/engine.py`, `agentd/orchestrator/scripted_engine.py` (modify) | `allowed_types`/`render_ctx`/`persona` on `create_controller_step`; `with_model`; scripted per-agent scripts |
| `agentd/tools/registry.py`, `agentd/tools/sources.py`, `agentd/chat/todo_source.py`, `agentd/mcp/tool_source.py` (modify) | tagged tool descriptions / MCP rejection text; `render_ctx` at construction (default main) |
| `agentd/chat/edit_session.py` (modify, Task 11 only) | neutral empty-ops message |

---

### Task 1: Capture main-agent goldens on unchanged code

This is spec §4.7.4 **commit 1**. It must run against the code exactly as it is on `feat/subagents` at `58898e1` — do not touch any `agentd/` file in this task.

**Files:**
- Create: `tests/prompt_goldens.py`
- Create (generated): `tests/goldens/controller_prompt_main.json`
- Create: `tests/test_prompt_goldens.py`

**Interfaces:**
- Produces: `tests.prompt_goldens.collect() -> dict[str, str]`, `tests.prompt_goldens.GOLDEN_PATH`, `tests.prompt_goldens.SAMPLE_INSTRUCTIONS`. Every later task re-runs `tests/test_prompt_goldens.py`; only Task 11 may re-capture.

- [ ] **Step 1: Write the collector**

Create `tests/prompt_goldens.py` with exactly this content (it only uses call signatures that exist both before and after the conversion; every parameter later tasks add defaults to the main agent):

```python
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
```

- [ ] **Step 2: Write the golden test**

Create `tests/test_prompt_goldens.py`:

```python
"""Main-agent byte identity (spec §4.7.4). Only Plan 1A Task 11 may re-capture."""
import json

from tests.prompt_goldens import GOLDEN_PATH, collect


def test_main_agent_text_matches_goldens() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    live = collect()
    assert sorted(live) == sorted(golden), "golden keys changed — re-capture is Task 11 only"
    mismatched = [key for key in golden if live[key] != golden[key]]
    assert not mismatched, f"main-agent text changed for: {mismatched}"
```

- [ ] **Step 3: Run the test to verify it fails (no golden yet)**

Run: `.venv/bin/python -m pytest tests/test_prompt_goldens.py > /tmp/t1.txt 2>&1; echo exit=$?; tail -5 /tmp/t1.txt`
Expected: `exit=1`, `FileNotFoundError` for `tests/goldens/controller_prompt_main.json`.

- [ ] **Step 4: Capture the golden on unchanged code**

Run: `git status --short agentd/` — Expected: no output (nothing under `agentd/` modified).
Run: `.venv/bin/python -m tests.prompt_goldens`
Expected: `wrote …/tests/goldens/controller_prompt_main.json`. Then:
Run: `.venv/bin/python -c "import json; d=json.load(open('tests/goldens/controller_prompt_main.json')); print(len(d))"` — Expected: `27`.

- [ ] **Step 5: Run the test to verify it passes, twice (determinism)**

Run: `.venv/bin/python -m pytest tests/test_prompt_goldens.py > /tmp/t1.txt 2>&1; echo exit=$?; .venv/bin/python -m pytest tests/test_prompt_goldens.py > /tmp/t1b.txt 2>&1; echo exit=$?`
Expected: `exit=0` both times.

- [ ] **Step 6: Commit**

```bash
git add tests/prompt_goldens.py tests/goldens/controller_prompt_main.json tests/test_prompt_goldens.py
git commit -m "test(prompts): capture main-agent prompt goldens before tagging"
```

---

### Task 2: The tagged-template resolver

**Files:**
- Create: `agentd/prompting/__init__.py` (empty)
- Create: `agentd/prompting/tagged.py`
- Test: `tests/test_tagged_prompt.py`

**Interfaces:**
- Produces (every later task imports these from `agentd.prompting.tagged`):
  - `class RenderContext` — frozen, hashable dataclass: `audience: Literal["main","child"]="main"`, `permission: Literal["default","acceptEdits","dontAsk","plan"]="default"`, `shell_policy: Literal["ask","allow_all"]="ask"`, `tools: frozenset[str]`, `base_types: frozenset[str]`, `agent_id: str=""`, `agent_label: str=""`; `RenderContext.main() -> RenderContext`; property `is_main: bool`.
  - `render_prompt(template: str, ctx: RenderContext) -> str` (lru-cached)
  - `validate_template(text: str, name: str = "<template>") -> None`
  - `tagged(name: str, text: str) -> str` (validates at import, returns `text`)
  - `class PromptTemplateError(ValueError)`
- Deviation from spec §4.7.2 (noted for reviewers): `RenderContext` carries the resolved fields (`audience`, `permission`, `shell_policy`, `tools`, `base_types`, `agent_id`, `agent_label`) instead of holding an `AgentContext`, so `agentd.prompting` has no dependency on `agentd.subagents` (Phase 2 builds a `RenderContext` from its `AgentContext`). Because it is hashable, the render cache keys on `(template, ctx)`, which the spec review explicitly allowed.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tagged_prompt.py`:

```python
import pytest

from agentd.prompting.tagged import (
    PromptTemplateError,
    RenderContext,
    render_prompt,
    tagged,
    validate_template,
)

MAIN = RenderContext.main()
CHILD = RenderContext(
    audience="child", permission="default", tools=frozenset({"write_todos"}),
    base_types=frozenset({"tool_call", "edit", "progress", "report"}),
    agent_id="a1", agent_label="impl")
READONLY = RenderContext(
    audience="child", permission="plan",
    base_types=frozenset({"tool_call", "progress", "report"}),
    agent_id="a2", agent_label="survey")


def test_block_regions_are_removed_with_their_lines() -> None:
    t = "a\n<<main>>\nm\n<</main>>\n<<child>>\nc\n<</child>>\nz\n"
    assert render_prompt(t, MAIN) == "a\nm\nz\n"
    assert render_prompt(t, CHILD) == "a\nc\nz\n"


def test_inline_regions_leave_surrounding_text_untouched() -> None:
    t = "You are <<main>>main<</main>><<child>>child<</child>>.\n"
    assert render_prompt(t, MAIN) == "You are main.\n"
    assert render_prompt(t, CHILD) == "You are child.\n"


def test_marker_on_last_line_without_newline_eats_the_preceding_newline() -> None:
    # _MEMORY_BLOCK / _INSTRUCTIONS_BLOCK_TEMPLATE end without a trailing newline (spec §4.7.2).
    t = "head\n<<tool:remember>>\n- remember it.\n<</tool:remember>>"
    assert render_prompt(t, MAIN) == "head\n- remember it."
    assert render_prompt(t, CHILD) == "head"


def test_a_region_renders_only_if_every_enclosing_region_does() -> None:
    t = ("<<perm:dontAsk>>\n<<shell:ask>>\nask\n<</shell:ask>>\n"
         "<<shell:allow_all>>\nall\n<</shell:allow_all>>\n<</perm:dontAsk>>\n")
    dont_ask = RenderContext(audience="child", permission="dontAsk", shell_policy="allow_all")
    assert render_prompt(t, dont_ask) == "all\n"
    assert render_prompt(t, MAIN) == ""
    assert render_prompt(t, CHILD) == ""


def test_main_is_never_gated_by_capability_tags() -> None:
    t = ("<<tool:nope>>\nt\n<</tool:nope>>\n<<type:clarify>>\nc\n<</type:clarify>>\n"
         "<<type:report>>\nr\n<</type:report>>\n")
    assert render_prompt(t, MAIN) == "t\nc\n"
    assert render_prompt(t, CHILD) == "r\n"


def test_child_edit_and_child_readonly_follow_effective_permission() -> None:
    t = "<<child:edit>>\ne\n<</child:edit>>\n<<child:readonly>>\nro\n<</child:readonly>>\n"
    assert render_prompt(t, CHILD) == "e\n"
    assert render_prompt(t, READONLY) == "ro\n"
    assert render_prompt(t, MAIN) == ""


def test_perm_tags_never_render_for_main() -> None:
    t = "<<perm:default>>x<</perm:default>>y"
    assert render_prompt(t, MAIN) == "y"
    assert render_prompt(t, CHILD) == "xy"


def test_render_is_cached_per_template_and_context() -> None:
    t = "<<perm:default>>x<</perm:default>>y"
    assert render_prompt(t, CHILD) is render_prompt(t, CHILD)


def test_tagged_returns_the_text_unchanged_when_valid() -> None:
    text = "a <<main>>b<</main>> c\n"
    assert tagged("ok", text) is text


@pytest.mark.parametrize(("text", "message"), [
    ("<<main>>\nx\n", "never closed"),
    ("<</main>>\n", "does not close"),
    ("<<main>>\n<<child>>\n<</main>>\n<</child>>\n", "does not close"),
    ("<<bogus>>\nx\n<</bogus>>\n", "unknown tag"),
    ("<<perm:root>>\nx\n<</perm:root>>\n", "unknown tag"),
    ("<<type:banana>>\n<</type:banana>>\n", "unknown tag"),
    ("a <<main>>b\nc<</main>> d\n", "spans a newline"),
    ("<<main>>\nx <</main>> y\n", "mixes block and inline"),
    ("<<main>>\nx\n<</main>><<child>>\ny\n<</child>>\n", "more than one marker"),
    ("a << b\n", "stray"),
    ("cat <<EOF\n", "stray"),
])
def test_validator_rejects_malformed_templates(text: str, message: str) -> None:
    with pytest.raises(PromptTemplateError, match=message):
        validate_template(text, "t")
    with pytest.raises(PromptTemplateError, match=message):
        tagged("t", text)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tagged_prompt.py > /tmp/t2.txt 2>&1; echo exit=$?; tail -3 /tmp/t2.txt`
Expected: `exit=2` (collection error: `ModuleNotFoundError: No module named 'agentd.prompting'`).

- [ ] **Step 3: Implement the resolver**

Create `agentd/prompting/__init__.py` as an empty file. Create `agentd/prompting/tagged.py`:

```python
"""Tagged prompt templates: one source text, audience-tagged regions, one pure resolver.

Spec: docs/superpowers/specs/2026-09-29-subagents-design.md §4.7.

Syntax: ``<<tag>>`` opens a region, ``<</tag>>`` closes it; regions nest. A region renders
iff its own tag holds for the RenderContext AND every enclosing region renders. Untagged
text renders for everyone.

Whitespace (the only rules):
  * a marker alone on its line (spaces/tabs allowed around it) is removed together with
    that line's newline;
  * a marker-only LAST line with no newline of its own is removed together with the newline
    immediately before it in the output;
  * an inline marker is removed with no other change; dropped content is removed verbatim.
No trimming or normalization anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

Audience = Literal["main", "child"]
Permission = Literal["default", "acceptEdits", "dontAsk", "plan"]
ShellPolicy = Literal["ask", "allow_all"]

_PERMISSIONS = frozenset({"default", "acceptEdits", "dontAsk", "plan"})
_SHELL_POLICIES = frozenset({"ask", "allow_all"})
_VARIANTS = frozenset({
    "tool_call", "answer", "clarify", "propose_mode", "edit", "submit_changes", "progress",
    "report",
})
_CHILD_SUBTAGS = frozenset({"edit", "readonly"})

_MARKER_RE = re.compile(r"<<(/?)([a-z_]+)(?::([A-Za-z0-9_]+))?>>")


class PromptTemplateError(ValueError):
    """A tagged template is malformed. Raised at import time via ``tagged()``."""


@dataclass(frozen=True)
class RenderContext:
    """Everything a tag can test. Hashable so renders are cacheable.

    ``agent_id``/``agent_label`` identify a sub-agent (render cache + per-agent scripts);
    both are empty for the main agent.
    """

    audience: Audience = "main"
    permission: Permission = "default"
    shell_policy: ShellPolicy = "ask"
    tools: frozenset[str] = field(default_factory=frozenset)
    base_types: frozenset[str] = field(default_factory=frozenset)
    agent_id: str = ""
    agent_label: str = ""

    @classmethod
    def main(cls) -> RenderContext:
        return _MAIN

    @property
    def is_main(self) -> bool:
        return self.audience == "main"


_MAIN = RenderContext()


def _holds(kind: str, arg: str | None, ctx: RenderContext) -> bool:
    if kind == "main":
        return ctx.is_main
    if kind == "child":
        if arg is None:
            return not ctx.is_main
        if arg == "edit":
            return not ctx.is_main and ctx.permission != "plan"
        return not ctx.is_main and ctx.permission == "plan"  # "readonly"
    if kind == "perm":
        return not ctx.is_main and ctx.permission == arg
    if kind == "shell":
        return not ctx.is_main and ctx.shell_policy == arg
    if kind == "tool":
        # Main is never gated by capability tags (§4.7.1): its prompt today is
        # unconditional in every tagged region.
        return ctx.is_main or arg in ctx.tools
    if kind == "type":
        if ctx.is_main:
            return arg != "report"
        return arg in ctx.base_types
    raise AssertionError(f"unvalidated tag {kind!r}")  # pragma: no cover


def _check_tag(kind: str, arg: str | None, where: str) -> None:
    ok = (
        (kind == "main" and arg is None)
        or (kind == "child" and (arg is None or arg in _CHILD_SUBTAGS))
        or (kind == "perm" and arg in _PERMISSIONS)
        or (kind == "shell" and arg in _SHELL_POLICIES)
        or (kind == "tool" and arg is not None)
        or (kind == "type" and arg in _VARIANTS)
    )
    if not ok:
        tag = kind if arg is None else f"{kind}:{arg}"
        raise PromptTemplateError(f"{where}: unknown tag <<{tag}>>")


def _tag_key(kind: str, arg: str | None) -> str:
    return kind if arg is None else f"{kind}:{arg}"


def validate_template(text: str, name: str = "<template>") -> None:
    """Raise PromptTemplateError for any structural problem (§4.7.3)."""
    stack: list[tuple[str, bool, int]] = []  # (tag, is_block, line_no)
    lines = text.split("\n")
    for line_no, line in enumerate(lines, start=1):
        where = f"{name}:{line_no}"
        markers = list(_MARKER_RE.finditer(line))
        # every "<<" must start a well-formed marker
        starts = {m.start() for m in markers}
        for idx in (i for i in range(len(line)) if line.startswith("<<", i)):
            if idx not in starts and not any(m.start() < idx < m.end() for m in markers):
                raise PromptTemplateError(f"{where}: stray '<<' that is not a valid tag")
        if not markers:
            continue
        residue = _MARKER_RE.sub("", line).strip(" \t")
        is_block_line = residue == ""
        if is_block_line and len(markers) > 1:
            raise PromptTemplateError(f"{where}: more than one marker on a marker-only line")
        for m in markers:
            closing, kind, arg = m.group(1) == "/", m.group(2), m.group(3)
            _check_tag(kind, arg, where)
            key = _tag_key(kind, arg)
            if not closing:
                stack.append((key, is_block_line, line_no))
                continue
            if not stack or stack[-1][0] != key:
                raise PromptTemplateError(f"{where}: <</{key}>> does not close the open region")
            open_key, open_block, open_line = stack.pop()
            if open_block != is_block_line:
                raise PromptTemplateError(
                    f"{where}: region <<{open_key}>> opened on line {open_line} mixes block and "
                    "inline markers")
            if not is_block_line and open_line != line_no:
                raise PromptTemplateError(
                    f"{where}: inline region <<{open_key}>> spans a newline")
    if stack:
        key, _, line_no = stack[-1]
        raise PromptTemplateError(f"{name}:{line_no}: <<{key}>> is never closed")


def tagged(name: str, text: str) -> str:
    """Validate a template at import time and return it unchanged."""
    validate_template(text, name)
    return text


@lru_cache(maxsize=512)
def render_prompt(template: str, ctx: RenderContext) -> str:
    """Render ``template`` for ``ctx``. Pure; cached per (template, ctx)."""
    out: list[str] = []
    active_stack: list[bool] = []

    def active() -> bool:
        return all(active_stack)

    lines = template.split("\n")
    last = len(lines) - 1
    for i, line in enumerate(lines):
        has_newline = i < last
        markers = list(_MARKER_RE.finditer(line))
        residue = _MARKER_RE.sub("", line).strip(" \t") if markers else None
        if markers and residue == "":
            m = markers[0]
            if m.group(1) == "/":
                active_stack.pop()
            else:
                active_stack.append(_holds(m.group(2), m.group(3), ctx))
            if not has_newline and out and out[-1].endswith("\n"):
                out[-1] = out[-1][:-1]
            continue
        if not active():
            continue
        if markers:
            pieces: list[str] = []
            inline_stack: list[bool] = []
            pos = 0
            for m in markers:
                if all(inline_stack):
                    pieces.append(line[pos:m.start()])
                if m.group(1) == "/":
                    inline_stack.pop()
                else:
                    inline_stack.append(_holds(m.group(2), m.group(3), ctx))
                pos = m.end()
            pieces.append(line[pos:])
            line = "".join(pieces)
        out.append(line + ("\n" if has_newline else ""))
    return "".join(out)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tagged_prompt.py tests/test_prompt_goldens.py > /tmp/t2.txt 2>&1; echo exit=$?; tail -3 /tmp/t2.txt`
Expected: `exit=0`, `21 passed`.

- [ ] **Step 5: Commit**

```bash
git add agentd/prompting/__init__.py agentd/prompting/tagged.py tests/test_tagged_prompt.py
git commit -m "feat(prompting): add tagged-template resolver and validator"
```

---

### Task 3: Convert the controller prompt constants (main stays byte-identical)

Spec §4.7.4 **commit 2**. After this task every constant is a tagged template with its child regions in place, and `format_controller_system_prompt` renders them for the main agent. Nothing renders for a child yet (Task 4).

**Files:**
- Modify: `agentd/chat/controller_prompts.py` (via the one-shot script below)
- Test: `tests/test_controller_prompts_tagged.py`

**Interfaces:**
- Consumes: `RenderContext`, `render_prompt`, `tagged` (Task 2).
- Produces: `controller_prompts.CONTROLLER_SYSTEM_PROMPT`, `_PROPOSE_MODE_MODES_ENABLED`, `_PROPOSE_MODE_MODES_DISABLED`, `_MEMORY_BLOCK`, `_INSTRUCTIONS_BLOCK_TEMPLATE`, `_MCP_BLOCK`, `_SESSIONS_BLOCK`, `_SKILLS_BLOCK_HEADER` are now **tagged templates** (raw text contains markers). Anything that wants the prompt text must call `render_prompt(CONST, ctx)`.

- [ ] **Step 1: Write the failing property test**

Create `tests/test_controller_prompts_tagged.py`:

```python
"""Tagged controller prompt constants (spec §4.7.4 property test + §4.7.2 injection rule)."""
import re

import pytest

from agentd.chat import controller_prompts as cp
from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.prompting.tagged import RenderContext, render_prompt

TAGGED_CONSTANTS = [
    "CONTROLLER_SYSTEM_PROMPT", "_PROPOSE_MODE_MODES_ENABLED", "_PROPOSE_MODE_MODES_DISABLED",
    "_MEMORY_BLOCK", "_INSTRUCTIONS_BLOCK_TEMPLATE", "_MCP_BLOCK", "_SESSIONS_BLOCK",
    "_SKILLS_BLOCK_HEADER",
]
_NON_MAIN = r"child(?::\w+)?|perm:\w+|shell:\w+|type:report"
_MARK = r"<</?[a-z_]+(?::\w+)?>>"


def _expected_main(template: str) -> str:
    """Independent of the resolver: delete regions that never render for main, then strip
    the remaining markers by the §4.7.2 whitespace rules."""
    t = re.sub(rf"^[ \t]*<<({_NON_MAIN})>>[ \t]*\n.*?^[ \t]*<</\1>>[ \t]*(?:\n|\Z)", "", template,
               flags=re.M | re.S)
    t = re.sub(rf"<<({_NON_MAIN})>>.*?<</\1>>", "", t)
    t = re.sub(rf"^[ \t]*{_MARK}[ \t]*\n", "", t, flags=re.M)
    t = re.sub(rf"\n[ \t]*{_MARK}[ \t]*\Z", "", t)
    return re.sub(_MARK, "", t)


@pytest.mark.parametrize("name", TAGGED_CONSTANTS)
def test_constant_is_tagged(name: str) -> None:
    assert "<<" in getattr(cp, name) or name == "_SESSIONS_BLOCK"


@pytest.mark.parametrize("name", TAGGED_CONSTANTS)
def test_main_render_equals_template_minus_non_main_regions(name: str) -> None:
    template = getattr(cp, name)
    assert render_prompt(template, RenderContext.main()) == _expected_main(template)


def test_injected_instructions_are_never_parsed_for_tags() -> None:
    out = format_controller_system_prompt(
        [], task_subsystem_enabled=False, memory_enabled=False,
        project_instructions="Keep <<main>>this<</main>> literal {x}")
    assert "Keep <<main>>this<</main>> literal {x}" in out
    assert "<<child>>" not in out and "<</main>>\n" not in out.split("Keep")[0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_controller_prompts_tagged.py > /tmp/t3.txt 2>&1; echo exit=$?; tail -3 /tmp/t3.txt`
Expected: `exit=1` — `test_constant_is_tagged` fails for every constant except `_SESSIONS_BLOCK` (no markers yet).

- [ ] **Step 3: Run the one-shot conversion**

Save this as `.superpowers/tag_controller_prompts.py` (repo-root `.superpowers/` is gitignored — the script is never committed) and run it from `services/agentd-py`: `.venv/bin/python ../../.superpowers/tag_controller_prompts.py`. Every edit asserts it matches exactly once, so drifted source fails loudly instead of half-converting. Expected output: `converted agentd/chat/controller_prompts.py`.

```python
"""One-shot conversion of agentd/chat/controller_prompts.py to tagged templates.

Plan 1A, Task 3. Run from services/agentd-py:  .venv/bin/python <this file>
Every edit asserts it matches exactly once, so a drifted source fails loudly instead of
half-converting. Not committed; tests/test_prompt_goldens.py proves the result.
"""
import pathlib

PATH = pathlib.Path("agentd/chat/controller_prompts.py")
src = PATH.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global src
    n = src.count(old)
    assert n == 1, (n, old[:80])
    src = src.replace(old, new)


# ---------------- CONTROLLER_SYSTEM_PROMPT ----------------
sub("You are an agentic coding assistant in a chat turn. You own this turn's loop.\n",
    "You are <<main>>an agentic coding assistant in a chat turn. You own this turn's loop.<</main>>"
    "<<child>>a sub-agent carrying out one task for another agent (your dispatcher). You own this "
    "task's loop.<</child>>\n")
sub('non-empty. A bare object like {"type":"answer"} or',
    'non-empty. A bare object like {"type":"<<main>>answer<</main>><<child>>report<</child>>"} or')
sub("""⚠ GROUND BEFORE YOU COMMIT — this is the difference between a correct turn and a confident wrong one:
Your retrieval seed""",
    """⚠ GROUND BEFORE YOU COMMIT — this is the difference between a correct turn and a confident wrong one:
<<main>>
Your retrieval seed""")
sub("""name, a wrong endpoint, a function that doesn't exist). The fix is cheap: READ the specific code
first.
""",
    """name, a wrong endpoint, a function that doesn't exist). The fix is cheap: READ the specific code
first.
<</main>>
<<child>>
Your task text tells you what to look at; it does NOT contain the code. If you claim anything
code-specific without reading it, you WILL confabulate the parts you haven't seen (a wrong class
name, a wrong endpoint, a function that doesn't exist). The fix is cheap: READ the specific code
first.
<</child>>
""")
sub("Cite only files/symbols you have READ this turn (or that appear verbatim in the seed excerpts);",
    "Cite only files/symbols you have READ this turn<<main>> (or that appear verbatim in the seed "
    "excerpts)<</main>>;")
sub("""  • A purely conversational message you can fully answer without the repo (a greeting, a question
    about your own capabilities) may be answered directly — tools are not mandatory for those.
""",
    """<<main>>
  • A purely conversational message you can fully answer without the repo (a greeting, a question
    about your own capabilities) may be answered directly — tools are not mandatory for those.
<</main>>
""")
sub("  • Stop exploring once further reads would not change your answer:",
    "  • Stop exploring once further reads would not change your <<main>>answer<</main>><<child>>report<</child>>:")
sub("""WHEN THE REQUEST NEEDS A CHANGE — editing is the default way to act; no permission step is
required. First ground yourself (search/read the EXISTING code you'll touch; a brand-new
isolated file may need none), then emit type="edit" actions directly, then
type="submit_changes" when done. (Plan Mode, a separate opt-in the user controls, is the
ONLY context where you propose a plan instead of editing directly — see the propose_mode
variant below, which does not apply outside Plan Mode.)
""",
    """<<main>>
WHEN THE REQUEST NEEDS A CHANGE — editing is the default way to act; no permission step is
required. First ground yourself (search/read the EXISTING code you'll touch; a brand-new
isolated file may need none), then emit type="edit" actions directly, then
type="submit_changes" when done. (Plan Mode, a separate opt-in the user controls, is the
ONLY context where you propose a plan instead of editing directly — see the propose_mode
variant below, which does not apply outside Plan Mode.)
<</main>>
<<child:edit>>
WHEN YOUR TASK NEEDS A CHANGE — first ground yourself (search/read the EXISTING code you'll
touch; a brand-new isolated file may need none), then emit type="edit" actions, then
type="report" when done.
<</child:edit>>
<<child:readonly>>
YOU ARE READ-ONLY for this task: investigate with the read tools and put everything you find in
type="report". You cannot edit files or run commands.
<</child:readonly>>
""")
sub("""  run_command and every other tool are directly available by default — no permission step
  required. The ONLY exception is Plan Mode (a separate opt-in the user controls): there, use
  ONLY read-only tools (search_code / read_file / list_directory / read_env_profile /
  search_semantic) and emit propose_mode instead of run_command to make a change.
""",
    """<<main>>
  run_command and every other tool are directly available by default — no permission step
  required. The ONLY exception is Plan Mode (a separate opt-in the user controls): there, use
  ONLY read-only tools (search_code / read_file / list_directory / read_env_profile /
  search_semantic) and emit propose_mode instead of run_command to make a change.
<</main>>
<<perm:default>>
  A command may pause for a human approval card; if it is rejected, adapt your approach.
<</perm:default>>
<<perm:acceptEdits>>
  A command may pause for a human approval card; if it is rejected, adapt your approach.
<</perm:acceptEdits>>
<<perm:dontAsk>>
<<shell:ask>>
  A command runs only if a remembered rule allows it; otherwise it is refused and nobody is asked.
<</shell:ask>>
<<shell:allow_all>>
  Commands run without approval.
<</shell:allow_all>>
<</perm:dontAsk>>
""")
sub("  response types (answer/clarify/propose_mode/edit/submit_changes/progress). Those are\n"
    "  never callable tools, even though write_todos (a real tool) is also invoked via tool_call:\n"
    '  {"type":"tool_call","tool":"edit","args":{"patch_ops":[...]}}  ← INVALID, "edit" is not a tool.\n'
    '  To write a file, emit a top-level {"type":"edit","patch_ops":[...]} object instead (see below).\n',
    "  response types (<<main>>answer/clarify/propose_mode/edit/submit_changes/progress<</main>>"
    "<<child:edit>>edit/progress/report<</child:edit>><<child:readonly>>progress/report<</child:readonly>>). Those are\n"
    "  never callable tools<<type:edit>>, even though write_todos (a real tool) is also invoked via tool_call:<</type:edit>>"
    "<<child:readonly>>.<</child:readonly>>\n"
    "<<type:edit>>\n"
    '  {"type":"tool_call","tool":"edit","args":{"patch_ops":[...]}}  ← INVALID, "edit" is not a tool.\n'
    '  To write a file, emit a top-level {"type":"edit","patch_ops":[...]} object instead (see below).\n'
    "<</type:edit>>\n")
# answer variant (266-280) + its trailing blank line
sub("""
Variant — answer (respond in text): {type, answer}""", """
<<type:answer>>
Variant — answer (respond in text): {type, answer}""")
sub("""  RIGHT: {"type":"tool_call","thought":"ground in the design spec before planning","tool":"read_file","args":{"path":"docs/superpowers/specs/....md"}}

""", """  RIGHT: {"type":"tool_call","thought":"ground in the design spec before planning","tool":"read_file","args":{"path":"docs/superpowers/specs/....md"}}

<</type:answer>>
""")
# progress variant: two main-only paragraphs with child alternatives
sub("""  Use when you want to tell the user what you are doing or about to do while you keep working
  in the SAME turn — e.g. between finishing one file and starting the next in a multi-step change.
  The 'note' is shown to the user immediately; the turn does NOT end and you run again right after.
""", """<<main>>
  Use when you want to tell the user what you are doing or about to do while you keep working
  in the SAME turn — e.g. between finishing one file and starting the next in a multi-step change.
  The 'note' is shown to the user immediately; the turn does NOT end and you run again right after.
<</main>>
<<child>>
  Use for a short status line while you keep working. It is shown to a human watching, but it
  does NOT reach your dispatcher — only 'report' does, so put every finding in the report.
  Posting a note does not end your task; you run again right after.
<</child>>
""")
sub("""  progress is for a status update mid-work; 'answer' is for the finished, self-contained reply that
  ENDS the turn. If you have more to do this turn, use progress and then take the next action; do not
  use 'answer' to describe a step you have not taken yet.
""", """<<main>>
  progress is for a status update mid-work; 'answer' is for the finished, self-contained reply that
  ENDS the turn. If you have more to do this turn, use progress and then take the next action; do not
  use 'answer' to describe a step you have not taken yet.
<</main>>
<<child>>
  progress is for a status update mid-work; 'report' is the finished result that ENDS your task.
  If you have more to do, post progress and then take the next action.
<</child>>
""")
# clarify variant + trailing blank
sub("""
Variant — clarify (you genuinely cannot proceed)""", """
<<type:clarify>>
Variant — clarify (you genuinely cannot proceed)""")
sub("""{"type":"clarify","thought":"ambiguous target","question":"Which pricing module?","options":["src/pricing.py","billing/pricing.py"]}

""", """{"type":"clarify","thought":"ambiguous target","question":"Which pricing module?","options":["src/pricing.py","billing/pricing.py"]}

<</type:clarify>>
""")
# propose_mode variant + trailing blank
sub("""Variant — propose_mode (Plan Mode only""", """<<type:propose_mode>>
Variant — propose_mode (Plan Mode only""")
sub("""{propose_mode_modes}

""", """{propose_mode_modes}

<</type:propose_mode>>
""")
# edit variant 308-341 (incl. trailing blank) with nested write_todos part
sub("""Variant — edit (make a change directly — this is the default way to act on any request that needs one, no permission step required): {type, patch_ops}""",
    """<<type:edit>>
Variant — edit (<<main>>make a change directly — this is the default way to act on any request that needs one, no permission step required<</main>><<perm:default>>make a change; an edit may pause for a human review card — if it is rejected, revise<</perm:default>><<perm:acceptEdits>>make a change; edits apply immediately<</perm:acceptEdits>><<perm:dontAsk>>make a change; edits apply immediately<</perm:dontAsk>>): {type, patch_ops}""")
sub("""return str(x)\\\\n","reason":"new helper"},{"op":"search_replace","file":"src/app.py","search":"import os","replace":"import os\\\\nfrom src.util import fmt","reason":"wire in helper"}]}

  Batching ops""", """return str(x)\\\\n","reason":"new helper"},{"op":"search_replace","file":"src/app.py","search":"import os","replace":"import os\\\\nfrom src.util import fmt","reason":"wire in helper"}]}
<<tool:write_todos>>

  Batching ops""")
sub("  STOP — sequencing rule: for a multi-part change your FIRST action MUST be write_todos, NOT edit.",
    "  STOP — sequencing rule: for a multi-part change <<main>>your FIRST action MUST be write_todos, "
    "NOT edit.<</main>><<child>>call write_todos BEFORE your first edit.<</child>>")
sub("  edit first does NOT finish faster — submit_changes stays BLOCKED until the list is clear, and you",
    "  edit first does NOT finish faster — <<main>>submit_changes<</main>><<child>>report<</child>> "
    "stays BLOCKED until the list is clear, and you")
sub("""  then emitting edit anyway is the exact mistake to avoid: act on it — call write_todos.

""", """  then emitting edit anyway is the exact mistake to avoid: act on it — call write_todos.
<</tool:write_todos>>

<</type:edit>>
""")
# submit_changes variant + trailing blank, then the child-only report variant
sub("""Variant — submit_changes (once all edits""", """<<type:submit_changes>>
Variant — submit_changes (once all edits""")
sub("""{"type":"submit_changes","thought":"done","summary":"Added with_tax() to src/tax.py and rounded the total in pricing.py."}

""", """{"type":"submit_changes","thought":"done","summary":"Added with_tax() to src/tax.py and rounded the total in pricing.py."}

<</type:submit_changes>>
<<type:report>>
Variant — report (END your task — the ONLY thing your dispatcher receives): {type, summary}
  "summary" is your complete report. It is never truncated, and nothing else you did (progress
  notes, files you wrote) reaches your dispatcher, so make it complete. BEFORE emitting this: if
  you edited code, re-run the relevant lint/tests ONE MORE TIME now — a result from before your
  latest edits is stale. Structure the summary as: Summary / Changes (files + what changed) /
  Verification (commands run + results) / Assumptions made / Open questions for the dispatcher /
  Unfinished.
  {"type":"report","thought":"done","summary":"Summary: added a token-bucket limiter. Changes: api/limiter.py (new TokenBucket), api/middleware.py (registered before auth). Verification: pytest tests/test_limiter.py — 6 passed. Assumptions made: 60 req/min default. Open questions: none. Unfinished: none."}

<</type:report>>
""")
# TODO LIST POLICY 350-376 (incl. trailing blank)
sub("""TODO LIST POLICY (the write_todos tool)""", """<<tool:write_todos>>
TODO LIST POLICY (the write_todos tool)""")
sub("resending in the new shape), and submit_changes stays BLOCKED until nothing is pending — this is",
    "resending in the new shape), and <<main>>submit_changes<</main>><<child>>report<</child>> stays "
    "BLOCKED until nothing is pending — this is")
sub("apply in one clean batch (see the edit variant), a plain answer, or a clarification — just edit",
    "apply in one clean batch (see the edit variant)<<main>>, a plain answer, or a clarification<</main>> — just edit")
sub("directly and submit. The list is the tool",
    "directly and <<main>>submit<</main>><<child>>report<</child>>. The list is the tool")
sub("'cancelled' (with why) instead of silently dropping. Every change must serve the user's original\ngoal — no",
    "'cancelled' (with why) instead of silently dropping. Every change must serve the "
    "<<main>>user's original<</main>><<child>>task you were<</child>>\n<<main>>goal<</main>><<child>>given<</child>> — no")
sub("""  item; the run item's 'done' evidence is the run_command output, never an edit.

""", """  item; the run item's 'done' evidence is the run_command output, never an edit.

<</tool:write_todos>>
""")
sub("""After an edit, prefer live tools (read_file/search_code) over the retrieval seed — your edit is
already on the real workspace. Available tools:
""", """<<type:edit>>
After an edit, prefer live tools (read_file/search_code)<<main>> over the retrieval seed<</main>> — your edit is
already on the real workspace. Available tools:
<</type:edit>>
<<child:readonly>>
Available tools:
<</child:readonly>>
""")

# ---------------- appended blocks ----------------
sub('''_PROPOSE_MODE_MODES_ENABLED = """\\
  "recommended"''', '''_PROPOSE_MODE_MODES_ENABLED = """\\
<<main>>
  "recommended"''')
sub('''"Draft a plan you approve, then execute."}]}"""''', '''"Draft a plan you approve, then execute."}]}
<</main>>"""''')
sub('''_PROPOSE_MODE_MODES_DISABLED = """\\
  "recommended"''', '''_PROPOSE_MODE_MODES_DISABLED = """\\
<<main>>
  "recommended"''')
sub('''"Exit Plan Mode; I make the change directly and you review it."}]}"""''',
    '''"Exit Plan Mode; I make the change directly and you review it."}]}
<</main>>"""''')
sub("""- recall(query): pull relevant""", """<<tool:recall>>
- recall(query): pull relevant""")
sub("""  would help; pass verbatim=true to also see the original source text.
- remember(content""", """  would help; pass verbatim=true to also see the original source text.
<</tool:recall>>
<<tool:remember>>
- remember(content""")
sub('''  for transient detail; consolidation also captures memories automatically."""''',
    '''  for transient detail; consolidation also captures memories automatically.
<</tool:remember>>"""''')
sub('''

PROJECT INSTRUCTIONS (from this workspace's AGENTS.md — always-on guidance from \\
the user; follow it unless it conflicts with a safety rule):
{instructions}"""''', '''

<<main>>
PROJECT INSTRUCTIONS (from this workspace's AGENTS.md — always-on guidance from \\
the user; follow it unless it conflicts with a safety rule):
<</main>>
<<child>>
PROJECT INSTRUCTIONS (from this workspace's AGENTS.md, written for an agent working with a
human). They apply to you BELOW the sub-agent rules above and your dispatcher's task; read
"the user" in them as your dispatcher:
<</child>>
{instructions}"""''')
sub("""tool; call one directly when the user's request needs the external system it""",
    """tool; call one directly when <<main>>the user's request<</main>><<child>>your task<</child>> needs the external system it""")
sub("""- Calling one pauses the turn for a live user approval card. That pause is
  expected behavior, not an error — wait for it, do not route around it.
- If the user rejects a call, do not silently retry the same call; adapt your
  approach or ask what they want to do next.
""", """<<main>>
- Calling one pauses the turn for a live user approval card. That pause is
  expected behavior, not an error — wait for it, do not route around it.
- If the user rejects a call, do not silently retry the same call; adapt your
  approach or ask what they want to do next.
<</main>>
<<perm:default>>
- A call may pause for a human approval card. That pause is expected
  behavior, not an error — wait for it, do not route around it.
<</perm:default>>
<<perm:acceptEdits>>
- A call may pause for a human approval card. That pause is expected
  behavior, not an error — wait for it, do not route around it.
<</perm:acceptEdits>>
<<perm:dontAsk>>
- A call runs only if a remembered rule already approves it; otherwise it
  is refused and nobody is asked.
<</perm:dontAsk>>
<<child>>
- If a call is rejected or refused, do not retry the same call; adapt your
  approach, or note the blocker in your report — you cannot ask.
<</child>>
""")
sub("""a line is an intent trigger, not a keyword filter. Every one of these applies
equally: judge each request against every line below, every turn (your per-turn
instruction repeats this check — it is not a one-time read of this block).

This check is UNCONDITIONAL""", """a line is an intent trigger, not a keyword filter.<<main>> Every one of these applies<</main>>
<<main>>
equally: judge each request against every line below, every turn (your per-turn
instruction repeats this check — it is not a one-time read of this block).

This check is UNCONDITIONAL""")
sub("""match your label against it, not to re-judge its importance.

A match is judged""", """match your label against it, not to re-judge its importance.
<</main>>

A match is judged""")
sub("""not the meaning on its own, the enumeration is.

Worked pattern""", """not the meaning on its own, the enumeration is.

<<main>>
Worked pattern""")
sub("""When a skill's line could apply, even partially, load it BEFORE answering, editing,
planning, or exploring:
""", """When a skill's line could apply, even partially, load it BEFORE answering, editing,
planning, or exploring:
<</main>>
<<child>>
When a skill's line matches your task, load it with read_skill before you start that part
of the work:
<</child>>
""")
sub("""A skill may bundle helper scripts under its scripts/ folder — run them with
run_command, e.g. run_command(command="python .crucible/skills/<name>/scripts/<file>.py").
read_skill loads instructions""", """<<tool:run_command>>
A skill may bundle helper scripts under its scripts/ folder — run them with
run_command, e.g. run_command(command="python .crucible/skills/<name>/scripts/<file>.py").
<</tool:run_command>>
<<main>>
read_skill loads instructions""")
sub('''describe what you're about to do is the single most common way this turn gets wasted.
"""''', '''describe what you're about to do is the single most common way this turn gets wasted.
<</main>>
<<child>>
read_skill loads instructions for YOU to execute. When it returns, continue — your next action
is the skill's actual first step, followed within your sub-agent rules above.
<</child>>
"""''')


# ---------------- validate at import + render at the use sites ----------------
sub("import copy\nimport json\n",
    "import copy\nimport json\n\nfrom agentd.prompting.tagged import RenderContext, render_prompt, tagged\n")
_CONSTANTS = ["CONTROLLER_SYSTEM_PROMPT", "_PROPOSE_MODE_MODES_ENABLED", "_PROPOSE_MODE_MODES_DISABLED",
              "_MEMORY_BLOCK", "_INSTRUCTIONS_BLOCK_TEMPLATE", "_MCP_BLOCK", "_SESSIONS_BLOCK",
              "_SKILLS_BLOCK_HEADER"]
for name in _CONSTANTS:
    opener = f'\n{name} = """'
    sub(opener, f'\n{name} = tagged("{name}", """')
    begin = src.index(f'{name} = tagged("{name}", """') + len(f'{name} = tagged("{name}", """')
    close = src.index('"""', begin)
    src = src[:close] + '""")' + src[close + 3:]
sub("""    modes = _PROPOSE_MODE_MODES_ENABLED if task_subsystem_enabled else _PROPOSE_MODE_MODES_DISABLED
    base = (
        CONTROLLER_SYSTEM_PROMPT
        .replace("{propose_mode_modes}", modes)""", """    ctx = RenderContext.main()
    modes = render_prompt(
        _PROPOSE_MODE_MODES_ENABLED if task_subsystem_enabled else _PROPOSE_MODE_MODES_DISABLED, ctx)
    base = (
        render_prompt(CONTROLLER_SYSTEM_PROMPT, ctx)
        .replace("{propose_mode_modes}", modes)""")
sub('    base = base + (_MEMORY_BLOCK if memory_enabled else "")',
    '    base = base + (render_prompt(_MEMORY_BLOCK, ctx) if memory_enabled else "")')
sub("        base += _INSTRUCTIONS_BLOCK_TEMPLATE.replace(",
    "        base += render_prompt(_INSTRUCTIONS_BLOCK_TEMPLATE, ctx).replace(")
sub("            base += _SKILLS_BLOCK_HEADER + rendered",
    "            base += render_prompt(_SKILLS_BLOCK_HEADER, ctx) + rendered")
sub("        base += _MCP_BLOCK\n", "        base += render_prompt(_MCP_BLOCK, ctx)\n")
sub("        base += _SESSIONS_BLOCK\n", "        base += render_prompt(_SESSIONS_BLOCK, ctx)\n")

PATH.write_text(src, encoding="utf-8")
print("converted", PATH)
```

The script adds the regions spec §4.6.1/§4.6.2 require (read the diff — this is the child prompt surface): the child opening line; the seed-free GROUND block; the `child:edit`/`child:readonly` preambles; per-permission `run_command` sentences (`perm:*`, with `shell:*` nested under `perm:dontAsk`); `type:*` regions around every variant; the new `type:report` segment; `tool:write_todos` around all TODO text; `child` wording for `submit_changes`→`report`; `tool:recall`/`tool:remember` in `_MEMORY_BLOCK`; `_PROPOSE_MODE_MODES_*` wholly `<<main>>`; child forms of the instructions/MCP/skills blocks; and `tagged(...)` + `render_prompt(..., RenderContext.main())` at every use site.

- [ ] **Step 4: Run the tests — goldens must be unchanged**

Run: `.venv/bin/python -m pytest tests/test_controller_prompts_tagged.py tests/test_prompt_goldens.py tests/test_tagged_prompt.py > /tmp/t3.txt 2>&1; echo exit=$?; tail -3 /tmp/t3.txt`
Expected: `exit=0`. If `test_main_agent_text_matches_goldens` fails, the conversion is wrong — never re-capture the golden here.

- [ ] **Step 5: Run the controller + prompt suites**

Run: `.venv/bin/python -m pytest tests/ -k "controller or prompt or skills or mcp or todo or exec_sessions_prompt" > /tmp/t3b.txt 2>&1; echo exit=$?; tail -3 /tmp/t3b.txt`
Expected: `exit=0`. (Verified during planning: the raw-constant tests — `test_controller_prompts_progress_narration.py:97`, `test_controller_loop_submit_requires_fresh_verify.py`, `test_controller_payload.py`, `test_controller_schema.py`, `test_controller_prompt_instructions.py`, `test_controller_answer_intent_divergence.py`, `test_exec_sessions_prompt_block.py` — all still pass because no marker splits the substrings they check. If a later edit makes one fail, migrate that assertion to `render_prompt(CONSTANT, RenderContext.main())`; do not weaken it.)

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/controller_prompts.py tests/test_controller_prompts_tagged.py
git commit -m "refactor(prompts): convert controller prompts to tagged templates (main byte-identical)"
```

---

### Task 4: Child system prompt assembly — role block, persona, append order

**Files:**
- Modify: `agentd/chat/controller_prompts.py`
- Test: `tests/test_controller_child_prompt.py`

**Interfaces:**
- Consumes: tagged constants (Task 3).
- Produces: `format_controller_system_prompt(tool_definitions, *, task_subsystem_enabled=None, memory_enabled=None, project_instructions=None, skills_catalog=None, render_ctx: RenderContext | None = None, persona: str | None = None) -> str`; constants `_AGENT_ROLE_BLOCK` (tagged; contains `{label}`) and `_PERSONA_BLOCK_TEMPLATE` (contains `{persona}`). Child append order is fixed (spec §4.2): role block → persona → memory → instructions → skills → MCP (→ sessions, which Phase 2 never offers children). Main's order is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_controller_child_prompt.py`:

```python
"""The rendered child system prompt (spec §4.2, §4.6.1, §4.6.2, §4.6.6)."""
from pathlib import Path

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.prompting.tagged import RenderContext
from agentd.skills.models import SkillManifest

EDIT_TYPES = frozenset({"tool_call", "edit", "progress", "report"})
CHILD = RenderContext(
    audience="child", permission="default",
    tools=frozenset({"read_file", "search_code", "run_command", "write_todos", "recall",
                     "read_skill"}),
    base_types=EDIT_TYPES, agent_id="agent-1", agent_label="limiter impl")
READONLY = RenderContext(
    audience="child", permission="plan", tools=frozenset({"read_file", "search_code"}),
    base_types=frozenset({"tool_call", "progress", "report"}), agent_id="agent-2",
    agent_label="survey")
DONT_ASK = RenderContext(
    audience="child", permission="dontAsk", tools=frozenset({"run_command"}),
    base_types=EDIT_TYPES, agent_id="agent-3", agent_label="fmt")
_MCP = {"name": "mcp__gh__create_issue", "description": "d", "parameters": {"type": "object"}}
_CATALOG = [SkillManifest(name="brainstorming", description="Use before creative work.",
                          body_path=Path("/x/SKILL.md"), dir=Path("/x"))]


def _child(ctx: RenderContext, **kw) -> str:
    return format_controller_system_prompt(
        kw.pop("tools", []), task_subsystem_enabled=False, render_ctx=ctx, **kw)


def test_main_is_unchanged_by_the_new_parameters() -> None:
    kw = dict(task_subsystem_enabled=True, memory_enabled=True, project_instructions="Be terse.",
              skills_catalog=_CATALOG)
    assert (format_controller_system_prompt([_MCP], **kw)
            == format_controller_system_prompt([_MCP], render_ctx=RenderContext.main(), **kw))


def test_child_identity_and_role_block_with_label() -> None:
    out = _child(CHILD, memory_enabled=False)
    assert out.startswith("You are a sub-agent carrying out one task for another agent")
    assert 'SUB-AGENT RULES — you are sub-agent "limiter impl"' in out
    assert "{label}" not in out


def test_child_append_order_role_persona_memory_instructions_skills_mcp() -> None:
    out = _child(CHILD, memory_enabled=True, project_instructions="Use tabs.",
                 skills_catalog=_CATALOG, tools=[_MCP], persona="You review code.")
    idx = [out.index(marker) for marker in (
        "SUB-AGENT RULES", "AGENT INSTRUCTIONS", "MEMORY (durable", "PROJECT INSTRUCTIONS",
        "AVAILABLE SKILLS", "EXTERNAL MCP TOOLS")]
    assert idx == sorted(idx)


def test_persona_and_instructions_render_verbatim() -> None:
    out = _child(CHILD, memory_enabled=False, persona="Keep <<main>>x<</main>> {y}",
                 project_instructions="AGENTS <<child>>z<</child>> {w}")
    assert "Keep <<main>>x<</main>> {y}" in out
    assert "AGENTS <<child>>z<</child>> {w}" in out


def test_child_memory_teaches_only_the_memory_tools_it_has() -> None:
    out = _child(CHILD, memory_enabled=True)
    assert "- recall(query)" in out and "- remember(" not in out


def test_readonly_child_cannot_be_taught_to_edit() -> None:
    out = _child(READONLY, memory_enabled=False)
    assert "YOU ARE READ-ONLY" in out
    assert "Variant — edit" not in out and "After an edit" not in out
    assert '"tool":"edit"' not in out
    assert "(progress/report)" in out


def test_dont_ask_child_is_told_commands_need_a_remembered_rule() -> None:
    out = _child(DONT_ASK, memory_enabled=False, tools=[_MCP])
    assert "runs only if a remembered rule allows it" in out
    assert "A call runs only if a remembered rule already approves it" in out
    assert "approval card" not in out.split("EXTERNAL MCP TOOLS")[1]


def test_child_skills_block_drops_the_first_action_framing() -> None:
    out = _child(CHILD, memory_enabled=False, skills_catalog=_CATALOG)
    assert "load it with read_skill before you start" in out
    assert "UNCONDITIONAL" not in out and "FIRST action" not in out
    assert "run bundled" not in out  # sanity: the scripts sentence is the run_command one
    assert "run them with\nrun_command" in out


def test_child_instructions_are_subordinate_to_sub_agent_rules() -> None:
    out = _child(CHILD, memory_enabled=False, project_instructions="Ask me first.")
    assert "They apply to you BELOW the sub-agent rules above" in out
    assert "always-on guidance from the user" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_controller_child_prompt.py > /tmp/t4.txt 2>&1; echo exit=$?; tail -3 /tmp/t4.txt`
Expected: `exit=1` — `TypeError: format_controller_system_prompt() got an unexpected keyword argument 'render_ctx'`.

- [ ] **Step 3: Add the role block and persona constants**

In `agentd/chat/controller_prompts.py`, directly after `_SKILLS_BLOCK_HEADER = tagged(...)`, add:

```python
# Child-only (spec §4.6.6). Appended right after the rendered CONTROLLER_SYSTEM_PROMPT and
# before every other appended block, so the AGENTS.md child line ("the sub-agent rules
# above") reads correctly. {label} is substituted after rendering.
_AGENT_ROLE_BLOCK = tagged("_AGENT_ROLE_BLOCK", """

SUB-AGENT RULES — you are sub-agent "{label}", dispatched by another agent (your dispatcher).
- Precedence: tool and safety constraints > these sub-agent rules > your dispatcher's task
  (in 'goal') > project instructions (AGENTS.md) > skill bodies. Text addressed to "the user"
  or "your human partner" means your dispatcher.
- You cannot ask questions or wait for replies. When any instruction says to ask, confirm,
  get approval, or present options: if a reasonable, reversible default exists, take it and
  list it under "Assumptions made" in your report; if the choice is consequential or
  irreversible, don't do it — list it under "Open questions" and finish everything else.
- A human may still approve or reject your commands and edits through cards. If one is
  rejected, adapt.
- Other agents are editing this workspace at the same time. Stay inside the files your task
  assigns; if you must touch another file, read it first and mention it in your report.
- Never run version-control changes (commit, add, checkout/switch, stash, reset, rebase,
  merge, pull, push, worktree, branch, restore, clean). The main agent commits after the work
  is done. Read-only git (status, diff, log, show) is fine.
<<tool:dispatch_agents>>
- You may dispatch your own sub-agents with dispatch_agents when parts of your task are
  independent and touch disjoint files.
<</tool:dispatch_agents>>
- If a skill tells you to dispatch sub-agents and dispatch_agents is not in your tools, do
  that work yourself.
- Only 'report' reaches your dispatcher. If an instruction tells you to write a report file,
  also put its full content in 'report'. Skip "announce what you're doing" steps.""")

# The agent definition's body (spec §4.2). .replace, never .format — personas may contain { }.
_PERSONA_BLOCK_TEMPLATE = """

AGENT INSTRUCTIONS (the agent definition your dispatcher chose for this task):
{persona}"""
```

- [ ] **Step 4: Render per context and append child blocks in order**

In `format_controller_system_prompt`, change the signature and body as follows (keep the docstring; add the two params):

```python
def format_controller_system_prompt(
    tool_definitions: list[dict[str, object]],
    *,
    task_subsystem_enabled: bool | None = None,
    memory_enabled: bool | None = None,
    project_instructions: str | None = None,
    skills_catalog: list | None = None,
    render_ctx: RenderContext | None = None,
    persona: str | None = None,
) -> str:
```

Replace the line `    ctx = RenderContext.main()` (added by Task 3) with:

```python
    ctx = render_ctx or RenderContext.main()
```

Then, immediately after the `base = (render_prompt(CONTROLLER_SYSTEM_PROMPT, ctx) ... )` statement and before the `# Appended (not a placeholder)` comment, insert:

```python
    if not ctx.is_main:
        # Child append order (spec §4.2): role block → persona → then today's order.
        label = ctx.agent_label or ctx.agent_id
        base += render_prompt(_AGENT_ROLE_BLOCK, ctx).replace("{label}", label)
        if persona and persona.strip():
            base += _PERSONA_BLOCK_TEMPLATE.replace("{persona}", persona.strip())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_controller_child_prompt.py tests/test_prompt_goldens.py tests/test_controller_prompts_tagged.py > /tmp/t4.txt 2>&1; echo exit=$?; tail -3 /tmp/t4.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/controller_prompts.py tests/test_controller_child_prompt.py
git commit -m "feat(prompts): assemble the child system prompt (role block, persona, order)"
```

---

### Task 5: Tagged correction strings and the loop's render context

**Files:**
- Modify: `agentd/reasoning/react_common.py`
- Modify: `agentd/chat/controller_loop.py`
- Test: `tests/test_controller_child_corrections.py`

**Interfaces:**
- Consumes: `RenderContext`, `render_prompt`, `tagged`.
- Produces:
  - `react_common.MALFORMED_CORRECTION: str` (unchanged value — now the main render of `_MALFORMED_TEMPLATE`), `react_common.malformed_correction(ctx: RenderContext, allowed_types: Sequence[str]) -> str`
  - `react_common.accepts_kwarg(fn: object, name: str) -> bool` (used by Task 9)
  - `controller_loop._reserved_tool_name_correction(resp, atype, ctx=_MAIN)`, `_progress_repeat_correction(resp, atype, last_was_progress, ctx=_MAIN)`, `_progress_dedup_correction(resp, atype, seen_notes, ctx=_MAIN)`, `_empty_edit_redirect(ctx) -> str`
  - `ControllerLoop.__init__(..., render_ctx: RenderContext | None = None)` → `self._render_ctx`
- Decision recorded (spec §4.1 vs §4.7.4): spec §4.1 says `MALFORMED_CORRECTION` "gains the allowed type list"; that would change a main correction string, which the byte-identity rule forbids. The allowed-type suffix is therefore a `child` region only.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_controller_child_corrections.py`:

```python
import pytest

from agentd.chat import controller_loop as cl
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.reasoning.react_common import MALFORMED_CORRECTION, accepts_kwarg, malformed_correction
from agentd.tools.sources import AggregatingToolRegistry

MAIN = RenderContext.main()
CHILD = RenderContext(audience="child", permission="default",
                      base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                      agent_id="a1", agent_label="impl")
READONLY = RenderContext(audience="child", permission="plan",
                         base_types=frozenset({"tool_call", "progress", "report"}),
                         agent_id="a2", agent_label="survey")
_BANNED = ("submit_changes", "propose_mode", "Plan Mode", "'answer'")


def test_malformed_correction_main_is_unchanged_and_child_names_allowed_types() -> None:
    assert malformed_correction(MAIN, ["tool_call", "answer"]) == MALFORMED_CORRECTION
    out = malformed_correction(CHILD, ["tool_call", "edit", "progress", "report"])
    assert out.endswith("Allowed types right now: tool_call, edit, progress, report.")


@pytest.mark.parametrize("ctx", [CHILD, READONLY])
def test_child_corrections_never_name_parent_terminals(ctx: RenderContext) -> None:
    texts = [
        cl._reserved_tool_name_correction({"tool": "edit"}, "tool_call", ctx) or "",
        cl._progress_repeat_correction({"note": "x"}, "progress", True, ctx) or "",
        cl._progress_dedup_correction({"note": "x"}, "progress", {"x"}, ctx) or "",
        cl._empty_edit_redirect(ctx),
    ]
    for text in texts:
        assert text and not any(b in text for b in _BANNED), text


def test_readonly_corrections_never_offer_edit() -> None:
    repeat = cl._progress_repeat_correction({"note": "x"}, "progress", True, READONLY) or ""
    reserved = cl._reserved_tool_name_correction({"tool": "edit"}, "tool_call", READONLY) or ""
    assert "'edit'" not in repeat
    assert "You are read-only" in reserved


def test_accepts_kwarg_reads_the_signature() -> None:
    def explicit(a, *, b): ...
    def open_kwargs(a, **kw): ...
    assert accepts_kwarg(explicit, "b") and not accepts_kwarg(explicit, "c")
    assert accepts_kwarg(open_kwargs, "anything")


@pytest.mark.asyncio
async def test_loop_uses_the_child_render_context_for_corrections() -> None:
    steps = [{"type": "progress", "thought": "t", "note": "n1"},
             {"type": "progress", "thought": "t", "note": "n2"},
             {"type": "answer", "thought": "t", "answer": "done"}]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        AggregatingToolRegistry([]), EventBroadcaster(), channel_id="c",
        phase_sm=ControllerPhaseSM(), render_ctx=CHILD)
    out = await loop.run({"goal": "g", "workspace_path": "/w"}, max_iters=6)
    corrections = [m["content"] for m in out.history or [] if m.get("role") == "tool_result"]
    assert any("(or 'report' if you are done)" in c for c in corrections)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_controller_child_corrections.py > /tmp/t5.txt 2>&1; echo exit=$?; tail -3 /tmp/t5.txt`
Expected: `exit=2` — `ImportError: cannot import name 'accepts_kwarg'`.

- [ ] **Step 3: Tag `MALFORMED_CORRECTION` and add the two helpers**

In `agentd/reasoning/react_common.py`, replace the imports block and the `MALFORMED_CORRECTION = (...)` definition with:

```python
from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from functools import lru_cache

from agentd.prompting.tagged import RenderContext, render_prompt, tagged
```

```python
# Tagged (spec §4.7): the main render is byte-identical to the historical constant; the
# allowed-type suffix is child-only so the main correction text never changes (§4.7.4).
_MALFORMED_TEMPLATE = tagged("malformed", (
    "Your previous response was empty or had no valid 'type'. Reply with EXACTLY ONE JSON object "
    "matching the schema. Do NOT return an empty object or any prose."
    "<<child>> Allowed types right now: {allowed}.<</child>>"
))
MALFORMED_CORRECTION = render_prompt(_MALFORMED_TEMPLATE, RenderContext.main())


def malformed_correction(ctx: RenderContext, allowed_types: Sequence[str]) -> str:
    """The malformed-response correction for ctx; a child also gets the allowed types."""
    return render_prompt(_MALFORMED_TEMPLATE, ctx).replace("{allowed}", ", ".join(allowed_types))


@lru_cache(maxsize=128)
def _declared_kwargs(fn: object) -> frozenset[str] | None:
    try:
        params = inspect.signature(fn).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    return frozenset(params)


def accepts_kwarg(fn: object, name: str) -> bool:
    """True when fn declares `name` (or **kwargs). Same rule as engine._accepts: ask the
    callee rather than maintain a capability flag — lets the loop pass new keywords to
    engines that declare them without breaking fixed-signature fakes."""
    params = _declared_kwargs(fn)
    return params is None or name in params
```

- [ ] **Step 4: Tag the loop's correction strings**

In `agentd/chat/controller_loop.py`, add to the imports:

```python
from agentd.prompting.tagged import RenderContext, render_prompt, tagged
```

and change the `react_common` import line to:

```python
from agentd.reasoning.react_common import assistant_turn, dedup_key, malformed_correction
```

Directly below the `logger = logging.getLogger(__name__)` line, add:

```python
_MAIN = RenderContext.main()
```

Replace the body of `_reserved_tool_name_correction` (keep its docstring) and its signature with:

```python
_RESERVED_TOOL_NAME_TEMPLATE = tagged("reserved_tool_name", (
    "'{tool}' is not a callable tool — there is no such tool in AVAILABLE TOOLS. "
    "'{tool}' is a top-level response TYPE, emitted as its own object — "
    '{"type":"{tool}", ...} (see the "{tool}" variant above for its required '
    'fields) — NEVER as {"type":"tool_call","tool":"{tool}",...}.'
    "<<main>> If you are "
    "trying to make a change: type='edit' is already directly available — emit it "
    "now (Plan Mode is the only phase where you'd emit propose_mode first).<</main>>"
    "<<child:edit>> If you are trying to make a change: type='edit' is directly available — "
    "emit it now.<</child:edit>>"
    "<<child:readonly>> You are read-only for this task — finish with "
    "type='report'.<</child:readonly>>"
))


def _reserved_tool_name_correction(
    resp: dict[str, object], atype: str, ctx: RenderContext = _MAIN,
) -> str | None:
    """Reject a tool_call whose 'tool' is actually a top-level response type name."""
    if atype != "tool_call":
        return None
    tool = str(resp.get("tool", ""))
    reserved = (_RESERVED_ACTION_TOOL_NAMES if ctx.is_main
                else _RESERVED_ACTION_TOOL_NAMES | {"report"})
    if tool not in reserved:
        return None
    return render_prompt(_RESERVED_TOOL_NAME_TEMPLATE, ctx).replace("{tool}", tool)
```

Replace `_progress_repeat_correction` and `_progress_dedup_correction` with:

```python
_PROGRESS_REPEAT_TEMPLATE = tagged("progress_repeat", (
    "You already posted a progress note and took no action after it. A progress note "
    "does NOT count as doing the work. Take the actual next action now — emit "
    "type='tool_call'<<type:edit>> / 'edit'<</type:edit>><<main>> / 'submit_changes' (or 'answer' "
    "if you are truly done)<</main>><<child>> (or 'report' if you are done)<</child>>."
))
_PROGRESS_DEDUP_TEMPLATE = tagged("progress_dedup", (
    "You already posted that exact progress note this turn. Do not repeat it — "
    "take the next real action instead (tool_call<<type:edit>> / edit<</type:edit>>"
    "<<main>> / submit_changes / answer<</main>><<child>> / report<</child>>)."
))


def _progress_repeat_correction(
    resp: dict[str, object], atype: str, last_was_progress: bool, ctx: RenderContext = _MAIN,
) -> str | None:
    """Reject a `progress` note that immediately follows another `progress` with no real
    action between — a weak model can turn a free non-terminal action into a narration
    attractor (spam notes instead of acting). Mirrors the emit_patch-dedup discipline;
    routes through the same _MAX_MALFORMED correction chain (no new retry primitive)."""
    if atype != "progress" or not last_was_progress:
        return None
    return render_prompt(_PROGRESS_REPEAT_TEMPLATE, ctx)


def _progress_dedup_correction(
    resp: dict[str, object], atype: str, seen_notes: set[str], ctx: RenderContext = _MAIN,
) -> str | None:
    """Reject an exact-duplicate progress note already emitted this turn (same attractor
    class as _progress_repeat_correction, but catches non-adjacent repeats)."""
    if atype != "progress":
        return None
    note = _normalize_progress_note(resp)
    # An empty note is _empty_action_correction's business (it fires earlier in the
    # chain); never let "" match a stored value here.
    if note and note in seen_notes:
        return render_prompt(_PROGRESS_DEDUP_TEMPLATE, ctx)
    return None


_EMPTY_EDIT_REDIRECT_TEMPLATE = tagged("empty_edit_redirect", (
    "That 'edit' had no patch_ops, so NOTHING was applied. If you meant to "
    "create or update the TODO LIST, that is a TOOL CALL — emit "
    '{"type":"tool_call","tool":"write_todos","args":{"items":[…]}}, NOT '
    "type='edit'. To change a file, emit type='edit' with a NON-EMPTY "
    "patch_ops (each op: file + its op fields). To finish, emit "
    "type='<<main>>submit_changes<</main>><<child>>report<</child>>'."
))


def _empty_edit_redirect(ctx: RenderContext) -> str:
    return render_prompt(_EMPTY_EDIT_REDIRECT_TEMPLATE, ctx)
```

- [ ] **Step 5: Thread the render context through the loop**

In `ControllerLoop.__init__`, add a keyword parameter after `pills_seal_cb`:

```python
        render_ctx: RenderContext | None = None,
```

and at the end of `__init__` add:

```python
        # Which audience this loop's text is rendered for (spec §4.7). None = the main agent,
        # whose every string stays byte-identical (tests/test_prompt_goldens.py).
        self._render_ctx = render_ctx or RenderContext.main()
```

In `_iterate`, change the correction chain (currently `MALFORMED_CORRECTION if atype not in self._allowed_action_types() else ...`) to:

```python
            correction = (
                malformed_correction(self._render_ctx, self._allowed_action_types())
                if atype not in self._allowed_action_types()
                else _propose_mode_correction(resp, self._allowed_modes_for_current_phase()) if atype == "propose_mode"
                else _reserved_tool_name_correction(resp, atype, self._render_ctx)
                or _decide_state_change_correction(resp, self._sm.phase)
                or _empty_action_correction(resp, atype)
                or _answer_intent_divergence_correction(resp, atype, tool_names)
                # After _empty_action_correction on purpose: a blank note must be
                # reported as EMPTY, not misdiagnosed as a duplicate of "".
                or _progress_repeat_correction(resp, atype, last_was_progress, self._render_ctx)
                or _progress_dedup_correction(resp, atype, seen_notes, self._render_ctx)
            )
```

In the empty-`patch_ops` branch of the `edit` action, replace the `"content": ( "That 'edit' had no patch_ops, …" … )` string with:

```python
                        "content": _empty_edit_redirect(self._render_ctx)})
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_controller_child_corrections.py tests/test_prompt_goldens.py tests/test_controller_reserved_tool_name.py tests/test_controller_progress_action.py > /tmp/t5.txt 2>&1; echo exit=$?; tail -3 /tmp/t5.txt`
Expected: `exit=0`.

- [ ] **Step 7: Commit**

```bash
git add agentd/reasoning/react_common.py agentd/chat/controller_loop.py tests/test_controller_child_corrections.py
git commit -m "feat(controller): render correction strings per audience; loop render context"
```

---

### Task 6: Tagged tool descriptions and the MCP rejection text

**Files:**
- Modify: `agentd/tools/registry.py`, `agentd/tools/sources.py`, `agentd/chat/todo_source.py`, `agentd/mcp/tool_source.py`
- Test: `tests/test_child_tool_texts.py`

**Interfaces:**
- Produces: `ToolRegistry(shadow_root, real_workspace_path, semantic_index=None, command_approval_callback=None, render_ctx: RenderContext | None = None)`; `BuiltinToolSource(..., render_ctx=None)`; `TodoToolSource(ledger, on_mutate=None, *, render_ctx=None)`; `McpToolSource(manager, approval_callback, *, render_ctx=None)`. All default to the main agent, so the task-path `ToolLoop` (`tools/loop.py:1218`) and every existing caller are unchanged (spec §4.7.6).
- `todo_source._WRITE_TODOS_DEF` stays as the main-rendered definition for existing importers.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_child_tool_texts.py`:

```python
from pathlib import Path

import pytest

from agentd.chat.todo_ledger import TodoLedger
from agentd.chat.todo_source import TodoToolSource
from agentd.mcp.tool_source import McpToolSource
from agentd.prompting.tagged import RenderContext
from agentd.tools.registry import ToolRegistry
from agentd.tools.sources import BuiltinToolSource


def _ctx(permission: str, shell: str = "ask") -> RenderContext:
    return RenderContext(audience="child", permission=permission, shell_policy=shell,
                         base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                         agent_id="a", agent_label="a")


def _run_command_description(reg: ToolRegistry) -> str:
    return next(d.description for d in reg.definitions() if d.name == "run_command")


def test_child_run_command_says_real_shared_workspace() -> None:
    reg = ToolRegistry(Path("/w"), Path("/w"), render_ctx=_ctx("default"))
    text = _run_command_description(reg)
    assert "real, shared workspace" in text and "shadow workspace" not in text
    assert "surfaced to the user" not in text and "approval card" in text


@pytest.mark.parametrize(("shell", "expected"), [
    ("ask", "runs only if a remembered rule allows it"),
    ("allow_all", "Commands run without approval"),
])
def test_dont_ask_run_command_follows_shell_policy(shell: str, expected: str) -> None:
    reg = ToolRegistry(Path("/w"), Path("/w"), render_ctx=_ctx("dontAsk", shell))
    text = _run_command_description(reg)
    assert expected in text


def test_builtin_source_forwards_the_render_context() -> None:
    src = BuiltinToolSource(shadow_root=Path("/w"), real_workspace_path=Path("/w"),
                            render_ctx=_ctx("default"))
    desc = next(d.description for d in src.definitions() if d.name == "run_command")
    assert "real, shared workspace" in desc


def test_child_write_todos_blocks_report_not_submit_changes() -> None:
    desc = TodoToolSource(TodoLedger(), render_ctx=_ctx("default")).definitions()[0].description
    assert "report is BLOCKED" in desc and "submit_changes" not in desc
    assert "from your task" in desc


@pytest.mark.asyncio
async def test_child_mcp_rejection_never_says_ask() -> None:
    async def deny(server: str, tool: str, args: dict[str, object]) -> bool:
        return False
    out = await McpToolSource(object(), deny, render_ctx=_ctx("default")).execute("mcp__gh__x", {})
    assert out.is_error and "or ask" not in out.output
    assert out.output.endswith("or note the blocker in your report.")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_child_tool_texts.py > /tmp/t6.txt 2>&1; echo exit=$?; tail -3 /tmp/t6.txt`
Expected: `exit=1` — `TypeError: ToolRegistry.__init__() got an unexpected keyword argument 'render_ctx'`.

- [ ] **Step 3: Tag `run_command` and accept a render context in `ToolRegistry`**

In `agentd/tools/registry.py`, add after the existing imports:

```python
from agentd.prompting.tagged import RenderContext, render_prompt, tagged
```

Add at module level, above `class ToolRegistry`:

```python
# Tagged (spec §4.6.3). Main renders today's text byte-for-byte; a child is told it runs in
# the real shared workspace and gets its permission's approval sentence.
_RUN_COMMAND_DESCRIPTION = tagged("run_command_description", (
    "Run a real shell command line <<main>>inside the shadow workspace<</main>><<child>>in the "
    "real, shared workspace (other agents may be editing files concurrently, so test results "
    "can reflect their in-progress work)<</child>> — "
    "'command' and 'args' are joined and executed via a shell, so "
    "pipes, redirects, and chaining work: put each token (including "
    "the operator) as its own args entry, e.g. args: [\"file.go\", "
    "\"|\", \"head\", \"-20\"] or [\"&&\", \"go\", \"vet\", \"./...\"]. "
    "Every other argument is passed through literally (spaces/quotes "
    "in a single argument are preserved). "
    "<<main>>Each command is surfaced to "
    "the user for approval (Accept / Accept & remember / Reject) "
    "unless the session was started in allow_all mode. If the user "
    "rejects, you will receive a tool-result error and should try a "
    "different approach (e.g. a static check). <</main>>"
    "<<perm:default>>A command may pause for a human approval card; if it is rejected you "
    "receive a tool-result error and should try a different approach. <</perm:default>>"
    "<<perm:acceptEdits>>A command may pause for a human approval card; if it is rejected you "
    "receive a tool-result error and should try a different approach. <</perm:acceptEdits>>"
    "<<perm:dontAsk>><<shell:ask>>A command runs only if a remembered rule allows it; otherwise "
    "it is refused (nobody is asked). <</shell:ask>><<shell:allow_all>>Commands run without "
    "approval. <</shell:allow_all>><</perm:dontAsk>>"
    "Use to run tests, "
    "linters, or type checkers."
))
```

Add `render_ctx: RenderContext | None = None,` as the last parameter of `ToolRegistry.__init__` and, at the end of `__init__`:

```python
        self._render_ctx = render_ctx or RenderContext.main()
```

In `definitions()`, replace the `run_command` `description=( "Run a real shell command line inside the shadow workspace — " … )` expression with:

```python
                description=render_prompt(_RUN_COMMAND_DESCRIPTION, self._render_ctx),
```

In `agentd/tools/sources.py`, add `from agentd.prompting.tagged import RenderContext` to the imports, add `render_ctx: RenderContext | None = None,` as the last keyword of `BuiltinToolSource.__init__`, and pass `render_ctx=render_ctx,` into the `ToolRegistry(...)` call.

- [ ] **Step 4: Tag `write_todos` and the MCP rejection**

In `agentd/chat/todo_source.py`, add `from agentd.prompting.tagged import RenderContext, render_prompt, tagged` and replace `_WRITE_TODOS_DEF = ToolDefinition(...)` with:

```python
_WRITE_TODOS_DESCRIPTION = tagged("write_todos_description", (
    "Create or update the todo list for a LARGE / multi-part change. Send the FULL "
    "list every call (full-list rewrite): every item with its current status. Use it "
    "when the request decomposes into multiple distinct features/steps; SKIP it for a "
    "single small edit. To reshape (split/insert/reorder), just resend the list in the "
    "new shape. Mark an item 'done' ONLY with evidence (cite the tool/edit result in "
    "'note'); 'blocked' (put the unblock condition in 'note') if you cannot proceed; "
    "'cancelled' (say why in 'note') to abandon one — never silently drop it. "
    "A 'run tests/verify' step from <<main>>the user's plan<</main>><<child>>your task<</child>> "
    "is always its OWN item, separate "
    "from creating the file it tests; its 'done' evidence is the command output, never "
    "an edit result. "
    "<<main>>submit_changes<</main>><<child>>report<</child>> is BLOCKED while any item is "
    "pending or in_progress."
))
_WRITE_TODOS_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "status": {"type": "string", "enum": list(_STATUSES)},
                    "note": {"type": "string"},
                },
                "required": ["title", "status"],
            },
        }
    },
    "required": ["items"],
}


def _write_todos_def(ctx: RenderContext) -> ToolDefinition:
    return ToolDefinition(
        name="write_todos",
        description=render_prompt(_WRITE_TODOS_DESCRIPTION, ctx),
        parameters=_WRITE_TODOS_PARAMETERS,
    )


# Main-rendered, kept for existing importers.
_WRITE_TODOS_DEF = _write_todos_def(RenderContext.main())
```

Replace `TodoToolSource.__init__` and `definitions` with:

```python
    def __init__(
        self,
        ledger: TodoLedger,
        on_mutate: Callable[[str | None], Awaitable[None]] | None = None,
        *,
        render_ctx: RenderContext | None = None,
    ) -> None:
        self._ledger = ledger
        # Awaited with ledger.to_json() right after a successful write_todos so the
        # controller can persist the in-flight ledger mid-turn (renders on /live while the
        # turn runs). None-safe: a source built without it (tests, no store) just no-ops.
        self._on_mutate = on_mutate
        self._render_ctx = render_ctx or RenderContext.main()

    def definitions(self) -> list[ToolDefinition]:
        return [_write_todos_def(self._render_ctx)]
```

In `agentd/mcp/tool_source.py`, add `from agentd.prompting.tagged import RenderContext, render_prompt, tagged`, add at module level:

```python
_MCP_REJECTED_TEMPLATE = tagged("mcp_rejected", (
    "MCP tool call rejected by user: {server}.{tool}. "
    "Do not retry the same call — adapt your approach<<main>> or ask<</main>><<child>>, or note "
    "the blocker in your report<</child>>."
))
```

replace `McpToolSource.__init__` with:

```python
    def __init__(
        self, manager: object, approval_callback: ApprovalCallback, *,
        render_ctx: RenderContext | None = None,
    ) -> None:
        self._manager = manager
        self._approve = approval_callback
        self._render_ctx = render_ctx or RenderContext.main()
```

and replace the rejection `ToolOutput(output=(f"MCP tool call rejected by user: …"), is_error=True)` with:

```python
            return ToolOutput(
                output=render_prompt(_MCP_REJECTED_TEMPLATE, self._render_ctx)
                .replace("{server}", server).replace("{tool}", tool_name),
                is_error=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_child_tool_texts.py tests/test_prompt_goldens.py tests/ -k "todo or mcp or registry or tool_texts or goldens" > /tmp/t6.txt 2>&1; echo exit=$?; tail -3 /tmp/t6.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/tools/registry.py agentd/tools/sources.py agentd/chat/todo_source.py agentd/mcp/tool_source.py tests/test_child_tool_texts.py
git commit -m "feat(tools): render run_command, write_todos and MCP rejection text per audience"
```

---

### Task 7: `AGENT` phase, `report` variant, allowed-types schema

**Files:**
- Modify: `agentd/chat/controller_prompts.py`, `agentd/chat/controller_phase.py`, `agentd/chat/controller_loop.py`
- Modify: `tests/test_controller_schema.py` (the flat enum gains `report`)
- Test: `tests/test_controller_agent_schema.py`

**Interfaces:**
- Produces: `_PHASE_TYPES["AGENT"] == ["tool_call", "edit", "progress", "report"]`; `_VARIANT_SPECS["report"]`; `controller_response_schema(*, phase: str, allowed_types: Sequence[str] | None = None, tight=False, anyof=False, all_fields_required=False)`; `ControllerPhaseSM(start="AGENT")`; `_empty_action_correction` handles `report`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_controller_agent_schema.py`:

```python
import pytest

from agentd.chat.controller_loop import _empty_action_correction
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.controller_prompts import _PHASE_TYPES, controller_response_schema


def _branch_types(schema: dict) -> list[str]:
    key = "oneOf" if "oneOf" in schema else "anyOf"
    return [b["properties"]["type"]["const"] for b in schema[key]]


def test_agent_phase_types() -> None:
    assert _PHASE_TYPES["AGENT"] == ["tool_call", "edit", "progress", "report"]
    assert ControllerPhaseSM(start="AGENT").allowed_types() == _PHASE_TYPES["AGENT"]


def test_invalid_start_still_raises() -> None:
    with pytest.raises(ValueError):
        ControllerPhaseSM(start="EDIT")


@pytest.mark.parametrize("mode", ["tight", "anyof"])
def test_agent_tight_schema_requires_a_report_summary(mode: str) -> None:
    schema = controller_response_schema(phase="AGENT", **{mode: True})
    assert _branch_types(schema) == ["tool_call", "edit", "progress", "report"]
    key = "oneOf" if mode == "tight" else "anyOf"
    report = next(b for b in schema[key] if b["properties"]["type"]["const"] == "report")
    assert report["required"] == ["type", "thought", "summary"]
    assert report["additionalProperties"] is False


def test_flat_agent_schema_trims_the_enum() -> None:
    schema = controller_response_schema(phase="AGENT")
    assert schema["properties"]["type"]["enum"] == ["tool_call", "edit", "progress", "report"]


def test_allowed_types_override_the_phase_table() -> None:
    types = ["tool_call", "answer", "clarify", "edit", "submit_changes", "progress", "propose_mode"]
    flat = controller_response_schema(phase="ACTIVE", allowed_types=types)
    assert flat["properties"]["type"]["enum"] == types
    tight = controller_response_schema(phase="ACTIVE", allowed_types=types, tight=True)
    assert _branch_types(tight) == types
    only_report = controller_response_schema(phase="AGENT", allowed_types=["report"], tight=True)
    assert _branch_types(only_report) == ["report"]


def test_no_allowed_types_keeps_todays_schemas() -> None:
    for phase in ("PLAN", "ACTIVE"):
        assert (controller_response_schema(phase=phase)
                == controller_response_schema(phase=phase, allowed_types=_PHASE_TYPES[phase]))


def test_empty_report_summary_is_corrected() -> None:
    assert _empty_action_correction({"type": "report", "summary": "  "}, "report")
    assert _empty_action_correction({"type": "report", "summary": "done"}, "report") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_controller_agent_schema.py > /tmp/t7.txt 2>&1; echo exit=$?; tail -3 /tmp/t7.txt`
Expected: `exit=1` — `KeyError: 'AGENT'`.

- [ ] **Step 3: Add the phase, the variant and `allowed_types`**

In `agentd/chat/controller_prompts.py`:

1. Add `from collections.abc import Sequence` to the imports.
2. In `CONTROLLER_RESPONSE_SCHEMA`, change the `type` enum to `["tool_call", "answer", "clarify", "propose_mode", "edit", "submit_changes", "progress", "report"]` and the `# submit_changes` comment above `"summary"` to `# submit_changes / report`.
3. Add to `_PHASE_TYPES`:

```python
    # AGENT: a dispatched sub-agent (spec §4.1). No user to ask (no clarify), no Plan Mode
    # (no propose_mode), and `report` is its only terminal.
    "AGENT": ["tool_call", "edit", "progress", "report"],
```

4. Add to `_VARIANT_SPECS`:

```python
    "report": {"required": ["summary"], "properties": {"summary": _STR}},
```

5. Replace `controller_response_schema` with:

```python
def controller_response_schema(
    *, phase: str, allowed_types: Sequence[str] | None = None, tight: bool = False,
    anyof: bool = False, all_fields_required: bool = False,
) -> dict[str, object]:
    """Return the controller response schema for a phase.

    `allowed_types` (when given) replaces `_PHASE_TYPES[phase]` as the variant set — the loop
    always sends its own per-iteration set, which is how ACTIVE + the task subsystem offers
    `propose_mode` and how a child's final iteration narrows to `report` (spec §4.1, §6.5).
    `tight=False` (default) → flat schema, `type` enum trimmed to the allowed actions.
    `tight=True` → discriminated-union (`oneOf`) — use ONLY for providers whose grammar enforces
    `oneOf` (TQP/llama.cpp; Gemini deadlocks — see module docstring).
    `anyof=True` → same per-variant branches as tight but wrapped in `anyOf` instead of `oneOf`.
    Use for providers that support `anyOf` strict enforcement but not `oneOf` (e.g. watsonx).
    `all_fields_required=True` → flat schema with all per-variant fields in `required` — fallback
    for providers where neither tight nor anyof is available.
    """
    types = list(allowed_types) if allowed_types is not None else list(_PHASE_TYPES[phase])
    if tight:
        return {"oneOf": [_tight_variant_branch(v) for v in types]}
    if anyof:
        return {"anyOf": [_tight_variant_branch(v) for v in types]}
    schema = copy.deepcopy(CONTROLLER_RESPONSE_SCHEMA)
    schema["properties"]["type"]["enum"] = types  # type: ignore[index]
    if all_fields_required:
        extra: list[str] = []
        for variant in types:
            spec = _VARIANT_SPECS.get(variant, {})
            for field in spec.get("required", []):  # type: ignore[union-attr]
                if field not in extra and field not in ("patch_ops",):
                    extra.append(field)
        existing = list(schema.get("required", []))  # type: ignore[arg-type]
        schema["required"] = existing + [f for f in extra if f not in existing]
    return schema
```

In `agentd/chat/controller_phase.py`, change `_VALID_STARTS = ("PLAN", "ACTIVE")` to `_VALID_STARTS = ("PLAN", "ACTIVE", "AGENT")` and add to the module docstring's list of starts (wrapped to stay under ruff's 100-char limit):

```text
  - "AGENT": a dispatched sub-agent's loop (Plan 1A / spec §4.1); constructed only by
    the sub-agent runtime.
```


In `agentd/chat/controller_loop.py`, in `_empty_action_correction`, before the final `return None`, add:

```python
    if atype == "report" and _blank("summary"):
        return (
            "Your 'report' summary was empty. The COMPLETE report goes in 'summary' — it is the "
            "only thing your dispatcher receives. Re-emit type='report' with a non-empty 'summary'."
        )
```

- [ ] **Step 4: Update the flat-enum assertion**

In `tests/test_controller_schema.py`, in the test that asserts the flat enum set (`assert set(enum) == {...}` near line 66), add `"report"` to the expected set.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_controller_agent_schema.py tests/test_controller_schema.py tests/test_controller_tight_schema.py tests/test_controller_phase.py tests/test_prompt_goldens.py > /tmp/t7.txt 2>&1; echo exit=$?; tail -3 /tmp/t7.txt`
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/chat/controller_prompts.py agentd/chat/controller_phase.py agentd/chat/controller_loop.py tests/test_controller_agent_schema.py tests/test_controller_schema.py
git commit -m "feat(controller): AGENT phase, report variant, allowed_types-driven schema"
```

---

### Task 8: AGENT branch in the per-iteration payload

**Files:**
- Modify: `agentd/chat/controller_prompts.py` (`build_controller_step_payload`)
- Test: `tests/test_controller_agent_payload.py`

**Interfaces:**
- Consumes (from `plan_context`, set by Phase 2's runtime): `iteration`, `max_iters`, `todo_status`, `pending_reconcile_files`, `reconcile_item`, and the new optional `agent_readonly: bool`.
- Produces: `payload["instruction"]` for `phase == "AGENT"`. Hint and the final-iteration schema restriction line up at `iteration == max_iters` (spec §4.3, §6.5).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_controller_agent_payload.py`:

```python
import pytest

from agentd.chat.controller_prompts import build_controller_step_payload

_TOOLS = [{"name": "search_code"}, {"name": "query_graph"}]
_BANNED = ("submit_changes", "propose_mode", "Plan Mode", "clarify", "SKILL CHECK")


def _instruction(**ctx) -> str:
    base = {"workspace_path": "/w", "goal": "g", "max_iters": 10}
    return str(build_controller_step_payload({**base, **ctx}, [{"role": "user", "content": "x"}],
                                             _TOOLS, phase="AGENT")["instruction"])


def test_entry_hint_grounds_first_and_names_report() -> None:
    text = _instruction(iteration=0)
    assert text.startswith("Phase=AGENT. This is your FIRST action.")
    assert "search_code/query_graph" in text and "type='report'" in text


def test_penultimate_and_final_hints_line_up_with_the_budget() -> None:
    assert "One step left" in _instruction(iteration=9)
    final = _instruction(iteration=10)
    assert "BUDGET REACHED" in final and "Unfinished" in final


def test_mid_hint_for_editing_and_readonly_children() -> None:
    assert "PATCH FAILED" in _instruction(iteration=3)
    readonly = _instruction(iteration=3, agent_readonly=True)
    assert "edit" not in readonly.replace("Phase=AGENT.", "") and "report" in readonly


def test_agent_reconcile_checkpoint_uses_report_wording() -> None:
    text = _instruction(iteration=3, todo_status="[~] a", pending_reconcile_files=["a.py"],
                        reconcile_item={"title": "a", "status": "in_progress"})
    assert text.startswith("Phase=AGENT. CHECKPOINT — you just edited a.py.")
    assert "then continue or report" in text


@pytest.mark.parametrize("iteration", [0, 3, 9, 10])
def test_agent_instructions_never_name_parent_actions(iteration: int) -> None:
    text = _instruction(iteration=iteration, todo_status="[~] a", pending_reconcile_files=["a.py"],
                        reconcile_item={"title": "a", "status": "in_progress"})
    assert not any(b in text for b in _BANNED), text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_controller_agent_payload.py > /tmp/t8.txt 2>&1; echo exit=$?; tail -3 /tmp/t8.txt`
Expected: `exit=1` — the AGENT phase currently falls into the `else:  # PLAN` branch, so `"You are in Plan Mode"` appears.

- [ ] **Step 3: Add the AGENT branch**

In `build_controller_step_payload`, insert this branch **immediately before** the existing `    else:  # PLAN` line (after the ACTIVE branch closes). Leave `else:  # PLAN` itself unchanged — turning it into `elif phase == "PLAN":` would leave `hint` unbound (an `UnboundLocalError`) for any other phase value, where today it falls back to the PLAN hints:

```python
    elif phase == "AGENT":
        # A dispatched sub-agent (spec §4.3). No skill check (it has no per-iteration
        # triage), no Plan Mode, and `report` is the only terminal. The final hint fires on
        # the same iteration the loop narrows the schema to ["report"] (spec §6.5).
        checkpoint = ""
        reconcile_files = plan_context.get("pending_reconcile_files")
        reconcile_item = plan_context.get("reconcile_item")
        if (reconcile_files and plan_context.get("todo_status")
                and isinstance(reconcile_item, dict) and reconcile_item.get("title")):
            files_str = (", ".join(str(f) for f in reconcile_files)
                         if isinstance(reconcile_files, list) else str(reconcile_files))
            title = reconcile_item.get("title")
            checkpoint = (
                f"CHECKPOINT — you just edited {files_str}. Your current todo item is '{title}'. "
                "Did this edit complete it? If yes, write_todos marking it 'done' (cite this edit "
                "in 'note'), then continue or report. If not, continue it. ")
        if iteration == 0:
            hint = (
                "This is your FIRST action. Your task is in 'goal'. Ground first: LOCATE the code "
                f"it names (search_code{_graph}) and READ it (read_file) before you change or "
                "claim anything. Stay inside the files your task assigns. Finish with "
                "type='report' — the only thing your dispatcher receives.")
        elif iteration >= max_iters:
            hint = (
                "⚠ BUDGET REACHED: emit type='report' NOW with everything you found and changed, "
                "and list what is unfinished under 'Unfinished'.")
        elif iteration == max_iters - 1:
            hint = checkpoint + "One step left: finish your current action, then report."
        elif plan_context.get("agent_readonly"):
            hint = checkpoint + (
                "Reflect on what you have read so far. Continue exploring with tool_call, or emit "
                "type='report' once you have what your task asks for. type='progress' posts a "
                "short status line without ending your task.")
        else:
            hint = checkpoint + (
                "Reflect on your last result: did an edit apply ('applied+promoted') or fail "
                "('PATCH FAILED: …' — re-read the lines and re-emit ONE corrected op)? Then "
                "continue with tool_call/edit, or emit type='report' when your task is done. "
                "type='progress' posts a short status line without ending your task.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_controller_agent_payload.py tests/test_controller_payload.py tests/test_prompt_goldens.py > /tmp/t8.txt 2>&1; echo exit=$?; tail -3 /tmp/t8.txt`
Expected: `exit=0` (the PLAN/ACTIVE payload goldens are unchanged).

- [ ] **Step 5: Commit**

```bash
git add agentd/chat/controller_prompts.py tests/test_controller_agent_payload.py
git commit -m "feat(controller): AGENT per-iteration payload hints"
```

---

### Task 9: `ReasoningEngine` seams — allowed types, render context, persona, `with_model`

**Files:**
- Modify: `agentd/reasoning/contracts.py`, `agentd/reasoning/engine.py`, `agentd/orchestrator/scripted_engine.py`, `agentd/chat/controller_loop.py`
- Test: `tests/test_engine_agent_seams.py`

**Interfaces:**
- Consumes: `accepts_kwarg` (Task 5), `controller_response_schema(allowed_types=...)` (Task 7), `format_controller_system_prompt(render_ctx=, persona=)` (Task 4).
- Produces:
  - `ReasoningEngine.create_controller_step(..., allowed_types: Sequence[str] | None = None, render_ctx: RenderContext | None = None, persona: str | None = None)` and `ReasoningEngine.with_model(model: str) -> ReasoningEngine`.
  - `ScriptedReasoningEngine(..., agent_scripts: dict[str, list[dict[str, object]]] | None = None)` — a child whose `render_ctx.agent_label` is a key replays that list (own index), otherwise the shared script.
  - `ControllerLoop` passes `allowed_types` and `render_ctx` to the engine **only when the engine's `create_controller_step` declares them** (`accepts_kwarg`), so fixed-signature fakes keep working (Review Focus 4).
- Deviation from spec §4.4 (noted for reviewers): the spec's `agent: AgentContext | None` parameter is realized as `render_ctx` + `persona`, because `AgentContext` belongs to Phase 2; Phase 2 derives both from it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine_agent_seams.py`:

```python
import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.prompting.tagged import RenderContext
from agentd.reasoning.engine import DefaultReasoningEngine
from agentd.tools.sources import AggregatingToolRegistry

CHILD = RenderContext(audience="child", permission="default",
                      base_types=frozenset({"tool_call", "edit", "progress", "report"}),
                      agent_id="a1", agent_label="impl")


class _CapturingTransport:
    supports_oneof_grammar = False

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"type": "answer", "thought": "t", "answer": "hi"}


@pytest.mark.asyncio
async def test_engine_uses_allowed_types_and_renders_for_the_child() -> None:
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    await engine.create_controller_step(
        plan_context={"goal": "g", "workspace_path": "/w"}, history=[], tool_definitions=[],
        phase="AGENT", allowed_types=["report"], render_ctx=CHILD, persona="Review code.")
    call = transport.calls[0]
    assert call["schema"]["properties"]["type"]["enum"] == ["report"]
    assert call["system_instructions"].startswith("You are a sub-agent")
    assert "AGENT INSTRUCTIONS" in call["system_instructions"]


@pytest.mark.asyncio
async def test_main_active_with_task_subsystem_now_offers_propose_mode() -> None:
    # Latent bug (spec §4.1): _allowed_action_types appended propose_mode but the schema never did.
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    types = ["tool_call", "answer", "clarify", "edit", "submit_changes", "progress", "propose_mode"]
    await engine.create_controller_step(
        plan_context={"goal": "g", "workspace_path": "/w"}, history=[], tool_definitions=[],
        phase="ACTIVE", allowed_types=types)
    assert "propose_mode" in transport.calls[0]["schema"]["properties"]["type"]["enum"]


def test_with_model_shares_the_transport() -> None:
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    other = engine.with_model("m2")
    assert other is not engine and other._transport is transport and other._model == "m2"
    scripted = ScriptedReasoningEngine(None, [])
    assert scripted.with_model("x") is scripted


@pytest.mark.asyncio
async def test_scripted_agent_scripts_are_per_label() -> None:
    eng = ScriptedReasoningEngine(
        None, [], controller_step_responses=[{"type": "answer", "thought": "t", "answer": "main"}],
        agent_scripts={"impl": [{"type": "report", "thought": "t", "summary": "one"},
                                {"type": "report", "thought": "t", "summary": "two"}]})
    args = dict(plan_context={}, history=[], tool_definitions=[], phase="AGENT")
    assert (await eng.create_controller_step(**args, render_ctx=CHILD))["summary"] == "one"
    assert (await eng.create_controller_step(**args, render_ctx=CHILD))["summary"] == "two"
    assert (await eng.create_controller_step(**args))["answer"] == "main"


@pytest.mark.asyncio
async def test_loop_passes_new_kwargs_only_to_engines_that_declare_them() -> None:
    seen: dict[str, object] = {}

    class _Declares:
        async def create_controller_step(self, plan_context, history, tool_definitions, *, phase,
                                         allowed_types=None, render_ctx=None, **_kw):
            seen["allowed_types"], seen["render_ctx"] = allowed_types, render_ctx
            return {"type": "answer", "thought": "t", "answer": "ok"}

    class _Fixed:  # like the existing test fakes: no new params, no **kwargs
        async def create_controller_step(self, plan_context, history, tool_definitions, *, phase,
                                         on_thinking=None, on_retry=None, on_progress=None,
                                         on_salvage=None, on_usage=None, unconstrained=False):
            return {"type": "answer", "thought": "t", "answer": "ok"}

    for engine in (_Declares(), _Fixed()):
        loop = ControllerLoop(engine, AggregatingToolRegistry([]), EventBroadcaster(),
                              channel_id="c", phase_sm=ControllerPhaseSM())
        out = await loop.run({"goal": "g", "workspace_path": "/w"}, max_iters=3)
        assert out.kind == "answer"
    assert seen["allowed_types"] == [
        "tool_call", "answer", "clarify", "edit", "submit_changes", "progress"]
    assert seen["render_ctx"] == RenderContext.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_agent_seams.py > /tmp/t9.txt 2>&1; echo exit=$?; tail -3 /tmp/t9.txt`
Expected: `exit=1` — `TypeError: … unexpected keyword argument 'allowed_types'`.

- [ ] **Step 3: Extend the protocol**

In `agentd/reasoning/contracts.py`, add imports `from collections.abc import Sequence` and, under `if TYPE_CHECKING:`, `from agentd.prompting.tagged import RenderContext`. Replace the `create_controller_step` stub's parameter list with:

```python
    async def create_controller_step(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_definitions: list[dict[str, object]],
        *,
        phase: str,
        on_thinking: Callable[[str], None] | None = None,
        on_retry: Callable[[int, int, str, str], None] | None = None,
        allowed_types: Sequence[str] | None = None,
        render_ctx: RenderContext | None = None,
        persona: str | None = None,
    ) -> dict[str, object]:
```

Append to its docstring: `allowed_types narrows the response schema's variants (the loop's per-iteration set); render_ctx/persona render the system prompt for a sub-agent (None = the main agent, byte-identical).` Then add a new stub after it:

```python
    def with_model(self, model: str) -> ReasoningEngine:
        """An engine for `model` sharing this engine's transport and loaders (spec §5.4)."""
        ...
```

- [ ] **Step 4: Implement in `DefaultReasoningEngine`**

In `agentd/reasoning/engine.py`, add `from collections.abc import Sequence` to the imports and, under `if TYPE_CHECKING:` (add the block if absent), `from agentd.prompting.tagged import RenderContext`. Add to `create_controller_step`'s keyword parameters (after `unconstrained: bool = False,`):

```python
        allowed_types: Sequence[str] | None = None,
        render_ctx: RenderContext | None = None,
        persona: str | None = None,
```

Replace the skills-catalog guard `if self._skill_catalog_loader is not None:` with:

```python
        # A child only sees the catalog when read_skill survived its tool filter (spec §4.2).
        wants_catalog = render_ctx is None or render_ctx.is_main or "read_skill" in render_ctx.tools
        if self._skill_catalog_loader is not None and wants_catalog:
```

Change the `format_controller_system_prompt(...)` call to:

```python
        system_instructions = format_controller_system_prompt(
            tool_definitions, project_instructions=instructions, skills_catalog=skills_catalog,
            render_ctx=render_ctx, persona=persona,
        )
```

and the schema line to:

```python
        schema = controller_response_schema(
            phase=phase, allowed_types=allowed_types, tight=tight, anyof=anyof,
            all_fields_required=all_fields_required)
```

Add the method to `DefaultReasoningEngine` (after `set_provider`):

```python
    def with_model(self, model: str) -> DefaultReasoningEngine:
        """Same transport and loaders, different model id (spec §5.4). `inherit` callers
        reuse `self` instead of calling this."""
        return DefaultReasoningEngine(
            model=model, transport=self._transport,
            project_instructions_loader=self._project_instructions_loader,
            skill_catalog_loader=self._skill_catalog_loader)
```

- [ ] **Step 5: Implement in `ScriptedReasoningEngine`**

In `agentd/orchestrator/scripted_engine.py`, add `agent_scripts: dict[str, list[dict[str, object]]] | None = None,` as the last `__init__` parameter and in the body:

```python
        # Per-agent scripts keyed by agent label: concurrent children can't share one index
        # without making tests order-dependent (spec §4.4).
        self._agent_scripts = {label: list(s) for label, s in (agent_scripts or {}).items()}
        self._agent_indexes: dict[str, int] = {}
```

Add `allowed_types: object = None, render_ctx: object = None, persona: object = None,` to `create_controller_step`'s keyword parameters and, as its first statements after the `_ = (...)` line:

```python
        label = getattr(render_ctx, "agent_label", "") if render_ctx is not None else ""
        if label and label in self._agent_scripts:
            script = self._agent_scripts[label]
            index = self._agent_indexes.get(label, 0)
            self._agent_indexes[label] = index + 1
            return script[min(index, len(script) - 1)]
```

Add the method:

```python
    def with_model(self, model: str) -> ScriptedReasoningEngine:
        _ = model
        return self
```

- [ ] **Step 6: Pass the kwargs from the loop when the engine declares them**

In `agentd/chat/controller_loop.py`, change the `react_common` import to also import `accepts_kwarg`. In `_iterate`, replace the `resp = await self._reasoning.create_controller_step(...)` call with:

```python
                step_fn = self._reasoning.create_controller_step
                # New keywords only for engines that declare them — the same ask-the-callee
                # rule as engine._accepts, so fixed-signature fakes keep working.
                seam_kwargs: dict[str, object] = {}
                if accepts_kwarg(step_fn, "allowed_types"):
                    seam_kwargs["allowed_types"] = self._allowed_action_types()
                if accepts_kwarg(step_fn, "render_ctx"):
                    seam_kwargs["render_ctx"] = self._render_ctx
                resp = await step_fn(
                    plan_context=plan_context, history=history,
                    tool_definitions=tool_defs, phase=self._sm.phase,
                    on_thinking=_on_thinking, on_retry=_on_retry,
                    on_progress=_on_progress, on_salvage=_on_salvage,
                    on_usage=_on_usage,
                    unconstrained=retry_unconstrained,
                    **seam_kwargs,
                )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine_agent_seams.py tests/test_create_controller_step.py tests/test_prompt_goldens.py > /tmp/t9.txt 2>&1; echo exit=$?; tail -3 /tmp/t9.txt`
Expected: `exit=0`.

- [ ] **Step 8: Run the controller suites (fixed-signature fakes)**

Run: `.venv/bin/python -m pytest tests/ -k "controller or skills or mentioned_files or memory_controller or clarify" > /tmp/t9b.txt 2>&1; echo exit=$?; tail -3 /tmp/t9b.txt`
Expected: `exit=0`.

- [ ] **Step 9: Commit**

```bash
git add agentd/reasoning/contracts.py agentd/reasoning/engine.py agentd/orchestrator/scripted_engine.py agentd/chat/controller_loop.py tests/test_engine_agent_seams.py
git commit -m "feat(reasoning): allowed_types/render_ctx/persona seams and with_model"
```

---

### Task 10: Structural leak lint and per-permission rendered leak tests

**Files:**
- Test: `tests/test_prompt_leak_lint.py`

**Interfaces:**
- Consumes: every tagged template from Tasks 3–7.
- Produces: the CI guard of spec §4.7.5 and the per-permission rendered checks of spec §15.

- [ ] **Step 1: Write the tests**

Create `tests/test_prompt_leak_lint.py`:

```python
"""Spec §4.7.5 (source-level lint) + §15 (rendered, per permission) prompt-leak guards."""
import re
from pathlib import Path

import pytest

from agentd.chat import controller_loop as cl
from agentd.chat import controller_prompts as cp
from agentd.chat import todo_source
from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.mcp import tool_source as mcp_source
from agentd.prompting.tagged import RenderContext, render_prompt
from agentd.reasoning import react_common
from agentd.skills.models import SkillManifest
from agentd.tools import registry
from agentd.tools.registry import ToolRegistry

TEMPLATES = {
    **{n: getattr(cp, n) for n in (
        "CONTROLLER_SYSTEM_PROMPT", "_PROPOSE_MODE_MODES_ENABLED", "_PROPOSE_MODE_MODES_DISABLED",
        "_MEMORY_BLOCK", "_INSTRUCTIONS_BLOCK_TEMPLATE", "_MCP_BLOCK", "_SESSIONS_BLOCK",
        "_SKILLS_BLOCK_HEADER", "_AGENT_ROLE_BLOCK")},
    "reserved": cl._RESERVED_TOOL_NAME_TEMPLATE,
    "progress_repeat": cl._PROGRESS_REPEAT_TEMPLATE,
    "progress_dedup": cl._PROGRESS_DEDUP_TEMPLATE,
    "empty_edit": cl._EMPTY_EDIT_REDIRECT_TEMPLATE,
    "malformed": react_common._MALFORMED_TEMPLATE,
    "run_command": registry._RUN_COMMAND_DESCRIPTION,
    "write_todos": todo_source._WRITE_TODOS_DESCRIPTION,
    "mcp_rejected": mcp_source._MCP_REJECTED_TEMPLATE,
}
# Main-only markers (spec §4.7.5). `clarify` only in identifier forms.
_MARKERS = [r"submit_changes", r"propose_mode", r"Plan Mode", r"retrieval seed",
            r'"type":"answer"', r"type='answer'", r"ask what they want", r"or ask\b",
            r"type='clarify'", r'"clarify"', r"`clarify`", r"clarify variant", r"/clarify/"]
_NEVER_CHILD = re.compile(r"^(main|type:(answer|clarify|propose_mode|submit_changes))$")
_MARKER_RE = re.compile(r"<<(/?)([a-z_]+(?::[A-Za-z0-9_]+)?)>>")


def _inside_a_marker(template: str, pos: int) -> bool:
    # A tag's own name (e.g. <<type:propose_mode>>) is not prompt wording.
    return any(m.start() <= pos < m.end() for m in _MARKER_RE.finditer(template))


def _enclosing_tags(template: str, pos: int) -> list[str]:
    stack: list[str] = []
    for m in _MARKER_RE.finditer(template[:pos]):
        if m.group(1):
            stack.pop()
        else:
            stack.append(m.group(2))
    return stack


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_main_only_wording_is_inside_a_never_child_region(name: str) -> None:
    template = TEMPLATES[name]
    offenders = []
    for marker in _MARKERS:
        for m in re.finditer(marker, template):
            if _inside_a_marker(template, m.start()):
                continue
            if not any(_NEVER_CHILD.match(tag) for tag in _enclosing_tags(template, m.start())):
                line = template.count("\n", 0, m.start()) + 1
                offenders.append(f"{name}:{line}: {m.group(0)!r}")
    assert not offenders, "untagged main-only wording:\n" + "\n".join(offenders)


_EDIT = frozenset({"tool_call", "edit", "progress", "report"})
_READ = frozenset({"tool_call", "progress", "report"})
_ALL_TOOLS = frozenset({"run_command", "write_todos", "recall", "read_skill", "read_file"})
CHILDREN = {
    "default": RenderContext(audience="child", permission="default", tools=_ALL_TOOLS,
                             base_types=_EDIT, agent_id="a", agent_label="a"),
    "acceptEdits": RenderContext(audience="child", permission="acceptEdits", tools=_ALL_TOOLS,
                                 base_types=_EDIT, agent_id="b", agent_label="b"),
    "dontAsk": RenderContext(audience="child", permission="dontAsk", tools=_ALL_TOOLS,
                             base_types=_EDIT, agent_id="c", agent_label="c"),
    "plan": RenderContext(audience="child", permission="plan",
                          tools=frozenset({"read_file", "recall"}),
                          base_types=_READ, agent_id="d", agent_label="d"),
}
_RENDERED_BANNED = ["submit_changes", '"type":"answer"', "type='answer'", "type='clarify'",
                    "propose_mode", "Plan Mode", "retrieval seed", "ask what they want", "or ask."]
_MCP = {"name": "mcp__gh__x", "description": "d", "parameters": {"type": "object"}}
_CATALOG = [SkillManifest(name="s", description="d", body_path=Path("/x"), dir=Path("/x"))]


@pytest.mark.parametrize("perm", sorted(CHILDREN))
def test_everything_a_child_is_shown_is_leak_free(perm: str) -> None:
    ctx = CHILDREN[perm]
    shown = [
        format_controller_system_prompt([_MCP], task_subsystem_enabled=True, memory_enabled=True,
                                        project_instructions="x", skills_catalog=_CATALOG,
                                        render_ctx=ctx, persona="p"),
        *(d.description
          for d in ToolRegistry(Path("/w"), Path("/w"), render_ctx=ctx).definitions()),
        todo_source._write_todos_def(ctx).description,
        cl._reserved_tool_name_correction({"tool": "edit"}, "tool_call", ctx) or "",
        cl._progress_repeat_correction({"note": "n"}, "progress", True, ctx) or "",
        cl._progress_dedup_correction({"note": "n"}, "progress", {"n"}, ctx) or "",
        cl._empty_edit_redirect(ctx),
        react_common.malformed_correction(ctx, sorted(ctx.base_types)),
        render_prompt(mcp_source._MCP_REJECTED_TEMPLATE, ctx),
    ]
    leaks = [(b, s[:120]) for s in shown for b in _RENDERED_BANNED if b in s]
    assert not leaks, leaks


@pytest.mark.parametrize("perm", sorted(CHILDREN))
@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_child_renders_add_no_extra_blank_lines(perm: str, name: str) -> None:
    template = TEMPLATES[name]
    child = render_prompt(template, CHILDREN[perm])
    main = render_prompt(template, RenderContext.main())
    assert child.count("\n\n\n") <= main.count("\n\n\n")
```

(`"rejected by user"` is deliberately **not** banned here: the command and MCP denial texts still say it until Plan 1B's `ApprovalOutcome` (`denied_by`) makes them truthful, and 1B adds that ban to this test. The edit-rejection string `"REJECTED by user: …"` is true for a gated child edit and stays allowed — spec §4.6.4.)

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_prompt_leak_lint.py > /tmp/t10.txt 2>&1; echo exit=$?; tail -3 /tmp/t10.txt`
Expected: `exit=0`. If the lint names an offender, the fix is to tag that wording in its template (a `main` region plus a child alternative where needed) — never to remove the marker from `_MARKERS`.

- [ ] **Step 3: Prove the lint catches a regression**

Temporarily append `" then submit_changes."` inside an **untagged** sentence of `_SESSIONS_BLOCK` in `agentd/chat/controller_prompts.py`, run `.venv/bin/python -m pytest tests/test_prompt_leak_lint.py > /tmp/t10b.txt 2>&1; echo exit=$?` — Expected: `exit=1` naming `_SESSIONS_BLOCK`. Revert the edit (`git checkout agentd/chat/controller_prompts.py`) and re-run — Expected: `exit=0`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_prompt_leak_lint.py
git commit -m "test(prompts): structural leak lint and per-permission child leak checks"
```

---

### Task 11: The two deliberate parent fixes (golden re-capture)

Spec §4.7.4 **commit 3**. The only task allowed to change main-agent text, and it changes exactly two strings.

**Files:**
- Modify: `agentd/tools/registry.py` (`_RUN_COMMAND_DESCRIPTION` main region), `agentd/chat/edit_session.py:58`
- Modify (re-captured): `tests/goldens/controller_prompt_main.json`

- [ ] **Step 1: Make the two edits**

In `agentd/tools/registry.py`, in `_RUN_COMMAND_DESCRIPTION`, change `<<main>>inside the shadow workspace<</main>>` to `<<main>>in the workspace<</main>>` (true on both the controller path and the task `ToolLoop`, where the CWD is the shadow — spec §4.6.3).

In `agentd/chat/edit_session.py`, change `"no patch_ops were emitted — emit at least one op or submit_changes."` to `"no patch_ops were emitted — emit at least one op."`.

- [ ] **Step 2: Watch the golden test fail for exactly those reasons**

Run: `.venv/bin/python -m pytest tests/test_prompt_goldens.py > /tmp/t11.txt 2>&1; echo exit=$?; grep "changed for" /tmp/t11.txt`
Expected: `exit=1`, listing only `history/edit_session_empty_ops`, `tools/builtin/explore`, `tools/builtin/verify`, and the `system/*` keys (the `run_command` description rides `tools_json` into every system prompt).

- [ ] **Step 3: Re-capture and prove the diff is exactly the two fixes**

```bash
cp tests/goldens/controller_prompt_main.json /tmp/golden_before.json
.venv/bin/python -m tests.prompt_goldens
.venv/bin/python - <<'EOF'
import json
old = json.load(open("/tmp/golden_before.json"))
new = json.load(open("tests/goldens/controller_prompt_main.json"))
# No em dash in the patterns: json.dumps (tools_json, the tool goldens) escapes it as \u2014.
fix = [("inside the shadow workspace", "in the workspace"),
       ("emit at least one op or submit_changes.", "emit at least one op.")]
assert old.keys() == new.keys()
for key in old:
    expected = old[key]
    for a, b in fix:
        expected = expected.replace(a, b)
    assert new[key] == expected, key
print("golden diff is exactly the two deliberate fixes")
EOF
```

Expected: `golden diff is exactly the two deliberate fixes`.

- [ ] **Step 4: Run the full backend suite and compare failure sets**

Run: `.venv/bin/python -m pytest tests/ --color=no > /tmp/full.txt 2>&1; echo exit=$?; tail -1 /tmp/full.txt; grep '^FAILED' /tmp/full.txt`
Expected: the only `FAILED` line is the pre-existing `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies` (see Global Constraints).

- [ ] **Step 5: Lint and types — new files clean, touched files at baseline**

New files must be fully clean:

Run: `.venv/bin/ruff check agentd/prompting tests/prompt_goldens.py tests/test_prompt_goldens.py tests/test_tagged_prompt.py tests/test_controller_prompts_tagged.py tests/test_controller_child_prompt.py tests/test_controller_child_corrections.py tests/test_child_tool_texts.py tests/test_controller_agent_schema.py tests/test_controller_agent_payload.py tests/test_engine_agent_seams.py tests/test_prompt_leak_lint.py; echo exit=$?`
Expected: `All checks passed!`, `exit=0`.

Touched files must add no findings. These seven exist on `main` before this plan and are not yours (verified 2026-09-30): `controller_loop.py` ×4 E501, `edit_session.py` ×1 I001, `scripted_engine.py` ×1 E501, `contracts.py` ×1 E501.

Run:
```bash
.venv/bin/ruff check agentd/chat/controller_prompts.py agentd/chat/controller_loop.py agentd/chat/controller_phase.py agentd/reasoning/contracts.py agentd/reasoning/engine.py agentd/reasoning/react_common.py agentd/tools/registry.py agentd/tools/sources.py agentd/chat/todo_source.py agentd/mcp/tool_source.py agentd/chat/edit_session.py agentd/orchestrator/scripted_engine.py --output-format json | .venv/bin/python -c "import collections, json, sys; c = collections.Counter((d['filename'].split('agentd-py/')[1], d['code']) for d in json.load(sys.stdin)); print(sorted(c.items()))"
```
Expected exactly: `[(('agentd/chat/controller_loop.py', 'E501'), 4), (('agentd/chat/edit_session.py', 'I001'), 1), (('agentd/orchestrator/scripted_engine.py', 'E501'), 1), (('agentd/reasoning/contracts.py', 'E501'), 1)]`.

Run: `.venv/bin/mypy agentd/prompting > /tmp/mypy.txt 2>&1; echo exit=$?; tail -1 /tmp/mypy.txt`
Expected: `exit=0`, `Success: no issues found in 2 source files`. (Repo-wide mypy has a known provider-file baseline; guard only new findings in the files you touched.)

- [ ] **Step 6: Commit**

```bash
git add agentd/tools/registry.py agentd/chat/edit_session.py tests/goldens/controller_prompt_main.json
git commit -m "fix(prompts): run_command says 'in the workspace'; neutral empty-ops message"
```

---

## After this plan

- **Plan 1B (gates & approvals):** multi-gate `pending_controller_gates` across the full stack, `ApprovalOutcome` (`denied_by`) with truthful denial wording (extends Task 10's banned list with command denials), `StaleWriteError`/`STALE_READ`, and the `TurnEditSession` re-seed fix — spec §4.5, §4.6.4, §7.4, §7.5.
- **Plans 2–5** follow spec §16, each written after the previous one lands so it builds on real code.
