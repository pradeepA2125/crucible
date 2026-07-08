# rust-analyzer Managed Install + Memory-On-By-Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the P4 managed-runtime installer fetch and install `rust-analyzer` the same way it already handles `uv`/`ripgrep`, and flip the memory harness (compaction + recall + reranker) to be enabled by default for every entry point.

**Architecture:** rust-analyzer becomes a 4th binary-download component in the existing `fetch_tools.py` → `make_manifest.py` → `installer.ts` pipeline, with one real wrinkle — its Unix release assets are a raw single-file `.gz` (not `.tar.gz` like uv/ripgrep), requiring a third decompression branch. The memory default flip is a two-line change in `agentd/memory/config.py` (the single source of truth all three entry points read from), plus a matching install-size change (`[memory]` extras) and cosmetic settings-schema alignment.

**Tech Stack:** Python 3.12 (pytest), TypeScript (vitest), GitHub Actions YAML.

## Global Constraints

- Every binary component's sha256 is computed automatically by `make_manifest.py` from staged bytes — never hand-pin a checksum.
- The exact rust-analyzer release tag to pin in CI is chosen at implementation time (this plan uses `2026-07-06` as the concrete value to write into `release.yml`/local dev fixtures — bump it to whatever the actual latest `rust-lang/rust-analyzer` release tag is when this ships).
- `CRUCIBLE_MEMORY_ENABLED`/`CRUCIBLE_MEMORY_RERANKER` keep kill-switch semantics: explicitly setting either to `0`/`false`/`no`/`off` must still disable it after this change.
- Follow existing test patterns exactly (table-driven fixtures in `test_fetch_tools.py`/`test_make_manifest.py`/`runtime-installer.test.ts` — extend, don't restructure).

---

### Task 1: rust-analyzer archive staging in `fetch_tools.py`

**Files:**
- Modify: `scripts/release/fetch_tools.py`
- Test: `scripts/release/test_fetch_tools.py`

**Interfaces:**
- Produces: `rust_analyzer_asset_name(platform: str) -> str`, `rust_analyzer_download_url(version: str, platform: str) -> str`, `stage(archive_bytes: bytes, kind: str, platform: str) -> bytes` (now also accepts `kind="rust-analyzer"`), `_BINARY_BASENAME["rust-analyzer"] == "rust-analyzer"`. Consumed by Task 2's CLI wiring is NOT part of this task (main() changes are here too, see below) — Task 5 (release.yml) consumes the `--rust-analyzer` CLI flag added here.

- [ ] **Step 1: Write the failing tests**

Replace the top of `scripts/release/test_fetch_tools.py` (everything up to and including the `from fetch_tools import (...)` block) with:

```python
import gzip
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
    rust_analyzer_asset_name,
    rust_analyzer_download_url,
    stage,
    uv_asset_name,
    uv_download_url,
)
```

Append these test functions to the same file:

```python
def test_stage_extracts_rust_analyzer_from_plain_gzip() -> None:
    archive = gzip.compress(b"rust-analyzer-binary-bytes")
    assert stage(archive, "rust-analyzer", "linux-x64") == b"rust-analyzer-binary-bytes"


def test_stage_extracts_rust_analyzer_darwin_from_plain_gzip() -> None:
    archive = gzip.compress(b"macos-binary-bytes")
    assert stage(archive, "rust-analyzer", "darwin-arm64") == b"macos-binary-bytes"


def test_stage_extracts_rust_analyzer_windows_exe_from_zip() -> None:
    archive = _zip_with("rust-analyzer.exe", b"exe-bytes")
    assert stage(archive, "rust-analyzer", "win32-x64") == b"exe-bytes"


def test_rust_analyzer_asset_name_and_url_conventions() -> None:
    assert rust_analyzer_asset_name("linux-x64") == "rust-analyzer-x86_64-unknown-linux-gnu.gz"
    assert rust_analyzer_asset_name("darwin-arm64") == "rust-analyzer-aarch64-apple-darwin.gz"
    assert rust_analyzer_asset_name("win32-x64") == "rust-analyzer-x86_64-pc-windows-msvc.zip"
    assert rust_analyzer_download_url("2026-07-06", "darwin-arm64") == (
        "https://github.com/rust-lang/rust-analyzer/releases/download/"
        "2026-07-06/rust-analyzer-aarch64-apple-darwin.gz")


def test_uv_and_ripgrep_staging_still_use_tar_gz_on_posix() -> None:
    # Regression guard: the _archive_ext(kind, platform) refactor must not
    # change uv/ripgrep's existing tar.gz behavior on non-Windows.
    archive = _tar_gz_with("uv-x86_64-apple-darwin/uv", b"uv-bytes")
    assert stage(archive, "uv", "darwin-x64") == b"uv-bytes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts/release && python -m pytest test_fetch_tools.py -v`
Expected: the four new tests FAIL with `ImportError: cannot import name 'rust_analyzer_asset_name'` (collection error).

- [ ] **Step 3: Implement the rust-analyzer staging support**

Replace the full contents of `scripts/release/fetch_tools.py` with:

```python
"""Download the pinned uv + ripgrep + rust-analyzer official release archives
per platform, extract the single binary from each, and restage under our
conventional artifact names (uv-<platform>[.exe], rg-<platform>[.exe],
rust-analyzer-<platform>[.exe]) for make_manifest.py (Task 16) to consume.

Network access is confined to main(); stage() (the archive -> binary-bytes
extractor) is pure and unit-tested with small fixture archives built via
tarfile/zipfile/gzip.
"""
from __future__ import annotations

import argparse
import gzip
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
_RUST_ANALYZER_TARGETS = {
    "darwin-arm64": "aarch64-apple-darwin",
    "darwin-x64": "x86_64-apple-darwin",
    "linux-x64": "x86_64-unknown-linux-gnu",
    "win32-x64": "x86_64-pc-windows-msvc",
}

_BINARY_BASENAME = {"uv": "uv", "ripgrep": "rg", "rust-analyzer": "rust-analyzer"}


def _archive_ext(kind: str, platform: str) -> str:
    """Upstream archive format for this (tool, platform) pair.

    uv/ripgrep ship a tar.gz on posix, zip on Windows. rust-analyzer ships a
    raw single-file gzip on posix (no tar wrapper at all) and a zip on
    Windows.
    """
    if platform == "win32-x64":
        return "zip"
    return "gz" if kind == "rust-analyzer" else "tar.gz"


def uv_asset_name(platform: str) -> str:
    return f"uv-{_UV_TARGETS[platform]}.{_archive_ext('uv', platform)}"


def ripgrep_asset_name(version: str, platform: str) -> str:
    return f"ripgrep-{version}-{_RIPGREP_TARGETS[platform]}.{_archive_ext('ripgrep', platform)}"


def rust_analyzer_asset_name(platform: str) -> str:
    return f"rust-analyzer-{_RUST_ANALYZER_TARGETS[platform]}.{_archive_ext('rust-analyzer', platform)}"


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


def rust_analyzer_download_url(version: str, platform: str) -> str:
    return (
        f"https://github.com/rust-lang/rust-analyzer/releases/download/"
        f"{version}/{rust_analyzer_asset_name(platform)}"
    )


def _binary_name(kind: str, platform: str) -> str:
    base = _BINARY_BASENAME[kind]
    return f"{base}.exe" if platform == "win32-x64" else base


def stage(archive_bytes: bytes, kind: str, platform: str) -> bytes:
    """Extract the single tool binary from a downloaded release archive.

    For zip/tar.gz archives, searches every member for one whose basename
    matches the expected binary name — release archives nest the binary
    under a version-and-target-specific directory, so we don't hardcode that
    path. rust-analyzer's posix assets are a raw single-file gzip (no tar
    wrapper, no member search needed): the decompressed payload IS the
    binary.
    """
    fmt = _archive_ext(kind, platform)

    if fmt == "gz":
        return gzip.decompress(archive_bytes)

    target_name = _binary_name(kind, platform)

    if fmt == "zip":
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
    parser.add_argument(
        "--rust-analyzer", required=True,
        help="rust-analyzer release tag, e.g. 2026-07-06")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    for platform in PLATFORMS:
        uv_bytes = stage(
            _download(uv_download_url(args.uv, platform)), "uv", platform)
        rg_bytes = stage(
            _download(ripgrep_download_url(args.rg, platform)), "ripgrep", platform)
        ra_bytes = stage(
            _download(rust_analyzer_download_url(args.rust_analyzer, platform)),
            "rust-analyzer", platform)

        for kind, data in (
            ("uv", uv_bytes), ("ripgrep", rg_bytes), ("rust-analyzer", ra_bytes),
        ):
            base = _BINARY_BASENAME[kind]
            suffix = ".exe" if platform == "win32-x64" else ""
            dest = args.out / f"{base}-{platform}{suffix}"
            dest.write_bytes(data)
            if platform != "win32-x64":
                dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            print(f"wrote {dest}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scripts/release && python -m pytest test_fetch_tools.py -v`
Expected: all tests PASS (the original 5 plus the 5 new ones = 10 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/release/fetch_tools.py scripts/release/test_fetch_tools.py
git commit -m "feat(release): stage rust-analyzer binaries (gzip + zip archive support)"
```

---

### Task 2: rust-analyzer in `make_manifest.py`

**Files:**
- Modify: `scripts/release/make_manifest.py`
- Test: `scripts/release/test_make_manifest.py`

**Interfaces:**
- Consumes: nothing from Task 1 (this module never imports `fetch_tools`; it only reads conventionally-named files off disk).
- Produces: `build_manifest(...)`'s output now includes `components["rust-analyzer"]` with the same `{version, urls, sha256}` shape as `indexer`/`ripgrep`/`uv`. Consumed by Task 5's `release.yml` `--component-version rust-analyzer=...` flag.

- [ ] **Step 1: Write the failing test**

Replace `scripts/release/test_make_manifest.py`'s `test_build_manifest_shape` function with:

```python
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
```

(`test_missing_platform_artifact_raises` is unchanged — it fails on the missing `rg-darwin-arm64` artifact before the loop ever reaches `rust-analyzer`, since `_BINARY_COMPONENTS`' iteration order is `indexer, ripgrep, uv, rust-analyzer`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts/release && python -m pytest test_make_manifest.py -v`
Expected: `test_build_manifest_shape` FAILS with `KeyError: 'rust-analyzer'`.

- [ ] **Step 3: Add rust-analyzer to `_BINARY_COMPONENTS`**

In `scripts/release/make_manifest.py`, change:

```python
_BINARY_COMPONENTS = (
    ("indexer", "ai-editor-indexer"),
    ("ripgrep", "rg"),
    ("uv", "uv"),
)
```

to:

```python
_BINARY_COMPONENTS = (
    ("indexer", "ai-editor-indexer"),
    ("ripgrep", "rg"),
    ("uv", "uv"),
    ("rust-analyzer", "rust-analyzer"),
)
```

No other change is needed — every other part of `build_manifest` iterates this tuple generically.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scripts/release && python -m pytest test_make_manifest.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/release/make_manifest.py scripts/release/test_make_manifest.py
git commit -m "feat(release): include rust-analyzer in the runtime manifest"
```

---

### Task 3: rust-analyzer as an installer component (`manifest.ts` + `installer.ts`)

**Files:**
- Modify: `apps/vscode-extension/src/runtime/manifest.ts`
- Modify: `apps/vscode-extension/src/runtime/installer.ts`
- Test: `apps/vscode-extension/test/runtime-installer.test.ts`

**Interfaces:**
- Consumes: `RuntimeManifest.components["rust-analyzer"]` shape produced by Task 2 (`{version, urls, sha256}`).
- Produces: `ComponentId` now includes `"rust-analyzer"`; `binPath(runtimeDir, "rust-analyzer", platform)` resolves to the installed binary. Consumed by Task 4 (`backend-process.ts`).

- [ ] **Step 1: Write the failing test**

In `apps/vscode-extension/test/runtime-installer.test.ts`, update the `manifest()` fixture function to:

```ts
function manifest(): RuntimeManifest {
  const sha = sha256Hex(BIN);
  return {
    manifestVersion: 1,
    releaseTag: "v0.1.0",
    components: {
      uv: { version: "0.5.0", urls: { "darwin-arm64": "https://r/uv" }, sha256: { "darwin-arm64": sha } },
      agentd: { version: "0.1.0" },
      indexer: { version: "0.1.0", urls: { "darwin-arm64": "https://r/ix" }, sha256: { "darwin-arm64": sha } },
      ripgrep: { version: "14.1.0", urls: { "darwin-arm64": "https://r/rg" }, sha256: { "darwin-arm64": sha } },
      "rust-analyzer": { version: "2026-07-06", urls: { "darwin-arm64": "https://r/ra" }, sha256: { "darwin-arm64": sha } },
      lsps: { version: "1", npmPackages: ["pyright@1.1.400", "typescript-language-server@4.3.3"] },
    },
  };
}
```

Update the first test in the `RuntimeInstaller` describe block from:

```ts
  it("happy path installs all five components and writes runtime.json", async () => {
    const d = deps();
    const result = await new RuntimeInstaller(d).installAll();
    expect(result.ok).toBe(true);
    expect(result.components.map((c) => c.status)).toEqual(
      ["done", "done", "done", "done", "done"]);
    expect(existsSync(join(d.runtimeDir, "bin", "uv"))).toBe(true);
    expect(d.calls.some(([c, a]) => c.endsWith("uv") && a === "venv")).toBe(true);
    const state = JSON.parse(readFileSync(join(d.runtimeDir, "runtime.json"), "utf8"));
    expect(state.releaseTag).toBe("v0.1.0");
  });
```

to:

```ts
  it("happy path installs all six components and writes runtime.json", async () => {
    const d = deps();
    const result = await new RuntimeInstaller(d).installAll();
    expect(result.ok).toBe(true);
    expect(result.components.map((c) => c.status)).toEqual(
      ["done", "done", "done", "done", "done", "done"]);
    expect(existsSync(join(d.runtimeDir, "bin", "uv"))).toBe(true);
    expect(existsSync(join(d.runtimeDir, "bin", "rust-analyzer"))).toBe(true);
    expect(d.calls.some(([c, a]) => c.endsWith("uv") && a === "venv")).toBe(true);
    const state = JSON.parse(readFileSync(join(d.runtimeDir, "runtime.json"), "utf8"));
    expect(state.releaseTag).toBe("v0.1.0");
  });
```

The "node absent skips lsps..." and "resume: matching install-state version..." tests are unaffected (they don't hardcode a component count). The "checksum mismatch..." test is unaffected (it only asserts on `uv`/`agentd`/`indexer` ids).

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run -w ai-editor-vscode-extension test -- runtime-installer`
Expected: FAIL — `result.components.map(...)` has 6 entries but the assertion (before Step 3) expects 5, and `bin/rust-analyzer` doesn't exist since `"rust-analyzer"` isn't in `ORDER`/`BIN_NAME` yet.

- [ ] **Step 3: Add rust-analyzer to the ComponentId union and the installer**

In `apps/vscode-extension/src/runtime/manifest.ts`, change:

```ts
export type ComponentId = "uv" | "agentd" | "indexer" | "ripgrep" | "lsps";
```

to:

```ts
export type ComponentId = "uv" | "agentd" | "indexer" | "ripgrep" | "rust-analyzer" | "lsps";
```

In `apps/vscode-extension/src/runtime/installer.ts`, change:

```ts
const ORDER: ComponentId[] = ["uv", "agentd", "indexer", "ripgrep", "lsps"];
const BIN_NAME: Partial<Record<ComponentId, string>> = {
  uv: "uv", indexer: "ai-editor-indexer", ripgrep: "rg",
};
```

to:

```ts
const ORDER: ComponentId[] = ["uv", "agentd", "indexer", "ripgrep", "rust-analyzer", "lsps"];
const BIN_NAME: Partial<Record<ComponentId, string>> = {
  uv: "uv", indexer: "ai-editor-indexer", ripgrep: "rg", "rust-analyzer": "rust-analyzer",
};
```

No other change to `installer.ts` is needed for this task — the existing generic binary-component branch in `installOne` (the fallthrough after the `lsps`/`agentd` special cases) already handles any id present in `BIN_NAME` with a `urls`/`sha256` entry.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w ai-editor-vscode-extension test -- runtime-installer`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/runtime/manifest.ts apps/vscode-extension/src/runtime/installer.ts apps/vscode-extension/test/runtime-installer.test.ts
git commit -m "feat(runtime): install rust-analyzer as a managed binary component"
```

---

### Task 4: resolve `CRUCIBLE_LSP_RS_CMD` to the managed binary (`backend-process.ts`)

**Files:**
- Modify: `apps/vscode-extension/src/runtime/backend-process.ts`
- Test: `apps/vscode-extension/test/runtime-backend-process.test.ts`

**Interfaces:**
- Consumes: `binPath(runtimeDir, "rust-analyzer", platform)` (already imported in this file from Task 3's `installer.ts`; the function itself is unchanged, just now meaningfully used for this id).

- [ ] **Step 1: Write the failing test**

Add to `apps/vscode-extension/test/runtime-backend-process.test.ts`, inside the `describe("BackendProcess.start", ...)` block (after the existing "skips the watcher when the indexer binary is missing" test):

```ts
  it("sets CRUCIBLE_LSP_RS_CMD to the managed binary when installed", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "ai-editor-indexer"), "");
    writeFileSync(join(d.runtimeDir, "bin", "rust-analyzer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_RS_CMD).toBe(join(d.runtimeDir, "bin", "rust-analyzer"));
  });

  it("falls back to the bare rust-analyzer command when the managed binary is absent", async () => {
    const d = deps();
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "ai-editor-indexer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(d.spawned[1].env.CRUCIBLE_LSP_RS_CMD).toBe("rust-analyzer");
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run -w ai-editor-vscode-extension test -- runtime-backend-process`
Expected: the first new test FAILS — `CRUCIBLE_LSP_RS_CMD` is currently always the literal string `"rust-analyzer"`, so `toBe(join(...))` fails; the second new test passes already (no code change needed for that direction), which is fine — it will still pass after Step 3.

- [ ] **Step 3: Update `spawnWatcher`'s env construction**

In `apps/vscode-extension/src/runtime/backend-process.ts`, change:

```ts
  private spawnWatcher(workspace: string, port: number): void {
    const indexer = binPath(this.deps.runtimeDir, "ai-editor-indexer", this.platform);
    if (!existsSync(indexer)) {
      this.deps.log("[runtime] indexer binary missing — watcher not started");
      return;
    }
    const lspBin = (name: string) => join(
      this.deps.runtimeDir, "node_modules", ".bin",
      this.platform === "win32-x64" ? `${name}.cmd` : name);
    const lspInstalled = existsSync(join(this.deps.runtimeDir, "node_modules"));
    const env = {
      ...process.env,
      CRUCIBLE_BACKEND_URL: `http://localhost:${port}`,
      CRUCIBLE_LSP_ENABLED: lspInstalled ? "true" : "false",
      ...(lspInstalled
        ? {
            CRUCIBLE_LSP_PY_CMD: `${lspBin("pyright-langserver")} --stdio`,
            CRUCIBLE_LSP_TS_CMD: `${lspBin("typescript-language-server")} --stdio`,
            // Detect-only: the indexer degrades gracefully when rust-analyzer is absent.
            CRUCIBLE_LSP_RS_CMD: "rust-analyzer",
          }
        : {}),
    } as Record<string, string>;
```

to:

```ts
  private spawnWatcher(workspace: string, port: number): void {
    const indexer = binPath(this.deps.runtimeDir, "ai-editor-indexer", this.platform);
    if (!existsSync(indexer)) {
      this.deps.log("[runtime] indexer binary missing — watcher not started");
      return;
    }
    const lspBin = (name: string) => join(
      this.deps.runtimeDir, "node_modules", ".bin",
      this.platform === "win32-x64" ? `${name}.cmd` : name);
    const lspInstalled = existsSync(join(this.deps.runtimeDir, "node_modules"));
    const rustAnalyzerBin = binPath(this.deps.runtimeDir, "rust-analyzer", this.platform);
    // Managed install lands at rustAnalyzerBin (Task 3); fall back to a bare
    // PATH lookup for a dev backend running outside the managed runtime — the
    // indexer degrades gracefully either way if it's still not found.
    const rsCmd = existsSync(rustAnalyzerBin) ? rustAnalyzerBin : "rust-analyzer";
    const env = {
      ...process.env,
      CRUCIBLE_BACKEND_URL: `http://localhost:${port}`,
      CRUCIBLE_LSP_ENABLED: lspInstalled ? "true" : "false",
      CRUCIBLE_LSP_RS_CMD: rsCmd,
      ...(lspInstalled
        ? {
            CRUCIBLE_LSP_PY_CMD: `${lspBin("pyright-langserver")} --stdio`,
            CRUCIBLE_LSP_TS_CMD: `${lspBin("typescript-language-server")} --stdio`,
          }
        : {}),
    } as Record<string, string>;
```

(`CRUCIBLE_LSP_RS_CMD` moves out of the `lspInstalled`-gated spread since rust-analyzer's presence is independent of the Node-based Python/TypeScript LSPs.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w ai-editor-vscode-extension test -- runtime-backend-process`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/runtime/backend-process.ts apps/vscode-extension/test/runtime-backend-process.test.ts
git commit -m "feat(runtime): resolve CRUCIBLE_LSP_RS_CMD to the managed rust-analyzer binary"
```

---

### Task 5: wire rust-analyzer into the release CI and dev manifest placeholder

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `apps/vscode-extension/resources/runtime-manifest.json`

**Interfaces:**
- Consumes: `fetch_tools.py --rust-analyzer` (Task 1), `make_manifest.py`'s `rust-analyzer` component-version requirement (Task 2).

No automated test for this task — it's CI/static-config wiring with no unit under test; verified via the manual JSON/YAML sanity check in Step 2.

- [ ] **Step 1: Update `release.yml`**

In `.github/workflows/release.yml`, change the `fetch-tools` job from:

```yaml
  fetch-tools:
    # uv + ripgrep: download official release binaries per platform, restage
    # under our naming convention. Pinned versions live in this job's env.
    needs: test
    runs-on: ubuntu-latest
    env: { UV_VERSION: "0.5.24", RG_VERSION: "14.1.1" }
    steps:
      - uses: actions/checkout@v4
      - run: python3 scripts/release/fetch_tools.py --uv "$UV_VERSION" --rg "$RG_VERSION" --out dist/
      - uses: actions/upload-artifact@v4
        with: { name: tools, path: dist/ }
```

to:

```yaml
  fetch-tools:
    # uv + ripgrep + rust-analyzer: download official release binaries per
    # platform, restage under our naming convention. Pinned versions live in
    # this job's env.
    needs: test
    runs-on: ubuntu-latest
    env: { UV_VERSION: "0.5.24", RG_VERSION: "14.1.1", RUST_ANALYZER_VERSION: "2026-07-06" }
    steps:
      - uses: actions/checkout@v4
      - run: python3 scripts/release/fetch_tools.py --uv "$UV_VERSION" --rg "$RG_VERSION" --rust-analyzer "$RUST_ANALYZER_VERSION" --out dist/
      - uses: actions/upload-artifact@v4
        with: { name: tools, path: dist/ }
```

And change the `package` job's `make_manifest.py` invocation from:

```yaml
      - run: |
          python scripts/release/make_manifest.py \
            --release-tag "${GITHUB_REF_NAME}" --dist dist-artifacts \
            --url-base "https://github.com/${GITHUB_REPOSITORY}/releases/download/${GITHUB_REF_NAME}" \
            --component-version indexer=${GITHUB_REF_NAME#v} \
            --component-version ripgrep=14.1.1 --component-version uv=0.5.24 \
            --lsp-packages "pyright@1.1.400,typescript-language-server@4.3.3" \
            --out dist-artifacts/manifest.json
```

to:

```yaml
      - run: |
          python scripts/release/make_manifest.py \
            --release-tag "${GITHUB_REF_NAME}" --dist dist-artifacts \
            --url-base "https://github.com/${GITHUB_REPOSITORY}/releases/download/${GITHUB_REF_NAME}" \
            --component-version indexer=${GITHUB_REF_NAME#v} \
            --component-version ripgrep=14.1.1 --component-version uv=0.5.24 \
            --component-version rust-analyzer=2026-07-06 \
            --lsp-packages "pyright@1.1.400,typescript-language-server@4.3.3" \
            --out dist-artifacts/manifest.json
```

- [ ] **Step 2: Update the dev-placeholder manifest and sanity-check it**

In `apps/vscode-extension/resources/runtime-manifest.json`, change:

```json
{
  "manifestVersion": 1,
  "releaseTag": "dev-unpinned",
  "_comment": "Hand-written dev placeholder. Release builds overwrite this with scripts/release/make_manifest.py output (real per-OS urls + sha256s).",
  "components": {
    "uv": { "version": "0.5.24", "urls": {}, "sha256": {} },
    "agentd": { "version": "0.0.0" },
    "indexer": { "version": "0.0.0", "urls": {}, "sha256": {} },
    "ripgrep": { "version": "14.1.1", "urls": {}, "sha256": {} },
    "lsps": {
      "version": "1",
      "npmPackages": ["pyright@1.1.400", "typescript-language-server@4.3.3"]
    }
  }
}
```

to:

```json
{
  "manifestVersion": 1,
  "releaseTag": "dev-unpinned",
  "_comment": "Hand-written dev placeholder. Release builds overwrite this with scripts/release/make_manifest.py output (real per-OS urls + sha256s).",
  "components": {
    "uv": { "version": "0.5.24", "urls": {}, "sha256": {} },
    "agentd": { "version": "0.0.0" },
    "indexer": { "version": "0.0.0", "urls": {}, "sha256": {} },
    "ripgrep": { "version": "14.1.1", "urls": {}, "sha256": {} },
    "rust-analyzer": { "version": "2026-07-06", "urls": {}, "sha256": {} },
    "lsps": {
      "version": "1",
      "npmPackages": ["pyright@1.1.400", "typescript-language-server@4.3.3"]
    }
  }
}
```

Sanity-check both files parse:

Run: `python3 -c "import json; json.load(open('apps/vscode-extension/resources/runtime-manifest.json'))" && echo OK`
Expected: `OK`

Run: `cd .github/workflows && python3 -c "import yaml; yaml.safe_load(open('release.yml'))" && echo OK` (if `pyyaml` isn't available locally, `python3 -c "import yaml"` first — it's already a hard dependency of `agentd-py`, so `services/agentd-py/.venv/bin/python3` has it if the repo-root env doesn't)
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml apps/vscode-extension/resources/runtime-manifest.json
git commit -m "feat(release): pin and wire rust-analyzer through CI and the dev manifest"
```

---

### Task 6: memory harness enabled by default (`agentd/memory/config.py`)

**Files:**
- Modify: `services/agentd-py/agentd/memory/config.py`
- Modify: `services/agentd-py/agentd/chat/controller_factory.py:31-35` (docstring only)
- Modify: `services/agentd-py/tests/test_memory_config.py`
- Modify: `services/agentd-py/tests/test_memory_reranker.py`
- Modify: `services/agentd-py/tests/test_memory_harness.py`

**Interfaces:**
- Produces: `MemoryConfig.from_env({}).enabled is True` and `.reranker_enabled is True` (was `False`); `is_memory_enabled()` in `controller_factory.py` inherits this automatically (it delegates directly to `MemoryConfig.from_env(os.environ).enabled` — no separate logic to change).

- [ ] **Step 1: Write the failing tests**

In `services/agentd-py/tests/test_memory_config.py`, replace `test_from_env_defaults_disabled` with:

```python
def test_from_env_defaults_enabled():
    cfg = MemoryConfig.from_env({})
    assert cfg.enabled is True
    assert cfg.db_path.endswith("memory.sqlite3")
    assert cfg.trigger_frac == 0.65
    assert cfg.hot_token_frac == 0.4
    assert cfg.hot_turns == 10
    assert cfg.window_tokens == 128000


def test_from_env_explicit_disable_still_works():
    cfg = MemoryConfig.from_env({"CRUCIBLE_MEMORY_ENABLED": "false"})
    assert cfg.enabled is False
```

(`test_from_env_overrides` is unaffected — it already sets `CRUCIBLE_MEMORY_ENABLED: "1"` explicitly and asserts `True`.)

In `services/agentd-py/tests/test_memory_reranker.py`, replace `test_config_reranker_defaults` with:

```python
def test_config_reranker_defaults():
    c = MemoryConfig.from_env({})
    assert c.reranker_enabled is True
    assert c.reranker_model == "BAAI/bge-reranker-base"
    assert c.rerank_min_candidates == 8


def test_config_reranker_explicit_disable_still_works():
    c = MemoryConfig.from_env({"CRUCIBLE_MEMORY_RERANKER": "0"})
    assert c.reranker_enabled is False
```

In `services/agentd-py/tests/test_memory_harness.py`, change the existing (this will otherwise start failing once Step 3 lands, since it relies on the old default):

```python
def test_build_memory_harness_disabled_returns_noop():
    cfg = MemoryConfig.from_env({})  # disabled
    assert build_memory_harness(cfg, _FakeTransport(), "m1") is NO_OP_HARNESS
```

to:

```python
def test_build_memory_harness_disabled_returns_noop():
    cfg = MemoryConfig.from_env({"CRUCIBLE_MEMORY_ENABLED": "false"})
    assert build_memory_harness(cfg, _FakeTransport(), "m1") is NO_OP_HARNESS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_config.py tests/test_memory_reranker.py tests/test_memory_harness.py -v`
Expected: `test_from_env_defaults_enabled` FAILS (`cfg.enabled` is currently `False`), `test_config_reranker_defaults` FAILS (`c.reranker_enabled` is currently `False`). The two new explicit-disable tests and the harness test already pass unchanged (nothing to break yet) — that's expected too, since only the two default-reading assertions require the Step 3 code change.

- [ ] **Step 3: Flip the two defaults**

In `services/agentd-py/agentd/memory/config.py`, change:

```python
            enabled=env.get("CRUCIBLE_MEMORY_ENABLED", "").lower() in _TRUTHY,
```

to:

```python
            enabled=env.get("CRUCIBLE_MEMORY_ENABLED", "true").lower() in _TRUTHY,
```

and change:

```python
            reranker_enabled=env.get("CRUCIBLE_MEMORY_RERANKER", "").lower() in _TRUTHY,
```

to:

```python
            reranker_enabled=env.get("CRUCIBLE_MEMORY_RERANKER", "true").lower() in _TRUTHY,
```

In `services/agentd-py/agentd/chat/controller_factory.py`, update the docstring (lines 31-35) from:

```python
def is_memory_enabled() -> bool:
    """Whether the memory harness (compaction + recall/remember) is active. Default OFF;
    opt in with CRUCIBLE_MEMORY_ENABLED=1. Gates the controller's memory tools + prompt."""
    from agentd.memory.config import MemoryConfig
    return MemoryConfig.from_env(os.environ).enabled
```

to:

```python
def is_memory_enabled() -> bool:
    """Whether the memory harness (compaction + recall/remember) is active. Default ON;
    kill-switch via CRUCIBLE_MEMORY_ENABLED=0/false/no/off. Gates the controller's memory
    tools + prompt."""
    from agentd.memory.config import MemoryConfig
    return MemoryConfig.from_env(os.environ).enabled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/test_memory_config.py tests/test_memory_reranker.py tests/test_memory_harness.py tests/test_memory_models_phase2.py -v`
Expected: all PASS. Then run the broader memory-adjacent suite to catch anything else coupled to the old default:

Run: `cd services/agentd-py && source .venv/bin/activate && pytest tests/ -k memory -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/memory/config.py services/agentd-py/agentd/chat/controller_factory.py services/agentd-py/tests/test_memory_config.py services/agentd-py/tests/test_memory_reranker.py services/agentd-py/tests/test_memory_harness.py
git commit -m "feat(memory): enable memory harness + reranker by default, keep kill-switch semantics"
```

---

### Task 7: install `[memory]` extras for the managed agentd wheel

**Files:**
- Modify: `apps/vscode-extension/src/runtime/installer.ts`
- Test: `apps/vscode-extension/test/runtime-installer.test.ts`

**Interfaces:**
- Consumes: `ComponentSpec.urls?.any` (unchanged shape — no schema change).
- Produces: the `uv pip install` target string for the `agentd` component now always requests the `[memory]` extra, in both the URL-present (production releases, PEP 508 direct-reference syntax) and URL-absent (test/dev fallback) branches.

- [ ] **Step 1: Write the failing tests**

Add to `apps/vscode-extension/test/runtime-installer.test.ts`, inside the `describe("RuntimeInstaller", ...)` block:

```ts
  it("agentd install requests the [memory] extra via the bare-version fallback", async () => {
    const d = deps();
    await new RuntimeInstaller(d).installAll();
    const pipCall = d.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeDefined();
    expect(pipCall![pipCall!.length - 1]).toBe("ai-editor-agentd[memory]==0.1.0");
  });

  it("agentd install wraps a manifest wheel URL with the [memory] extra as a PEP 508 direct reference", async () => {
    const d = deps();
    d.manifest.components.agentd = { version: "0.3.0", urls: { any: "https://example.com/pkg.whl" } };
    await new RuntimeInstaller(d).installAll();
    const pipCall = d.calls.find((call) => call.includes("pip"));
    expect(pipCall).toBeDefined();
    expect(pipCall![pipCall!.length - 1]).toBe(
      "ai-editor-agentd[memory] @ https://example.com/pkg.whl");
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run -w ai-editor-vscode-extension test -- runtime-installer`
Expected: both new tests FAIL — today's target is `ai-editor-agentd==0.1.0` (no extra) for the first, and the raw URL with no `[memory]` wrapping for the second.

- [ ] **Step 3: Update the agentd install target construction**

In `apps/vscode-extension/src/runtime/installer.ts`, inside `installOne`'s `agentd` branch, change:

```ts
    if (id === "agentd") {
      const uv = binPath(this.deps.runtimeDir, "uv", this.platform);
      const venv = await this.deps.exec(uv, ["venv", join(this.deps.runtimeDir, "venv"), "--python", "3.12"]);
      if (venv.code !== 0) throw new Error(`uv venv failed: ${venv.stderr.slice(0, 400)}`);
      const target = spec.urls?.any ?? `ai-editor-agentd==${spec.version}`;
      const pip = await this.deps.exec(
        uv, ["pip", "install", "--python", venvPython(this.deps.runtimeDir, this.platform), target]);
      if (pip.code !== 0) throw new Error(`uv pip install failed: ${pip.stderr.slice(0, 400)}`);
      return { id, status: "done" };
    }
```

to:

```ts
    if (id === "agentd") {
      const uv = binPath(this.deps.runtimeDir, "uv", this.platform);
      const venv = await this.deps.exec(uv, ["venv", join(this.deps.runtimeDir, "venv"), "--python", "3.12"]);
      if (venv.code !== 0) throw new Error(`uv venv failed: ${venv.stderr.slice(0, 400)}`);
      // [memory] pulls in sentence-transformers/numpy (and PyTorch, transitively) so the
      // memory harness (on by default — see agentd/memory/config.py) works out of the box
      // instead of silently degrading its embedder. PEP 508 direct-reference syntax
      // ("name[extra] @ url") is required to combine an extras marker with a URL install.
      const target = spec.urls?.any
        ? `ai-editor-agentd[memory] @ ${spec.urls.any}`
        : `ai-editor-agentd[memory]==${spec.version}`;
      const pip = await this.deps.exec(
        uv, ["pip", "install", "--python", venvPython(this.deps.runtimeDir, this.platform), target]);
      if (pip.code !== 0) throw new Error(`uv pip install failed: ${pip.stderr.slice(0, 400)}`);
      return { id, status: "done" };
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run -w ai-editor-vscode-extension test -- runtime-installer`
Expected: PASS (including the Task 3 test, which uses the URL-less `agentd: { version: "0.1.0" }` fixture entry and so exercises the `==` branch — unaffected by this change's behavior, just now expects `[memory]` in the string, which is not asserted there so it stays green).

- [ ] **Step 5: Commit**

```bash
git add apps/vscode-extension/src/runtime/installer.ts apps/vscode-extension/test/runtime-installer.test.ts
git commit -m "feat(runtime): install agentd[memory] extras so the memory harness works by default"
```

---

### Task 8: flip extension settings defaults + update CLAUDE.md

**Files:**
- Modify: `apps/vscode-extension/package.json`
- Modify: `CLAUDE.md`

**Interfaces:** none (config/doc only, no code consumes these beyond what Tasks 6-7 already changed).

No automated test — this is a JSON schema default (not independently unit-tested anywhere in this repo, confirmed during design) and prose documentation.

- [ ] **Step 1: Flip the two settings defaults**

In `apps/vscode-extension/package.json`, change:

```json
        "aiEditor.memory.enabled": {
          "type": "boolean",
          "default": false,
          "description": "Enable the cross-session memory harness (compaction, recall, consolidation). Requires a managed restart to take effect."
        },
        "aiEditor.memory.reranker": {
          "type": "boolean",
          "default": false,
          "description": "Enable the cross-encoder reranker for memory recall (requires aiEditor.memory.enabled). Requires a managed restart to take effect."
        }
```

to:

```json
        "aiEditor.memory.enabled": {
          "type": "boolean",
          "default": true,
          "description": "Enable the cross-session memory harness (compaction, recall, consolidation). Requires a managed restart to take effect."
        },
        "aiEditor.memory.reranker": {
          "type": "boolean",
          "default": true,
          "description": "Enable the cross-encoder reranker for memory recall (requires aiEditor.memory.enabled). Requires a managed restart to take effect."
        }
```

- [ ] **Step 2: Sanity-check the JSON still parses**

Run: `python3 -c "import json; json.load(open('apps/vscode-extension/package.json'))" && echo OK`
Expected: `OK`

- [ ] **Step 3: Update CLAUDE.md**

Change line 446 from:

```
Self-contained module (`agentd/memory/`): `harness.py` (the only unit the loops see), `compactor.py` (token-trigger eviction + summarize), `store.py` (SQLite — `compaction_segments`, `anchored_summaries`, + P2 `memories`/sqlite-vec/FTS5), `consolidator.py` + `recall.py` + `embedder.py` + `tool_source.py` (P2), `models.py`, `config.py`. OFF by default (`CRUCIBLE_MEMORY_ENABLED`); P2 also needs a workspace scope (wired via `build_memory_harness(..., workspace_path=…)` in `main.py` + `controller_factory.py`).
```

to:

```
Self-contained module (`agentd/memory/`): `harness.py` (the only unit the loops see), `compactor.py` (token-trigger eviction + summarize), `store.py` (SQLite — `compaction_segments`, `anchored_summaries`, + P2 `memories`/sqlite-vec/FTS5), `consolidator.py` + `recall.py` + `embedder.py` + `tool_source.py` (P2), `models.py`, `config.py`. ON by default since 2026-07-08 (`CRUCIBLE_MEMORY_ENABLED`; kill-switch via `0/false/no/off`); P2 also needs a workspace scope (wired via `build_memory_harness(..., workspace_path=…)` in `main.py` + `controller_factory.py`).
```

Change line 470 from:

```
- **Reranker (`memory/reranker.py`):** local `sentence-transformers` CrossEncoder (`BAAI/bge-reranker-base`), **independent of `MEMORY_ENABLED`** (own flag `CRUCIBLE_MEMORY_RERANKER`, default OFF) and **degrade-not-raise** (model/lib absent → fused order, `available=False`). Slots into `RecallEngine` at the post-floor seam, **count-gated** (`CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES`, default 8) — reorders floor-passing candidates, never resurrects below-floor ones. `recall()` signature is **UNCHANGED**; `recall_with_trace()` does the work and `recall()` = `(await recall_with_trace(...))[0]`. Only the harness `_fill_recall` switched (every `_SpyRecall` test fake gained `recall_with_trace` in lockstep — same breakage class as P2's `prepare_turn(query=…)`).
```

to:

```
- **Reranker (`memory/reranker.py`):** local `sentence-transformers` CrossEncoder (`BAAI/bge-reranker-base`), **independent of `MEMORY_ENABLED`** (own flag `CRUCIBLE_MEMORY_RERANKER`, default ON since 2026-07-08, kill-switch via `0/false/no/off`) and **degrade-not-raise** (model/lib absent → fused order, `available=False`). Slots into `RecallEngine` at the post-floor seam, **count-gated** (`CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES`, default 8) — reorders floor-passing candidates, never resurrects below-floor ones. `recall()` signature is **UNCHANGED**; `recall_with_trace()` does the work and `recall()` = `(await recall_with_trace(...))[0]`. Only the harness `_fill_recall` switched (every `_SpyRecall` test fake gained `recall_with_trace` in lockstep — same breakage class as P2's `prepare_turn(query=…)`).
```

Change line 475 from:

```
- **Phase-3 config env vars:** `CRUCIBLE_MEMORY_RERANKER` (default off), `CRUCIBLE_MEMORY_RERANKER_MODEL` (default `BAAI/bge-reranker-base`), `CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES` (default 8).
```

to:

```
- **Phase-3 config env vars:** `CRUCIBLE_MEMORY_RERANKER` (default on since 2026-07-08), `CRUCIBLE_MEMORY_RERANKER_MODEL` (default `BAAI/bge-reranker-base`), `CRUCIBLE_MEMORY_RERANK_MIN_CANDIDATES` (default 8).
```

Change line 548 from:

```
- `CRUCIBLE_MEMORY_ENABLED` — master switch (default OFF; truthy = `1/true/yes/on`). When off, `prepare_turn` is a byte-identical passthrough. (Phase-2 recall/consolidation additionally needs a workspace scope, which the factories pass.)
```

to:

```
- `CRUCIBLE_MEMORY_ENABLED` — master switch, default **ON** since 2026-07-08 (truthy = `1/true/yes/on`; set to `0/false/no/off` to disable). When off, `prepare_turn` is a byte-identical passthrough. (Phase-2 recall/consolidation additionally needs a workspace scope, which the factories pass.) The managed runtime installer (`apps/vscode-extension/src/runtime/installer.ts`) installs the `ai-editor-agentd[memory]` extra (pulls in `sentence-transformers`/PyTorch, ~500MB-1GB+) specifically so this default works out of the box instead of silently degrading the embedder.
```

Change line 587 from:

```
- `aiEditor.memory.enabled` / `aiEditor.memory.reranker` — booleans, default `false`. Become `CRUCIBLE_MEMORY_ENABLED` / `CRUCIBLE_MEMORY_RERANKER`; a change flags `restartRequired` in the settings panel.
```

to:

```
- `aiEditor.memory.enabled` / `aiEditor.memory.reranker` — booleans, default `true` since 2026-07-08. Become `CRUCIBLE_MEMORY_ENABLED` / `CRUCIBLE_MEMORY_RERANKER`; a change flags `restartRequired` in the settings panel.
```

- [ ] **Step 4: Commit**

```bash
git add apps/vscode-extension/package.json CLAUDE.md
git commit -m "docs: memory harness + reranker on by default since 2026-07-08"
```

---

## Final verification (run after all 8 tasks)

- [ ] **Full Python suite:** `cd services/agentd-py && source .venv/bin/activate && pytest` — expect all green (this catches any test elsewhere in the suite coupled to the old memory-disabled default that the targeted greps in Task 6 might have missed).
- [ ] **Full TypeScript suite:** `npm run build && npm run test && npm run typecheck` from repo root.
- [ ] **Full release-scripts suite:** `cd scripts/release && python -m pytest -v`.
