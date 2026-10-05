"""Every GET route is reviewed read-only (spec §4). A cross-site <img>/<script> GET
carries no Origin and is stopped only by the token, so a GET must never write.
A new GET route fails this test until its handler is reviewed and added here."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "agentd"

REVIEWED_READ_ONLY_GET_ROUTES = frozenset({
    ("api/routes.py", "/channels/{channel_id}/stream"),  # creates an empty replay entry
    ("api/routes.py", "/chat/attention"),
    ("api/routes.py", "/chat/threads"),
    ("api/routes.py", "/chat/threads/{thread_id}"),
    ("api/routes.py", "/chat/threads/{thread_id}/agents"),
    ("api/routes.py", "/chat/threads/{thread_id}/agents/{agent_id}"),
    ("api/routes.py", "/chat/threads/{thread_id}/live"),
    ("api/routes.py", "/chat/threads/{thread_id}/rewind-preview"),
    ("api/routes.py", "/chat/threads/{thread_id}/sessions/{session_id}/transcript"),
    ("api/routes.py", "/chat/threads/{thread_id}/teams"),
    ("api/routes.py", "/chat/threads/{thread_id}/teams/{team_id}"),
    ("api/routes.py", "/config"),
    ("api/routes.py", "/index/status"),
    ("api/routes.py", "/mcp/servers"),
    ("api/routes.py", "/memory"),
    ("api/routes.py", "/memory/inspect"),
    ("api/routes.py", "/memory/{memory_id}/chain"),
    ("api/routes.py", "/skills"),
    ("api/routes.py", "/tasks/{task_id}"),
    ("api/routes.py", "/tasks/{task_id}/artifacts"),
    ("api/routes.py", "/tasks/{task_id}/events"),
    ("api/routes.py", "/tasks/{task_id}/result"),
    ("api/routes.py", "/tasks/{task_id}/stream-patch"),
    ("api/routes.py", "/workspaces/env-profile"),
    ("api/agents_routes.py", "/agents"),  # reads the catalog; trust/writes are POST/PUT/DELETE
    ("main.py", "/health"),
})


def _get_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr in {"get", "api_route"} and deco.args
                        and isinstance(deco.args[0], ast.Constant)):
                    found.add((path.relative_to(ROOT).as_posix(), deco.args[0].value))
    return found


def test_every_get_route_is_reviewed() -> None:
    unreviewed = _get_routes() - REVIEWED_READ_ONLY_GET_ROUTES
    assert unreviewed == set(), f"review these GET routes for side effects: {unreviewed}"


def test_the_allowlist_has_no_stale_entries() -> None:
    assert REVIEWED_READ_ONLY_GET_ROUTES - _get_routes() == set()
