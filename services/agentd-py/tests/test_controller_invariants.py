"""Spec §9 invariant guards for the agentic chat controller.

Each test pins one invariant from the design spec; several are also exercised
incidentally elsewhere, but consolidated here as explicit regression guards.
"""
import inspect
from pathlib import Path

import pytest

from agentd.chat import controller as controller_mod
from agentd.chat import controller_loop as controller_loop_mod
from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.controller_prompts import (
    controller_response_schema,
    format_controller_system_prompt,
)
from agentd.chat.edit_session import TurnEditSession
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


def _reg(path):
    return AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=path, real_workspace_path=path)])


# Invariant 2 — never auto-enter a mutating mode.
def test_decide_phase_schema_forbids_edit():
    decide = controller_response_schema(phase="DECIDE")["properties"]["type"]["enum"]
    assert "edit" not in decide
    assert "submit_changes" not in decide
    edit = controller_response_schema(phase="EDIT")["properties"]["type"]["enum"]
    assert "edit" in edit


# Invariant 1 — cache-prefix immutability: tool defs serialize deterministically
# (sorted keys, stable order), no volatile content in the cached system prefix.
def test_system_prompt_tool_defs_serialize_deterministically():
    tools = [
        {"name": "read_file", "description": "d", "parameters": {"b": 1, "a": 2}},
        {"name": "search_code", "description": "d", "parameters": {}},
    ]
    a = format_controller_system_prompt(tools)
    b = format_controller_system_prompt(list(tools))
    assert a == b  # byte-identical across calls
    # sorted keys: "a" must serialize before "b" regardless of input dict order
    assert a.index('"a"') < a.index('"b"')


# Invariant 3 — reads always hit real in the chat path (no use_shadow_for_reads).
def test_controller_never_flips_reads_to_shadow():
    loop_src = inspect.getsource(controller_loop_mod)
    ctrl_src = inspect.getsource(controller_mod)
    assert "use_shadow_for_reads" not in loop_src
    assert "use_shadow_for_reads" not in ctrl_src


class _SpyEdit:
    """Records the apply/accept call order to prove no batching."""

    def __init__(self, inner: TurnEditSession):
        self._inner = inner
        self.calls: list[str] = []

    async def apply(self, ops):
        self.calls.append("apply")
        return await self._inner.apply(ops)

    async def accept(self):
        self.calls.append("accept")
        return await self._inner.accept()

    async def reject(self):
        self.calls.append("reject")
        return await self._inner.reject()

    async def close(self):
        return await self._inner.close()


# Invariant 4 — per-patch instant promote, no batching: each edit promotes before
# the next edit is processed.
@pytest.mark.asyncio
async def test_each_edit_promotes_before_next(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("a = 0\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    spy = _SpyEdit(TurnEditSession(
        turn_id="t", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"), patch_engine=PatchEngine()))
    loop = ControllerLoop(ScriptedReasoningEngine(None, [], controller_step_responses=[
        {"type": "edit", "thought": "1", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "a = 0", "replace": "a = 1", "reason": "r"}]},
        {"type": "edit", "thought": "2", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "a = 1", "replace": "a = 2", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "s"},
    ]), _reg(real), EventBroadcaster(), channel_id="c", phase_sm=sm, edit_session=spy)
    await loop.run({"goal": "g", "workspace_path": str(real)}, max_iters=8, auto_accept_edits=True)
    # Interleaved (apply, accept, apply, accept) — NOT batched (apply, apply, accept, accept).
    assert spy.calls == ["apply", "accept", "apply", "accept"]
    assert (real / "f.py").read_text() == "a = 2\n"


# Invariant 5 — shadow==real across a reject round that touched a DIFFERENT file than
# the next accepted edit (the subtle ACID case flagged in F review).
@pytest.mark.asyncio
async def test_shadow_equals_real_across_cross_file_reject(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "a.py").write_text("A = 1\n")
    (real / "b.py").write_text("B = 1\n")
    sm = ControllerPhaseSM()
    sm.enter_edit_mode()
    sess = TurnEditSession(
        turn_id="t", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"), patch_engine=PatchEngine())

    async def edit_cb(diff):
        # Reject the first edit (touches a.py), accept the second (touches b.py).
        return {"decision": "reject"} if any(d.path == "a.py" for d in diff) \
            else {"decision": "accept"}

    loop = ControllerLoop(ScriptedReasoningEngine(None, [], controller_step_responses=[
        {"type": "edit", "thought": "edit a", "patch_ops": [
            {"op": "search_replace", "file": "a.py",
             "search": "A = 1", "replace": "A = 99", "reason": "r"}]},
        {"type": "edit", "thought": "edit b", "patch_ops": [
            {"op": "search_replace", "file": "b.py",
             "search": "B = 1", "replace": "B = 2", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "s"},
    ]), _reg(real), EventBroadcaster(), channel_id="c", phase_sm=sm,
        edit_session=sess)
    await loop.run(
        {"goal": "g", "workspace_path": str(real)}, max_iters=8,
        auto_accept_edits=False, edit_decision_cb=edit_cb)
    # a.py rejected → real untouched; b.py accepted → real promoted.
    assert (real / "a.py").read_text() == "A = 1\n"
    assert (real / "b.py").read_text() == "B = 2\n"
