"""Download the pinned Eclipse Temurin JRE per platform and restage under our
conventional artifact name (jre-<platform>.tar.gz / .zip) for
make_manifest.py to consume.

Unlike uv/ripgrep/rust-analyzer/gopls, the JRE ships as a whole directory
tree (bin/, lib/, ...), not a single binary — so unlike fetch_tools.py's
stage(), nothing is extracted here. The archive is re-hosted as-is; the
managed-runtime installer (installer.ts) extracts it client-side at install
time (there's no reason to unpack-then-repack on the release runner).

Version note: jdtls (Eclipse JDT Language Server) requires Java 21+ (its own
launcher enforces this — `if java_major_version < 21: raise Exception
("jdtls requires at least Java 21")`, verified against a real installed
jdtls). Pin JAVA_FEATURE_VERSION to match whatever jdtls's current minimum
is, not just "whatever LTS is newest."
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-x64", "win32-x64")

# platform key -> (Adoptium os, Adoptium architecture)
_JRE_TARGETS = {
    "darwin-arm64": ("mac", "aarch64"),
    "darwin-x64": ("mac", "x64"),
    "linux-x64": ("linux", "x64"),
    "win32-x64": ("windows", "x64"),
}


def _archive_ext(platform: str) -> str:
    return "zip" if platform == "win32-x64" else "tar.gz"


def jre_asset_name(feature_version: str, version_us: str, platform: str) -> str:
    """`version_us`: the release's dotted version with `+` replaced by `_`,
    e.g. "21.0.11_10" for release tag "jdk-21.0.11+10"."""
    os_name, arch = _JRE_TARGETS[platform]
    return f"OpenJDK{feature_version}U-jre_{arch}_{os_name}_hotspot_{version_us}.{_archive_ext(platform)}"


def jre_download_url(feature_version: str, version_tag: str, version_us: str, platform: str) -> str:
    """`version_tag`: the GitHub release tag, e.g. "jdk-21.0.11+10"."""
    encoded_tag = version_tag.replace("+", "%2B")
    return (
        f"https://github.com/adoptium/temurin{feature_version}-binaries/releases/download/"
        f"{encoded_tag}/{jre_asset_name(feature_version, version_us, platform)}"
    )


def _restaged_name(platform: str) -> str:
    return f"jre-{platform}.{_archive_ext(platform)}"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - pinned https URLs only
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-version", required=True, help='e.g. "21"')
    parser.add_argument("--version-tag", required=True, help='e.g. "jdk-21.0.11+10"')
    parser.add_argument("--version-us", required=True, help='e.g. "21.0.11_10"')
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    for platform in PLATFORMS:
        url = jre_download_url(args.feature_version, args.version_tag, args.version_us, platform)
        data = _download(url)
        dest = args.out / _restaged_name(platform)
        dest.write_bytes(data)
        print(f"wrote {dest}")


if __name__ == "__main__":
    main()
