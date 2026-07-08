"""Cross-compile gopls for all 4 target platforms from a single host with the
Go toolchain, restaging under our conventional artifact name
(gopls-<platform>[.exe]) for make_manifest.py to consume.

Unlike uv/ripgrep/rust-analyzer (fetch_tools.py), there is no prebuilt gopls
release binary to download — the Go team ships source only, distributed via
`go install`. Cross-compiling via `go build` works fine from any single host
(verified locally: a darwin-arm64 machine produced valid linux-x64/win32-x64/
darwin-x64 binaries), so no per-OS build matrix is needed here, unlike the
Rust indexer job.

Two Go-tooling quirks made the naive approach fail, both confirmed locally
before writing this script:
  - `go install pkg@version` refuses to cross-compile when GOBIN is set:
    "cannot install cross-compiled binaries when GOBIN is set".
  - `go build` doesn't accept the bare `path@version` remote-package syntax
    at all: "can only use path@version syntax with 'go get' and 'go install'
    in module-aware mode".
The working two-step sequence this script uses:
  1. `go mod init` a throwaway module + `go get golang.org/x/tools/gopls@<version>`
     — resolves the dependency into go.mod/go.sum; no cross-compile
     restriction applies to `go get` itself.
  2. `GOOS=<x> GOARCH=<y> go build -o <dest> golang.org/x/tools/gopls`
     — now building a package resolved within a real module, where
     cross-compilation is fully supported (unlike step 1's version-suffixed
     shortcut).

Network/subprocess access is confined behind the injectable `run_cmd` so the
platform/naming logic is unit-tested without a real Go toolchain in the test
job.
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-x64", "win32-x64")

# platform key -> (GOOS, GOARCH)
_GOPLS_TARGETS: dict[str, tuple[str, str]] = {
    "darwin-arm64": ("darwin", "arm64"),
    "darwin-x64": ("darwin", "amd64"),
    "linux-x64": ("linux", "amd64"),
    "win32-x64": ("windows", "amd64"),
}

RunCmd = Callable[..., subprocess.CompletedProcess]


def gopls_artifact_name(platform: str) -> str:
    return f"gopls-{platform}.exe" if platform == "win32-x64" else f"gopls-{platform}"


def build_gopls_binaries(
    version: str,
    out_dir: Path,
    *,
    work_dir: Path,
    run_cmd: RunCmd = subprocess.run,
) -> list[Path]:
    """Resolve `version` into a throwaway module at `work_dir`, then
    cross-compile it for every platform in PLATFORMS into `out_dir`.

    Returns the list of written artifact paths, in PLATFORMS order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    run_cmd(
        ["go", "mod", "init", "gopls-build-shim"],
        cwd=work_dir, check=True, capture_output=True, text=True,
    )
    result = run_cmd(
        ["go", "get", f"golang.org/x/tools/gopls@{version}"],
        cwd=work_dir, check=False, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"go get gopls@{version} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    written: list[Path] = []
    for platform in PLATFORMS:
        goos, goarch = _GOPLS_TARGETS[platform]
        dest = out_dir / gopls_artifact_name(platform)
        env = {**os.environ, "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0"}
        
        # Debug: list work_dir before build
        work_contents = sorted([p.name for p in work_dir.iterdir()])
        
        result = run_cmd(
            ["go", "build", "-v", "-o", str(dest), "golang.org/x/tools/gopls"],
            cwd=work_dir, check=False, capture_output=True, text=True,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"go build failed for {platform} (GOOS={goos} GOARCH={goarch}):\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        
        # Debug: check dest and out_dir after build
        out_dir_contents = sorted([p.name for p in out_dir.iterdir()]) if out_dir.exists() else []
        
        if not dest.exists():
            go_mod_path = work_dir / "go.mod"
            go_sum_path = work_dir / "go.sum"
            go_mod_exists = go_mod_path.exists()
            go_sum_exists = go_sum_path.exists()
            raise FileNotFoundError(
                f"go build succeeded but output file not created: {dest}\n"
                f"(GOOS={goos} GOARCH={goarch}, CGO_ENABLED=0)\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}\n"
                f"work_dir: {work_dir}\nwork_dir contents: {work_contents}\n"
                f"out_dir: {out_dir}\nout_dir contents: {out_dir_contents}\n"
                f"go.mod exists: {go_mod_exists}\ngo.sum exists: {go_sum_exists}"
            )
        if platform != "win32-x64":
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        written.append(dest)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="gopls version tag, e.g. v0.22.0")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="gopls-build-") as tmp:
        written = build_gopls_binaries(args.version, args.out, work_dir=Path(tmp))

    for path in written:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
