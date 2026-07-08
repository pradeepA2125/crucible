import asyncio
import httpx
import json
import os
import sys
import time

BASE_URL = os.getenv("AGENTD_BASE_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = 3.0
TIMEOUT_SEC = 600.0
STATE_DIR = os.getenv("CRUCIBLE_VERIFY_STATE_DIR", os.path.join("/tmp", "ai-editor-verify-state"))
TASK_ID_PATH = os.path.join(STATE_DIR, "current_task_id.txt")

async def main():
    if not os.path.exists(TASK_ID_PATH):
        print("❌ Task ID file not found. Run stage 1 first.")
        sys.exit(1)

    with open(TASK_ID_PATH, "r", encoding="utf-8") as f:
        task_id = f.read().strip()

    async with httpx.AsyncClient(timeout=60.0) as client:
        print(f"\n✅ Approving Plan for Task: {task_id}")
        
        resp = await client.post(
            f"{BASE_URL}/v1/tasks/{task_id}/plan/feedback",
            json={"feedback": None}
        )
        if resp.status_code != 200:
            print(f"❌ Failed: {resp.text}")
            return

        print("\n⏳ Waiting for Execution (READY_FOR_REVIEW)...")
        start_time = time.time()
        last_status = None
        while time.time() - start_time < TIMEOUT_SEC:
            resp = await client.get(f"{BASE_URL}/v1/tasks/{task_id}")
            task = resp.json()
            status = task["status"]
            
            if status != last_status:
                print(f"🔔 Status Transition: {last_status} -> {status}")
                last_status = status

            if status == "READY_FOR_REVIEW":
                print("\n✅ Stage 3 checkpoint met: task reached READY_FOR_REVIEW")
                await accept_changes(client, task_id)
                return

            if status in ["FAILED", "ABORTED"]:
                print(f"❌ Task terminated: {status}")
                # Print diagnostics
                print(json.dumps(task.get("diagnostics", []), indent=2))
                sys.exit(1)

            await asyncio.sleep(POLL_INTERVAL)

        print("❌ Timeout waiting for execution.")
        sys.exit(1)

async def accept_changes(client, task_id):
    print(f"\n[Finalization] POST /tasks/{task_id}/accept")
    resp = await client.post(f"{BASE_URL}/v1/tasks/{task_id}/accept")
    if resp.status_code == 200:
        task_resp = await client.get(f"{BASE_URL}/v1/tasks/{task_id}")
        task_resp.raise_for_status()
        status = task_resp.json().get("status")
        if status != "SUCCEEDED":
            print(f"❌ Stage 3 failed: expected SUCCEEDED after accept, got {status}")
            sys.exit(1)
        print("🎉 Stage 3 complete: accept promoted changes and task is SUCCEEDED.")
    else:
        print(f"❌ Promotion Failed: {resp.text}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
