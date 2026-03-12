from __future__ import annotations

from pathlib import Path

import pytest

from agentd.domain.models import PatchCandidateV2, PatchDocument, PatchFailureCode
from agentd.patch.engine import ParserUnavailableError, PatchEngine
from agentd.patch.policy import PatchPolicyViolation


@pytest.mark.asyncio
async def test_patch_engine_applies_operations(tmp_path: Path) -> None:
    engine = PatchEngine()

    target = tmp_path / "a.txt"
    target.write_text("line1\nline2\nline3", encoding="utf-8")

    patch = PatchDocument.model_validate(
        {
            "patch_ops": [
                {
                    "op": "replace_range",
                    "file": "a.txt",
                    "anchor": {"start_line": 2, "end_line": 2},
                    "content": "replaced",
                    "reason": "test",
                },
                {
                    "op": "create_file",
                    "file": "nested/b.txt",
                    "content": "hello",
                    "reason": "test",
                },
            ]
        }
    )

    result = await engine.apply_patch_document(tmp_path, patch)
    assert set(result.touched_files) == {"a.txt", "nested/b.txt"}
    assert target.read_text(encoding="utf-8") == "line1\nreplaced\nline3"
    assert (tmp_path / "nested/b.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_patch_engine_rejects_forbidden_paths(tmp_path: Path) -> None:
    engine = PatchEngine()
    patch = PatchDocument.model_validate(
        {
            "patch_ops": [
                {
                    "op": "create_file",
                    "file": ".env",
                    "content": "SECRET=1",
                    "reason": "bad",
                }
            ]
        }
    )

    with pytest.raises(PatchPolicyViolation):
        await engine.apply_patch_document(tmp_path, patch)


@pytest.mark.asyncio
async def test_patch_preflight_rejects_scope_violation(tmp_path: Path) -> None:
    engine = PatchEngine()
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    patch = PatchDocument.model_validate(
        {
            "patch_ops": [
                {
                    "op": "replace_range",
                    "file": "a.txt",
                    "anchor": {"start_line": 1, "end_line": 1},
                    "content": "updated",
                    "reason": "test",
                }
            ]
        }
    )

    report = await engine.preflight_patch_document(
        tmp_path,
        patch,
        allowed_files={"b.txt"},
    )
    assert not report.success
    assert report.issues[0].code == PatchFailureCode.SCOPE_VIOLATION


@pytest.mark.asyncio
async def test_patch_preflight_rejects_python_unsafe_insert(tmp_path: Path) -> None:
    engine = PatchEngine()
    target = tmp_path / "x.py"
    target.write_text("class X:\n    pass\n", encoding="utf-8")
    patch = PatchDocument.model_validate(
        {
            "patch_ops": [
                {
                    "op": "insert_after_symbol",
                    "file": "x.py",
                    "anchor": {"symbol": "class X"},
                    "content": "    y = 1",
                    "reason": "unsafe",
                }
            ]
        }
    )

    report = await engine.preflight_patch_document(tmp_path, patch)
    assert not report.success
    assert report.issues[0].code == PatchFailureCode.PYTHON_UNSAFE_INSERT


@pytest.mark.asyncio
async def test_patch_preflight_rejects_order_conflict_when_anchor_invalidated(tmp_path: Path) -> None:
    engine = PatchEngine()
    target = tmp_path / "x.py"
    target.write_text("value = 1\nprint(value)\n", encoding="utf-8")
    patch = PatchDocument.model_validate(
        {
            "patch_ops": [
                {
                    "op": "replace_range",
                    "file": "x.py",
                    "anchor": {"start_line": 2, "end_line": 2},
                    "content": "value = value + 1",
                    "reason": "remove print anchor",
                },
                {
                    "op": "insert_after_symbol",
                    "file": "x.py",
                    "anchor": {"symbol": "print"},
                    "content": "print('done')",
                    "reason": "uses removed anchor",
                },
            ]
        }
    )

    report = await engine.preflight_patch_document(tmp_path, patch)
    assert not report.success
    assert any(issue.code == PatchFailureCode.ORDER_CONFLICT for issue in report.issues)


@pytest.mark.asyncio
async def test_patch_preflight_allows_safe_insert_after_symbol_after_replace(tmp_path: Path) -> None:
    engine = PatchEngine()
    target = tmp_path / "x.py"
    target.write_text("value = 1\nprint(value)\n", encoding="utf-8")
    patch = PatchDocument.model_validate(
        {
            "patch_ops": [
                {
                    "op": "replace_range",
                    "file": "x.py",
                    "anchor": {"start_line": 1, "end_line": 1},
                    "content": "value = 2",
                    "reason": "update",
                },
                {
                    "op": "insert_after_symbol",
                    "file": "x.py",
                    "anchor": {"symbol": "print"},
                    "content": "print('done')",
                    "reason": "safe ordering",
                },
            ]
        }
    )

    report = await engine.preflight_patch_document(tmp_path, patch)
    assert report.success
    assert report.issues == []


@pytest.mark.asyncio
async def test_patch_candidate_preflight_reports_parser_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = PatchEngine()
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src/main.ts").write_text("function build() { return 0; }\n", encoding="utf-8")
    candidate = PatchCandidateV2.model_validate(
        {
            "candidate_id": "c1",
            "patch_ops": [
                {
                    "op": "replace_node",
                    "file": "src/main.ts",
                    "language": "typescript",
                    "selector": {"kind": "symbol", "value": "build", "match": "exact"},
                    "content": "function build() { return 1; }\n",
                    "reason": "test",
                }
            ],
        }
    )

    def _raise_parser_unavailable(language: str) -> object:
        _ = language
        raise ParserUnavailableError("tree_sitter_languages unavailable")

    monkeypatch.setattr(engine, "_get_tree_sitter_parser", _raise_parser_unavailable)
    report = await engine.preflight_patch_candidate(tmp_path, candidate)
    assert not report.success
    assert report.issues[0].code == PatchFailureCode.PARSER_UNAVAILABLE


@pytest.mark.asyncio
async def test_patch_candidate_preflight_uses_declaration_level_matching(tmp_path: Path) -> None:
    engine = PatchEngine()
    target = tmp_path / "mod.rs"
    target.write_text(
        "fn build() -> i32 { 1 }\nfn build_helper() -> i32 { 2 }\n",
        encoding="utf-8",
    )
    candidate = PatchCandidateV2.model_validate(
        {
            "candidate_id": "c1",
            "patch_ops": [
                {
                    "op": "replace_node",
                    "file": "mod.rs",
                    "language": "rust",
                    "selector": {"kind": "symbol", "value": "build", "match": "exact"},
                    "content": "fn build() -> i32 { 42 }\n",
                    "reason": "test",
                }
            ],
        }
    )

    report = await engine.preflight_patch_candidate(tmp_path, candidate)
    if not report.success and report.issues[0].code == PatchFailureCode.PARSER_UNAVAILABLE:
        pytest.skip("tree_sitter_languages is not installed in this environment")
    assert report.success


@pytest.mark.asyncio
async def test_patch_candidate_applies_python_cst_replace(tmp_path: Path) -> None:
    pytest.importorskip("libcst")

    engine = PatchEngine()
    target = tmp_path / "sample.py"
    target.write_text(
        "class X:\n    def run(self) -> int:\n        return 1\n",
        encoding="utf-8",
    )
    candidate = PatchCandidateV2.model_validate(
        {
            "candidate_id": "c1",
            "patch_ops": [
                {
                    "op": "replace_node",
                    "file": "sample.py",
                    "language": "python",
                    "selector": {"kind": "symbol", "value": "run", "match": "exact"},
                    "content": "def run(self) -> int:\n    return 2\n",
                    "reason": "test",
                }
            ],
        }
    )

    report = await engine.preflight_patch_candidate(tmp_path, candidate)
    assert report.success

    result = await engine.apply_patch_candidate(tmp_path, candidate)
    assert result.touched_files == ["sample.py"]
    assert "return 2" in target.read_text(encoding="utf-8")
