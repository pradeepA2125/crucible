import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_manifest import build_manifest  # noqa: E402


def _touch(d: Path, name: str, content: bytes = b"bin") -> None:
    (d / name).write_bytes(content)


def test_build_manifest_shape(tmp_path: Path) -> None:
    for plat in ("darwin-arm64", "darwin-x64", "linux-x64"):
        _touch(tmp_path, f"crucible-indexer-{plat}")
        _touch(tmp_path, f"rg-{plat}")
        _touch(tmp_path, f"uv-{plat}")
        _touch(tmp_path, f"rust-analyzer-{plat}")
        _touch(tmp_path, f"gopls-{plat}")
        _touch(tmp_path, f"jre-{plat}.tar.gz")
    _touch(tmp_path, "crucible-indexer-win32-x64.exe")
    _touch(tmp_path, "rg-win32-x64.exe")
    _touch(tmp_path, "uv-win32-x64.exe")
    _touch(tmp_path, "rust-analyzer-win32-x64.exe")
    _touch(tmp_path, "gopls-win32-x64.exe")
    _touch(tmp_path, "jre-win32-x64.zip")
    _touch(tmp_path, "jdtls.tar.gz")
    _touch(tmp_path, "crucible_agentd-0.2.0-py3-none-any.whl")

    m = build_manifest(
        "v0.2.0", tmp_path, "https://gh/rel/v0.2.0",
        component_versions={
            "indexer": "0.2.0", "agentd": "0.2.0", "ripgrep": "14.1.0", "uv": "0.5.0",
            "rust-analyzer": "2026-07-06", "gopls": "v0.22.0",
            "jre": "21.0.11+10", "jdtls": "1.61.0-202607070104",
        },
        lsp_packages=["pyright@1.1.400", "typescript-language-server@4.3.3"],
    )
    assert m["manifestVersion"] == 1 and m["releaseTag"] == "v0.2.0"
    ix = m["components"]["indexer"]
    assert ix["urls"]["darwin-arm64"] == "https://gh/rel/v0.2.0/crucible-indexer-darwin-arm64"
    assert ix["urls"]["win32-x64"].endswith(".exe")
    assert ix["sha256"]["darwin-arm64"] == hashlib.sha256(b"bin").hexdigest()
    ra = m["components"]["rust-analyzer"]
    assert ra["version"] == "2026-07-06"
    assert ra["urls"]["darwin-arm64"] == "https://gh/rel/v0.2.0/rust-analyzer-darwin-arm64"
    assert ra["urls"]["win32-x64"].endswith(".exe")
    assert ra["sha256"]["darwin-arm64"] == hashlib.sha256(b"bin").hexdigest()
    gopls = m["components"]["gopls"]
    assert gopls["version"] == "v0.22.0"
    assert gopls["urls"]["darwin-arm64"] == "https://gh/rel/v0.2.0/gopls-darwin-arm64"
    assert gopls["urls"]["win32-x64"].endswith(".exe")
    assert gopls["sha256"]["darwin-arm64"] == hashlib.sha256(b"bin").hexdigest()
    jre = m["components"]["jre"]
    assert jre["version"] == "21.0.11+10"
    assert jre["urls"]["darwin-arm64"] == "https://gh/rel/v0.2.0/jre-darwin-arm64.tar.gz"
    assert jre["urls"]["win32-x64"] == "https://gh/rel/v0.2.0/jre-win32-x64.zip"
    assert jre["sha256"]["darwin-arm64"] == hashlib.sha256(b"bin").hexdigest()
    jdtls = m["components"]["jdtls"]
    assert jdtls["version"] == "1.61.0-202607070104"
    assert jdtls["urls"]["any"] == "https://gh/rel/v0.2.0/jdtls.tar.gz"
    assert jdtls["sha256"]["any"] == hashlib.sha256(b"bin").hexdigest()
    agentd = m["components"]["agentd"]
    assert agentd["version"] == "0.2.0"
    assert agentd["urls"]["any"].endswith("crucible_agentd-0.2.0-py3-none-any.whl")
    assert m["components"]["lsps"]["npmPackages"] == [
        "pyright@1.1.400", "typescript-language-server@4.3.3"]


