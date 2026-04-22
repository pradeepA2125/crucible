from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentd.domain.models import (
    PlanCritiqueIssue,
    PlanEvidenceCategoryFacts,
    PlanEvidenceFile,
    PlanEvidencePack,
    PlanDocument,
)


class EvidenceAdapter(Protocol):
    name: str

    def path_relevance_score(self, *, goal: str, normalized_path: str) -> int: ...

    def build_category_facts(
        self,
        *,
        evidence_files: list[PlanEvidenceFile],
    ) -> PlanEvidenceCategoryFacts: ...


class PlanningAdapter(Protocol):
    name: str

    def additional_grounding_issues(
        self,
        *,
        plan: PlanDocument,
        approved_markdown: str,
        workspace_files_index: list[str],
        planner_evidence: PlanEvidencePack,
    ) -> list[PlanCritiqueIssue]: ...


class LanguageAdapter(Protocol):
    name: str
    extensions: tuple[str, ...]

    def planning_hints(self) -> list[str]: ...


@dataclass(frozen=True)
class GenericEvidenceAdapter:
    name: str = "generic"

    def path_relevance_score(self, *, goal: str, normalized_path: str) -> int:
        _ = (goal, normalized_path)
        return 0

    def build_category_facts(
        self,
        *,
        evidence_files: list[PlanEvidenceFile],
    ) -> PlanEvidenceCategoryFacts:
        _ = evidence_files
        return PlanEvidenceCategoryFacts()


@dataclass(frozen=True)
class LegacyRepoEvidenceAdapter(GenericEvidenceAdapter):
    name: str = "legacy_repo"

    def path_relevance_score(self, *, goal: str, normalized_path: str) -> int:
        goal_lower = goal.lower()
        score = 0
        if "agentd-py" in goal_lower and normalized_path.startswith("services/agentd-py/"):
            score += 5
        if "indexer" in goal_lower and normalized_path.startswith("services/indexer-rs/"):
            score += 5
        if ("vscode" in goal_lower or "extension" in goal_lower) and normalized_path.startswith(
            "apps/vscode-extension/"
        ):
            score += 4
        if (
            "editor-client" in goal_lower
            or "typescript client" in goal_lower
            or "sdk" in goal_lower
        ) and normalized_path.startswith("apps/editor-client/"):
            score += 4
        if "docs" in goal_lower and normalized_path.startswith("docs/"):
            score += 2
        return score

    def build_category_facts(
        self,
        *,
        evidence_files: list[PlanEvidenceFile],
    ) -> PlanEvidenceCategoryFacts:
        facts = PlanEvidenceCategoryFacts()
        for item in evidence_files:
            excerpt = item.excerpt.strip()
            if not excerpt:
                continue
            fact = f"{item.path}: {excerpt.splitlines()[0][:160]}"
            normalized = item.path.lower()
            if "/api/" in normalized or normalized.endswith("/routes.py"):
                facts.routes.append(fact)
            if "/storage/" in normalized or normalized.endswith("store.py"):
                facts.storage.append(fact)
            if "/domain/" in normalized or "/models" in normalized or normalized.endswith("schemas.py"):
                facts.models.append(fact)
        return facts


@dataclass(frozen=True)
class GenericPlanningAdapter:
    name: str = "generic"

    def additional_grounding_issues(
        self,
        *,
        plan: PlanDocument,
        approved_markdown: str,
        workspace_files_index: list[str],
        planner_evidence: PlanEvidencePack,
    ) -> list[PlanCritiqueIssue]:
        _ = planner_evidence
        issues: list[PlanCritiqueIssue] = []
        approved_existing_files = set(_extract_markdown_file_mentions(approved_markdown, workspace_files_index))
        step_target_files = {target for step in plan.steps for target in step.target_paths()}

        for step in plan.steps:
            for target in step.target_paths():
                if (
                    approved_existing_files
                    and target in workspace_files_index
                    and target not in approved_existing_files
                ):
                    issues.append(
                        PlanCritiqueIssue(
                            code="path_prefix_mismatch",
                            message=(
                                f"JSON plan target '{target}' is not part of the approved markdown blueprint."
                            ),
                            file=target,
                            evidence=", ".join(sorted(approved_existing_files)[:6]) or None,
                        )
                    )

        for expected_file in plan.expected_files:
            if expected_file in workspace_files_index or expected_file in step_target_files:
                continue
            issues.append(
                PlanCritiqueIssue(
                    code="invented_file",
                    message=(
                        f"expected_files includes '{expected_file}' without evidence that it exists or will be created."
                    ),
                    file=expected_file,
                )
            )

        mentioned_verification_files = _extract_markdown_file_mentions(
            "\n".join(plan.stop_conditions),
            workspace_files_index,
        )
        for verification_file in mentioned_verification_files:
            if verification_file not in workspace_files_index:
                issues.append(
                    PlanCritiqueIssue(
                        code="verification_mismatch",
                        message=f"Stop condition references missing verification file '{verification_file}'.",
                        file=verification_file,
                    )
                )

        return _dedupe_critique_issues(issues)


