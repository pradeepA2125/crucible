"""Reject placeholder/incomplete manifests before packaging a local VSIX."""
import json
import re
import sys
from pathlib import Path


def validate(manifest):
    if manifest.get("manifestVersion") != 1 or not manifest.get("releaseTag") or manifest["releaseTag"] == "dev-unpinned":
        raise ValueError("expected a complete released runtime manifest")
    components = manifest["components"]
    for component in ("uv", "indexer", "ripgrep", "rust-analyzer", "gopls", "jre", "agentd", "jdtls"):
        spec = components[component]
        platforms = ("any",) if component in ("agentd", "jdtls") else (
            "darwin-arm64", "darwin-x64", "linux-x64", "win32-x64")
        for platform in platforms:
            if not spec.get("urls", {}).get(platform, "").startswith("https://") or not re.fullmatch(
                r"[0-9a-fA-F]{64}", spec.get("sha256", {}).get(platform, "")
            ):
                raise ValueError(f"missing URL/checksum for {component}/{platform}")
    if not components["lsps"].get("npmPackages"):
        raise ValueError("missing language server packages")


if __name__ == "__main__":
    manifest = json.loads(Path(sys.argv[1]).read_text())
    validate(manifest)
    print(f"    using complete manifest for {manifest['releaseTag']}")