def test_agentd_version_comes_from_component_versions_not_wheel_filename(tmp_path: Path) -> None:
    # Regression test for the crucible-agentd 0.2.0/0.2.1 incident: the manifest's
    # agentd "version" is what the installer's staleness check trusted as a
    # fallback, and used to be parsed straight from the wheel filename — i.e. from
    # pyproject.toml's own (easy to forget to bump) version. It must now come from
    # component_versions, tag-derived in release.yml, same as every other
    # component. Deliberately mismatch the wheel filename's version against
    # component_versions to prove which one wins.
    for plat in ("darwin-arm64", "darwin-x64", "linux-x64"):
        _touch(tmp_path, f"crucible-indexer-{plat}")
        _touch(tmp_path, f"rg-{plat}")
        _touch(tmp_path, f"uv-{plat}")
        _touch(tmp_path, f"rust-analyzer-{plat}")
        _touch(tmp_path, f"gopls-{plat}")
        _touch(tmp_path, f"jre-{plat}.tar.gz")
    _touch(tmp_path, "crucible-indexer-win32-x64.exe")
    _touch(tmp_path, "rg-win32-x64.exe")
    _touch(tmp_path, "uv-win32-x64.exe")
    _touch(tmp_path, "rust-analyzer-win32-x64.exe")
    _touch(tmp_path, "gopls-win32-x64.exe")
    _touch(tmp_path, "jre-win32-x64.zip")
    _touch(tmp_path, "jdtls.tar.gz")
    _touch(tmp_path, "crucible_agentd-0.2.0-py3-none-any.whl")  # filename says 0.2.0

    m = build_manifest(
        "v0.5.2", tmp_path, "https://gh/rel/v0.5.2",
        component_versions={
            "indexer": "0.5.2", "agentd": "0.5.2",  # tag-derived says 0.5.2
            "ripgrep": "14.1.0", "uv": "0.5.0", "rust-analyzer": "2026-07-06",
            "gopls": "v0.22.0", "jre": "21.0.11+10", "jdtls": "1.61.0-202607070104",
        },
        lsp_packages=[],
    )
    assert m["components"]["agentd"]["version"] == "0.5.2"


def test_missing_platform_artifact_raises(tmp_path: Path) -> None:
    _touch(tmp_path, "crucible-indexer-darwin-arm64")
    import pytest
    with pytest.raises(FileNotFoundError, match="rg-darwin-arm64"):
        build_manifest("v1", tmp_path, "u",
                       component_versions={"indexer": "1", "ripgrep": "1", "uv": "1"},
                       lsp_packages=[])


def test_missing_jre_artifact_raises(tmp_path: Path) -> None:
    for plat in ("darwin-arm64", "darwin-x64", "linux-x64"):
        _touch(tmp_path, f"crucible-indexer-{plat}")
        _touch(tmp_path, f"rg-{plat}")
        _touch(tmp_path, f"uv-{plat}")
        _touch(tmp_path, f"rust-analyzer-{plat}")
        _touch(tmp_path, f"gopls-{plat}")
    _touch(tmp_path, "crucible-indexer-win32-x64.exe")
    _touch(tmp_path, "rg-win32-x64.exe")
    _touch(tmp_path, "uv-win32-x64.exe")
    _touch(tmp_path, "rust-analyzer-win32-x64.exe")
    _touch(tmp_path, "gopls-win32-x64.exe")
    _touch(tmp_path, "crucible_agentd-0.2.0-py3-none-any.whl")
    # no jre-* files at all

    import pytest
    with pytest.raises(FileNotFoundError, match="jre-darwin-arm64.tar.gz"):
        build_manifest(
            "v1", tmp_path, "u",
            component_versions={
                "indexer": "1", "agentd": "1", "ripgrep": "1", "uv": "1", "rust-analyzer": "1",
                "gopls": "1", "jre": "21.0.11+10", "jdtls": "1.0",
            },
            lsp_packages=[])
