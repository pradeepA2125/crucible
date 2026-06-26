"""Live smoke: does the clarify gate appear end-to-end through the REAL assembled app?

Drives the real FastAPI routes + ChatController + /live (not unit harnesses) with a
scripted engine that emits a clarify-with-options, then:
  1. POST /message      → controller emits clarify
  2. GET  /live         → assert pending_gate.kind == "clarify" + options present
  3. POST /clarify-decision {answer} → assert it streams, clears the gate, breadcrumbs Q→A

Run: cd services/agentd-py && source .venv/bin/activate && python ../../scripts/smoke_clarify_gate.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore

QUESTION = "Which pricing module?"
OPTIONS = ["src/pricing.py", "billing/pricing.py"]
ANSWER = "src/pricing.py"

_OK = "\033[32mPASS\033[0m"
_NO = "\033[31mFAIL\033[0m"


def _check(label: str, cond: bool) -> bool:
    print(f"  [{_OK if cond else _NO}] {label}")
    return cond


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="clarify-smoke-"))
    store = InMemoryTaskStore()
    chat_store = ChatThreadStore(tmp / "chat.sqlite3")
    handler = ChatController(
        workspace_path=str(tmp),
        reasoning_engine=ScriptedReasoningEngine(None, [], controller_step_responses=[
            {"type": "clarify", "thought": "ambiguous", "question": QUESTION, "options": OPTIONS},
            {"type": "answer", "thought": "resolved", "answer": f"Using {ANSWER}."},
        ]),
        thread_store=chat_store, orchestrator=None,
        broadcaster=EventBroadcaster(), retrieval_client=None)

    class _Orch:
        broadcaster = handler._broadcaster
        _running_tasks: set = set()

    app = FastAPI()
    app.include_router(build_router(store, _Orch(), None, None, handler))

    thread = chat_store.create_thread(str(tmp), title="smoke")
    tid = thread.thread_id
    transport = ASGITransport(app=app)
    all_ok = True

    async with AsyncClient(transport=transport, base_url="http://t") as client:
        # 1. Send a message → controller emits clarify (consume the stream to completion).
        print("\n1. POST /message (triggers clarify)")
        async with client.stream(
            "POST", f"/v1/chat/threads/{tid}/message", json={"content": "fix pricing"}) as r:
            async for _ in r.aiter_lines():
                pass

        # 2. GET /live → the clarify gate must be present with its options.
        print("2. GET /live (the card's data source)")
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
        gate = live.get("pending_gate")
        all_ok &= _check("pending_gate present", gate is not None)
        all_ok &= _check(f"gate.kind == 'clarify' (got {gate and gate.get('kind')!r})",
                         bool(gate) and gate.get("kind") == "clarify")
        payload = (gate or {}).get("payload", {})
        all_ok &= _check(f"payload.question == {QUESTION!r}", payload.get("question") == QUESTION)
        all_ok &= _check(f"payload.options == {OPTIONS}", payload.get("options") == OPTIONS)
        print(f"     → /live pending_gate = {json.dumps(gate)}")

        # 3. Resolve via the card's route (pick an option) → stream + gate clears + breadcrumb.
        print("3. POST /clarify-decision {answer} (user picks an option)")
        saw_done = False
        async with client.stream(
            "POST", f"/v1/chat/threads/{tid}/clarify-decision", json={"answer": ANSWER}) as r:
            async for line in r.aiter_lines():
                if line.startswith("data:") and '"chat_done"' in line:
                    saw_done = True
        all_ok &= _check("clarify-decision stream emitted chat_done", saw_done)

        live2 = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
        all_ok &= _check("gate cleared after decision", live2.get("pending_gate") is None)

        msgs = chat_store.get_thread(tid).messages
        crumb = next((m for m in msgs if m.metadata.get("breadcrumb")), None)
        crumb_ok = crumb is not None and QUESTION in crumb.content and ANSWER in crumb.content
        all_ok &= _check("combined Q→A breadcrumb persisted", crumb_ok)
        if crumb:
            print(f"     → breadcrumb = {crumb.content!r}")

    print(f"\n{'='*48}\n{_OK if all_ok else _NO}: clarify gate {'appears + resolves' if all_ok else 'smoke FAILED'}\n{'='*48}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
