"""McpRuleStore: the mcp_tool gate's "Approve & remember (this workspace)" store,
keyed on (server, tool) — the MCP analog of CommandRuleStore."""
from pathlib import Path

from agentd.mcp.rules import McpRuleStore


def test_empty_store_matches_nothing(tmp_path: Path):
    assert McpRuleStore(str(tmp_path)).matches("gh", "create_issue") is False


def test_add_then_match_exact_pair_only(tmp_path: Path):
    store = McpRuleStore(str(tmp_path))
    store.add("gh", "create_issue")
    fresh = McpRuleStore(str(tmp_path))  # persisted, not just in-memory
    assert fresh.matches("gh", "create_issue") is True
    assert fresh.matches("gh", "delete_repo") is False
    assert fresh.matches("other", "create_issue") is False


def test_add_is_idempotent(tmp_path: Path):
    store = McpRuleStore(str(tmp_path))
    store.add("gh", "t")
    store.add("gh", "t")
    assert len(store.load()) == 1


def test_corrupt_file_degrades_to_empty(tmp_path: Path):
    p = tmp_path / ".ai-editor" / "approved-mcp-tools.json"
    p.parent.mkdir(parents=True)
    p.write_text("{nope", encoding="utf-8")
    assert McpRuleStore(str(tmp_path)).matches("a", "b") is False
