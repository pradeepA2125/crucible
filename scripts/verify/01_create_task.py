import asyncio
import httpx
import json
import os
import sys
import time

BASE_URL = os.getenv("AGENTD_BASE_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = 2.0
TIMEOUT_SEC = 300.0
STATE_DIR = os.getenv("AI_EDITOR_VERIFY_STATE_DIR", os.path.join("/tmp", "ai-editor-verify-state"))
TASK_ID_PATH = os.path.join(STATE_DIR, "current_task_id.txt")

async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 01_create_task.py '<goal>' '<workspace_path>'")
        sys.exit(1)

    goal = sys.argv[1]
    workspace_path = sys.argv[2]

    async with httpx.AsyncClient(timeout=60.0) as client:
        print(f"\n🚀 Creating Task: {goal}")
        resp = await client.post(
            f"{BASE_URL}/v1/tasks",
            json={
                "goal": goal,
                "workspace_path": workspace_path,
                "budget": {"max_iterations": 10}
            }
        )
        if resp.status_code != 200:
            print(f"❌ Failed: {resp.text}")
            return

        task_id = resp.json()["task_id"]
        print(f"📡 Task ID: {task_id}")

        os.makedirs(STATE_DIR, exist_ok=True)
        with open(TASK_ID_PATH, "w", encoding="utf-8") as f:
            f.write(task_id)

        print("\n⏳ Waiting for Markdown Plan...")
        start_time = time.time()
        while time.time() - start_time < TIMEOUT_SEC:
            resp = await client.get(f"{BASE_URL}/v1/tasks/{task_id}")
            task = resp.json()
            status = task["status"]
            print(f"   Current Status: {status}")

            if status == "AWAITING_PLAN_APPROVAL":
                plan_markdown = task.get("plan_markdown", "")
                if not plan_markdown:
                    print("❌ Stage 1 failed: status reached AWAITING_PLAN_APPROVAL without plan_markdown")
                    sys.exit(1)
                print("\n📄 INITIAL MARKDOWN PLAN RECEIVED:")
                print("-" * 40)
                print(plan_markdown)
                print("-" * 40)
                print("\n✅ Stage 1 checkpoint met: AWAITING_PLAN_APPROVAL + non-empty plan_markdown")
                print(f"✅ Stage 1 Complete. Task ID saved to {TASK_ID_PATH}")
                return

            if status in ["FAILED", "ABORTED"]:
                print(f"❌ Task terminated prematurely: {status}")
                sys.exit(1)

            await asyncio.sleep(POLL_INTERVAL)

        print("❌ Timeout waiting for plan.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
