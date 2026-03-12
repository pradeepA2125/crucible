import asyncio
import httpx
import json
import os
import sys
import time
from datetime import datetime

BASE_URL = os.getenv("AGENTD_BASE_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = 2.0
TIMEOUT_SEC = 600.0  # 10 minutes for complex reasoning models

class E2EStressTest:
    def __init__(self, goal: str, workspace_path: str):
        self.goal = goal
        self.workspace_path = workspace_path
        self.task_id = None
        self.last_event_count = 0

    async def run(self, max_retries: int = 5):
        print(f"\n🚀 Starting E2E Orchestration Simulation")
        print(f"Goal: {self.goal}")
        print(f"Target: {self.workspace_path}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Submission with retry on 429
            print("\n[Submission] POST /v1/tasks")
            for attempt in range(max_retries):
                resp = await client.post(
                    f"{BASE_URL}/v1/tasks",
                    json={"goal": self.goal, "workspace_path": self.workspace_path}
                )
                if resp.status_code == 200:
                    self.task_id = resp.json()["task_id"]
                    print(f"📡 Task ID: {self.task_id}")
                    break
                elif resp.status_code == 429 or "rate-limited" in resp.text:
                    wait = 20 * (attempt + 1)
                    print(f"⚠️ Rate limited (429). Retrying in {wait}s... (Attempt {attempt+1}/{max_retries})")
                    await asyncio.sleep(wait)
                else:
                    print(f"❌ Submission Failed: {resp.text}")
                    return
            else:
                print("❌ Failed to submit task after multiple retries.")
                return

            # 2. State Machine Monitoring
            start_time = time.time()
            while time.time() - start_time < TIMEOUT_SEC:
                resp = await client.get(f"{BASE_URL}/v1/tasks/{self.task_id}/result")
                if resp.status_code != 200:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                task = resp.json()
                status = task["status"]
                events = task.get("events", [])
                usage = task.get("usage", {})

                # Check if the orchestrator itself is reporting a 429 failure in its events
                # This happens if the submission succeeded but the LLM call within the engine failed
                is_failed = status == "FAILED"
                is_429 = any("429" in str(d.get("message", "")) for d in task.get("diagnostics", []))

                if is_failed and is_429:
                    print(f"⚠️ Orchestrator hit 429. Waiting 30s before automatic re-submission...")
                    await asyncio.sleep(30)
                    # We recursively call run to effectively "keep retrying" the whole flow
                    return await self.run(max_retries - 1)

                # Print new events
                if len(events) > self.last_event_count:
                    for i in range(self.last_event_count, len(events)):
                        ev = events[i]
                        prefix = "🔹" if ev["to_status"] not in ["FAILED", "SUCCEEDED", "READY_FOR_REVIEW"] else "🏁"
                        print(f" {prefix} {ev['from_status']} -> {ev['to_status']} | Reason: {ev['reason']}")
                        if ev['to_status'] == "REPAIRING":
                            print(f"    🔄 Iteration: {usage.get('iterations', 0)}")
                    self.last_event_count = len(events)

                # Check terminal states
                if status == "READY_FOR_REVIEW":
                    print("\n✅ READY FOR REVIEW. Validating Patch...")
                    await self.accept_patch(client)
                    return

                if status == "SUCCEEDED":
                    print("\n✨ TASK COMPLETED SUCCESSFULLY")
                    return

                if status in ["FAILED", "ABORTED"]:
                    print(f"\n❌ TASK TERMINATED: {status}")
                    self.dump_diagnostics(task)
                    sys.exit(1)

                await asyncio.sleep(POLL_INTERVAL)

            print(f"\n❌ TIMEOUT after {TIMEOUT_SEC}s")
            sys.exit(1)

    async def accept_patch(self, client):
        print("[Finalization] POST /accept")
        resp = await client.post(f"{BASE_URL}/v1/tasks/{self.task_id}/accept")
        if resp.status_code == 200:
            print("🎉 Success! Workspace updated and promoted.")
        else:
            print(f"❌ Promotion Failed: {resp.text}")
            sys.exit(1)

    def dump_diagnostics(self, task):
        print("\n🔎 Failure Audit:")
        diags = task.get("diagnostics", [])
        if diags:
            for d in diags:
                print(f"   [{d['level'].upper()}] {d['source']}: {d['message']}")
        else:
            print("   No diagnostics found.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 e2e-stress-test.py '<goal>' '<workspace_path>'")
        sys.exit(1)

    test = E2EStressTest(sys.argv[1], sys.argv[2])
    asyncio.run(test.run())
