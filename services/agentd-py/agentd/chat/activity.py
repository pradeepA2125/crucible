"""Read-only reactive activity authority; not an atomic workspace execution lock."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Protocol

from agentd.chat.storage import ChatThreadStore


class SessionReader(Protocol):
    def live_summaries(self, thread_id: str | None) -> list[dict[str, object]]: ...


def activity_snapshot(
    store: ChatThreadStore,
    workspace: str,
    requested: str,
    process_id: str,
    active_threads: Collection[str],
    sessions: SessionReader | None,
    *,
    task_mode: bool,
    legacy_work: bool,
) -> dict[str, object]:
    """Capture synchronously; no await between registry and stored activity reads.

    Unsupported evidence never becomes idle. Metadata is capped independently of
    transcripts; canonical aliases are included rather than silently missed.
    """

    def unknown(reason: str) -> dict[str, object]:
        return {"supported": False, "reason": reason}

    if task_mode or legacy_work:
        return unknown("legacy_task_activity")
    if not workspace or not requested:
        return unknown("unbound_project")
    root = Path(workspace).resolve()
    if Path(requested).resolve() != root:
        return unknown("different_project")
    rows = store.activity_threads()
    if rows is None:
        return unknown("thread_limit")
    if any(
        not isinstance(row["workspace_path"], str)
        or not row["workspace_path"]
        or not isinstance(row["thread_id"], str)
        or not row["thread_id"]
        for row in rows
    ):
        return unknown("unknown_project_metadata")
    selected = [row for row in rows if Path(str(row["workspace_path"])).resolve() == root]
    identities = {str(row["thread_id"]) for row in selected}
    if not set(active_threads).issubset(identities):
        return unknown("unbound_activity")
    running_sessions: dict[str, int] = {}
    if sessions is not None:
        summaries = sessions.live_summaries(None)
        if len(summaries) > 1024:
            return unknown("session_limit")
        for session in summaries:
            if session.get("status") not in ("running", "exited"):
                return unknown("unknown_session")
            if session["status"] == "running":
                thread_id = session.get("thread_id")
                if not isinstance(thread_id, str) or thread_id not in identities:
                    return unknown("unbound_session")
                running_sessions[thread_id] = running_sessions.get(thread_id, 0) + 1
    threads: list[dict[str, object]] = []
    for row in selected:
        if row["agents_unknown"]:
            return unknown("unknown_agent")
        thread_id = str(row["thread_id"])
        threads.append(
            {
                "thread_id": thread_id,
                "turn_active": thread_id in active_threads,
                "agents_running": row["agents_running"],
                "sessions_running": running_sessions.get(thread_id, 0),
            }
        )
    if not process_id:
        return unknown("unknown_process")
    return {
        "supported": True,
        "process_id": process_id,
        "workspace_path": str(root),
        "threads": threads,
    }
