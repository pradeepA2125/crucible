"""Download the pinned uv + ripgrep official release archives per platform, extract
the single binary from each, and restage under our conventional artifact names
(uv-<platform>[.exe], rg-<platform>[.exe]) for make_manifest.py (Task 16) to consume.

Network access is confined to main(); stage() (the archive -> binary-bytes extractor)
is pure and unit-tested with small fixture archives built via tarfile/zipfile.
"""
from __future__ import annotations

import argparse
import io
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path

PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-x64", "win32-x64")

# Upstream target-triple naming per our platform key.
_UV_TARGETS = {
    "darwin-arm64": "aarch64-apple-darwin",
    "darwin-x64": "x86_64-apple-darwin",
    "linux-x64": "x86_64-unknown-linux-gnu",
    "win32-x64": "x86_64-pc-windows-msvc",
}
_RIPGREP_TARGETS = {
    "darwin-arm64": "aarch64-apple-darwin",
    "darwin-x64": "x86_64-apple-darwin",
    "linux-x64": "x86_64-unknown-linux-musl",
    "win32-x64": "x86_64-pc-windows-msvc",
}

_BINARY_BASENAME = {"uv": "uv", "ripgrep": "rg"}


def _archive_ext(platform: str) -> str:
    return "zip" if platform == "win32-x64" else "tar.gz"


def uv_asset_name(platform: str) -> str:
    return f"uv-{_UV_TARGETS[platform]}.{_archive_ext(platform)}"


def ripgrep_asset_name(version: str, platform: str) -> str:
    return f"ripgrep-{version}-{_RIPGREP_TARGETS[platform]}.{_archive_ext(platform)}"


def uv_download_url(version: str, platform: str) -> str:
    return (
        f"https://github.com/astral-sh/uv/releases/download/"
        f"{version}/{uv_asset_name(platform)}"
    )


def ripgrep_download_url(version: str, platform: str) -> str:
    return (
        f"https://github.com/BurntSushi/ripgrep/releases/download/"
        f"{version}/{ripgrep_asset_name(version, platform)}"
    )


def _binary_name(kind: str, platform: str) -> str:
    base = _BINARY_BASENAME[kind]
    return f"{base}.exe" if platform == "win32-x64" else base


def stage(archive_bytes: bytes, kind: str, platform: str) -> bytes:
    """Extract the single tool binary from a downloaded release archive.

    Searches every member for one whose basename matches the expected binary
    name (uv/uv.exe or rg/rg.exe) — release archives nest the binary under a
    version-and-target-specific directory, so we don't hardcode that path.
    """
    target_name = _binary_name(kind, platform)

    if _archive_ext(platform) == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            for name in zf.namelist():
                if Path(name).name == target_name:
                    return zf.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and Path(member.name).name == target_name:
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        return extracted.read()

    raise FileNotFoundError(
        f"no member named {target_name!r} found in {kind} archive for {platform}")


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - pinned https URLs only
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", required=True, help="uv release tag, e.g. 0.5.24")
    parser.add_argument("--rg", required=True, help="ripgrep release tag, e.g. 14.1.1")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    for platform in PLATFORMS:
        uv_bytes = stage(
            _download(uv_download_url(args.uv, platform)), "uv", platform)
        rg_bytes = stage(
            _download(ripgrep_download_url(args.rg, platform)), "ripgrep", platform)

        for kind, data in (("uv", uv_bytes), ("ripgrep", rg_bytes)):
            base = _BINARY_BASENAME[kind]
            suffix = ".exe" if platform == "win32-x64" else ""
            dest = args.out / f"{base}-{platform}{suffix}"
            dest.write_bytes(data)
            if platform != "win32-x64":
                dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            print(f"wrote {dest}")


if __name__ == "__main__":
    main()
