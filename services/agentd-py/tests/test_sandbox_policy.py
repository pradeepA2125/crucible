"""The sandbox policy: what a command may write, may not read, and its environment."""

from __future__ import annotations

from pathlib import Path

from agentd.sandbox.env import sandbox_env
from agentd.sandbox.policy import build_policy

CACHES = (".npm", ".cache", "Library/Caches", ".cargo/registry", ".cargo/git", "go/pkg/mod",
          ".gradle/caches", ".m2/repository", ".pnpm-store", ".bun/install/cache",
          ".local/share/uv")


def _policy(tmp_path: Path, shadow: Path | None = None, **kw: object):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return build_policy(workspace=ws, home=home, shadow_root=shadow,
                        extra_writable=kw.get("extra", ()), proxy_port=9),  ws, home


def test_policy_writable_and_never_bin(tmp_path):
    (policy, ws, home) = _policy(tmp_path)
    writable = set(policy.writable)
    assert ws.resolve() in writable
    for cache in CACHES:
        assert (home / cache).resolve() in writable
    assert Path("/tmp").resolve() in writable
    for path in writable:
        assert not str(path).endswith((".cargo/bin", ".local/bin", "go/bin"))


def test_extra_writable_folder_included(tmp_path):
    extra = tmp_path / "builds"
    extra.mkdir()
    (policy, _ws, _home) = _policy(tmp_path, extra=(extra,))
    assert extra.resolve() in policy.writable


def test_policy_read_only_includes_always_ask_files(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.code-workspace").write_text("{}")
    (policy, ws, _home) = _policy(tmp_path)
    read_only = set(policy.read_only)
    for rel in ("AGENTS.md", ".crucible/approved-hosts.json", ".crucible/approved-commands.json",
                ".crucible/sandbox.json", ".crucible/mcp.json", ".crucible/agents",
                ".crucible/state", ".git/hooks", ".git/config", ".vscode/tasks.json",
                ".zshrc", ".bashrc", ".gitconfig", "app.code-workspace"):
        assert ws.resolve() / rel in read_only, rel


def test_shadow_is_writable_not_read_only(tmp_path):
    shadow = tmp_path / "ws" / ".crucible" / "state" / "shadows" / "task-1"
    shadow.mkdir(parents=True)
    (policy, _ws, _home) = _policy(tmp_path, shadow=shadow)
    assert shadow.resolve() in policy.writable
    assert shadow.resolve() not in policy.read_only


def test_unreadable_credentials(tmp_path):
    (policy, _ws, home) = _policy(tmp_path)
    unreadable = set(policy.unreadable)
    for rel in (".ssh", ".aws", ".config/gh", ".crucible/run", ".crucible/auth",
                ".crucible/trust.json", ".claude", "Library/Keychains"):
        assert (home / rel).resolve() in unreadable, rel
    assert (home / ".crucible/runtime").resolve() not in unreadable
    assert (home / ".crucible").resolve() not in unreadable


def test_paths_are_canonical(tmp_path):
    real = tmp_path / "real-ws"
    real.mkdir()
    link = tmp_path / "ws"
    link.symlink_to(real)
    home = tmp_path / "home"
    home.mkdir()
    policy = build_policy(workspace=link, home=home, shadow_root=None, extra_writable=(),
                          proxy_port=None)
    assert policy.workspace == real.resolve()
    assert real.resolve() in policy.writable
    assert link not in policy.writable


def test_sandbox_env_strips_secrets():
    env = sandbox_env({"GITHUB_TOKEN": "x", "OPENAI_API_KEY": "y", "AWS_REGION": "z",
                       "GOOGLE_APPLICATION_CREDENTIALS": "/k.json", "DB_PASSWORD": "p",
                       "SSH_AUTH_SOCK": "/s", "PATH": "/bin", "HOME": "/h"},
                      "http://t:x@127.0.0.1:9")
    assert set(env) >= {"PATH", "HOME", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                        "http_proxy", "ALL_PROXY", "all_proxy"}
    assert not {"GITHUB_TOKEN", "OPENAI_API_KEY", "AWS_REGION", "GOOGLE_APPLICATION_CREDENTIALS",
                "DB_PASSWORD", "SSH_AUTH_SOCK"} & set(env)
    assert env["HTTPS_PROXY"] == "http://t:x@127.0.0.1:9"


def test_sandbox_env_keeps_loopback_direct():
    env = sandbox_env({"PATH": "/bin"}, "http://t:x@127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        assert {"localhost", "127.0.0.1", "::1"} <= set(env[name].split(","))


def test_sandbox_env_drops_inherited_proxy_without_one():
    env = sandbox_env({"PATH": "/bin", "HTTPS_PROXY": "http://corp:8080"}, None)
    assert "HTTPS_PROXY" not in env
