"""Read-only, bounded resource names for desktop lifecycle decisions."""
from pathlib import Path

from agentd.chat.activity import activity_snapshot


def lifecycle_snapshot(store, workspace, requested, process_id, active_threads, sessions, *, task_mode, legacy_work):
    active = activity_snapshot(store, workspace, requested, process_id, active_threads, sessions,
                               task_mode=task_mode, legacy_work=legacy_work)
    if not active["supported"]:
        return active
    rows, agents, teams = store.lifecycle_metadata()
    if len(rows) > 1000 or len(agents) > 1024 or len(teams) > 1024:
        return {"supported": False, "reason": "metadata_limit"}
    root = Path(workspace).resolve()
    selected = {row["thread_id"]: row for row in rows if Path(row["workspace_path"]).resolve() == root}
    known = {row["thread_id"] for row in rows}
    if any(a["thread_id"] not in known or a["status"] not in ("queued", "running", "waiting") for a in agents):
        return {"supported": False, "reason": "unbound_agent"}
    live_phases = {"DELIBERATING", "FINALIZING", "AWAITING_APPROVAL", "IMPLEMENTING", "REVIEWING", "DEADLOCKED", "PAUSED"}
    if any(t["thread_id"] not in known or t["phase"] not in live_phases | {"DONE", "DISBANDED", "FAILED"} for t in teams):
        return {"supported": False, "reason": "unknown_team"}
    running = sessions.live_summaries(None) if sessions is not None else []
    result = []
    for item in active["threads"]:
        thread_id = item["thread_id"]
        row = selected[thread_id]
        gates = store._gates_from_row(row)
        if len(gates) > 256:
            return {"supported": False, "reason": "gate_limit"}
        gate_ids = [gate.gate_id for gate in gates]
        if any(not identity for identity in gate_ids) or len(set(gate_ids)) != len(gate_ids):
            return {"supported": False, "reason": "unknown_gate_identity"}
        result.append({**item, "title": row["title"], "gates": len(gates), "gate_ids": gate_ids,
                       "agents": [{"id": a["agent_id"], "label": a["label"]} for a in agents if a["thread_id"] == thread_id],
                       "teams": [{"id": t["team_id"], "label": t["name"]} for t in teams if t["thread_id"] == thread_id],
                       "sessions": [{"id": r["id"], "label": str(r.get("command", "Command session"))[:200]}
                                    for r in running if r["status"] == "running" and r["thread_id"] == thread_id]})
    return {**active, "threads": result}
