"""Build runtime-manifest.json (the Task 8 RuntimeManifest shape) from a directory of
conventionally-named release artifacts. Used by the release CI (Task 17) and runnable
standalone for local dry-runs.

Artifact naming convention (CI produces exactly these):
  crucible-indexer-<platform>[.exe]
  rg-<platform>[.exe]
  uv-<platform>[.exe]
  rust-analyzer-<platform>[.exe]
  gopls-<platform>[.exe]
  jre-<platform>.tar.gz  (win32-x64: .zip instead)
  jdtls.tar.gz           (single platform-independent archive)
  crucible_agentd-<version>-py3-none-any.whl

<platform> is one of: darwin-arm64, darwin-x64, linux-x64, win32-x64.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-x64", "win32-x64")

# (component id, artifact basename prefix)
_BINARY_COMPONENTS = (
    ("indexer", "crucible-indexer"),
    ("ripgrep", "rg"),
    ("uv", "uv"),
    ("rust-analyzer", "rust-analyzer"),
    ("gopls", "gopls"),
)

_WHEEL_RE = re.compile(r"^crucible_agentd-(?P<version>.+)-py3-none-any\.whl$")


def _artifact_name(prefix: str, platform: str) -> str:
    return f"{prefix}-{platform}.exe" if platform == "win32-x64" else f"{prefix}-{platform}"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_wheel(dist_dir: Path) -> Path:
    for path in sorted(dist_dir.glob("crucible_agentd-*-py3-none-any.whl")):
        return path
    raise FileNotFoundError(
        f"no crucible_agentd-*-py3-none-any.whl found in {dist_dir}")


def _jre_archive_ext(platform: str) -> str:
    return "zip" if platform == "win32-x64" else "tar.gz"


def _jre_artifact_name(platform: str) -> str:
    return f"jre-{platform}.{_jre_archive_ext(platform)}"


def build_manifest(
    release_tag: str,
    dist_dir: Path,
    url_base: str,
    *,
    component_versions: dict[str, str],
    lsp_packages: list[str],
) -> dict:
    dist_dir = Path(dist_dir)
    urls_by_component: dict[str, dict[str, str]] = {c: {} for c, _ in _BINARY_COMPONENTS}
    sha256_by_component: dict[str, dict[str, str]] = {c: {} for c, _ in _BINARY_COMPONENTS}

    # Platform-outer / component-inner so a missing-artifact error names the
    # first (platform, component) pair in a stable, predictable order.
    for platform in PLATFORMS:
        for component_id, prefix in _BINARY_COMPONENTS:
            name = _artifact_name(prefix, platform)
            path = dist_dir / name
            if not path.is_file():
                raise FileNotFoundError(
                    f"missing artifact for {component_id}/{platform}: {name} "
                    f"(expected at {path})")
            urls_by_component[component_id][platform] = f"{url_base}/{name}"
            sha256_by_component[component_id][platform] = _sha256_file(path)

    components: dict[str, dict] = {
        component_id: {
            "version": component_versions[component_id],
            "urls": urls_by_component[component_id],
            "sha256": sha256_by_component[component_id],
        }
        for component_id, _ in _BINARY_COMPONENTS
    }

    wheel_path = _find_wheel(dist_dir)
    match = _WHEEL_RE.match(wheel_path.name)
    if not match:
        raise ValueError(f"unexpected wheel filename: {wheel_path.name}")
    agentd_version = match.group("version")
    components["agentd"] = {
        "version": agentd_version,
        "urls": {"any": f"{url_base}/{wheel_path.name}"},
        "sha256": {"any": _sha256_file(wheel_path)},
    }

    # jre: per-platform like the binary components above, but the archive
    # extension is format-dependent (tar.gz posix / zip windows) rather than
    # a uniform "one exe suffix on windows only" — doesn't fit
    # _BINARY_COMPONENTS'/_artifact_name's convention, so it's built
    # separately. Not extracted here — the raw archive ships as-is;
    # installer.ts extracts it client-side at install time (see
    # fetch_jre.py's module docstring).
    jre_urls: dict[str, str] = {}
    jre_sha256: dict[str, str] = {}
    for platform in PLATFORMS:
        name = _jre_artifact_name(platform)
        path = dist_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"missing artifact for jre/{platform}: {name} (expected at {path})")
        jre_urls[platform] = f"{url_base}/{name}"
        jre_sha256[platform] = _sha256_file(path)
    components["jre"] = {
        "version": component_versions["jre"],
        "urls": jre_urls,
        "sha256": jre_sha256,
    }

    # jdtls: a single platform-independent archive (see fetch_jdtls.py's
    # module docstring) — same "any" key shape as agentd's wheel above.
    jdtls_path = dist_dir / "jdtls.tar.gz"
    if not jdtls_path.is_file():
        raise FileNotFoundError(f"missing jdtls artifact: jdtls.tar.gz (expected at {jdtls_path})")
    components["jdtls"] = {
        "version": component_versions["jdtls"],
        "urls": {"any": f"{url_base}/jdtls.tar.gz"},
        "sha256": {"any": _sha256_file(jdtls_path)},
    }

    lsps_version = hashlib.sha1(
        ",".join(lsp_packages).encode("utf-8")).hexdigest()
    components["lsps"] = {
        "version": lsps_version,
        "npmPackages": list(lsp_packages),
    }

    return {
        "manifestVersion": 1,
        "releaseTag": release_tag,
        "components": components,
    }


def _parse_component_version(raw: str) -> tuple[str, str]:
    name, _, version = raw.partition("=")
    if not _:
        raise argparse.ArgumentTypeError(
            f"--component-version must be name=version, got: {raw!r}")
    return name, version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--url-base", required=True)
    parser.add_argument(
        "--component-version", action="append", default=[],
        type=_parse_component_version, metavar="name=version",
        help="repeatable; required for indexer, ripgrep, uv")
    parser.add_argument(
        "--lsp-packages", default="",
        help="comma-separated name@version pairs, e.g. pyright@1.1.400,...")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    component_versions = dict(args.component_version)
    lsp_packages = [p for p in args.lsp_packages.split(",") if p]

    manifest = build_manifest(
        args.release_tag, args.dist, args.url_base,
        component_versions=component_versions, lsp_packages=lsp_packages)

    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
