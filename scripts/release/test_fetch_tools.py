import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fetch_tools import (  # noqa: E402
    ripgrep_asset_name,
    ripgrep_download_url,
    stage,
    uv_asset_name,
    uv_download_url,
)


def _tar_gz_with(member_path: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name=member_path)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _zip_with(member_path: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member_path, content)
    return buf.getvalue()


def test_stage_extracts_posix_binary_from_nested_tar_gz() -> None:
    archive = _tar_gz_with("uv-x86_64-apple-darwin/uv", b"#!/bin/sh\necho uv\n")
    assert stage(archive, "uv", "darwin-x64") == b"#!/bin/sh\necho uv\n"


def test_stage_extracts_ripgrep_from_nested_tar_gz() -> None:
    archive = _tar_gz_with(
        "ripgrep-14.1.1-x86_64-unknown-linux-musl/rg", b"rg-binary-bytes")
    assert stage(archive, "ripgrep", "linux-x64") == b"rg-binary-bytes"


def test_stage_extracts_windows_exe_from_zip() -> None:
    archive = _zip_with("uv-x86_64-pc-windows-msvc/uv.exe", b"exe-bytes")
    assert stage(archive, "uv", "win32-x64") == b"exe-bytes"


def test_stage_missing_member_raises() -> None:
    archive = _tar_gz_with("some-dir/README.md", b"not a binary")
    with pytest.raises(FileNotFoundError, match="uv"):
        stage(archive, "uv", "darwin-arm64")


def test_asset_name_and_url_conventions() -> None:
    assert uv_asset_name("darwin-arm64") == "uv-aarch64-apple-darwin.tar.gz"
    assert uv_asset_name("win32-x64") == "uv-x86_64-pc-windows-msvc.zip"
    assert uv_download_url("0.5.24", "linux-x64") == (
        "https://github.com/astral-sh/uv/releases/download/"
        "0.5.24/uv-x86_64-unknown-linux-gnu.tar.gz")

    assert ripgrep_asset_name("14.1.1", "darwin-x64") == (
        "ripgrep-14.1.1-x86_64-apple-darwin.tar.gz")
    assert ripgrep_download_url("14.1.1", "win32-x64") == (
        "https://github.com/BurntSushi/ripgrep/releases/download/"
        "14.1.1/ripgrep-14.1.1-x86_64-pc-windows-msvc.zip")
