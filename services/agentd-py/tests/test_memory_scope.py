from pathlib import Path

import pytest

from agentd.memory.scope import (
    ScopeContext,
    ScopeKind,
    discover_repo_identity,
    resolve_repo_identity,
)


@pytest.mark.parametrize(
    "origin",
    [
        "git@GitHub.com:Owner/Repo.git",
        "https://github.com/Owner/Repo.git/",
        "ssh://git@github.com:22/Owner/Repo",
        "https://token:secret@github.com:443/Owner/Repo?access_token=secret#fragment",
    ],
)
def test_equivalent_origins_and_moved_checkouts(origin):
    assert resolve_repo_identity(origin, (), Path("/one")) == "git:github.com/Owner/Repo"
    assert resolve_repo_identity(origin, (), Path("/two")) == "git:github.com/Owner/Repo"


def test_forks_ports_and_path_case_remain_distinct():
    assert resolve_repo_identity("git@x:Owner/Repo.git", (), Path("/x")) == "git:x/Owner/Repo"
    base = resolve_repo_identity("git@github.com:Owner/Repo.git", (), Path("/x"))
    assert base != resolve_repo_identity("git@github.com:Fork/Repo.git", (), Path("/x"))
    assert base != resolve_repo_identity("ssh://github.com:2222/Owner/Repo", (), Path("/x"))
    assert base != resolve_repo_identity("https://github.com/owner/repo", (), Path("/x"))


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "not a URL",
        "/local/clone",
        "file:///clone",
        "https://github.com:invalid/owner/repo",
        "C:/clone",
    ],
)
def test_invalid_origin_uses_stable_sorted_roots(origin):
    assert resolve_repo_identity(origin, ("b" * 40, "a" * 40), Path("/x")) == (
        "root:" + "a" * 40 + "," + "b" * 40
    )


def test_non_git_path_fallback_and_thread_isolation(tmp_path):
    identity = discover_repo_identity(tmp_path)
    assert identity == f"path:{tmp_path}"
    one, two = ScopeContext(identity, "one"), ScopeContext(identity, "two")
    assert one.permits(ScopeKind.USER, "user")
    assert one.permits(ScopeKind.REPO, identity)
    assert one.permits(ScopeKind.THREAD, "one")
    assert not one.permits(ScopeKind.THREAD, "two")
    assert one.keys[:2] == two.keys[:2]
