"""Download the pinned Eclipse JDT Language Server (jdtls) snapshot and
restage under our conventional artifact name (jdtls.tar.gz) for
make_manifest.py to consume.

Unlike every other bundled component, jdtls's distribution is platform-
INDEPENDENT: one archive holds a shared jar tree plus OS-named (and, on
recent builds, OS+arch-named) config subdirectories, selected at spawn time
by the installer rather than downloaded separately per platform (see
apps/vscode-extension/src/runtime/jdtls.ts::configDirForPlatform). So there's
a single "any"-keyed artifact, not one per PLATFORMS entry — verified against
a real downloaded archive (config_mac_arm/config_linux_arm/config_mac/
config_linux/config_win all present in one tar.gz) before writing this.

No extraction happens here (same reasoning as fetch_jre.py) — the archive is
re-hosted as-is; installer.ts extracts it client-side at install time.
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

RESTAGED_NAME = "jdtls.tar.gz"


def jdtls_download_url(version: str) -> str:
    """`version`: an exact snapshot tag, e.g. "1.61.0-202607070104" — pin a
    specific build, not the rolling `jdt-language-server-latest.tar.gz`
    symlink, for release reproducibility."""
    return f"https://www.eclipse.org/downloads/download.php?file=/jdtls/snapshots/jdt-language-server-{version}.tar.gz"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - pinned https URL only
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help='e.g. "1.61.0-202607070104"')
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    data = _download(jdtls_download_url(args.version))
    dest = args.out / RESTAGED_NAME
    dest.write_bytes(data)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
