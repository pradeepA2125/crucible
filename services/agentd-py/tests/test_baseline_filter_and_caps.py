from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from agentd.domain.models import Diagnostic, ValidationResult
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.workspace.shadow import ShadowWorkspaceManager


class _StubReasoner:
    pass


class _StubValidator:
    def __init__(self, result: ValidationResult) -> None:
        self._result = result

    async def run(self, workspace_path: str) -> ValidationResult:
        _ = workspace_path
        return self._result


def _make_orchestrator(tmp_path: Path, validator: _StubValidator | None = None) -> AgentOrchestrator:
    return AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoning_engine=_StubReasoner(),
        validator=validator
        or _StubValidator(ValidationResult(success=True, diagnostics=[], duration_ms=0)),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"),
    )


def _diag(
    message: str, level: Literal["error", "warning"] = "error", source: str = "mypy"
) -> Diagnostic:
    return Diagnostic(source=source, message=message, level=level)


def test_filter_removes_preexisting_warning(tmp_path: Path) -> None:
    """A pre-existing WARNING must be stripped — it's the repair-context bloat source."""
    orch = _make_orchestrator(tmp_path)
    baseline = frozenset({orch._normalize_error_message("preexisting ruff noise")})
    result = ValidationResult(
        success=False,
        diagnostics=[
            _diag("preexisting ruff noise", level="warning", source="ruff"),
            _diag("brand new failure", level="error"),
        ],
        duration_ms=1,
    )
    filtered = orch._filter_baseline_errors(result, baseline)
    msgs = [d.message for d in filtered.diagnostics]
    assert "preexisting ruff noise" not in msgs
    assert "brand new failure" in msgs
    assert filtered.success is False  # a genuinely-new error still fails the run


def test_filter_keeps_new_warning_and_passes(tmp_path: Path) -> None:
    """Pre-existing error stripped; a brand-new warning is kept but does NOT fail."""
    orch = _make_orchestrator(tmp_path)
    baseline = frozenset({orch._normalize_error_message("old error")})
    result = ValidationResult(
        success=False,
        diagnostics=[
            _diag("old error", level="error"),
            _diag("new lint warning", level="warning"),
        ],
        duration_ms=1,
    )
    filtered = orch._filter_baseline_errors(result, baseline)
    msgs = [d.message for d in filtered.diagnostics]
    assert "old error" not in msgs
    assert "new lint warning" in msgs
    assert filtered.success is True  # only a warning remains -> success unchanged


@pytest.mark.asyncio
async def test_baseline_capture_includes_warnings(tmp_path: Path) -> None:
    validator = _StubValidator(
        ValidationResult(
            success=False,
            diagnostics=[
                _diag("pre-existing warning", level="warning", source="ruff"),
                _diag("pre-existing error", level="error"),
            ],
            duration_ms=1,
        )
    )
    orch = _make_orchestrator(tmp_path, validator=validator)
    baseline = await orch._collect_baseline_errors(tmp_path)
    assert orch._normalize_error_message("pre-existing warning") in baseline
    assert orch._normalize_error_message("pre-existing error") in baseline


def test_cap_diagnostic_message_bounds_and_keeps_head_tail(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path)
    big = "HEAD_MARKER" + ("x" * 200_000) + "TAIL_MARKER"
    capped = orch._cap_diagnostic_message(big, limit=5000)
    assert len(capped) < 5200  # bounded near the limit plus marker overhead
    assert capped.startswith("HEAD_MARKER")  # error type/location preserved
    assert capped.endswith("TAIL_MARKER")  # pytest/cargo summary preserved
    assert "truncated" in capped


def test_cap_diagnostic_message_short_passthrough(tmp_path: Path) -> None:
    orch = _make_orchestrator(tmp_path)
    msg = "short error message"
    assert orch._cap_diagnostic_message(msg, limit=5000) == msg
