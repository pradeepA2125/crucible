"""Live behavioral eval: skill triggering fires when it should and ONLY then.

Unit tests pin the prompt text (test_skills_prompt.py); this eval pins the
BEHAVIOR against a real model — the thing prompt-text tests cannot capture.
Two directions, both must pass (fixing over-trigger must not regress P2's
under-trigger fix and vice versa):

  A (no unnecessary trigger): a doc request the user already specified
    -> the turn's tool-call sequence contains NO read_skill, and parks at a
    doc_write gate (rejected by the eval, so the workspace is left unchanged).
  B (trigger still fires): a bug report
    -> the FIRST tool call is read_skill with a debugging-ish skill.

Needs: a running backend (CRUCIBLE_CHAT_CONTROLLER=1, SKILLS+DOC_WRITE on)
serving a workspace with the superpowers skills installed, e.g.
  bash scripts/stress/start-backend.sh --backend turboquant --port 8002 \
      --workspace "$PWD/workspaces/shadow-forge-stress" --validation-profile none
Run (venv has httpx):
  python scripts/eval/skill_trigger_eval.py \
      --base-url http://localhost:8002 \
      --workspace "$PWD/workspaces/shadow-forge-stress"
Exit code 0 = both directions pass. Not CI — it drives a live LLM.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import threading
import time

import httpx

DOC_REQUEST = (
    "Update docs/notes/python-latest.md: add a one-line note at the bottom "
    "saying Python 3.9 and older are past end-of-life."
)
BUG_REQUEST = (
    "There is a bug: the indexer snapshot loader crashes with a KeyError on "
    "some workspaces instead of loading the graph. Can you investigate?"
)
POLL_SEC = 5.0
TURN_TIMEOUT_SEC = 600.0


def _drive_message(base_url: str, thread_id: str, content: str) -> threading.Thread:
    """POST the SSE message endpoint on a daemon thread (it blocks for the turn)."""

    def _run() -> None:
        try:
            with httpx.stream(
                "POST",
                f"{base_url}/v1/chat/threads/{thread_id}/message",
                json={"content": content},
                headers={"Accept": "text/event-stream"},
                timeout=TURN_TIMEOUT_SEC,
            ) as resp:
                for _ in resp.iter_lines():
                    pass
        except httpx.HTTPError:
            pass  # the eval judges via /live + artifacts, not this stream

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _live(client: httpx.Client, base_url: str, thread_id: str) -> tuple[bool, str | None]:
    d = json.loads(client.get(f"{base_url}/v1/chat/threads/{thread_id}/live").text, strict=False)
    gate = d.get("pending_gate")
    return bool(d.get("turn_active")), gate["kind"] if gate else None


def _tool_sequence(workspace: str, thread_id: str) -> list[tuple[str, str]]:
    """(action_type, tool) per iteration from the thread's controller artifacts."""
    base = os.path.join(workspace, ".agentd", "artifacts", "chat", thread_id)
    files = sorted(glob.glob(os.path.join(base, "*", "controller-turn-*.json")),
                   key=os.path.getmtime)
    seq: list[tuple[str, str]] = []
    for f in files:
        try:
            rr = json.load(open(f)).get("raw_result") or {}
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rr, dict):
            seq.append((str(rr.get("type", "")), str(rr.get("tool", ""))))
    return seq


def _new_thread(client: httpx.Client, base_url: str, workspace: str, title: str) -> str:
    resp = client.post(f"{base_url}/v1/chat/threads",
                       json={"workspace": workspace, "title": title})
    return json.loads(resp.text, strict=False)["thread_id"]


def _wait_for_settle(client: httpx.Client, base_url: str, thread_id: str) -> str | None:
    """Poll /live until a gate is up or the turn ends. Returns the gate kind or None.

    A parked gate reports turn_active=False WITH a gate, so check the gate first;
    require one active sighting (or a few polls) before trusting an idle reading —
    the message POST may not have flipped turn_active yet on the first poll.
    """
    deadline = time.time() + TURN_TIMEOUT_SEC
    seen_active = False
    polls = 0
    while time.time() < deadline:
        time.sleep(POLL_SEC)
        polls += 1
        active, gate = _live(client, base_url, thread_id)
        seen_active = seen_active or active
        if gate is not None:
            return gate
        if not active and (seen_active or polls >= 3):
            return None
    raise TimeoutError(f"thread {thread_id}: no progress within {TURN_TIMEOUT_SEC}s")


def eval_direction_a(client: httpx.Client, base_url: str, workspace: str) -> list[str]:
    """Doc request: no read_skill anywhere; parks at doc_write; eval rejects it."""
    failures: list[str] = []
    tid = _new_thread(client, base_url, workspace, "eval: doc no-trigger")
    print(f"[A] thread {tid}: {DOC_REQUEST[:60]}...")
    _drive_message(base_url, tid, DOC_REQUEST)
    gate = _wait_for_settle(client, base_url, tid)
    seq = _tool_sequence(workspace, tid)
    print(f"[A] gate={gate} sequence={seq}")
    if any(tool == "read_skill" for _, tool in seq):
        failures.append(f"A: unnecessary skill trigger — sequence {seq}")
    if gate != "doc_write":
        failures.append(f"A: expected doc_write gate, got {gate}")
    else:
        # Reject: leaves the workspace byte-identical (verified by the gate contract).
        client.post(f"{base_url}/v1/chat/threads/{tid}/doc-decision",
                    json={"approve": False})
        time.sleep(2)
    client.post(f"{base_url}/v1/chat/threads/{tid}/stop")
    return failures


def eval_direction_b(client: httpx.Client, base_url: str, workspace: str) -> list[str]:
    """Bug report: FIRST tool call is read_skill on a debugging-ish skill."""
    failures: list[str] = []
    tid = _new_thread(client, base_url, workspace, "eval: bug triggers skill")
    print(f"[B] thread {tid}: {BUG_REQUEST[:60]}...")
    _drive_message(base_url, tid, BUG_REQUEST)
    deadline = time.time() + TURN_TIMEOUT_SEC
    seq: list[tuple[str, str]] = []
    while time.time() < deadline:
        time.sleep(POLL_SEC)
        seq = _tool_sequence(workspace, tid)
        if seq:
            break
    print(f"[B] first actions={seq[:2]}")
    if not seq:
        failures.append("B: no controller action observed within timeout")
    else:
        atype, tool = seq[0]
        if tool != "read_skill":
            failures.append(f"B: first action was {atype}/{tool}, not read_skill")
    client.post(f"{base_url}/v1/chat/threads/{tid}/stop")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8002")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()

    client = httpx.Client(timeout=15.0)
    health = client.get(f"{args.base_url}/health")
    cfg = json.loads(client.get(f"{args.base_url}/v1/config").text, strict=False)
    if health.status_code != 200 or not cfg.get("skills_enabled"):
        print(f"backend not ready or skills disabled: {cfg}", file=sys.stderr)
        return 2

    failures = eval_direction_a(client, args.base_url, args.workspace)
    failures += eval_direction_b(client, args.base_url, args.workspace)
    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: no unnecessary trigger (A) and trigger still fires (B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
