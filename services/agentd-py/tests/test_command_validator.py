from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentd.validation.command_validator import CommandValidator, ValidationCommand


@pytest.mark.asyncio
async def test_command_validator_with_explicit_commands_passes(tmp_path: Path) -> None:
    validator = CommandValidator(
        configured_commands=[
            ValidationCommand(
                stage="syntax",
                name="echo",
                command=f'"{sys.executable}" -c "print(\'ok\')"',
            )
        ]
    )

    result = await validator.run(str(tmp_path))
    assert result.success
    assert result.diagnostics == []


@pytest.mark.asyncio
async def test_command_validator_reports_failure(tmp_path: Path) -> None:
    validator = CommandValidator(
        configured_commands=[
            ValidationCommand(
                stage="test",
                name="force-fail",
                command=f'"{sys.executable}" -c "import sys; sys.exit(2)"',
            )
        ]
    )

    result = await validator.run(str(tmp_path))
    assert not result.success
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].source == "validator:force-fail"
    assert result.diagnostics[0].level == "error"


@pytest.mark.asyncio
async def test_command_validator_fails_when_no_commands_detected(tmp_path: Path) -> None:
    validator = CommandValidator(configured_commands=None)

    result = await validator.run(str(tmp_path))
    assert not result.success
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].source == "validator"


@pytest.mark.asyncio
async def test_run_touched_python_syntax_error_fails(tmp_path: Path) -> None:
    validator = CommandValidator(configured_commands=None)
    target = tmp_path / "bad.py"
    target.write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = await validator.run_touched(str(tmp_path), ["bad.py"])
    assert not result.success
    assert any(
        diagnostic.source == "validator:fast-python-compile"
        and diagnostic.level == "error"
        for diagnostic in result.diagnostics
    )


@pytest.mark.asyncio
async def test_run_touched_typescript_unavailable_is_warning_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validator = CommandValidator(configured_commands=None)
    target = tmp_path / "x.ts"
    target.write_text("const x: number = 1;", encoding="utf-8")

    async def _fake_support(workspace_path: Path) -> tuple[bool, str | None]:
        _ = workspace_path
        return False, "typescript unavailable for test"

    monkeypatch.setattr(validator, "_check_typescript_fast_support", _fake_support)
    result = await validator.run_touched(str(tmp_path), ["x.ts"])
    assert result.success
    assert any(
        diagnostic.source == "validator:fast-typescript"
        and diagnostic.level == "warning"
        for diagnostic in result.diagnostics
    )
