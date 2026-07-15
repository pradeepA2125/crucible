import asyncio
from pathlib import Path

from agentd.instructions.loader import ProjectInstructionsLoader
from agentd.reasoning.engine import DefaultReasoningEngine


class _CapturingTransport:
    supports_oneof_grammar = False

    def __init__(self) -> None:
        self.system_instructions = ""

    async def generate_json(
        self,
        *,
        model,
        schema_name,
        schema,
        system_instructions,
        user_payload,
        on_thinking=None,
        on_retry=None,
    ):
        self.system_instructions = system_instructions
        return {"type": "answer", "thought": "", "message": "ok"}


def _run(coro):
    return asyncio.run(coro)


def test_engine_injects_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Prefix replies with FOX.", encoding="utf-8")
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(
        model="m",
        transport=transport,
        project_instructions_loader=ProjectInstructionsLoader(tmp_path),
    )
    _run(
        engine.create_controller_step(
            plan_context={"goal": "hi", "workspace_path": str(tmp_path)},
            history=[],
            tool_definitions=[],
            phase="DECIDE",
        )
    )
    assert "Prefix replies with FOX." in transport.system_instructions


def test_engine_without_loader_has_no_block(tmp_path: Path) -> None:
    transport = _CapturingTransport()
    engine = DefaultReasoningEngine(model="m", transport=transport)
    _run(
        engine.create_controller_step(
            plan_context={"goal": "hi", "workspace_path": str(tmp_path)},
            history=[],
            tool_definitions=[],
            phase="DECIDE",
        )
    )
    assert "PROJECT INSTRUCTIONS" not in transport.system_instructions
