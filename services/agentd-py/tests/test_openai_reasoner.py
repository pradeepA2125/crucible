from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentd.domain.models import TaskRecord
from agentd.providers.openai_reasoner import OpenAIReasoningEngine


async def _stream(text: str) -> AsyncIterator[Any]:
    yield SimpleNamespace(type="response.output_text.delta", delta=text)
    yield SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=None))


class FakeResponsesClient:
    """Streams each queued payload the way the Responses API does."""

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self._outputs = outputs
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.calls.append(kwargs)
        return _stream(json.dumps(self._outputs.pop(0)))


@pytest.mark.asyncio
async def test_openai_reasoner_generates_schema_valid_plan_and_patch(tmp_path: Path) -> None:
    fake_client = FakeResponsesClient(
        outputs=[
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
    reasoner = OpenAIReasoningEngine(model="gpt-5", responses_client=fake_client)

    task = TaskRecord(task_id="t1", goal="goal", workspace_path=str(tmp_path))

    retrieval_context = {"related_files": ["a.py"], "related_symbols": ["build"]}
    plan = await reasoner.create_plan(task, str(tmp_path), retrieval_context=retrieval_context)
    patch = await reasoner.create_patch(
        task,
        str(tmp_path),
        diagnostics=[],
        retrieval_context=retrieval_context,
    )

    assert plan["steps"][0]["id"] == "S1"
    assert patch["candidates"][0]["patch_ops"][0]["op"] == "create_file"
    assert len(fake_client.calls) == 2
    # Responses `input` is an item array (the ChatGPT plan route requires it).
    first_payload = json.loads(fake_client.calls[0]["input"][0]["content"])
    second_payload = json.loads(fake_client.calls[1]["input"][0]["content"])
    assert first_payload["retrieval_context"]["related_files"] == ["a.py"]
    assert second_payload["retrieval_context"]["related_symbols"] == ["build"]


