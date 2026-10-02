"""The report action carries a status the dispatcher acts on (spec §3.3)."""
import pytest

from agentd.chat.controller_prompts import controller_response_schema
from agentd.subagents.runtime import IDLE_STATUSES, LIVE_STATUSES, TERMINAL_STATUSES


def test_status_sets() -> None:
    assert IDLE_STATUSES == {"completed", "awaiting_peer", "partial", "failed",
                             "failed_transient", "stopped"}
    assert LIVE_STATUSES == {"queued", "running", "waiting"}
    assert TERMINAL_STATUSES is IDLE_STATUSES


def test_flat_schema_offers_status_only_with_report() -> None:
    child = controller_response_schema(phase="AGENT", allowed_types=["tool_call", "report"])
    main = controller_response_schema(phase="ACTIVE")
    assert child["properties"]["status"] == {"type": "string", "enum": ["completed", "partial"]}
    assert "status" not in main["properties"]


def test_tight_schema_report_branch_has_status() -> None:
    schema = controller_response_schema(phase="AGENT", allowed_types=["report"], tight=True)
    [branch] = schema["oneOf"]
    assert branch["properties"]["status"]["enum"] == ["completed", "partial"]
    assert "status" not in branch["required"]


@pytest.mark.asyncio
async def test_loop_returns_the_model_status(tmp_path, monkeypatch) -> None:
    from tests.loop_harness import run_child_loop  # created in this task

    outcome = await run_child_loop(tmp_path, [
        {"type": "report", "thought": "t", "summary": "half done", "status": "partial"}])
    assert outcome.payload == {"status": "partial"}
    outcome = await run_child_loop(tmp_path, [
        {"type": "report", "thought": "t", "summary": "done"}])
    assert outcome.payload == {"status": "completed"}
    outcome = await run_child_loop(tmp_path, [
        {"type": "report", "thought": "t", "summary": "x", "status": "bogus"}])
    assert outcome.payload == {"status": "completed"}
