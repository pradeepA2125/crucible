"""Agents are told the sandbox is on, what it allows and how to ask past it."""

from __future__ import annotations

import dataclasses
import sys

import pytest

from agentd.chat.controller_prompts import (
    SANDBOX_NOTE,
    SANDBOX_UNAVAILABLE_NOTE,
    build_controller_step_payload,
)
from agentd.prompting.tagged import RenderContext, render_prompt
from agentd.sandbox.runtime import SandboxRuntime
from agentd.tools.registry import _RUN_COMMAND_DESCRIPTION, ToolRegistry


def _payload(sandbox: str | None) -> dict[str, object]:
    ctx: dict[str, object] = {"goal": "g", "iteration": 1, "max_iters": 10}
    if sandbox is not None:
        ctx["sandbox"] = sandbox
    return build_controller_step_payload(ctx, [], [], phase="ACTIVE")


def test_payload_tail_has_sandbox_note_when_on():
    payload = _payload("on")
    assert payload["sandbox_note"] == SANDBOX_NOTE
    keys = list(payload)
    assert keys.index("goal") < keys.index("sandbox_note") < keys.index("instruction")


def test_unavailable_note():
    assert _payload("unavailable")["sandbox_note"] == SANDBOX_UNAVAILABLE_NOTE


def test_no_note_when_off_or_unknown():
    assert "sandbox_note" not in _payload("off")
    assert "sandbox_note" not in _payload(None)


def test_note_names_the_two_escalations():
    assert "allow_hosts" in SANDBOX_NOTE and "outside_sandbox" in SANDBOX_NOTE


def test_description_mentions_sandbox_only_when_on():
    off = render_prompt(_RUN_COMMAND_DESCRIPTION, RenderContext.main())
    on = render_prompt(_RUN_COMMAND_DESCRIPTION,
                       dataclasses.replace(RenderContext.main(), sandbox="on"))
    assert "sandbox" not in off
    assert "outside_sandbox" in on and "allow_hosts" in on


def test_registry_without_runtime_keeps_todays_description(tmp_path):
    registry = ToolRegistry(tmp_path, tmp_path)
    run = next(d for d in registry.definitions() if d.name == "run_command")
    assert run.description == render_prompt(_RUN_COMMAND_DESCRIPTION, RenderContext.main())


@pytest.mark.skipif(sys.platform != "darwin", reason="needs a working sandbox")
@pytest.mark.asyncio
async def test_registry_description_follows_the_runtime(tmp_path):
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    registry = ToolRegistry(tmp_path, tmp_path, sandbox_runtime=runtime)
    run = next(d for d in registry.definitions() if d.name == "run_command")
    assert "outside_sandbox" in run.description
    runtime.enabled = False
    run = next(d for d in registry.definitions() if d.name == "run_command")
    assert "outside_sandbox" not in run.description
