"""Failure-mode tests for the `query_graph` tool dispatch path.

These pin the tool's contract from the planner's point of view: any failure
inside the walker MUST land in `ToolOutput` (a regular tool result with
`is_error=True`), not propagate as a Python exception. A raised exception
would crash the planning loop turn and force a delta-replan or task abort.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agentd.planning.registry import PlanningToolRegistry
from agentd.tools.registry import ToolRegistry


def _write_corrupt(snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text("{ not even json", encoding="utf-8")


def test_planning_registry_corrupt_snapshot_returns_error_output(tmp_path: Path) -> None:
    snapshot = tmp_path / ".crucible" / "index-snapshot.json"
    _write_corrupt(snapshot)
    reg = PlanningToolRegistry(real_path=tmp_path)

    out = asyncio.run(reg.execute("query_graph", {"node": "anything"}))

    assert out.is_error is True
    assert "snapshot" in out.output.lower()
    assert "search_code" in out.output  # nudges the model toward fallback tools


def test_planning_registry_missing_snapshot_returns_error_output(tmp_path: Path) -> None:
    reg = PlanningToolRegistry(real_path=tmp_path)

    out = asyncio.run(reg.execute("query_graph", {"node": "anything"}))

    # No snapshot at all → tool registers query_graph absent OR returns error.
    # The current shape: snapshot_path() guard returns None → tool isn't even
    # registered. But if it IS called, we expect a clean error.
    # `execute("query_graph", ...)` when not registered also returns an error.
    assert out.is_error is True


def test_planning_registry_blank_node_returns_error_output(tmp_path: Path) -> None:
    # Need an existing (valid) snapshot so query_graph is registered.
    snapshot = tmp_path / ".crucible" / "index-snapshot.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps({
            "schema_version": 1, "workspace_root": str(tmp_path),
            "generated_at_ms": 0,
            "graph": {"nodes": [], "edges": []},
            "diagnostics": [], "stats": {"node_count": 0, "edge_count": 0, "diagnostic_count": 0},
        }),
        encoding="utf-8",
    )
    reg = PlanningToolRegistry(real_path=tmp_path)

    out = asyncio.run(reg.execute("query_graph", {"node": "   "}))

    assert out.is_error is True
    assert "node" in out.output.lower()


def test_planning_registry_bad_depth_does_not_crash(tmp_path: Path) -> None:
    snapshot = tmp_path / ".crucible" / "index-snapshot.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps({
            "schema_version": 1, "workspace_root": str(tmp_path),
            "generated_at_ms": 0,
            "graph": {"nodes": [], "edges": []},
            "diagnostics": [], "stats": {"node_count": 0, "edge_count": 0, "diagnostic_count": 0},
        }),
        encoding="utf-8",
    )
    reg = PlanningToolRegistry(real_path=tmp_path)

    # Realistic LLM mistake: passing a descriptive string instead of an int.
    out = asyncio.run(reg.execute("query_graph", {
        "node": "src/foo.py", "depth": "medium", "limit": None,
    }))

    # Not necessarily an error — the walker clamps; but it MUST be a ToolOutput.
    assert hasattr(out, "is_error")
    assert hasattr(out, "output")


def test_execution_registry_corrupt_snapshot_returns_error_output(tmp_path: Path) -> None:
    snapshot = tmp_path / ".crucible" / "index-snapshot.json"
    _write_corrupt(snapshot)
    shadow = tmp_path / "shadow"
    shadow.mkdir()

    reg = ToolRegistry(shadow_root=shadow, real_workspace_path=tmp_path)
    out = asyncio.run(reg.execute("query_graph", {"node": "src/foo.py"}))

    assert out.is_error is True
    assert "snapshot" in out.output.lower()


def test_execution_registry_missing_snapshot_returns_error_output(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    shadow.mkdir()

    reg = ToolRegistry(shadow_root=shadow, real_workspace_path=tmp_path)
    out = asyncio.run(reg.execute("query_graph", {"node": "src/foo.py"}))

    assert out.is_error is True
    assert "not available" in out.output.lower() or "indexer" in out.output.lower()
