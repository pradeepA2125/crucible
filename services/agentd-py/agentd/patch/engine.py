from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

try:
    import libcst as cst
    from libcst.metadata import PositionProvider
except Exception:  # pragma: no cover - optional dependency at import time
    cst = None
    PositionProvider = None  # type: ignore[assignment]

from agentd.domain.models import (
    ApplyDiffOpV2,
    CreateFileOp,
    CreateFileOpV2,
    DeleteFileOp,
    DeleteFileOpV2,
    InsertAfterSymbolOp,
    InsertAfterNodeOpV2,
    NodeSelector,
    PatchCandidateV2,
    PatchFailureCode,
    PatchDocument,
    PatchPreflightIssue,
    PatchPreflightReport,
    ReplaceRangeOp,
    ReplaceNodeOpV2,
    SearchReplaceOpV2,
)
from agentd.patch.policy import ForbiddenPathPolicy, PatchPolicyViolation


@dataclass(frozen=True)
class PatchResult:
    touched_files: list[str]


class SelectorAmbiguousError(ValueError):
    pass


class ParserUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class PythonDeclMatch:
    kind: Literal["class", "function"]
    name: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int


class PatchEngine:
    def __init__(self, policy: ForbiddenPathPolicy | None = None) -> None:
        self._policy = policy or ForbiddenPathPolicy()
        self._ts_parser = None
        self._rs_parser = None
        self._tree_sitter_ready = False

    async def preflight_patch_document(
        self,
        base_dir: str | Path,
        patch: PatchDocument,
        *,
        allowed_files: set[str] | None = None,
    ) -> PatchPreflightReport:
        base_path = Path(base_dir).resolve()
        if not base_path.exists() or not base_path.is_dir():
            msg = f"Patch base path is not a directory: {base_path}"
            return PatchPreflightReport(
                success=False,
                issues=[
                    PatchPreflightIssue(
                        code=PatchFailureCode.FILE_MISSING,
                        message=msg,
                    )
                ],
            )

        try:
            self._policy.validate_paths(op.file for op in patch.patch_ops)
        except PatchPolicyViolation as exc:
            return PatchPreflightReport(
                success=False,
                issues=[
                    PatchPreflightIssue(
                        code=PatchFailureCode.POLICY_VIOLATION,
                        message=str(exc),
                    )
                ],
            )

        issues: list[PatchPreflightIssue] = []
        simulated_files: dict[str, list[str] | None] = {}
        original_files: dict[str, list[str] | None] = {}
        mutated_files: set[str] = set()
        for index, operation in enumerate(patch.patch_ops):
            if allowed_files is not None and operation.file not in allowed_files:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.SCOPE_VIOLATION,
                        file=operation.file,
                        message=f"Patch op targets file outside current step scope: {operation.file}",
                    )
                )
                continue

            try:
                target = self._resolve_inside(base_path, operation.file)
            except RuntimeError as exc:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.PATH_ESCAPE,
                        file=operation.file,
                        message=str(exc),
                    )
                )
                continue

            if operation.file not in simulated_files:
                if target.exists():
                    try:
                        loaded = target.read_text(encoding="utf-8").splitlines()
                    except OSError as exc:
                        issues.append(
                            PatchPreflightIssue(
                                op_index=index,
                                code=PatchFailureCode.APPLY_ERROR,
                                file=operation.file,
                                message=f"Unable to read file for preflight simulation: {exc}",
                            )
                        )
                        continue
                    simulated_files[operation.file] = loaded
                    original_files[operation.file] = [*loaded]
                else:
                    simulated_files[operation.file] = None
                    original_files[operation.file] = None

            current_lines = simulated_files[operation.file]

            if isinstance(operation, CreateFileOp):
                if current_lines is not None:
                    issues.append(
                        PatchPreflightIssue(
                            op_index=index,
                            code=PatchFailureCode.FILE_EXISTS,
                            file=operation.file,
                            message=f"File already exists: {operation.file}",
                        )
                    )
                    continue
                simulated_files[operation.file] = operation.content.splitlines()
                mutated_files.add(operation.file)
                continue

            if current_lines is None:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.FILE_MISSING,
                        file=operation.file,
                        message=f"File is missing for op '{operation.op}': {operation.file}",
                    )
                )
                continue

            if isinstance(operation, DeleteFileOp):
                simulated_files[operation.file] = None
                mutated_files.add(operation.file)
                continue

            if isinstance(operation, ReplaceRangeOp):
                start = operation.anchor.start_line - 1
                end = operation.anchor.end_line - 1
                if start < 0 or end < start or end >= len(current_lines):
                    issues.append(
                        PatchPreflightIssue(
                            op_index=index,
                            code=PatchFailureCode.RANGE_INVALID,
                            file=operation.file,
                            message=(
                                f"Invalid replace_range {operation.anchor.start_line}-"
                                f"{operation.anchor.end_line}; file has {len(current_lines)} lines"
                            ),
                        )
                    )
                    continue
                replacement = operation.content.splitlines()
                simulated_files[operation.file] = [
                    *current_lines[:start],
                    *replacement,
                    *current_lines[end + 1 :],
                ]
                mutated_files.add(operation.file)
                continue

            if isinstance(operation, InsertAfterSymbolOp):
                matches = self._find_symbol_indices(current_lines, operation.anchor.symbol)
                if not matches:
                    code = PatchFailureCode.ANCHOR_MISSING
                    message = (
                        f"Symbol '{operation.anchor.symbol}' not found in "
                        f"{operation.file}"
                    )
                    original_lines = original_files.get(operation.file)
                    if (
                        operation.file in mutated_files
                        and original_lines is not None
                        and self._find_symbol_indices(original_lines, operation.anchor.symbol)
                    ):
                        code = PatchFailureCode.ORDER_CONFLICT
                        message = (
                            f"Anchor '{operation.anchor.symbol}' was invalidated by an earlier "
                            f"operation in {operation.file}"
                        )
                    issues.append(
                        PatchPreflightIssue(
                            op_index=index,
                            code=code,
                            file=operation.file,
                            message=message,
                        )
                    )
                    continue

                if len(matches) > 1:
                    issues.append(
                        PatchPreflightIssue(
                            op_index=index,
                            code=PatchFailureCode.ANCHOR_AMBIGUOUS,
                            file=operation.file,
                            message=(
                                f"Symbol '{operation.anchor.symbol}' is ambiguous in "
                                f"{operation.file}; matched {len(matches)} lines"
                            ),
                        )
                    )
                    continue

                if target.suffix == ".py":
                    matched_line = current_lines[matches[0]].lstrip()
                    if matched_line.startswith("def ") or matched_line.startswith("class "):
                        issues.append(
                            PatchPreflightIssue(
                                op_index=index,
                                code=PatchFailureCode.PYTHON_UNSAFE_INSERT,
                                file=operation.file,
                                message=(
                                    "insert_after_symbol on Python def/class signatures is "
                                    f"unsafe: '{operation.anchor.symbol}' in {operation.file}"
                                ),
                            )
                        )
                        continue

                insertion = operation.content.splitlines()
                symbol_index = matches[0]
                simulated_files[operation.file] = [
                    *current_lines[: symbol_index + 1],
                    *insertion,
                    *current_lines[symbol_index + 1 :],
                ]
                mutated_files.add(operation.file)

        return PatchPreflightReport(success=not issues, issues=issues)

    async def apply_patch_document(
        self,
        base_dir: str | Path,
        patch: PatchDocument,
        *,
        allowed_files: set[str] | None = None,
    ) -> PatchResult:
        base_path = Path(base_dir).resolve()
        report = await self.preflight_patch_document(
            base_path,
            patch,
            allowed_files=allowed_files,
        )
        if not report.success:
            if report.issues and report.issues[0].code == PatchFailureCode.POLICY_VIOLATION:
                raise PatchPolicyViolation(report.issues[0].message)
            details = "; ".join(
                issue.message for issue in report.issues[:3]
            )
            raise RuntimeError(f"Patch preflight failed: {details}")

        touched: set[str] = set()
        for operation in patch.patch_ops:
            if isinstance(operation, ReplaceRangeOp):
                self._apply_replace_range(base_path, operation)
            elif isinstance(operation, InsertAfterSymbolOp):
                self._apply_insert_after_symbol(base_path, operation)
            elif isinstance(operation, CreateFileOp):
                self._apply_create_file(base_path, operation)
            elif isinstance(operation, DeleteFileOp):
                self._apply_delete_file(base_path, operation)
            else:
                msg = f"Unsupported patch operation type: {type(operation).__name__}"
                raise RuntimeError(msg)
            touched.add(operation.file)

        return PatchResult(touched_files=sorted(touched))

    def _apply_replace_range(self, base_path: Path, operation: ReplaceRangeOp) -> None:
        target = self._resolve_inside(base_path, operation.file)
        lines = target.read_text(encoding="utf-8").splitlines()
        start = operation.anchor.start_line - 1
        end = operation.anchor.end_line - 1

        if start < 0 or end < start or end >= len(lines):
            msg = (
                f"Invalid replace_range for {operation.file}: "
                f"{operation.anchor.start_line}-{operation.anchor.end_line} "
                f"(file has {len(lines)} lines)"
            )
            raise RuntimeError(msg)

        replacement = operation.content.splitlines()
        updated = [*lines[:start], *replacement, *lines[end + 1 :]]
        target.write_text("\n".join(updated), encoding="utf-8")

    def _apply_insert_after_symbol(self, base_path: Path, operation: InsertAfterSymbolOp) -> None:
        target = self._resolve_inside(base_path, operation.file)
        lines = target.read_text(encoding="utf-8").splitlines()

        index = -1
        for idx, line in enumerate(lines):
            if operation.anchor.symbol in line:
                index = idx
                break

        if index == -1:
            msg = f"Symbol '{operation.anchor.symbol}' not found in {operation.file}"
            raise RuntimeError(msg)

        insertion = operation.content.splitlines()
        updated = [*lines[: index + 1], *insertion, *lines[index + 1 :]]
        target.write_text("\n".join(updated), encoding="utf-8")

    def _apply_create_file(self, base_path: Path, operation: CreateFileOp) -> None:
        target = self._resolve_inside(base_path, operation.file)
        if target.exists():
            msg = f"File already exists: {operation.file}"
            raise RuntimeError(msg)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(operation.content, encoding="utf-8")

    def _apply_delete_file(self, base_path: Path, operation: DeleteFileOp) -> None:
        target = self._resolve_inside(base_path, operation.file)
        if not target.exists():
            msg = f"Cannot delete missing path: {operation.file}"
            raise RuntimeError(msg)

        if target.is_dir():
            shutil.rmtree(target)
            return


    def _apply_search_replace(self, base_path: Path, operation: SearchReplaceOpV2) -> None:
        """Apply search/replace operation (Fast Apply).
        
        O(N) text search and replace - very fast for large files.
        """
        target = self._resolve_inside(base_path, operation.file)
        
        if not target.exists():
            msg = f"File not found for search/replace: {operation.file}"
            raise RuntimeError(msg)
        
        original_content = target.read_text(encoding="utf-8")
        
        # Fast Apply: exact text search
        if operation.search not in original_content:
            msg = f"Search text not found in {operation.file}. File may have changed since patch was generated."
            raise RuntimeError(msg)
        
        # Count occurrences
        occurrences = original_content.count(operation.search)
        if occurrences > 1:
            msg = f"Search text appears {occurrences} times in {operation.file}. Search text must be unique for safe replacement."
            raise RuntimeError(msg)
        
        # Apply replacement
        new_content = original_content.replace(operation.search, operation.replace, 1)
        target.write_text(new_content, encoding="utf-8")

    def _apply_diff(self, base_path: Path, operation: ApplyDiffOpV2) -> None:
        """Apply unified diff to file using unidiff library."""
        target = self._resolve_inside(base_path, operation.file)
        
        if not target.exists():
            msg = f"File not found for diff application: {operation.file}"
            raise RuntimeError(msg)
        
        original_content = target.read_text(encoding="utf-8")
        
        try:
            # Use unidiff library for parsing
            try:
                from unidiff import PatchSet
            except ImportError as exc:
                msg = "unidiff library required for diff operations"
                raise RuntimeError(msg) from exc
            
            # Parse Codex-style format if present
            diff_content = self._parse_codex_diff(operation.diff)
            
            # Construct full diff with file headers
            full_diff = f"""--- a/{operation.file}
+++ b/{operation.file}
{diff_content}"""
            
            patch_set = PatchSet(full_diff)
            if len(patch_set) != 1:
                msg = f"Diff must target single file, got {len(patch_set)}"
                raise RuntimeError(msg)
            
            patched_file = patch_set[0]
            
            # Apply hunks sequentially
            lines = original_content.splitlines(keepends=True)
            offset = 0  # Track line number shifts from previous hunks
            
            for hunk in patched_file:
                # Validate hunk can be applied
                source_start = hunk.source_start - 1 + offset
                source_length = hunk.source_length
                
                # Extract expected context
                # Normalize line endings for comparison (unidiff may not include trailing newlines)
                expected_lines = [line.value for line in hunk if line.is_context or line.is_removed]
                actual_lines = lines[source_start : source_start + source_length]
                
                # Normalize by ensuring both have consistent line endings
                expected_normalized = [line.rstrip('\n') + '\n' if not line.endswith('\n') else line for line in expected_lines]
                actual_normalized = [line.rstrip('\n') + '\n' for line in actual_lines]
                
                # For the last line, handle the case where it might not have a newline
                if expected_normalized and not expected_lines[-1].endswith('\n'):
                    expected_normalized[-1] = expected_normalized[-1].rstrip('\n')
                    actual_normalized[-1] = actual_normalized[-1].rstrip('\n')
                
                if actual_normalized != expected_normalized:
                    msg = (
                        f"Hunk context mismatch at line {hunk.source_start}: "
                        f"expected {len(expected_lines)} lines, file may have changed"
                    )
                    raise RuntimeError(msg)
                
                # Apply hunk
                new_lines = [line.value for line in hunk if not line.is_removed]
                lines[source_start : source_start + source_length] = new_lines
                
                # Update offset for next hunk
                offset += len(new_lines) - source_length
            
            # Write patched content
            target.write_text("".join(lines), encoding="utf-8")
            
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            msg = f"Failed to apply diff to {operation.file}: {exc}"
            raise RuntimeError(msg) from exc

    def _parse_codex_diff(self, diff_text: str) -> str:
        """Convert Codex-style diff to unified diff format.
        
        Codex format:
        *** Begin Patch
        @@ context @@
        -old line
        +new line
        *** End Patch
        
        Converts to standard unified diff for processing.
        """
        if "*** Begin Patch" in diff_text and "*** End Patch" in diff_text:
            # Extract content between markers
            start_idx = diff_text.index("*** Begin Patch") + len("*** Begin Patch")
            end_idx = diff_text.index("*** End Patch")
            return diff_text[start_idx:end_idx].strip()
        
        return diff_text  # Already in unified format
        target.unlink()

    def _resolve_inside(self, base_path: Path, relative_path: str) -> Path:
        candidate = (base_path / relative_path).resolve()
        try:
            candidate.relative_to(base_path)
        except ValueError as exc:
            msg = f"Path escapes workspace: {relative_path}"
            raise RuntimeError(msg) from exc
        return candidate

    def _find_symbol_indices(self, lines: Iterable[str], symbol: str) -> list[int]:
        indices: list[int] = []
        for idx, line in enumerate(lines):
            if symbol in line:
                indices.append(idx)
        return indices

    async def preflight_patch_candidate(
        self,
        base_dir: str | Path,
        candidate: PatchCandidateV2,
        *,
        allowed_files: set[str] | None = None,
    ) -> PatchPreflightReport:
        base_path = Path(base_dir).resolve()
        if not base_path.exists() or not base_path.is_dir():
            msg = f"Patch base path is not a directory: {base_path}"
            return PatchPreflightReport(
                success=False,
                issues=[PatchPreflightIssue(code=PatchFailureCode.FILE_MISSING, message=msg)],
            )

        try:
            self._policy.validate_paths(op.file for op in candidate.patch_ops)
        except PatchPolicyViolation as exc:
            return PatchPreflightReport(
                success=False,
                issues=[PatchPreflightIssue(code=PatchFailureCode.POLICY_VIOLATION, message=str(exc))],
            )

        issues: list[PatchPreflightIssue] = []
        simulated_sources: dict[str, str | None] = {}
        original_sources: dict[str, str | None] = {}
        mutated_files: set[str] = set()
        for index, operation in enumerate(candidate.patch_ops):
            if allowed_files is not None and operation.file not in allowed_files:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.SCOPE_VIOLATION,
                        file=operation.file,
                        message=f"Patch op targets file outside current step scope: {operation.file}",
                    )
                )
                continue

            try:
                target = self._resolve_inside(base_path, operation.file)
            except RuntimeError as exc:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.PATH_ESCAPE,
                        file=operation.file,
                        message=str(exc),
                    )
                )
                continue

            if operation.file not in simulated_sources:
                if target.exists():
                    try:
                        source = target.read_text(encoding="utf-8")
                    except OSError as exc:
                        issues.append(
                            PatchPreflightIssue(
                                op_index=index,
                                code=PatchFailureCode.APPLY_ERROR,
                                file=operation.file,
                                message=f"Unable to read file for preflight simulation: {exc}",
                            )
                        )
                        continue
                    simulated_sources[operation.file] = source
                    original_sources[operation.file] = source
                else:
                    simulated_sources[operation.file] = None
                    original_sources[operation.file] = None

            current_source = simulated_sources[operation.file]

            if isinstance(operation, CreateFileOpV2):
                if current_source is not None:
                    issues.append(
                        PatchPreflightIssue(
                            op_index=index,
                            code=PatchFailureCode.FILE_EXISTS,
                            file=operation.file,
                            message=f"File already exists: {operation.file}",
                        )
                    )
                    continue
                simulated_sources[operation.file] = operation.content
                mutated_files.add(operation.file)
                continue

            if current_source is None:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.FILE_MISSING,
                        file=operation.file,
                        message=f"File is missing for op '{operation.op}': {operation.file}",
                    )
                )
                continue

            if isinstance(operation, DeleteFileOpV2):
                simulated_sources[operation.file] = None
                mutated_files.add(operation.file)
                continue

            try:
                if isinstance(operation, ReplaceNodeOpV2):
                    span = self._resolve_unique_selector_span(
                        operation.language,
                        current_source,
                        operation.selector,
                        operation.file,
                    )
                    if span is None:
                        issues.append(
                            self._missing_or_conflict_issue(
                                index=index,
                                file=operation.file,
                                selector=operation.selector,
                                original_source=original_sources.get(operation.file),
                                mutated=operation.file in mutated_files,
                            )
                        )
                        continue
                    start, end = span
                    simulated_sources[operation.file] = current_source[:start] + operation.content + current_source[end:]
                    mutated_files.add(operation.file)
                    continue

                if isinstance(operation, InsertAfterNodeOpV2):
                    span = self._resolve_unique_selector_span(
                        operation.language,
                        current_source,
                        operation.selector,
                        operation.file,
                    )
                    if span is None:
                        issues.append(
                            self._missing_or_conflict_issue(
                                index=index,
                                file=operation.file,
                                selector=operation.selector,
                                original_source=original_sources.get(operation.file),
                                mutated=operation.file in mutated_files,
                            )
                        )
                        continue
                    _start, end = span
                    insertion = operation.content
                    if insertion and not insertion.endswith("\n"):
                        insertion = insertion + "\n"
                    simulated_sources[operation.file] = current_source[:end] + insertion + current_source[end:]
                    mutated_files.add(operation.file)
                    continue

                # Handle SearchReplaceOpV2 (Fast Apply)
                if isinstance(operation, SearchReplaceOpV2):
                    if operation.search not in current_source:
                        code = PatchFailureCode.ANCHOR_MISSING
                        if operation.file in mutated_files:
                            code = PatchFailureCode.ORDER_CONFLICT
                        issues.append(
                            PatchPreflightIssue(
                                op_index=index,
                                code=code,
                                file=operation.file,
                                message=f"Search text not found in file",
                            )
                        )
                        continue

                    occurrences = current_source.count(operation.search)
                    if occurrences > 1:
                        issues.append(
                            PatchPreflightIssue(
                                op_index=index,
                                code=PatchFailureCode.ANCHOR_AMBIGUOUS,
                                file=operation.file,
                                message=f"Search text appears {occurrences} times (must be unique)",
                            )
                        )
                        continue

                    # Simulate replacement
                    simulated_sources[operation.file] = current_source.replace(operation.search, operation.replace, 1)
                    mutated_files.add(operation.file)
                    continue

                # Handle ApplyDiffOpV2 (Unified Diff)
                if isinstance(operation, ApplyDiffOpV2):
                    try:
                        # Parse and validate diff
                        try:
                            from unidiff import PatchSet
                        except ImportError:
                            issues.append(
                                PatchPreflightIssue(
                                    op_index=index,
                                    code=PatchFailureCode.APPLY_ERROR,
                                    file=operation.file,
                                    message="unidiff library required for diff operations",
                                )
                            )
                            continue

                        # Parse Codex-style format if present
                        diff_content = self._parse_codex_diff(operation.diff)
                        full_diff = f"--- a/{operation.file}\n+++ b/{operation.file}\n{diff_content}"
                        patch_set = PatchSet(full_diff)

                        if len(patch_set) != 1:
                            issues.append(
                                PatchPreflightIssue(
                                    op_index=index,
                                    code=PatchFailureCode.APPLY_ERROR,
                                    file=operation.file,
                                    message=f"Diff must target single file, got {len(patch_set)} files",
                                )
                            )
                            continue

                        # Simulate application
                        lines = current_source.splitlines(keepends=True)
                        offset = 0

                        for hunk in patch_set[0]:
                            source_start = hunk.source_start - 1 + offset
                            source_length = hunk.source_length

                            if source_start < 0 or source_start + source_length > len(lines):
                                issues.append(
                                    PatchPreflightIssue(
                                        op_index=index,
                                        code=PatchFailureCode.RANGE_INVALID,
                                        file=operation.file,
                                        message=f"Hunk @@ -{hunk.source_start},{source_length} out of range",
                                    )
                                )
                                break

                            # Validate context
                            # Normalize line endings for comparison (unidiff may not include trailing newlines)
                            expected = [l.value for l in hunk if l.is_context or l.is_removed]
                            actual = lines[source_start : source_start + source_length]
                            
                            # Normalize by ensuring both have consistent line endings
                            expected_normalized = [line.rstrip('\n') + '\n' if not line.endswith('\n') else line for line in expected]
                            actual_normalized = [line.rstrip('\n') + '\n' for line in actual]
                            
                            # For the last line, handle the case where it might not have a newline
                            if expected_normalized and not expected[-1].endswith('\n'):
                                expected_normalized[-1] = expected_normalized[-1].rstrip('\n')
                                actual_normalized[-1] = actual_normalized[-1].rstrip('\n')

                            if actual_normalized != expected_normalized:
                                code = PatchFailureCode.ANCHOR_MISSING
                                if operation.file in mutated_files:
                                    code = PatchFailureCode.ORDER_CONFLICT
                                issues.append(
                                    PatchPreflightIssue(
                                        op_index=index,
                                        code=code,
                                        file=operation.file,
                                        message=f"Hunk context mismatch at line {hunk.source_start}",
                                    )
                                )
                                break

                            # Apply to simulation
                            new_lines = [l.value for l in hunk if not l.is_removed]
                            lines[source_start : source_start + source_length] = new_lines
                            offset += len(new_lines) - source_length

                        if not issues or issues[-1].op_index != index:
                            simulated_sources[operation.file] = "".join(lines)
                            mutated_files.add(operation.file)

                    except Exception as exc:
                        issues.append(
                            PatchPreflightIssue(
                                op_index=index,
                                code=PatchFailureCode.APPLY_ERROR,
                                file=operation.file,
                                message=f"Diff validation failed: {exc}",
                            )
                        )
                    continue

            except SelectorAmbiguousError as exc:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.ANCHOR_AMBIGUOUS,
                        file=operation.file,
                        message=str(exc),
                    )
                )
                continue
            except ParserUnavailableError as exc:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.PARSER_UNAVAILABLE,
                        file=operation.file,
                        message=str(exc),
                    )
                )
                continue
            except RuntimeError as exc:
                issues.append(
                    PatchPreflightIssue(
                        op_index=index,
                        code=PatchFailureCode.APPLY_ERROR,
                        file=operation.file,
                        message=str(exc),
                    )
                )
                continue

        return PatchPreflightReport(success=not issues, issues=issues)

    async def apply_patch_candidate(
        self,
        base_dir: str | Path,
        candidate: PatchCandidateV2,
        *,
        allowed_files: set[str] | None = None,
    ) -> PatchResult:
        base_path = Path(base_dir).resolve()
        report = await self.preflight_patch_candidate(
            base_path,
            candidate,
            allowed_files=allowed_files,
        )
        if not report.success:
            if report.issues and report.issues[0].code == PatchFailureCode.POLICY_VIOLATION:
                raise PatchPolicyViolation(report.issues[0].message)
            details = "; ".join(issue.message for issue in report.issues[:3])
            raise RuntimeError(f"Patch preflight failed: {details}")

        touched: set[str] = set()
        for operation in candidate.patch_ops:
            if isinstance(operation, ReplaceNodeOpV2):
                self._apply_replace_node(base_path, operation)
            elif isinstance(operation, InsertAfterNodeOpV2):
                self._apply_insert_after_node(base_path, operation)
            elif isinstance(operation, SearchReplaceOpV2):
                self._apply_search_replace(base_path, operation)
            elif isinstance(operation, ApplyDiffOpV2):
                self._apply_diff(base_path, operation)
            elif isinstance(operation, CreateFileOpV2):
                self._apply_create_file(base_path, operation)
            elif isinstance(operation, DeleteFileOpV2):
                self._apply_delete_file(base_path, operation)
            else:
                msg = f"Unsupported patch operation type: {type(operation).__name__}"
                raise RuntimeError(msg)
            touched.add(operation.file)

        return PatchResult(touched_files=sorted(touched))

    def _missing_or_conflict_issue(
        self,
        *,
        index: int,
        file: str,
        selector: NodeSelector,
        original_source: str | None,
        mutated: bool,
    ) -> PatchPreflightIssue:
        code = PatchFailureCode.ANCHOR_MISSING
        message = f"Selector '{selector.value}' not found in {file}"
        if mutated and original_source:
            try:
                original_spans = self._find_symbol_offsets(original_source, selector.value, selector.match)
            except Exception:
                original_spans = []
            if original_spans:
                code = PatchFailureCode.ORDER_CONFLICT
                message = f"Selector '{selector.value}' was invalidated by earlier operation in {file}"

        return PatchPreflightIssue(
            op_index=index,
            code=code,
            file=file,
            message=message,
        )

    def _apply_replace_node(self, base_path: Path, operation: ReplaceNodeOpV2) -> None:
        target = self._resolve_inside(base_path, operation.file)
        source = target.read_text(encoding="utf-8")
        if operation.language == "python":
            updated = self._apply_python_replace_node(
                source,
                selector=operation.selector,
                replacement_source=operation.content,
                file_path=operation.file,
            )
        else:
            span = self._resolve_unique_selector_span(
                operation.language,
                source,
                operation.selector,
                operation.file,
            )
            if span is None:
                msg = f"Selector '{operation.selector.value}' not found in {operation.file}"
                raise RuntimeError(msg)
            start, end = span
            updated = source[:start] + operation.content + source[end:]
        target.write_text(updated, encoding="utf-8")

    def _apply_insert_after_node(self, base_path: Path, operation: InsertAfterNodeOpV2) -> None:
        target = self._resolve_inside(base_path, operation.file)
        source = target.read_text(encoding="utf-8")
        if operation.language == "python":
            updated = self._apply_python_insert_after_node(
                source,
                selector=operation.selector,
                insertion_source=operation.content,
                file_path=operation.file,
            )
        else:
            span = self._resolve_unique_selector_span(
                operation.language,
                source,
                operation.selector,
                operation.file,
            )
            if span is None:
                msg = f"Selector '{operation.selector.value}' not found in {operation.file}"
                raise RuntimeError(msg)
            _start, end = span
            insertion = operation.content
            if insertion and not insertion.endswith("\n"):
                insertion = insertion + "\n"
            updated = source[:end] + insertion + source[end:]
        target.write_text(updated, encoding="utf-8")

    def _apply_python_replace_node(
        self,
        source: str,
        *,
        selector: NodeSelector,
        replacement_source: str,
        file_path: str,
    ) -> str:
        if cst is None or PositionProvider is None:
            msg = "libcst is required for Python AST patching"
            raise ParserUnavailableError(msg)
        module = cst.parse_module(source)
        matches = self._python_declaration_matches(
            source,
            symbol=selector.value,
            match=selector.match,
        )
        if not matches:
            msg = f"Selector '{selector.value}' not found in {file_path}"
            raise RuntimeError(msg)
        if len(matches) > 1:
            msg = f"Selector '{selector.value}' is ambiguous in {file_path}; matched {len(matches)} nodes"
            raise SelectorAmbiguousError(msg)
        target = matches[0]

        replacement_statements = self._python_parse_statements(
            replacement_source,
            file_path=file_path,
        )
        if len(replacement_statements) != 1:
            msg = f"replace_node for Python requires exactly one declaration statement in {file_path}"
            raise RuntimeError(msg)
        replacement_stmt = replacement_statements[0]
        if not isinstance(replacement_stmt, (cst.ClassDef, cst.FunctionDef)):
            msg = f"replace_node for Python requires class/def replacement in {file_path}"
            raise RuntimeError(msg)

        class _ReplaceTransformer(cst.CSTTransformer):
            METADATA_DEPENDENCIES = (PositionProvider,)

            def _is_target(self, node: cst.CSTNode, kind: str) -> bool:
                position = self.get_metadata(PositionProvider, node)
                return (
                    target.kind == kind
                    and target.start_line == position.start.line
                    and target.start_col == position.start.column
                    and target.end_line == position.end.line
                    and target.end_col == position.end.column
                )

            def leave_ClassDef(
                self,
                original_node: cst.ClassDef,  # noqa: N803
                updated_node: cst.ClassDef,  # noqa: N803
            ) -> cst.BaseStatement:
                if self._is_target(original_node, "class"):
                    return replacement_stmt
                return updated_node

            def leave_FunctionDef(
                self,
                original_node: cst.FunctionDef,  # noqa: N803
                updated_node: cst.FunctionDef,  # noqa: N803
            ) -> cst.BaseStatement:
                if self._is_target(original_node, "function"):
                    return replacement_stmt
                return updated_node

        wrapper = cst.MetadataWrapper(module)
        updated = wrapper.visit(_ReplaceTransformer())
        return updated.code

    def _apply_python_insert_after_node(
        self,
        source: str,
        *,
        selector: NodeSelector,
        insertion_source: str,
        file_path: str,
    ) -> str:
        if cst is None or PositionProvider is None:
            msg = "libcst is required for Python AST patching"
            raise ParserUnavailableError(msg)
        module = cst.parse_module(source)
        matches = self._python_declaration_matches(
            source,
            symbol=selector.value,
            match=selector.match,
        )
        if not matches:
            msg = f"Selector '{selector.value}' not found in {file_path}"
            raise RuntimeError(msg)
        if len(matches) > 1:
            msg = f"Selector '{selector.value}' is ambiguous in {file_path}; matched {len(matches)} nodes"
            raise SelectorAmbiguousError(msg)
        target = matches[0]
        insertion_stmts = self._python_parse_statements(insertion_source, file_path=file_path)

        class _InsertAfterTransformer(cst.CSTTransformer):
            METADATA_DEPENDENCIES = (PositionProvider,)

            def _is_target(self, node: cst.CSTNode, kind: str) -> bool:
                position = self.get_metadata(PositionProvider, node)
                return (
                    target.kind == kind
                    and target.start_line == position.start.line
                    and target.start_col == position.start.column
                    and target.end_line == position.end.line
                    and target.end_col == position.end.column
                )

            def leave_ClassDef(
                self,
                original_node: cst.ClassDef,  # noqa: N803
                updated_node: cst.ClassDef,  # noqa: N803
            ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement]:
                if self._is_target(original_node, "class"):
                    return cst.FlattenSentinel([updated_node, *insertion_stmts])
                return updated_node

            def leave_FunctionDef(
                self,
                original_node: cst.FunctionDef,  # noqa: N803
                updated_node: cst.FunctionDef,  # noqa: N803
            ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement]:
                if self._is_target(original_node, "function"):
                    return cst.FlattenSentinel([updated_node, *insertion_stmts])
                return updated_node

        wrapper = cst.MetadataWrapper(module)
        updated = wrapper.visit(_InsertAfterTransformer())
        return updated.code

    def _python_parse_statements(
        self,
        content: str,
        *,
        file_path: str,
    ) -> list["cst.BaseStatement"]:
        if cst is None:
            msg = "libcst is required for Python AST patching"
            raise ParserUnavailableError(msg)
        try:
            parsed = cst.parse_module(content)
        except Exception as exc:  # pragma: no cover - parser errors vary
            raise RuntimeError(f"Python parse error in replacement for {file_path}: {exc}") from exc
        statements = list(parsed.body)
        if not statements:
            msg = f"Python replacement/insertion content is empty for {file_path}"
            raise RuntimeError(msg)
        return statements

    def _resolve_unique_selector_span(
        self,
        language: Literal["python", "typescript", "rust"],
        source: str,
        selector: NodeSelector,
        file_path: str,
    ) -> tuple[int, int] | None:
        if selector.kind != "symbol":
            msg = f"Unsupported selector kind '{selector.kind}' in {file_path}"
            raise RuntimeError(msg)

        if language == "python":
            spans = self._python_symbol_spans(source, selector.value, selector.match)
        elif language in {"typescript", "rust"}:
            spans = self._treesitter_symbol_spans(language, source, selector.value, selector.match)
        else:
            msg = f"Unsupported selector language '{language}' in {file_path}"
            raise RuntimeError(msg)

        if not spans:
            return None
        if len(spans) > 1:
            msg = f"Selector '{selector.value}' is ambiguous in {file_path}; matched {len(spans)} nodes"
            raise SelectorAmbiguousError(msg)
        return spans[0]

    def _python_symbol_spans(
        self,
        source: str,
        symbol: str,
        match: Literal["exact", "contains"],
    ) -> list[tuple[int, int]]:
        matches = self._python_declaration_matches(source, symbol=symbol, match=match)
        line_starts = self._line_start_offsets(source)
        spans: list[tuple[int, int]] = []
        for item in matches:
            start = self._offset(line_starts, item.start_line, item.start_col)
            end = self._offset(line_starts, item.end_line, item.end_col)
            spans.append((start, end))
        return spans

    def _python_declaration_matches(
        self,
        source: str,
        *,
        symbol: str,
        match: Literal["exact", "contains"],
    ) -> list[PythonDeclMatch]:
        if cst is None or PositionProvider is None:
            msg = "libcst is required for Python AST patching"
            raise ParserUnavailableError(msg)

        try:
            module = cst.parse_module(source)
        except Exception as exc:  # pragma: no cover - parser errors vary
            raise RuntimeError(f"Python parse error: {exc}") from exc

        class _DeclVisitor(cst.CSTVisitor):
            METADATA_DEPENDENCIES = (PositionProvider,)

            def __init__(self) -> None:
                self.items: list[PythonDeclMatch] = []

            def _is_match(self, name: str) -> bool:
                if match == "exact":
                    return name == symbol
                return symbol in name

            def visit_ClassDef(self, node: cst.ClassDef) -> None:  # noqa: N802
                name = node.name.value
                if not self._is_match(name):
                    return
                position = self.get_metadata(PositionProvider, node)
                self.items.append(
                    PythonDeclMatch(
                        kind="class",
                        name=name,
                        start_line=position.start.line,
                        start_col=position.start.column,
                        end_line=position.end.line,
                        end_col=position.end.column,
                    )
                )

            def visit_FunctionDef(self, node: cst.FunctionDef) -> None:  # noqa: N802
                name = node.name.value
                if not self._is_match(name):
                    return
                position = self.get_metadata(PositionProvider, node)
                self.items.append(
                    PythonDeclMatch(
                        kind="function",
                        name=name,
                        start_line=position.start.line,
                        start_col=position.start.column,
                        end_line=position.end.line,
                        end_col=position.end.column,
                    )
                )

        wrapper = cst.MetadataWrapper(module)
        visitor = _DeclVisitor()
        wrapper.visit(visitor)
        return visitor.items

    def _treesitter_symbol_spans(
        self,
        language: Literal["typescript", "rust"],
        source: str,
        symbol: str,
        match: Literal["exact", "contains"],
    ) -> list[tuple[int, int]]:
        parser = self._get_tree_sitter_parser(language)
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)

        spans: list[tuple[int, int]] = []
        stack = [tree.root_node]
        declaration_kinds = (
            {
                "function_declaration",
                "method_definition",
                "class_declaration",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
                "variable_declarator",
            }
            if language == "typescript"
            else {
                "function_item",
                "struct_item",
                "enum_item",
                "trait_item",
                "impl_item",
                "mod_item",
                "type_item",
            }
        )
        while stack:
            node = stack.pop()
            if node.kind in declaration_kinds:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name_text = source_bytes[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8",
                        errors="ignore",
                    )
                else:
                    name_text = source_bytes[node.start_byte : node.end_byte].decode(
                        "utf-8",
                        errors="ignore",
                    )[:120]
                if self._selector_matches(name_text, symbol, match):
                    spans.append((int(node.start_byte), int(node.end_byte)))
            for child in node.children:
                stack.append(child)
        return self._dedupe_spans(spans)

    def _selector_matches(
        self,
        text: str,
        symbol: str,
        match: Literal["exact", "contains"],
    ) -> bool:
        if match == "contains":
            return symbol in text
        pattern = re.compile(rf"\\b{re.escape(symbol)}\\b")
        return bool(pattern.search(text))

    def _find_symbol_offsets(
        self,
        source: str,
        symbol: str,
        match: Literal["exact", "contains"],
    ) -> list[tuple[int, int]]:
        if match == "contains":
            indices: list[tuple[int, int]] = []
            start = 0
            while True:
                idx = source.find(symbol, start)
                if idx == -1:
                    break
                indices.append((idx, idx + len(symbol)))
                start = idx + len(symbol)
            return indices
        pattern = re.compile(rf"\\b{re.escape(symbol)}\\b")
        return [(item.start(), item.end()) for item in pattern.finditer(source)]

    def _get_tree_sitter_parser(self, language: Literal["typescript", "rust"]):
        if not self._tree_sitter_ready:
            try:
                from tree_sitter_languages import get_parser  # type: ignore
            except Exception as exc:
                msg = "tree_sitter_languages is required for TypeScript/Rust AST patching"
                raise ParserUnavailableError(msg) from exc

            self._ts_parser = get_parser("typescript")
            self._rs_parser = get_parser("rust")
            self._tree_sitter_ready = True

        if language == "typescript":
            return self._ts_parser
        return self._rs_parser

    def _line_start_offsets(self, source: str) -> list[int]:
        starts = [0]
        for idx, char in enumerate(source):
            if char == "\n":
                starts.append(idx + 1)
        return starts

    def _offset(self, line_starts: list[int], lineno: int, col: int) -> int:
        index = max(0, min(lineno - 1, len(line_starts) - 1))
        return line_starts[index] + max(col, 0)

    def _dedupe_spans(self, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        deduped = sorted(set(spans))
        if not deduped:
            return deduped
        # Keep only minimal spans when one span fully contains another.
        minimal: list[tuple[int, int]] = []
        for span in deduped:
            start, end = span
            contains_other = False
            for other in deduped:
                if other == span:
                    continue
                o_start, o_end = other
                if start <= o_start and end >= o_end:
                    contains_other = True
                    break
            if not contains_other:
                minimal.append(span)
        return minimal or deduped
