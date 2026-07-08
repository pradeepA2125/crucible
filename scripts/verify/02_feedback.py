import asyncio
import httpx
import json
import os
import sys
import time

BASE_URL = os.getenv("AGENTD_BASE_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = 2.0
TIMEOUT_SEC = float(os.getenv("CRUCIBLE_VERIFY_FEEDBACK_TIMEOUT_SEC", "600"))
STATE_DIR = os.getenv("CRUCIBLE_VERIFY_STATE_DIR", os.path.join("/tmp", "ai-editor-verify-state"))
TASK_ID_PATH = os.path.join(STATE_DIR, "current_task_id.txt")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 02_feedback.py '<feedback_text>'")
        sys.exit(1)

    feedback = sys.argv[1]

    if not os.path.exists(TASK_ID_PATH):
        print("❌ Task ID file not found. Run stage 1 first.")
        sys.exit(1)

    with open(TASK_ID_PATH, "r", encoding="utf-8") as f:
        task_id = f.read().strip()

    async with httpx.AsyncClient(timeout=60.0) as client:
        print(f"\n📡 Providing Feedback for Task: {task_id}")
        print(f"💬 Feedback: {feedback}")
        
        resp = await client.post(
            f"{BASE_URL}/v1/tasks/{task_id}/plan/feedback",
            json={"feedback": feedback}
        )
        if resp.status_code != 200:
            print(f"❌ Failed: {resp.text}")
            return

        print("\n⏳ Waiting for Revised Markdown Plan...")
        start_time = time.time()
        while time.time() - start_time < TIMEOUT_SEC:
            resp = await client.get(f"{BASE_URL}/v1/tasks/{task_id}")
            task = resp.json()
            status = task["status"]
            print(f"   Current Status: {status}")

            if status == "AWAITING_PLAN_APPROVAL":
                plan_markdown = task.get("plan_markdown", "")
                if not plan_markdown:
                    print("❌ Stage 2 failed: status reached AWAITING_PLAN_APPROVAL without revised plan_markdown")
                    sys.exit(1)
                print("\n📄 REVISED MARKDOWN PLAN RECEIVED:")
                print("-" * 40)
                print(plan_markdown)
                print("-" * 40)
                print("\n✅ Stage 2 checkpoint met: revised non-empty plan_markdown received")
                print("✅ Stage 2 Complete. You can run this again or proceed to stage 3.")
                return

            if status in ["FAILED", "ABORTED"]:
                print(f"❌ Task terminated prematurely: {status}")
                sys.exit(1)

            await asyncio.sleep(POLL_INTERVAL)

        print("❌ Timeout waiting for revised plan.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
