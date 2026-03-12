from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from agentd.domain.models import TaskStatus

from .bundle import TaskReplayBundle, load_bundle_file


class FailureCluster(BaseModel):
    key: str
    count: int
    sample_task_ids: list[str] = Field(default_factory=list)


class BenchmarkScoreSummary(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_bundles: int
    succeeded: int
    failed: int
    aborted: int
    ready_for_review: int
    success_rate: float
    average_step_attempts: float
    unsafe_mutation_rate: float
    top_failure_clusters: list[FailureCluster] = Field(default_factory=list)
    failure_counts: dict[str, int] = Field(default_factory=dict)


class Phase1GateReport(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    baseline_cluster_failures: int
    current_cluster_failures: int
    reduction_ratio: float
    target_reduction_ratio: float = 0.7
    passed: bool


_PHASE1_FAILURE_KEYWORDS = (
    "anchor",
    "indent",
    "syntax",
    "parser_unavailable",
    "python_unsafe_insert",
    "order_conflict",
)


def _collect_bundle_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _bundle_attempts(bundle: TaskReplayBundle) -> int:
    by_step: dict[str, int] = {}
    for item in bundle.execution_trace:
        prev = by_step.get(item.step_id, 0)
        if item.attempt > prev:
            by_step[item.step_id] = item.attempt
    return sum(by_step.values())


def _is_unsafe(bundle: TaskReplayBundle) -> bool:
    for diagnostic in bundle.diagnostics:
        source = diagnostic.source.lower()
        if "policy_violation" in source or "path_escape" in source:
            return True
    return False


def score_bundles(bundle_files: list[Path]) -> BenchmarkScoreSummary:
    bundles: list[TaskReplayBundle] = []
    for path in bundle_files:
        try:
            bundle = load_bundle_file(path)
        except Exception:
            continue
        bundles.append(bundle)

    total = len(bundles)
    succeeded = sum(1 for bundle in bundles if bundle.status == TaskStatus.SUCCEEDED)
    failed = sum(1 for bundle in bundles if bundle.status == TaskStatus.FAILED)
    aborted = sum(1 for bundle in bundles if bundle.status == TaskStatus.ABORTED)
    ready_for_review = sum(1 for bundle in bundles if bundle.status == TaskStatus.READY_FOR_REVIEW)
    attempts_sum = sum(_bundle_attempts(bundle) for bundle in bundles)
    unsafe_count = sum(1 for bundle in bundles if _is_unsafe(bundle))

    failure_counter: Counter[str] = Counter()
    failure_examples: dict[str, list[str]] = {}
    for bundle in bundles:
        if bundle.status not in {TaskStatus.FAILED, TaskStatus.ABORTED}:
            continue
        if not bundle.diagnostics:
            key = "orchestrator:unknown_failure"
            failure_counter[key] += 1
            failure_examples.setdefault(key, []).append(bundle.task_id)
            continue
        for diagnostic in bundle.diagnostics:
            snippet = diagnostic.message.strip().splitlines()[0][:120]
            key = f"{diagnostic.source}:{snippet}"
            failure_counter[key] += 1
            failure_examples.setdefault(key, []).append(bundle.task_id)

    top_clusters: list[FailureCluster] = []
    for key, count in failure_counter.most_common(5):
        top_clusters.append(
            FailureCluster(
                key=key,
                count=count,
                sample_task_ids=failure_examples.get(key, [])[:3],
            )
        )

    success_rate = (succeeded / total) if total else 0.0
    average_step_attempts = (attempts_sum / total) if total else 0.0
    unsafe_mutation_rate = (unsafe_count / total) if total else 0.0

    return BenchmarkScoreSummary(
        total_bundles=total,
        succeeded=succeeded,
        failed=failed,
        aborted=aborted,
        ready_for_review=ready_for_review,
        success_rate=round(success_rate, 4),
        average_step_attempts=round(average_step_attempts, 4),
        unsafe_mutation_rate=round(unsafe_mutation_rate, 4),
        top_failure_clusters=top_clusters,
        failure_counts=dict(failure_counter),
    )


def score_bundles_root(root: Path) -> BenchmarkScoreSummary:
    return score_bundles(_collect_bundle_files(root))


def phase1_failure_cluster_count(summary: BenchmarkScoreSummary) -> int:
    total = 0
    for key, count in summary.failure_counts.items():
        key_lower = key.lower()
        if any(keyword in key_lower for keyword in _PHASE1_FAILURE_KEYWORDS):
            total += count
    return total


def build_phase1_gate_report(
    *,
    baseline_summary: BenchmarkScoreSummary,
    current_summary: BenchmarkScoreSummary,
    target_reduction_ratio: float = 0.7,
) -> Phase1GateReport:
    baseline = phase1_failure_cluster_count(baseline_summary)
    current = phase1_failure_cluster_count(current_summary)
    if baseline <= 0:
        reduction = 0.0
        passed = False
    else:
        reduction = max(0.0, min(1.0, (baseline - current) / baseline))
        passed = reduction >= target_reduction_ratio
    return Phase1GateReport(
        baseline_cluster_failures=baseline,
        current_cluster_failures=current,
        reduction_ratio=round(reduction, 4),
        target_reduction_ratio=target_reduction_ratio,
        passed=passed,
    )
