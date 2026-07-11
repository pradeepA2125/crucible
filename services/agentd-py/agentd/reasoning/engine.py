from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from agentd.domain.models import (
    Diagnostic,
    PatchDocumentV2,
    PlanDocument,
    PlanStep,
    TaskRecord,
)
from agentd.providers.contracts import ModelJsonTransport
from agentd.reasoning.contracts import ReasoningEngine
from agentd.reasoning.prompt_builder import (
    PATCH_SYSTEM_INSTRUCTIONS,
    PLAN_SYSTEM_INSTRUCTIONS,
    build_patch_payload,
    build_plan_payload,
)
from agentd.runtime.artifacts import task_artifacts_root

if TYPE_CHECKING:
    from agentd.instructions.loader import ProjectInstructionsLoader


def _debug_dump(
    task_id: str,
    name: str,
    data: object,
    *,
    workspace_path: str,
    step_id: str | None = None,
) -> None:
    try:
        out = task_artifacts_root(task_id, workspace_path)
        if step_id:
            out = out / f"step-{step_id}"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"debug-{name}.json").write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def _chat_debug_dump(
    thread_id: str, turn_id: str, name: str, data: object, *, workspace_path: str,
) -> None:
    """Controller analog of _debug_dump — nests under chat/<thread_id>/<turn_id>/.
    Best-effort: a dump failure never breaks a turn."""
    try:
        from agentd.runtime.artifacts import chat_turn_artifacts_root

        out = chat_turn_artifacts_root(thread_id, turn_id, workspace_path)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.json").write_text(
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
        project_instructions_loader: ProjectInstructionsLoader | None = None,
        skill_catalog_loader: object | None = None,
    ) -> None:
        self._model = model
        self._transport = transport
        self._project_instructions_loader = project_instructions_loader
        self._skill_catalog_loader = skill_catalog_loader

    def set_provider(self, *, model: str, transport: ModelJsonTransport) -> None:
        """Hot-swap seam (PUT /v1/config/provider). Safe between turns: in-flight
        coroutines already hold self._transport locally; the next call reads the
        new pair. Loaders (instructions/skills) are untouched."""
        self._model = model
        self._transport = transport

    async def create_plan(
        self,
        task: TaskRecord,
        workspace_path: str,
        retrieval_context: dict[str, object],
        on_thinking: Callable[[str], None] | None = None,
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
                plan_validation_feedback=retrieval_context.get("plan_validation_feedback")
                if isinstance(retrieval_context.get("plan_validation_feedback"), dict)
                else None,  # type: ignore[arg-type]
            ),
            on_thinking=on_thinking,
        )
        _debug_dump(task.task_id, "plan-raw", payload, workspace_path=task.workspace_path)
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
        payload = build_patch_payload(
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
        )
        
        # Generate patch operations using the enriched payload
        patch_payload = await self._transport.generate_json(
            model=self._model,
            schema_name="patch_document_v2",
            schema=PatchDocumentV2.model_json_schema(),
            system_instructions=PATCH_SYSTEM_INSTRUCTIONS,
            user_payload=payload,
        )
        _debug_dump(
            task.task_id,
            "patch-raw",
            patch_payload,
            workspace_path=task.workspace_path,
            step_id=current_step.id if current_step else None,
        )
        return PatchDocumentV2.model_validate(patch_payload).model_dump(mode="json")

    async def create_tool_step(
        self,
        step_context: dict[str, object],
        history: list[dict[str, object]],
        tool_definitions: list[dict[str, object]],
        on_thinking: Callable[[str], None] | None = None,
        state_description: str = "",
        allowed_action_types: frozenset[str] | None = None,
    ) -> dict[str, object]:
        import copy

        from agentd.reasoning.tool_prompts import (
            AGENT_STEP_RESPONSE_SCHEMA,
            build_tool_step_payload,
            format_tool_system_prompt,
            inject_tools_into_payload,
        )
        user_payload = build_tool_step_payload(
            step_context, history, state_description=state_description,
        )
        inject_tools_into_payload(user_payload, tool_definitions)
        system_instructions = format_tool_system_prompt()

        # Filter the outer `type` enum per SM state when caller specifies what's
        # allowed. Deep-copy the module-level schema so other callers aren't affected.
        schema: dict[str, object] = AGENT_STEP_RESPONSE_SCHEMA
        if allowed_action_types is not None:
            schema = copy.deepcopy(AGENT_STEP_RESPONSE_SCHEMA)
            props = schema.get("properties")
            if isinstance(props, dict):
                type_prop = props.get("type")
                if isinstance(type_prop, dict):
                    # Preserve original ordering for stability.
                    original_enum = type_prop.get("enum")
                    if isinstance(original_enum, list):
                        type_prop["enum"] = [
                            t for t in original_enum if t in allowed_action_types
                        ]

        result = await self._transport.generate_json(
            model=self._model,
            schema_name="agent_step_response",
            schema=schema,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
        )
        return result

    async def create_planning_step(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_definitions: list[dict[str, object]],
        on_thinking: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        from agentd.planning.prompts import (
            _DEFAULT_MAX_TOOL_CALLS,
            build_planning_step_payload,
            format_planning_system_prompt,
            planning_response_schema,
        )
        revision_mode = "revision_request" in plan_context
        _raw_max = plan_context.get("max_tool_calls", _DEFAULT_MAX_TOOL_CALLS)
        max_calls = int(_raw_max) if isinstance(_raw_max, (int, str)) else _DEFAULT_MAX_TOOL_CALLS
        system_instructions = format_planning_system_prompt(
            tool_definitions, max_calls=max_calls, revision_mode=revision_mode
        )
        user_payload = build_planning_step_payload(plan_context, history, tool_definitions)
        # Per-turn payload capture: dump the EXACT system + user strings sent to the model
        # before the call fires, so a stuck or context-overflowing loop can be inspected
        # byte-for-byte from artifacts even when the call itself 400s. Skipped (via the
        # type guards) on code paths where task_id/workspace_path aren't in plan_context.
        _dbg_task_id = plan_context.get("task_id")
        _dbg_workspace = plan_context.get("workspace_path")
        if isinstance(_dbg_task_id, str) and isinstance(_dbg_workspace, str):
            _turn = len(history) // 2 + 1
            _debug_dump(
                _dbg_task_id,
                f"plan-turn-{_turn:02d}",
                {"system_instructions": system_instructions, "user_payload": user_payload},
                workspace_path=_dbg_workspace,
            )
        # Gate emit_plan_patch into the response schema only on feedback rounds. The
        # schema is appended trailing in the payload, so this does not disturb the
        # KV prefix (see planning/prompts.py::planning_response_schema).
        response_schema = planning_response_schema(
            allow_plan_patch=bool(plan_context.get("allow_plan_patch"))
        )
        result = await self._transport.generate_json(
            model=self._model,
            schema_name="planning_step_response",
            schema=response_schema,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
        )
        return result if isinstance(result, dict) else {}

    async def create_controller_step(
        self,
        plan_context: dict[str, object],
        history: list[dict[str, object]],
        tool_definitions: list[dict[str, object]],
        *,
        phase: str,
        on_thinking: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        from agentd.chat.controller_prompts import (
            build_controller_step_payload,
            controller_response_schema,
            format_controller_system_prompt,
        )

        instructions = (
            self._project_instructions_loader.load()
            if self._project_instructions_loader is not None
            else None
        )
        skills_catalog = None
        if self._skill_catalog_loader is not None:
            from agentd.skills.catalog import select_catalog_for_budget
            from agentd.skills.config import skills_catalog_max_chars

            full = self._skill_catalog_loader.load_catalog()  # type: ignore[attr-defined]
            shown, _hidden = select_catalog_for_budget(full, skills_catalog_max_chars())
            skills_catalog = shown
        system_instructions = format_controller_system_prompt(
            tool_definitions, project_instructions=instructions, skills_catalog=skills_catalog
        )
        user_payload = build_controller_step_payload(
            plan_context, history, tool_definitions, phase=phase,
            skills_available=bool(skills_catalog),
        )
        # Tier 2: use the tight discriminated-union schema only on a provider whose
        # grammar enforces `oneOf` (getattr-defensive — older transports lack the flag).
        tight = getattr(self._transport, "supports_oneof_grammar", False)
        anyof = getattr(self._transport, "supports_anyof_grammar", False)
        all_fields_required = getattr(self._transport, "requires_all_fields", False)
        schema = controller_response_schema(phase=phase, tight=tight, anyof=anyof, all_fields_required=all_fields_required)
        result = await self._transport.generate_json(
            model=self._model,
            schema_name="controller_step_response",
            schema=schema,
            system_instructions=system_instructions,
            user_payload=user_payload,
            on_thinking=on_thinking,
        )
        result = result if isinstance(result, dict) else {}
        # Artifact: the EXACT bytes entering the LLM this iteration (controller analog of
        # the task path's debug-plan-turn-NN). Keyed by thread/turn from plan_context
        # (debug-only keys the payload builder ignores — KV-safe). The iteration is the
        # 0-based index WITHIN this turn — `history` includes the replayed seed_history,
        # so we subtract its length (artifact_seed_len) so every turn starts at -00.
        thread_id = str(plan_context.get("artifact_thread_id") or "")
        turn_id = str(plan_context.get("artifact_turn_id") or "")
        if thread_id and turn_id:
            _seed_raw = plan_context.get("artifact_seed_len")
            seed_len = _seed_raw if isinstance(_seed_raw, int) else 0
            iteration = max(0, (len(history) - seed_len) // 2)
            # `goal` in the payload is THIS turn's user message, not the objective — the
            # original intent is the first user message in history. Surface it so the
            # artifact is self-explanatory mid-conversation.
            original_goal = next(
                (str(m.get("content")) for m in history if m.get("role") == "user"), "")
            _chat_debug_dump(
                thread_id, turn_id, f"controller-turn-{iteration:02d}",
                {
                    "phase": phase,
                    "tight_schema": tight,
                    "original_goal": original_goal,
                    "system_instructions": system_instructions,
                    "user_payload": user_payload,
                    "schema": schema,
                    "raw_result": result,
                },
                workspace_path=str(plan_context.get("workspace_path") or ""),
            )
        return result

    async def summarize_run(
        self, *, goal: str, outcome: str, run_events: list[dict[str, object]],
        deviations: list[str], modified_files: list[str],
    ) -> dict[str, object]:
        from agentd.reasoning.narrative_prompts import (
            TASK_NARRATIVE_RESPONSE_SCHEMA,
            build_narrative_payload,
            format_narrative_system_prompt,
        )
        payload = build_narrative_payload(
            goal=goal, outcome=outcome, run_events=run_events,
            deviations=deviations, modified_files=modified_files,
        )
        result = await self._transport.generate_json(
            model=self._model,
            schema_name="task_narrative",
            schema=TASK_NARRATIVE_RESPONSE_SCHEMA,
            system_instructions=format_narrative_system_prompt(),
            user_payload=payload,
        )
        return result if isinstance(result, dict) else {}

    async def draft_conventions(self, *, probe: object) -> dict[str, object]:
        from agentd.reasoning.env_prompts import (
            DRAFT_CONVENTIONS_RESPONSE_SCHEMA,
            DRAFT_CONVENTIONS_SYSTEM_PROMPT,
            build_draft_conventions_payload,
        )
        payload = build_draft_conventions_payload(probe)  # type: ignore[arg-type]
        result = await self._transport.generate_json(
            model=self._model,
            schema_name="env_profile_conventions",
            schema=DRAFT_CONVENTIONS_RESPONSE_SCHEMA,
            system_instructions=DRAFT_CONVENTIONS_SYSTEM_PROMPT,
            user_payload=payload,
        )
        return result if isinstance(result, dict) else {}
