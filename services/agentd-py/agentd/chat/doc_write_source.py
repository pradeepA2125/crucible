"""DocWriteToolSource — per-write-gated writes of non-executable files.

The lightweight alternative to EDIT mode for standalone artifacts (docs, diagrams,
data): one `write_doc(path, content)` tool, extension-allowlisted, every call pauses
for a live doc_write approval card, approve lands the file directly in the REAL
workspace. Validation failures return is_error output WITHOUT raising a gate, so the
model can self-correct cheaply. No remember-rule store: every write is unique content.
"""
from __future__ import annotations

import difflib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from agentd.patch.diffing import cap_unified_diff
from agentd.tools.registry import ToolDefinition, ToolOutput

DOC_WRITE_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".mmd", ".mermaid", ".txt", ".rst", ".adoc",
    ".svg", ".json", ".yaml", ".yml", ".csv",
})

_MAX_CONTENT_BYTES = 1_048_576  # 1 MB — standalone docs, not bulk data dumps

ApprovalCallback = Callable[[str, bool, str], Awaitable[bool]]


def doc_write_decision_timeout_sec() -> float:
    """0 = wait forever (mirrors CRUCIBLE_MCP_DECISION_TIMEOUT_SEC)."""
    raw = os.getenv("CRUCIBLE_DOC_WRITE_DECISION_TIMEOUT_SEC", "").strip()
    try:
        val = float(raw)
    except ValueError:
        return 0.0
    return val if val >= 0 else 0.0


class DocWriteToolSource:
    name = "doc_write"

    def __init__(self, workspace_path: str | Path, approval_callback: ApprovalCallback) -> None:
        self._workspace = Path(workspace_path)
        self._approve = approval_callback

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="write_doc",
            description=(
                "Write ONE standalone non-executable file (docs, diagrams, data: "
                + ", ".join(sorted(DOC_WRITE_ALLOWED_EXTENSIONS))
                + ") directly to the workspace. Each call pauses for a user approval "
                "card showing the path and a preview/diff — that pause is expected. "
                "For source-code changes use the edit flow instead."),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Workspace-relative file path"},
                    "content": {"type": "string",
                                "description": "Full file content (replaces any existing)"},
                },
                "required": ["path", "content"],
            },
        )]

    def owns(self, tool: str) -> bool:
        return tool == "write_doc"

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        rel = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        if not rel:
            return ToolOutput(output="Error: write_doc requires a non-empty path", is_error=True)
        if Path(rel).is_absolute():
            return ToolOutput(
                output=f"Error: path must be workspace-relative, got absolute '{rel}'",
                is_error=True)
        suffix = Path(rel).suffix.lower()
        if suffix not in DOC_WRITE_ALLOWED_EXTENSIONS:
            return ToolOutput(
                output=(f"Error: extension '{suffix or '(none)'}' is not writable via "
                        f"write_doc (allowed: {', '.join(sorted(DOC_WRITE_ALLOWED_EXTENSIONS))}). "
                        "Use the edit flow for code files."),
                is_error=True)
        if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            return ToolOutput(
                output="Error: content exceeds the 1 MB write_doc limit — split the file "
                       "or use the edit flow.",
                is_error=True)
        target = (self._workspace / rel).resolve()
        try:
            target.relative_to(self._workspace.resolve())
        except ValueError:
            return ToolOutput(
                output=f"Error: path traversal rejected — '{rel}' is outside the workspace",
                is_error=True)

        exists = target.is_file()
        preview = self._preview(target, rel, content, exists)
        approved = await self._approve(rel, exists, preview)
        if not approved:
            return ToolOutput(
                output=(f"Doc write rejected by user: {rel}. Do not retry the same "
                        "write — adapt your approach or ask."),
                is_error=True)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolOutput(output=f"Error: writing {rel} failed: {exc}", is_error=True)
        return ToolOutput(output=f"Wrote {rel} ({len(content.encode('utf-8'))} bytes)")

    @staticmethod
    def _preview(target: Path, rel: str, content: str, exists: bool) -> str:
        """Existing file → capped unified diff; new file → capped content."""
        if not exists:
            return cap_unified_diff(content)
        old = target.read_text(encoding="utf-8", errors="replace")
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        return cap_unified_diff(diff)
