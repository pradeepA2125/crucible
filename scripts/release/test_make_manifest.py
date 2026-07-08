import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_manifest import build_manifest  # noqa: E402


def _touch(d: Path, name: str, content: bytes = b"bin") -> None:
    (d / name).write_bytes(content)


def test_build_manifest_shape(tmp_path: Path) -> None:
    for plat in ("darwin-arm64", "darwin-x64", "linux-x64"):
        _touch(tmp_path, f"ai-editor-indexer-{plat}")
        _touch(tmp_path, f"rg-{plat}")
        _touch(tmp_path, f"uv-{plat}")
        _touch(tmp_path, f"rust-analyzer-{plat}")
    _touch(tmp_path, "ai-editor-indexer-win32-x64.exe")
    _touch(tmp_path, "rg-win32-x64.exe")
    _touch(tmp_path, "uv-win32-x64.exe")
    _touch(tmp_path, "rust-analyzer-win32-x64.exe")
    _touch(tmp_path, "ai_editor_agentd-0.2.0-py3-none-any.whl")

    m = build_manifest(
        "v0.2.0", tmp_path, "https://gh/rel/v0.2.0",
        component_versions={
            "indexer": "0.2.0", "ripgrep": "14.1.0", "uv": "0.5.0",
            "rust-analyzer": "2026-07-06",
        },
        lsp_packages=["pyright@1.1.400", "typescript-language-server@4.3.3"],
    )
    assert m["manifestVersion"] == 1 and m["releaseTag"] == "v0.2.0"
    ix = m["components"]["indexer"]
    assert ix["urls"]["darwin-arm64"] == "https://gh/rel/v0.2.0/ai-editor-indexer-darwin-arm64"
    assert ix["urls"]["win32-x64"].endswith(".exe")
    assert ix["sha256"]["darwin-arm64"] == hashlib.sha256(b"bin").hexdigest()
    ra = m["components"]["rust-analyzer"]
    assert ra["version"] == "2026-07-06"
    assert ra["urls"]["darwin-arm64"] == "https://gh/rel/v0.2.0/rust-analyzer-darwin-arm64"
    assert ra["urls"]["win32-x64"].endswith(".exe")
    assert ra["sha256"]["darwin-arm64"] == hashlib.sha256(b"bin").hexdigest()
    agentd = m["components"]["agentd"]
    assert agentd["version"] == "0.2.0"
    assert agentd["urls"]["any"].endswith("ai_editor_agentd-0.2.0-py3-none-any.whl")
    assert m["components"]["lsps"]["npmPackages"] == [
        "pyright@1.1.400", "typescript-language-server@4.3.3"]


def test_missing_platform_artifact_raises(tmp_path: Path) -> None:
    _touch(tmp_path, "ai-editor-indexer-darwin-arm64")
    import pytest
    with pytest.raises(FileNotFoundError, match="rg-darwin-arm64"):
        build_manifest("v1", tmp_path, "u",
                       component_versions={"indexer": "1", "ripgrep": "1", "uv": "1"},
                       lsp_packages=[])
