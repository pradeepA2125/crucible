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

        async with httpx.AsyncClient(timeout=300.0) as client:
            # 1. Submission with retry on 429
            print("\n[Submission] POST /v1/tasks")
            for attempt in range(max_retries):
                resp = await client.post(
                    f"{BASE_URL}/v1/tasks",
                    json={
                        "goal": self.goal, 
                        "workspace_path": self.workspace_path,
                        "budget": {"max_iterations": 15}
                    }
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
                trace = task.get("execution_trace", [])
                progress = task.get("step_progress", {})

                # Check if the orchestrator itself is reporting a 429 failure in its trace
                is_failed = status == "FAILED"
                is_429 = any("429" in str(d.get("message", "")) for d in task.get("diagnostics", []))

                if is_failed and is_429:
                    print(f"⚠️ Orchestrator hit 429. Waiting 30s before automatic re-submission...")
                    await asyncio.sleep(30)
                    # We recursively call run to effectively "keep retrying" the whole flow
                    return await self.run(max_retries - 1)

                # Print new trace events
                if len(trace) > self.last_event_count:
                    for i in range(self.last_event_count, len(trace)):
                        ev = trace[i]
                        step_id = ev.get("step_id", "?")
                        ev_status = ev.get("status", "unknown")
                        attempt = ev.get("attempt", 0)
                        msg = ev.get("message", "")
                        prefix = "🔹"
                        if ev_status in ["validation_failed", "preflight_failed", "step_exhausted"]:
                            prefix = "❌"
                        elif ev_status == "step_completed":
                            prefix = "✅"
                        
                        print(f" {prefix} Step {step_id} (Attempt {attempt}): {ev_status} | {msg}")
                    self.last_event_count = len(trace)

                # Auto-approve plan (stress test skips human review gate)
                if status == "AWAITING_PLAN_APPROVAL":
                    plan_md = task.get("plan_markdown", "")
                    print(f"\n📄 Plan ready ({len(plan_md)} chars). Auto-approving...")
                    approve_resp = await client.post(
                        f"{BASE_URL}/v1/tasks/{self.task_id}/plan/feedback",
                        json={"feedback": None},
                    )
                    if approve_resp.status_code != 200:
                        print(f"❌ Plan approval failed: {approve_resp.text}")
                        sys.exit(1)
                    print("✅ Plan approved — execution starting.")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

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
