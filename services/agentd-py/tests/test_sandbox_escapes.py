"""A sandboxed command must not be able to widen its own wall or get code run outside it.

Real sandboxed processes and the real policy, on both platforms. The workspace sits in a
canary folder under the home folder: pytest's tmp_path is inside the writable temp roots,
where "outside" would not be outside. Every proof here was first found by running it against
the branch.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentd.child_env import child_env
from agentd.sandbox.blocks import classify_block
from agentd.sandbox.runtime import SandboxRuntime

MACOS = sys.platform == "darwin"
pytestmark = pytest.mark.skipif(
    not MACOS and shutil.which("bwrap") is None, reason="needs Seatbelt or bubblewrap"
)
macos_only = pytest.mark.skipif(not MACOS, reason="bubblewrap can't express this")
linux_only = pytest.mark.skipif(MACOS, reason="the Linux form of this rule")

_IDENTITY = [
    "-c",
    "user.name=a",
    "-c",
    "user.email=a@example.invalid",
    "-c",
    "core.hooksPath=/dev/null",
]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_IDENTITY, *args], cwd=cwd, check=True, capture_output=True, env=child_env()
    )


@pytest.fixture
def base() -> Iterator[Path]:
    path = Path.home() / f".crucible-sbx-test-{uuid.uuid4().hex[:10]}"
    path.mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def repo(base: Path) -> Path:
    workspace = base / "work space"
    workspace.mkdir()
    (workspace / ".crucible").mkdir()
    (workspace / "a.txt").write_text("a\n")
    _git(workspace, "init", "-q", "-b", "main", ".")
    _git(workspace, "add", "a.txt")
    _git(workspace, "commit", "-qm", "one")
    return workspace


def _run(workspace: Path, line: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
    async def go() -> subprocess.CompletedProcess[str]:
        runtime = SandboxRuntime(workspace, home)
        await runtime.probe()
        launch = runtime.launch_for(shadow_root=None)
        assert launch is not None
        return await asyncio.to_thread(
            subprocess.run,
            launch.wrap(["/bin/sh", "-c", line]),
            capture_output=True,
            text=True,
            timeout=60,
            env=launch.env(child_env()),
            cwd=workspace,
        )

    return asyncio.run(go())


def _policy(workspace: Path):
    async def go():
        runtime = SandboxRuntime(workspace)
        await runtime.probe()
        return runtime.launch_for(shadow_root=None).policy

    return asyncio.run(go())


def test_control_folders_cannot_be_moved_aside_and_rewritten(repo: Path) -> None:
    for name in (".git", ".crucible"):
        done = _run(repo, f"mv {name} moved")
        assert done.returncode != 0, name
        assert (repo / name).is_dir() and not (repo / "moved").exists()


def test_workspace_cannot_be_moved_to_reach_protected_files(repo: Path, base: Path) -> None:
    (repo / "AGENTS.md").write_text("original\n")
    done = _run(
        repo, f"cd /; mv '{repo}' '{base}/moved' || mv '{repo}' /tmp/crucible-moved-{base.name}"
    )
    assert done.returncode != 0
    assert (repo / "AGENTS.md").read_text() == "original\n"


@pytest.mark.parametrize(
    "name",
    [
        "commondir",
        "gitdir",
        "config",
        "config.worktree",
        "hooks/pre-commit",
        "info/x",
        "modules/a/config",
        "worktrees/a/commondir",
        "new-file",
    ],
)
def test_git_control_files_are_not_writable(repo: Path, name: str) -> None:
    done = _run(repo, f"mkdir -p .git/$(dirname {name}); echo /tmp/evil > .git/{name}")
    assert done.returncode != 0, name
    assert not (repo / ".git" / name).exists() or name in {"config"}
    assert "/tmp/evil" not in (repo / ".git/config").read_text()


@pytest.mark.parametrize("name", [".vscode", ".claude"])
def test_a_missing_control_folder_cannot_be_planted_as_a_symlink(repo: Path, name: str) -> None:
    assert _run(repo, f"ln -s /tmp {name}").returncode != 0
    assert not (repo / name).is_symlink()
    assert _run(repo, f"mkdir {name}").returncode != 0


@macos_only
def test_everyday_git_still_works(repo: Path) -> None:
    identity = "-c user.name=a -c user.email=a@example.invalid -c core.hooksPath=/dev/null"
    line = (
        f"echo b >> a.txt && git add a.txt && git {identity} commit -qm two && "
        f"git checkout -q -b topic && echo c >> a.txt && git {identity} commit -qam three && "
        f"git checkout -q main && git {identity} merge -q --no-edit topic && "
        f"echo d >> a.txt && git {identity} stash -q && git {identity} stash pop -q && "
        f"git {identity} tag v1 && git {identity} gc -q && git log --oneline | wc -l"
    )
    done = _run(repo, line)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "3"


@linux_only
def test_linux_git_reads_and_fetches_but_committing_is_a_named_block(repo: Path) -> None:
    # bwrap can't allow `.git/index.lock` while blocking a new `.git/commondir`, so on Linux
    # .git stays read-only and a commit goes through an escalation, as in Codex.
    assert _run(repo, "git status --short && git log --oneline").returncode == 0
    (repo / "b.txt").write_text("b\n")
    done = _run(repo, "git add b.txt")
    assert done.returncode != 0
    assert "index.lock" in done.stderr and "Read-only file system" in done.stderr
    block = classify_block(done.stderr, done.returncode, _policy(repo), [])
    assert block is not None and block.kind == "write"


def test_ordinary_workspace_writes_still_work(repo: Path) -> None:
    done = _run(
        repo, "mkdir -p src/x && echo a > src/x/b && mv src/x src/y && rm -r src && touch ok.txt"
    )
    assert done.returncode == 0, done.stderr


@macos_only
def test_scratch_files_in_crucible_stay_writable(repo: Path) -> None:
    assert _run(repo, "echo s > .crucible/scratch.txt").returncode == 0


def test_mcp_servers_and_rules_cannot_be_planted(repo: Path) -> None:
    # A new server here runs unsandboxed the next time the backend starts.
    for name in ("mcp.json", "approved-commands.json", "agents/x.md", "skills/x/SKILL.md"):
        assert (
            _run(
                repo, f"mkdir -p .crucible/$(dirname {name}); echo x > .crucible/{name}"
            ).returncode
            != 0
        ), name
        assert not (repo / ".crucible" / name).exists(), name


def test_private_temp_folder_is_where_temp_files_go(repo: Path) -> None:
    done = _run(repo, 'python3 -c "import tempfile; print(tempfile.gettempdir())"')
    assert done.returncode == 0, done.stderr
    assert "crucible-sbx" in done.stdout or done.stdout.strip() == "/tmp"
    if not MACOS:
        marker = f"/tmp/crucible-host-{uuid.uuid4().hex}"
        Path(marker).write_text("host")
        try:
            assert _run(repo, f"cat {marker}").returncode != 0
        finally:
            Path(marker).unlink()


def test_unix_sockets_work_inside_the_sandbox(repo: Path) -> None:
    line = (
        'python3 -c "import multiprocessing as m\n'
        "if __name__ == '__main__':\n    print(m.Manager().list([1])[0])\""
    )
    done = _run(repo, line)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "1"


def test_host_unix_sockets_are_out_of_reach(repo: Path) -> None:
    # /tmp holds the host's tmux, VS Code and X11 sockets; on macOS $TMPDIR holds VS Code's.
    folder = Path(tempfile.mkdtemp(prefix="crucible-host-sock-", dir="/tmp"))
    host = socket.socket(socket.AF_UNIX)
    try:
        host.bind(str(folder / "s"))
        host.listen()
        line = f"python3 -c \"import socket; socket.socket(socket.AF_UNIX).connect('{folder}/s')\""
        assert _run(repo, line).returncode != 0
    finally:
        host.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_linked_worktree_cannot_redirect_itself(repo: Path, base: Path) -> None:
    linked = base / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "w", str(linked))
    assert _run(linked, "git status --short").returncode == 0
    if MACOS:
        identity = "-c user.name=a -c user.email=a@example.invalid -c core.hooksPath=/dev/null"
        done = _run(
            linked,
            f"echo b >> a.txt && git add a.txt && git {identity} commit -qm two && "
            "git log --oneline | wc -l",
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == "2"
    assert _run(linked, "echo 'gitdir: /tmp/evil' > .git").returncode != 0
    gitdir = repo / ".git/worktrees/linked"
    assert _run(linked, f"echo /tmp/evil > '{gitdir}/commondir'").returncode != 0
    assert _run(linked, f"echo x > '{repo}/.git/hooks/pre-commit'").returncode != 0
    assert _run(linked, f"echo x > '{repo}/.git/config'").returncode != 0
    assert "/tmp/evil" not in (gitdir / "commondir").read_text()


def test_a_committed_settings_file_widens_nothing(repo: Path, base: Path) -> None:
    home = base / "home"
    home.mkdir()
    (home / "planted").write_text("")
    (repo / ".crucible/sandbox.json").write_text(f'{{"writable": ["{base}"]}}')
    (repo / ".crucible/approved-hosts.json").write_text('{"hosts": ["evil.example"]}')
    runtime = SandboxRuntime(repo, home)
    assert runtime.store.folders() == []
    assert runtime.store.hosts() == []
    assert _run(repo, f"echo x > '{base}/planted'", home).returncode != 0


def test_stored_settings_live_outside_the_workspace_and_the_wall(repo: Path, base: Path) -> None:
    home = base / "home"
    home.mkdir()
    runtime = SandboxRuntime(repo, home)
    runtime.store.add_host("example.com")
    assert runtime.store.hosts() == ["example.com"]
    stored = list(home.rglob("*.json"))
    assert len(stored) == 1 and not stored[0].is_relative_to(repo)
    assert _run(repo, f"echo '{{}}' > '{stored[0]}'").returncode != 0
    assert runtime.store.hosts() == ["example.com"]


def test_each_workspace_keeps_its_own_settings(base: Path) -> None:
    home = base / "home"
    (base / "one").mkdir()
    (base / "two").mkdir()
    SandboxRuntime(base / "one", home).store.add_host("example.com")
    assert SandboxRuntime(base / "two", home).store.hosts() == []


def test_a_workspace_holding_credentials_cannot_write_them(base: Path) -> None:
    home = base / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh/authorized_keys").write_text("")
    done = _run(home, "echo evil >> .ssh/authorized_keys", home)
    assert done.returncode != 0
    assert (home / ".ssh/authorized_keys").read_text() == ""


@linux_only
def test_existing_startup_file_is_protected_on_linux(base: Path) -> None:
    home = base / "home"
    home.mkdir()
    (home / ".zshenv").write_text("original\n")
    assert _run(home, "echo evil >> .zshenv", home).returncode != 0
    assert (home / ".zshenv").read_text() == "original\n"


@macos_only
@pytest.mark.parametrize("name", [".zshenv", ".zlogin", ".bash_login"])
def test_every_shell_startup_file_is_protected(base: Path, name: str) -> None:
    home = base / "home"
    home.mkdir()
    assert _run(home, f"echo evil > {name}", home).returncode != 0
    assert not (home / name).exists()


@macos_only
def test_the_clipboard_is_out_of_reach(repo: Path) -> None:
    subprocess.run(["pbcopy"], input="crucible-clipboard-canary", text=True, check=True)
    done = _run(repo, "pbpaste")
    assert "crucible-clipboard-canary" not in done.stdout


@pytest.mark.parametrize("name", [".mcp.json", ".envrc", ".claude/settings.json",
                                  ".claude/settings.local.json"])
def test_files_other_tools_run_automatically_are_protected(repo: Path, name: str) -> None:
    (repo / name).parent.mkdir(exist_ok=True)
    (repo / name).write_text("original\n")
    assert _run(repo, f"echo evil >> {name}").returncode != 0
    assert (repo / name).read_text() == "original\n"


@pytest.mark.parametrize("name", [".cargo/credentials.toml", ".pgpass", ".my.cnf", ".vault-token",
                                  ".config/git/credentials", ".codex/auth.json",
                                  ".gemini/oauth.json", ".zsh_history", ".bash_history"])
def test_more_credential_files_are_unreadable(base: Path, name: str) -> None:
    home = base / "home"
    (home / name).parent.mkdir(parents=True, exist_ok=True)
    (home / name).write_text("secret\n")
    workspace = base / "ws"
    workspace.mkdir()
    assert _run(workspace, f"cat '{home / name}'", home).stdout == ""


@macos_only
@pytest.mark.parametrize("name", ["rebase-merge/git-rebase-todo", "rebase-apply/x", "sequencer/todo",
                                  "rr-cache/x", "BISECT_RUN", "objects/info/alternates",
                                  "objects/info/http-alternates"])
def test_files_git_acts_on_later_are_not_writable(repo: Path, name: str) -> None:
    # `exec` lines in a rebase todo run outside the wall on `git rebase --continue`.
    done = _run(repo, f"mkdir -p .git/$(dirname {name}); echo 'exec touch /tmp/evil' > .git/{name}")
    assert done.returncode != 0, name
    assert not (repo / ".git" / name).exists()


@macos_only
def test_a_paused_rebase_cannot_be_given_commands(repo: Path) -> None:
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "two")
    subprocess.run(["git", *_IDENTITY, "rebase", "-i", "HEAD~1"], cwd=repo, check=True,
                   capture_output=True, env={**child_env(), "GIT_SEQUENCE_EDITOR": "sed -i '' '1i\\\nbreak\n'"})
    todo = repo / ".git/rebase-merge/git-rebase-todo"
    before = todo.read_text()
    assert _run(repo, "echo 'exec touch /tmp/evil' >> .git/rebase-merge/git-rebase-todo").returncode != 0
    assert todo.read_text() == before


@macos_only
def test_a_rebase_inside_the_sandbox_is_a_named_block(repo: Path) -> None:
    identity = "-c user.name=a -c user.email=a@example.invalid -c core.hooksPath=/dev/null"
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "two")
    before = (repo / ".git/refs/heads/main").read_text()
    done = _run(repo, f"git {identity} rebase --force-rebase HEAD~1")
    # Not the exit code: git 2.39 exits 1 here, while 2.53 and later print the error and exit 0.
    assert not (repo / ".git/rebase-merge").exists()
    assert (repo / ".git/refs/heads/main").read_text() == before
    block = classify_block(done.stderr + done.stdout, done.returncode, _policy(repo), [])
    assert block is not None and block.kind == "write", done.stderr
