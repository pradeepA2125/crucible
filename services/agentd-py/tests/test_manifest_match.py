"""Tests for resolve_manifest_scope_key — pure resolver."""
from datetime import datetime, timezone

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.manifest_match import resolve_manifest_scope_key


def _profile(*entries: EnvEcosystemEntry) -> EnvProfile:
    return EnvProfile(
        workspace_root="/tmp/ws",
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=list(entries),
        conventions_notes=None,
        diagnostics=[],
    )


def _py_at_subdir(subdir: str) -> EnvEcosystemEntry:
    manifest = f"{subdir}/pyproject.toml" if subdir else "pyproject.toml"
    return EnvEcosystemEntry(
        ecosystem="python", subdir=subdir, manifest_path=manifest,
        package_manager="uv", install_command="uv sync",
        interpreter_or_runner=".venv/bin/python", test_command="pytest",
        declared_dependencies_top=[], notes=None,
    )


def test_returns_scope_key_when_touched_file_matches_manifest():
    profile = _profile(_py_at_subdir("services/agentd-py"))
    result = resolve_manifest_scope_key(
        ["services/agentd-py/pyproject.toml", "services/agentd-py/agentd/x.py"],
        profile,
    )
    assert result == "python:services/agentd-py"


def test_returns_none_when_no_touched_file_is_a_manifest():
    profile = _profile(_py_at_subdir(""))
    result = resolve_manifest_scope_key(
        ["agentd/x.py", "tests/test_x.py"], profile,
    )
    assert result is None


def test_returns_none_when_manifest_outside_profile_scopes():
    # touched a node manifest at a path the profile doesn't know about
    profile = _profile(_py_at_subdir(""))
    result = resolve_manifest_scope_key(
        ["apps/foo/package.json"], profile,
    )
    assert result is None


def test_picks_first_match_when_multiple_manifests_touched():
    """If a patch writes two manifests in different scopes, the first match
    wins. This is conservative — only one install runs before the next
    run_command; if a later run_command depends on the other scope, that
    scope's install fires on its next patch."""
    profile = _profile(
        _py_at_subdir("services/agentd-py"),
        EnvEcosystemEntry(
            ecosystem="node", subdir="apps/editor-client",
            manifest_path="apps/editor-client/package.json",
            package_manager="npm", install_command="npm ci",
            interpreter_or_runner=None, test_command="vitest run",
            declared_dependencies_top=[], notes=None,
        ),
    )
    result = resolve_manifest_scope_key(
        [
            "services/agentd-py/pyproject.toml",
            "apps/editor-client/package.json",
        ],
        profile,
    )
    # Order matches the touched_files order — python first.
    assert result == "python:services/agentd-py"


def test_normalizes_relative_paths_with_leading_dot_slash():
    profile = _profile(_py_at_subdir(""))
    result = resolve_manifest_scope_key(
        ["./pyproject.toml"], profile,
    )
    assert result == "python:"


def test_returns_none_when_profile_has_no_ecosystems():
    profile = _profile()  # bare
    result = resolve_manifest_scope_key(["pyproject.toml"], profile)
    assert result is None
