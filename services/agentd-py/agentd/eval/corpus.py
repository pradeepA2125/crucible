from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


LanguageKind = Literal["python", "typescript", "rust", "mixed", "unknown"]
SourceKind = Literal["internal", "oss"]


class BenchmarkTaskSpec(BaseModel):
    task_id: str
    goal: str
    workspace_path: str
    source: SourceKind
    language: LanguageKind
    tags: list[str] = Field(default_factory=list)


class BenchmarkTargets(BaseModel):
    internal: int = 100
    oss: int = 50


class BenchmarkCorpusStats(BaseModel):
    total_tasks: int
    internal_tasks: int
    oss_tasks: int


class BenchmarkCorpusManifest(BaseModel):
    schema_version: str = "benchmark-corpus.v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    frozen: bool = False
    targets: BenchmarkTargets = Field(default_factory=BenchmarkTargets)
    tasks: list[BenchmarkTaskSpec] = Field(default_factory=list)
    stats: BenchmarkCorpusStats


def _guess_language(repo_name: str) -> LanguageKind:
    name = repo_name.lower()
    if "python" in name or "pydantic" in name:
        return "python"
    if "typescript" in name or "ts" in name:
        return "typescript"
    if "rust" in name:
        return "rust"
    return "unknown"


def _build_stats(tasks: list[BenchmarkTaskSpec]) -> BenchmarkCorpusStats:
    internal_tasks = sum(1 for task in tasks if task.source == "internal")
    oss_tasks = sum(1 for task in tasks if task.source == "oss")
    return BenchmarkCorpusStats(
        total_tasks=len(tasks),
        internal_tasks=internal_tasks,
        oss_tasks=oss_tasks,
    )


def init_manifest(
    *,
    workspace_root: Path,
    internal_target: int = 100,
    oss_target: int = 50,
    frozen: bool = False,
) -> BenchmarkCorpusManifest:
    tasks: list[BenchmarkTaskSpec] = []
    if workspace_root.exists():
        for candidate in sorted(workspace_root.iterdir()):
            if not candidate.is_dir():
                continue
            if candidate.name.startswith("."):
                continue
            tasks.append(
                BenchmarkTaskSpec(
                    task_id=f"oss-{candidate.name}",
                    goal=f"Benchmark task placeholder for {candidate.name}",
                    workspace_path=str(candidate.resolve()),
                    source="oss",
                    language=_guess_language(candidate.name),
                    tags=["phase0", "seeded"],
                )
            )

    return BenchmarkCorpusManifest(
        frozen=frozen,
        targets=BenchmarkTargets(internal=internal_target, oss=oss_target),
        tasks=tasks,
        stats=_build_stats(tasks),
    )
