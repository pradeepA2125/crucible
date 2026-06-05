"""Weak models emit alias param names; the normalizer bridges them to canonical keys.

Regression focus: `search_code`'s path param is `path_filter`, but models reuse
`path` (as they correctly do for read_file/list_directory). Without the bridge the
filter is silently dropped and ripgrep searches the whole workspace.
"""
from __future__ import annotations

from agentd.tools.arg_aliases import normalize_tool_args


def test_search_code_path_alias_maps_to_path_filter() -> None:
    out = normalize_tool_args(
        "search_code", {"pattern": "build_router", "path": "agentd/api/routes.py"}
    )
    assert out["path_filter"] == "agentd/api/routes.py"


def test_search_code_explicit_path_filter_is_not_overwritten() -> None:
    out = normalize_tool_args(
        "search_code",
        {"pattern": "x", "path": "agentd/api/routes.py", "path_filter": "*.py"},
    )
    assert out["path_filter"] == "*.py"


def test_search_code_pattern_aliases_still_work() -> None:
    out = normalize_tool_args("search_code", {"regex": "foo", "path": "a/b.py"})
    assert out["pattern"] == "foo"
    assert out["path_filter"] == "a/b.py"


def test_read_file_path_is_canonical_not_remapped_to_path_filter() -> None:
    # `path` is the canonical key for read_file — the search_code alias must not leak.
    out = normalize_tool_args("read_file", {"path": "a/b.py"})
    assert out["path"] == "a/b.py"
    assert "path_filter" not in out
