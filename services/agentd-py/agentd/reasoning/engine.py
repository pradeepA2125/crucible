from __future__ import annotations

import json
from pathlib import Path as _Path

from agentd.domain.models import Diagnostic, PatchDocumentV2, PlanStep, PlanDocument, TaskRecord
from agentd.providers.contracts import ModelJsonTransport
from agentd.reasoning.contracts import ReasoningEngine
from agentd.reasoning.prompt_builder import (
    PATCH_SYSTEM_INSTRUCTIONS,
    PLAN_SYSTEM_INSTRUCTIONS,
    build_patch_payload,
    build_plan_payload,
)


def _debug_dump(
    task_id: str,
    name: str,
    data: object,
    *,
    step_id: str | None = None,
) -> None:
    try:
        out = _Path("/tmp/ai-editor-stress") / task_id
        if step_id:
            out = out / f"step-{step_id}"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"debug-{name}.json").write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass


class DefaultReasoningEngine(ReasoningEngine):
    def __init__(
        self,
        *,
        model: str,
        transport: ModelJsonTransport,
    ) -> None:
        self._model = model
        self._transport = transport

    async def create_plan(
        self,
        task: TaskRecord,
        workspace_path: str,
        retrieval_context: dict[str, object],
    ) -> object:
        payload = await self._transport.generate_json(
            model=self._model,
            schema_name="plan_document",
            schema=PlanDocument.model_json_schema(),
            system_instructions=PLAN_SYSTEM_INSTRUCTIONS,
            user_payload=build_plan_payload(
                task,
                workspace_path=workspace_path,
                retrieval_context=retrieval_context,
            ),
        )
        _debug_dump(task.task_id, "plan-raw", payload)
        return PlanDocument.model_validate(payload).model_dump(mode="json")

    async def create_patch(
        self,
        task: TaskRecord,
        workspace_path: str,
        diagnostics: list[Diagnostic],
        retrieval_context: dict[str, object],
        *,
        current_step: PlanStep | None = None,
        allowed_files: list[str] | None = None,
        max_ops: int | None = None,
        max_files: int | None = None,
        candidate_count: int | None = None,
        last_failure: dict[str, object] | None = None,
    ) -> object:
        payload = await self._transport.generate_json(
            model=self._model,
            schema_name="patch_document_v2",
            schema=PatchDocumentV2.model_json_schema(),
            system_instructions=PATCH_SYSTEM_INSTRUCTIONS,
            user_payload=build_patch_payload(
                task,
                workspace_path=workspace_path,
                diagnostics=diagnostics,
                retrieval_context=retrieval_context,
                current_step=current_step,
                allowed_files=allowed_files,
                max_ops=max_ops,
                max_files=max_files,
                candidate_count=candidate_count,
                last_failure=last_failure,
            ),
        )
        _debug_dump(
            task.task_id,
            "patch-raw",
            payload,
            step_id=current_step.id if current_step else None,
        )
        return PatchDocumentV2.model_validate(payload).model_dump(mode="json")
