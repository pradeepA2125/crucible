from __future__ import annotations

from agentd.domain.models import PlanDocument, PlanEvidenceFile, PlanEvidencePack
from agentd.runtime.adapters import (
    GenericEvidenceAdapter,
    GenericPlanningAdapter,
    LegacyRepoEvidenceAdapter,
    LegacyRepoPlanningAdapter,
    build_evidence_adapter,
    build_planning_adapter,
    default_language_adapters,
)


def test_builders_default_to_generic() -> None:
    assert build_evidence_adapter(None).name == "generic"
    assert build_planning_adapter(None).name == "generic"


def test_generic_evidence_adapter_does_not_bias_repo_paths() -> None:
    adapter = GenericEvidenceAdapter()
    assert adapter.path_relevance_score(
        goal="implement backend route",
        normalized_path="services/agentd-py/agentd/api/routes.py",
    ) == 0


def test_legacy_evidence_adapter_preserves_old_repo_bias() -> None:
    adapter = LegacyRepoEvidenceAdapter()
    assert adapter.path_relevance_score(
        goal="implement endpoint in agentd-py service",
        normalized_path="services/agentd-py/agentd/api/routes.py",
    ) > 0


def test_generic_planning_adapter_is_schema_neutral() -> None:
    adapter = GenericPlanningAdapter()
    plan = PlanDocument.model_validate(
        {
            "analysis": "test",
            "steps": [
                {
                    "id": "s1",
                    "goal": "replace TaskEvent wrapper with payload_json response",
                    "targets": [{"path": "src/routes.py", "intent": "existing"}],
                    "risk": "low",
                }
            ],
            "expected_files": ["src/routes.py"],
            "stop_conditions": ["tests pass"],
        }
    )
    issues = adapter.additional_grounding_issues(
        plan=plan,
        approved_markdown="# Plan\n\nEXISTING\n- `src/routes.py`",
        workspace_files_index=["src/routes.py"],
        planner_evidence=PlanEvidencePack(),
    )
    assert all(issue.code.value != "schema_mismatch" for issue in issues)


def test_legacy_planning_adapter_adds_repo_specific_schema_guard() -> None:
    adapter = LegacyRepoPlanningAdapter()
    plan = PlanDocument.model_validate(
        {
            "analysis": "test",
            "steps": [
                {
                    "id": "s1",
                    "goal": "replace TaskEvent wrapper with payload_json response",
                    "targets": [{"path": "src/routes.py", "intent": "existing"}],
                    "risk": "low",
                }
            ],
            "expected_files": ["src/routes.py"],
            "stop_conditions": ["tests pass"],
        }
    )
    issues = adapter.additional_grounding_issues(
        plan=plan,
        approved_markdown="TaskEvent uses at/from_status/to_status/reason while payload_json should not be added.",
        workspace_files_index=["src/routes.py"],
        planner_evidence=PlanEvidencePack(),
    )
    assert any(issue.code.value == "schema_mismatch" for issue in issues)


def test_legacy_evidence_adapter_builds_category_facts() -> None:
    adapter = LegacyRepoEvidenceAdapter()
    facts = adapter.build_category_facts(
        evidence_files=[
            PlanEvidenceFile(
                path="services/agentd-py/agentd/api/routes.py",
                excerpt="async def get_task():\n    pass",
                rationale="symbol-grounded excerpt",
                line_start=1,
                line_end=2,
            )
        ]
    )
    assert facts.routes


def test_language_adapters_are_explicit_and_language_scoped() -> None:
    adapters = {adapter.name: adapter for adapter in default_language_adapters()}
    assert set(adapters) == {"python", "typescript", "rust"}
    assert ".py" in adapters["python"].extensions
