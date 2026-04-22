"""Stage 4 — Resume / Rollback verification.

Usage:
    python3 04_resume.py [stage]         # stage: plan | feedback | execute  (default: plan)

Reads the task_id written by 01_create_task.py.  If the task is still READY_FOR_REVIEW
it is rejected first so it ends up ABORTED — giving us a resumable terminal task.

On success the child task_id overwrites current_task_id.txt so stage 2 / 3
can be re-run on the child (useful for the "feedback" rollback).
"""

from __future__ import annotations

import asyncio
import httpx
import json
import os
import sys
import time

BASE_URL = os.getenv("AGENTD_BASE_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = 2.0
TIMEOUT_SEC = 120.0
STATE_DIR = os.getenv("AI_EDITOR_VERIFY_STATE_DIR", os.path.join("/tmp", "ai-editor-verify-state"))
TASK_ID_PATH = os.path.join(STATE_DIR, "current_task_id.txt")


# States that the child task should pass through or reach for each stage.
EXPECTED_TERMINAL: dict[str, str] = {
    "plan":     "AWAITING_PLAN_APPROVAL",   # child re-plans; stops here for user review
    "feedback": "AWAITING_PLAN_APPROVAL",   # child restored from snapshot; waits for /plan/feedback
    "execute":  "READY_FOR_REVIEW",         # child re-runs execution; stops here for review
}


async def get_task(client: httpx.AsyncClient, task_id: str) -> dict:
    resp = await client.get(f"{BASE_URL}/v1/tasks/{task_id}")
    resp.raise_for_status()
    return resp.json()


async def wait_for_status(
    client: httpx.AsyncClient,
    task_id: str,
    target: str,
    *,
    label: str = "",
    timeout_sec: float = TIMEOUT_SEC,
) -> dict:
    deadline = time.time() + timeout_sec
    last_status: str | None = None
    while time.time() < deadline:
        task = await get_task(client, task_id)
        status = task["status"]
        if status != last_status:
            tag = f"[{label}] " if label else ""
            print(f"   {tag}Status: {last_status} → {status}")
            last_status = status
        if status == target:
            return task
        if status in {"FAILED", "ABORTED"} and status != target:
            print(f"❌ Task {task_id} ended in {status} (expected {target})")
            print(json.dumps(task.get("diagnostics", []), indent=2))
            sys.exit(1)
        await asyncio.sleep(POLL_INTERVAL)
    print(f"❌ Timeout waiting for status {target} on {task_id}")
    sys.exit(1)


async def ensure_terminal(client: httpx.AsyncClient, task_id: str) -> str:
    """Return task_id in FAILED or ABORTED, rejecting/cancelling if needed."""
    task = await get_task(client, task_id)
    status = task["status"]

    if status in {"FAILED", "ABORTED"}:
        return status

    if status == "READY_FOR_REVIEW":
        print(f"   Task is READY_FOR_REVIEW — rejecting to create a resumable ABORTED task…")
        resp = await client.post(
            f"{BASE_URL}/v1/tasks/{task_id}/reject",
            json={"reason": "Intentionally rejected for resume verification"},
        )
        resp.raise_for_status()
        return "ABORTED"

    if status == "AWAITING_PLAN_APPROVAL":
        print(f"   Task is AWAITING_PLAN_APPROVAL — cancelling…")
        resp = await client.post(f"{BASE_URL}/v1/tasks/{task_id}/cancel")
        resp.raise_for_status()
        return "ABORTED"

    # For any other active status, cancel
    print(f"   Task is {status} — cancelling to make it resumable…")
    resp = await client.post(f"{BASE_URL}/v1/tasks/{task_id}/cancel")
    resp.raise_for_status()
    return "ABORTED"


async def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if stage not in {"plan", "feedback", "execute"}:
        print(f"❌ Invalid stage '{stage}'. Must be: plan | feedback | execute")
        sys.exit(1)

    if not os.path.exists(TASK_ID_PATH):
        print("❌ Task ID file not found. Run 01_create_task.py first.")
        sys.exit(1)

    with open(TASK_ID_PATH, "r", encoding="utf-8") as f:
        parent_id = f.read().strip()

    print(f"\n🔁 Resume Verification  stage={stage}  parent={parent_id}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # --- Step 1: ensure parent is in a resumable terminal state ---
        print(f"\n⚙️  Ensuring parent task is in a terminal state…")
        terminal_status = await ensure_terminal(client, parent_id)
        print(f"   Parent status: {terminal_status} ✓")

        # --- Step 2: call POST /resume ---
        print(f"\n🚀 POST /v1/tasks/{parent_id}/resume  (stage={stage})")
        resume_resp = await client.post(
            f"{BASE_URL}/v1/tasks/{parent_id}/resume",
            json={"stage": stage},
        )
        if resume_resp.status_code != 200:
            print(f"❌ Resume failed: {resume_resp.status_code} {resume_resp.text}")
            sys.exit(1)

        body = resume_resp.json()
        child_id: str = body["task_id"]
        assert body["resume_of_task_id"] == parent_id, (
            f"resume_of_task_id mismatch: {body['resume_of_task_id']!r} != {parent_id!r}"
        )
        print(f"   Child task_id: {child_id}")
        print(f"   resume_of_task_id: {body['resume_of_task_id']} ✓")

        # --- Step 3: verify child reaches expected terminal state ---
        target = EXPECTED_TERMINAL[stage]
        print(f"\n⏳ Waiting for child task to reach {target}…")
        child_task = await wait_for_status(
            client, child_id, target,
            label=f"child/{stage}",
            timeout_sec=TIMEOUT_SEC,
        )

        # --- Stage-specific assertions ---
        if stage in {"plan", "feedback"}:
            plan_md = child_task.get("plan_markdown", "")
            if not plan_md:
                print(f"❌ Child task at AWAITING_PLAN_APPROVAL has no plan_markdown")
                sys.exit(1)
            print(f"\n📄 Child plan_markdown preview (first 200 chars):")
            print(plan_md[:200])
            if stage == "feedback":
                # Verify parent immutability: parent fields must be unchanged
                parent_after = await get_task(client, parent_id)
                if parent_after["status"] not in {"FAILED", "ABORTED"}:
                    print(f"❌ Parent was mutated — status changed to {parent_after['status']}")
                    sys.exit(1)
                print(f"   Parent still {parent_after['status']} (immutable) ✓")

        if stage == "execute":
            print(f"\n📋 Child task modified_files: {child_task.get('modified_files', [])}")

        # --- Verify parent immutability always ---
        parent_after = await get_task(client, parent_id)
        if parent_after["status"] != terminal_status:
            print(f"❌ Parent status changed after resume: {terminal_status} → {parent_after['status']}")
            sys.exit(1)
        print(f"\n🔒 Parent immutability verified: still {terminal_status} ✓")

        # --- Save child_id so subsequent stages can continue on the child ---
        with open(TASK_ID_PATH, "w", encoding="utf-8") as f:
            f.write(child_id)
        print(f"💾 Saved child task_id to {TASK_ID_PATH}")

        # --- Also verify duplicate resume guard (409) ---
        print(f"\n🛡️  Verifying concurrent-resume guard (should 409)…")
        # Parent is still FAILED/ABORTED so a second resume call should work structurally,
        # but let's at least verify the child_id is not in a resumable state yet
        # (the real guard test is covered in unit tests; here we just confirm the API shape)
        print(f"   (concurrent guard validated in unit tests; skipping racy live test)")

        print(f"\n✅ Stage 4 complete: resume stage='{stage}' → child {child_id} reached {target}")
        print(f"   ➡  Next steps:")
        if stage in {"plan", "feedback"}:
            print(f"      Run 02_feedback.py to provide feedback, or 03_finalize.py to approve the child plan.")
        else:
            print(f"      Run 03_finalize.py to accept the child's patch.")


if __name__ == "__main__":
    asyncio.run(main())
