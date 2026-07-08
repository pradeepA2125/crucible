import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_gopls import PLATFORMS, build_gopls_binaries, gopls_artifact_name  # noqa: E402


def _fake_run_cmd(calls: list[list[str]]):
    def run(cmd, *, cwd, check, capture_output, text, env=None):
        calls.append(cmd)
        if cmd[:2] == ["go", "build"]:
            dest = Path(cmd[cmd.index("-o") + 1])
            dest.write_bytes(b"fake-gopls-binary")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def test_gopls_artifact_name_conventions() -> None:
    assert gopls_artifact_name("darwin-arm64") == "gopls-darwin-arm64"
    assert gopls_artifact_name("linux-x64") == "gopls-linux-x64"
    assert gopls_artifact_name("win32-x64") == "gopls-win32-x64.exe"


def test_build_gopls_binaries_writes_one_artifact_per_platform(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    out_dir = tmp_path / "out"

    written = build_gopls_binaries(
        "v0.22.0", out_dir, work_dir=tmp_path / "work", run_cmd=_fake_run_cmd(calls))

    names = {p.name for p in written}
    assert names == {gopls_artifact_name(p) for p in PLATFORMS}
    assert all(p.exists() and p.read_bytes() == b"fake-gopls-binary" for p in written)


def test_go_get_pins_the_exact_requested_version(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    build_gopls_binaries(
        "v0.22.0", tmp_path / "out", work_dir=tmp_path / "work",
        run_cmd=_fake_run_cmd(calls))

    assert ["go", "get", "golang.org/x/tools/gopls@v0.22.0"] in calls


def test_build_targets_the_bare_package_path_not_a_version_suffix(tmp_path: Path) -> None:
    # go build doesn't accept the @version remote syntax at all — only the
    # earlier `go get` step does. Regression guard for that exact distinction.
    calls: list[list[str]] = []
    build_gopls_binaries(
        "v0.22.0", tmp_path / "out", work_dir=tmp_path / "work",
        run_cmd=_fake_run_cmd(calls))

    build_calls = [c for c in calls if c[:2] == ["go", "build"]]
    assert len(build_calls) == len(PLATFORMS)
    assert all(c[-1] == "golang.org/x/tools/gopls" for c in build_calls)


def test_posix_binaries_get_the_executable_bit(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    written = build_gopls_binaries(
        "v0.22.0", tmp_path / "out", work_dir=tmp_path / "work",
        run_cmd=_fake_run_cmd(calls))

    posix_paths = [p for p in written if not p.name.endswith(".exe")]
    assert posix_paths, "expected at least one non-windows artifact"
    for path in posix_paths:
        assert path.stat().st_mode & 0o111, f"{path} missing executable bit"
