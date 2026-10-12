"""The seed's repository tree leaves out what the project itself ignores.

Live (2026-10-12): this repo's tree was 87k characters, 74% of every request's retrieval
seed, and listed 243 gitignored test leftovers (tmp-review-files-*), .ruff_cache and vector
index dirs, because the walk only knew a fixed list of names."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentd.retrieval.artifact_client import RetrievalArtifactClient


def _write(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")


def _tree(workspace: Path) -> list[str]:
    payload = {"workspace_root": str(workspace), "generated_at_ms": 1,
               "graph": {"nodes": [], "edges": []}, "diagnostics": []}
    context = RetrievalArtifactClient()._build_context(payload, "anything", 0.0, str(workspace))
    return context.repository_structure


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_gitignored_dirs_and_files_stay_out_of_the_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    for rel in ("src/app.py", "src/generated_out.py", "tmp-review-files-abc/src/a.py",
                "build_out/gen.py", ".ruff_cache/0.1/x.py"):
        _write(workspace, rel)
    (workspace / ".gitignore").write_text(
        "tmp-review-files-*/\nbuild_out/\n.ruff_cache/\nsrc/generated_out.py\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)

    lines = _tree(workspace)
    text = "\n".join(lines)

    assert "  src/ (0 dirs, 1 source files)" in lines  # generated_out.py is ignored
    assert "tmp-review-files" not in text
    assert "build_out" not in text and ".ruff_cache" not in text


def test_outside_a_git_repo_the_fixed_ignore_list_still_applies(tmp_path: Path) -> None:
    workspace = tmp_path / "plain"
    for rel in ("src/app.py", "node_modules/pkg/index.py", "build_out/gen.py"):
        _write(workspace, rel)

    lines = _tree(workspace)

    assert "  src/ (0 dirs, 1 source files)" in lines
    assert not any("node_modules" in line for line in lines)
    assert "  build_out/ (0 dirs, 1 source files)" in lines  # no .gitignore to say otherwise