@dataclass(frozen=True)
class LegacyRepoPlanningAdapter(GenericPlanningAdapter):
    name: str = "legacy_repo"

    def additional_grounding_issues(
        self,
        *,
        plan: PlanDocument,
        approved_markdown: str,
        workspace_files_index: list[str],
        planner_evidence: PlanEvidencePack,
    ) -> list[PlanCritiqueIssue]:
        issues = list(
            super().additional_grounding_issues(
                plan=plan,
                approved_markdown=approved_markdown,
                workspace_files_index=workspace_files_index,
                planner_evidence=planner_evidence,
            )
        )
        approved_lower = approved_markdown.lower()
        candidate_text = json.dumps(plan.model_dump(mode="json"), sort_keys=True).lower()
        if (
            "taskevent" in approved_lower
            and all(token in approved_lower for token in ("at", "from_status", "to_status", "reason"))
        ):
            for banned_token in ("payload_json", "taskeventsresponse"):
                if banned_token in candidate_text:
                    issues.append(
                        PlanCritiqueIssue(
                            code="schema_mismatch",
                            message=(
                                f"JSON plan reintroduces unsupported structure '{banned_token}' despite approved TaskEvent fields."
                            ),
                            evidence="Approved markdown references TaskEvent fields at/from_status/to_status/reason.",
                        )
                    )
        return _dedupe_critique_issues(issues)


@dataclass(frozen=True)
class PythonLanguageAdapter:
    name: str = "python"
    extensions: tuple[str, ...] = (".py",)

    def planning_hints(self) -> list[str]:
        return [
            "Python edits should preserve indentation and import ordering.",
            "Prefer symbol-aware updates for Python declarations and imports.",
        ]


@dataclass(frozen=True)
class TypeScriptLanguageAdapter:
    name: str = "typescript"
    extensions: tuple[str, ...] = (".ts", ".tsx")

    def planning_hints(self) -> list[str]:
        return [
            "TypeScript edits should preserve export/import surfaces and compile under the existing module layout.",
        ]


@dataclass(frozen=True)
class RustLanguageAdapter:
    name: str = "rust"
    extensions: tuple[str, ...] = (".rs",)

    def planning_hints(self) -> list[str]:
        return [
            "Rust edits should respect module boundaries and keep items consistent with existing visibility.",
        ]


def build_evidence_adapter(name: str | None) -> EvidenceAdapter:
    normalized = (name or "generic").strip().lower()
    if normalized == "legacy_repo":
        return LegacyRepoEvidenceAdapter()
    return GenericEvidenceAdapter()


def build_planning_adapter(name: str | None) -> PlanningAdapter:
    normalized = (name or "generic").strip().lower()
    if normalized == "legacy_repo":
        return LegacyRepoPlanningAdapter()
    return GenericPlanningAdapter()


def default_language_adapters() -> list[LanguageAdapter]:
    return [
        PythonLanguageAdapter(),
        TypeScriptLanguageAdapter(),
        RustLanguageAdapter(),
    ]


def _extract_markdown_file_mentions(
    text: str,
    workspace_files_index: list[str],
) -> list[str]:
    if not text:
        return []
    mentions: list[str] = []
    seen: set[str] = set()
    for candidate in workspace_files_index:
        if candidate in text and candidate not in seen:
            seen.add(candidate)
            mentions.append(candidate)
    return mentions


def _dedupe_critique_issues(
    issues: list[PlanCritiqueIssue],
) -> list[PlanCritiqueIssue]:
    deduped: list[PlanCritiqueIssue] = []
    seen: set[tuple[str, str | None, str]] = set()
    for issue in issues:
        key = (issue.code.value, issue.file, issue.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped
