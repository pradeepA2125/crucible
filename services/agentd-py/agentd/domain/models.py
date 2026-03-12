from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    CONTEXT_READY = "CONTEXT_READY"
    PLANNED = "PLANNED"
    PATCHED = "PATCHED"
    VALIDATING = "VALIDATING"
    REPAIRING = "REPAIRING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PROMOTING = "PROMOTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class PatchFailureCode(StrEnum):
    SCOPE_VIOLATION = "scope_violation"
    FILE_MISSING = "file_missing"
    FILE_EXISTS = "file_exists"
    RANGE_INVALID = "range_invalid"
    ANCHOR_MISSING = "anchor_missing"
    ANCHOR_AMBIGUOUS = "anchor_ambiguous"
    ORDER_CONFLICT = "order_conflict"
    PYTHON_UNSAFE_INSERT = "python_unsafe_insert"
    PATH_ESCAPE = "path_escape"
    POLICY_VIOLATION = "policy_violation"
    PARSER_UNAVAILABLE = "parser_unavailable"
    APPLY_ERROR = "apply_error"


class TaskBudget(BaseModel):
    max_iterations: int = 6
    max_files_touched: int = 20
    max_tokens: int = 120_000
    max_runtime_ms: int = 20 * 60 * 1000


class TaskUsage(BaseModel):
    iterations: int = 0
    tokens_used: int = 0


class TaskEvent(BaseModel):
    at: datetime
    from_status: TaskStatus
    to_status: TaskStatus
    reason: str


class Diagnostic(BaseModel):
    source: str
    message: str
    level: Literal["error", "warning"]
    file: str | None = None
    line: int | None = None
    column: int | None = None


class PlanStep(BaseModel):
    id: str
    goal: str
    targets: list[str]
    risk: Literal["low", "med", "high"]


class PlanDocument(BaseModel):
    analysis: str
    steps: list[PlanStep]
    expected_files: list[str]
    stop_conditions: list[str]


class RangeAnchor(BaseModel):
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "RangeAnchor":
        if self.end_line < self.start_line:
            msg = "end_line must be >= start_line"
            raise ValueError(msg)
        return self


class SymbolAnchor(BaseModel):
    symbol: str


class ReplaceRangeOp(BaseModel):
    op: Literal["replace_range"]
    file: str
    anchor: RangeAnchor
    content: str
    reason: str


class InsertAfterSymbolOp(BaseModel):
    op: Literal["insert_after_symbol"]
    file: str
    anchor: SymbolAnchor
    content: str
    reason: str


class CreateFileOp(BaseModel):
    op: Literal["create_file"]
    file: str
    content: str
    reason: str


class DeleteFileOp(BaseModel):
    op: Literal["delete_file"]
    file: str
    reason: str


PatchOperation = Annotated[
    Union[ReplaceRangeOp, InsertAfterSymbolOp, CreateFileOp, DeleteFileOp],
    Field(discriminator="op"),
]


class PatchDocument(BaseModel):
    patch_ops: list[PatchOperation] = Field(min_length=1)


class NodeSelector(BaseModel):
    kind: Literal["symbol"] = "symbol"
    value: str = Field(min_length=1)
    match: Literal["exact", "contains"] = "exact"


class ReplaceNodeOpV2(BaseModel):
    op: Literal["replace_node"]
    file: str
    language: Literal["python", "typescript", "rust"]
    selector: NodeSelector
    content: str
    reason: str


class InsertAfterNodeOpV2(BaseModel):
    op: Literal["insert_after_node"]
    file: str
    language: Literal["python", "typescript", "rust"]
    selector: NodeSelector
    content: str
    reason: str


class CreateFileOpV2(BaseModel):
    op: Literal["create_file"]
    file: str
    content: str
    reason: str


class DeleteFileOpV2(BaseModel):
    op: Literal["delete_file"]
    file: str
    reason: str

class SearchReplaceOpV2(BaseModel):
    """Apply search/replace patch to a file.
    
    Fast apply engine: O(N) text search and replace.
    Ideal for precise, targeted edits with exact anchors.
    Inspired by Aider's search/replace format.
    """
    op: Literal["search_replace"]
    file: str
    search: str = Field(min_length=1)
    replace: str
    reason: str
    
    @model_validator(mode="after")
    def validate_search_not_empty(self) -> "SearchReplaceOpV2":
        """Ensure search text is not empty."""
        if not self.search.strip():
            raise ValueError("search text cannot be empty")
        return self


class ApplyDiffOpV2(BaseModel):
    """Apply a unified diff patch to a file.
    
    Supports standard unified diff format with @@ hunks.
    Ideal for multi-section edits and LLM-generated patches.
    Compatible with Git diff format patches.
    """
    op: Literal["apply_diff"]
    file: str
    diff: str = Field(min_length=1)
    reason: str
    
    @model_validator(mode="after")
    def validate_diff_format(self) -> "ApplyDiffOpV2":
        """Ensure diff contains valid hunk headers."""
        import re
        if not re.search(r'@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@', self.diff):
            raise ValueError("diff must contain valid @@ hunk headers")
        return self



PatchOperationV2 = Annotated[
    Union[
        ReplaceNodeOpV2,
        InsertAfterNodeOpV2,
        SearchReplaceOpV2,
        ApplyDiffOpV2,
        CreateFileOpV2,
        DeleteFileOpV2,
    ],
    Field(discriminator="op"),
]


class PatchCandidateV2(BaseModel):
    candidate_id: str = Field(min_length=1)
    patch_ops: list[PatchOperationV2] = Field(min_length=1)


class PatchDocumentV2(BaseModel):
    candidates: list[PatchCandidateV2] = Field(min_length=1)


class CandidateScoreBreakdown(BaseModel):
    preflight_pass: bool
    validation_pass: bool
    changed_lines: int = 0
    op_count: int = 0
    new_file_count: int = 0
    score: float = 0.0
    selected: bool = False


class CheckpointManifest(BaseModel):
    task_id: str
    step_id: str
    attempt: int
    candidate_id: str | None = None
    checkpoint_id: str
    checkpoint_path: str
    shadow_path: str
    file_hashes_before: dict[str, str] = Field(default_factory=dict)
    file_hashes_after: dict[str, str] = Field(default_factory=dict)
    preflight_report_path: str | None = None
    validation_report_path: str | None = None
    ranking_report_path: str | None = None


class PatchPreflightIssue(BaseModel):
    op_index: int | None = None
    code: PatchFailureCode
    file: str | None = None
    message: str


class PatchPreflightReport(BaseModel):
    success: bool
    issues: list[PatchPreflightIssue] = Field(default_factory=list)


class StepExecutionTrace(BaseModel):
    step_id: str
    attempt: int
    status: Literal[
        "preflight_failed",
        "patch_applied",
        "validation_failed",
        "step_completed",
        "step_exhausted",
    ]
    issues: list[PatchPreflightIssue] = Field(default_factory=list)
    message: str | None = None
    candidate_id: str | None = None
    checkpoint_id: str | None = None
    score: float | None = None
    preflight_summary: dict[str, Any] | None = None
    validation_summary: dict[str, Any] | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class StepProgress(BaseModel):
    total_steps: int
    completed_steps: int
    remaining_steps: int
    current_step_id: str | None = None


class ValidationResult(BaseModel):
    success: bool
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    duration_ms: int


class TaskRecord(BaseModel):
    task_id: str
    goal: str
    workspace_path: str
    status: TaskStatus = TaskStatus.QUEUED
    mode: Literal["inline", "file_edit", "project_edit", "autonomous"] = "project_edit"
    shadow_workspace_path: str | None = None
    plan: PlanDocument | None = None
    latest_patch: PatchDocument | None = None
    latest_patch_v2: PatchDocumentV2 | None = None
    selected_candidate_id: str | None = None
    promoted_at: datetime | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    usage: TaskUsage = Field(default_factory=TaskUsage)
    events: list[TaskEvent] = Field(default_factory=list)
    execution_trace: list[StepExecutionTrace] = Field(default_factory=list)
    checkpoints: list[CheckpointManifest] = Field(default_factory=list)
    artifacts_root_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskCreateRequest(BaseModel):
    goal: str
    workspace_path: str
    mode: Literal["inline", "file_edit", "project_edit", "autonomous"] = "project_edit"
    budget: TaskBudget = Field(default_factory=TaskBudget)


class TaskCreateResponse(BaseModel):
    task_id: str


class TaskView(BaseModel):
    task_id: str
    goal: str
    status: TaskStatus
    modified_files: list[str]
    diagnostics: list[Diagnostic]


class TaskResult(BaseModel):
    task_id: str
    goal: str
    status: TaskStatus
    plan: PlanDocument | None = None
    patch: PatchDocument | PatchCandidateV2 | None = None
    patch_candidates: list[PatchCandidateV2] = Field(default_factory=list)
    selected_candidate_id: str | None = None
    modified_files: list[str]
    diagnostics: list[Diagnostic]
    promoted_at: datetime | None = None
    shadow_workspace_path: str | None = None
    step_progress: StepProgress | None = None
    execution_trace: list[StepExecutionTrace] = Field(default_factory=list)
    artifacts_root_path: str | None = None


class RejectPatchRequest(BaseModel):
    reason: str


class TaskArtifactEntry(BaseModel):
    relative_path: str
    kind: Literal["checkpoint", "preflight", "validation", "ranking", "plan", "patch", "other"]
    step_id: str | None = None
    attempt: int | None = None
    candidate_id: str | None = None


class TaskArtifactsResponse(BaseModel):
    task_id: str
    artifacts_root_path: str | None = None
    entries: list[TaskArtifactEntry] = Field(default_factory=list)
