from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentd.domain.models import Diagnostic, TaskRecord
from agentd.reasoning.engine import DefaultReasoningEngine


class FakeTransport:
    def __init__(
        self,
        *,
        json_outputs: list[dict[str, object]] | None = None,
        text_outputs: list[str] | None = None,
    ) -> None:
        self._json_outputs = json_outputs or []
        self._text_outputs = text_outputs or []
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self,
        *,
        model: str,
        schema_name: str,
        schema: dict[str, object],
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append(
            {
                "kind": "json",
                "model": model,
                "schema_name": schema_name,
                "schema": schema,
                "system_instructions": system_instructions,
                "user_payload": user_payload,
            }
        )
        return self._json_outputs.pop(0)

    async def generate_text(
        self,
        *,
        model: str,
        system_instructions: str,
        user_payload: dict[str, object],
    ) -> str:
        self.calls.append(
            {
                "kind": "text",
                "model": model,
                "system_instructions": system_instructions,
                "user_payload": user_payload,
            }
        )
        return self._text_outputs.pop(0)


@pytest.mark.asyncio
async def test_reasoning_engine_builds_plan_and_patch_with_transport(tmp_path: Path) -> None:
    transport = FakeTransport(
        json_outputs=[
            {
                "analysis": "Plan",
                "steps": [
                    {
                        "id": "S1",
                        "goal": "Edit",
                        "targets": [{"path": "a.py", "intent": "existing"}],
                        "risk": "low",
                    }
                ],
                "expected_files": ["a.py"],
                "stop_conditions": ["tests pass"],
            },
            {
                "candidates": [
                    {
                        "candidate_id": "c1",
                        "patch_ops": [
                            {
                                "op": "create_file",
                                "file": "a.py",
                                "content": "print('hi')",
                                "reason": "add file",
                            }
                        ],
                    }
                ],
            },
        ]
    )
    engine = DefaultReasoningEngine(model="gpt-5", transport=transport)
    task = TaskRecord(task_id="t1", goal="goal", workspace_path=str(tmp_path))
    retrieval_context = {"related_files": ["a.py"], "related_symbols": ["build"]}
    diagnostics = [Diagnostic(source="validator", message="warn", level="warning")]

    plan = await engine.create_plan(task, str(tmp_path), retrieval_context)
    patch = await engine.create_patch(task, str(tmp_path), diagnostics, retrieval_context)

    assert plan["steps"][0]["id"] == "S1"
    assert patch["candidates"][0]["patch_ops"][0]["op"] == "create_file"
    assert len(transport.calls) == 2

    plan_call = transport.calls[0]
    patch_call = transport.calls[1]
    assert plan_call["schema_name"] == "plan_document"
    assert patch_call["schema_name"] == "patch_document_v2"
    assert plan_call["user_payload"]["retrieval_context"] == retrieval_context
    assert patch_call["user_payload"]["retrieval_context"] == retrieval_context
    assert patch_call["user_payload"]["diagnostics"][0]["source"] == "validator"
    assert plan_call["user_payload"]["constraints"]["max_files_touched"] == 20
    assert patch_call["user_payload"]["intent"]["execution_mode"] == "step_scoped_bounded_patching"
    assert "replace_node" in patch_call["user_payload"]["patch_op_catalog"]
    assert "deterministic planning engine" in plan_call["system_instructions"]
    assert "deterministic code patch generation engine" in patch_call["system_instructions"]


@pytest.mark.asyncio
async def test_reasoning_engine_rejects_schema_mismatch() -> None:
    transport = FakeTransport(json_outputs=[{"analysis": "incomplete"}])
    engine = DefaultReasoningEngine(model="gpt-5", transport=transport)
    task = TaskRecord(task_id="t1", goal="goal", workspace_path=".")

    with pytest.raises(ValidationError):
        await engine.create_plan(task, ".", retrieval_context={})


@pytest.mark.asyncio
async def test_reasoning_engine_builds_markdown_plan_with_transport(tmp_path: Path) -> None:
    transport = FakeTransport(text_outputs=["# Plan\n\n- Update endpoint"])
    engine = DefaultReasoningEngine(model="gpt-5", transport=transport)
    task = TaskRecord(task_id="t1", goal="goal", workspace_path=str(tmp_path), plan_markdown="# Existing")

    markdown = await engine.create_markdown_plan(
        task,
        str(tmp_path),
        retrieval_context={"related_files": ["a.py"], "plan_feedback": "tighten scope"},
    )

    assert markdown == "# Plan\n\n- Update endpoint"
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["kind"] == "text"
    assert call["user_payload"]["plan_markdown"] == "# Existing"
    assert call["user_payload"]["plan_feedback"] == "tighten scope"


@pytest.mark.asyncio
async def test_reasoning_engine_builds_plan_critiques_with_transport(tmp_path: Path) -> None:
    transport = FakeTransport(
        json_outputs=[
            {
                "verdict": "revise",
                "issues": [
                    {
                        "code": "invented_file",
                        "message": "tasks.py is not a real target",
                        "file": "agentd/api/tasks.py",
                    }
                ],
            },
            {
                "verdict": "pass",
                "issues": [],
            },
        ]
    )
    engine = DefaultReasoningEngine(model="gpt-5", transport=transport)
    task = TaskRecord(
        task_id="t1",
        goal="goal",
        workspace_path=str(tmp_path),
        plan_markdown="# Approved\n\n- Update `src/routes.py`",
    )
    retrieval_context = {
        "planner_evidence": {
            "workspace_files_index": ["src/routes.py"],
            "evidence_files": [{"path": "src/routes.py", "excerpt": "def route():\n    pass"}],
            "evidence_symbols": [],
            "evidence_routes_models_storage": {"routes": [], "models": [], "storage": []},
            "diagnostics_excerpt": [],
            "confidence_notes": [],
        },
        "plan_feedback": "use existing route",
    }

    markdown_critique = await engine.critique_markdown_plan(
        task,
        str(tmp_path),
        retrieval_context,
        "# Draft\n\n- Update `agentd/api/tasks.py`",
    )
    json_critique = await engine.critique_json_plan(
        task,
        str(tmp_path),
        retrieval_context,
        {
            "analysis": "x",
            "steps": [
                {
                    "id": "s1",
                    "goal": "x",
                    "targets": [{"path": "src/routes.py", "intent": "existing"}],
                    "risk": "low",
                }
            ],
            "expected_files": ["src/routes.py"],
            "stop_conditions": ["tests pass"],
        },
    )

    assert markdown_critique["verdict"] == "revise"
    assert markdown_critique["issues"][0]["code"] == "invented_file"
    assert json_critique["verdict"] == "pass"
    assert transport.calls[0]["schema_name"] == "markdown_plan_critique"
    assert transport.calls[1]["schema_name"] == "json_plan_critique"
