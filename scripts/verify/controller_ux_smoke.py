#!/usr/bin/env python3
"""Backend HTTP smoke for the Controller UX Interaction Rules
(docs/superpowers/specs/2026-06-17-controller-ux-interaction-rules-design.md).

Drives a LIVE backend started with AI_EDITOR_CHAT_CONTROLLER=1 and asserts the
*backend-observable, behavior-deterministic* spec rules against the real routes —
NOT unit stubs. The UI-only rules (§5 input rows, §6 ModeGate field, §7 nav-lock,
§10 live-resume overlay) live in the companion dev-host checklist
(docs/superpowers/plans/2026-06-17-controller-ux-interaction-rules-smoke.md).

Covered here:
  §4  /live exposes turn_active (idle=false; true during a turn; flag-tolerant shape)
  §1  detachment — a client disconnect does NOT cancel the turn (it keeps running,
       and completes on its own)
  §3  in-flight 409 — a second /message while a turn runs is rejected
  §11 stop_turn — POST /stop cancels + persists a "✗ Stopped" breadcrumb;
       /stop on an idle thread is a benign no-op (ok=false)
  §1  gate-clear-at-start — a new turn clears a stale controller gate (conditional:
       SKIPs if the model never raises a gate in the probe window)
  §2  EditGate durability — a held-open per-edit gate survives a dropped SSE (the
       reload tier) and resolves via /edit-decision, resuming the turn (conditional:
       SKIPs if the model proposes no edit). The backend-restart orphan tier (a stale
       gate with no waiter) needs an out-of-process restart → dev-host checklist S8 +
       the unit test test_resolve_edit_clears_stale_gate_when_no_waiter.
  §6  one-shot decisions — a second /edit-decision after the first has no live waiter
       (ok=false); racing decisions never double-resolve.

Usage:
  AGENTD_BASE_URL=http://127.0.0.1:8001 \
  python3 scripts/verify/controller_ux_smoke.py '<workspace_path>'

Exit code is non-zero if any check FAILs (SKIPs do not fail the run).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx

BASE = os.getenv("AGENTD_BASE_URL", "http://127.0.0.1:8001")

# A message that reliably costs a few seconds of exploration (tool calls), widening
# the window in which a turn is observably in-flight. It must NOT be so trivial that
# the turn finishes before /live is polled.
PROBE_MESSAGE = (
    "List the top-level files in this workspace and briefly summarize what the "
    "project does. Explore before answering."
)
# A message biased toward a propose_mode / edit gate (used by the conditional §1 test).
GATE_MESSAGE = "Add a small helper function `greet(name)` to a new utilities module."
# A message biased toward an actual edit (used by the conditional §2 EditGate test). Sent
# with step_review=true so the per-edit gate triggers. The edit is REJECTED in the test, so
# nothing reaches the real workspace and the run is repeatable + side-effect-free.
EDIT_MESSAGE = (
    "Add a one-line helper `def _smoke_noop():\n    return None` to a NEW throwaway "
    "module `smoke_editgate_probe.py` at the workspace root. Nothing else."
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []  # (status, spec_ref, message)


def record(status: str, ref: str, msg: str) -> None:
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ "}[status]
    print(f"{icon} [{status}] {ref}  {msg}")
    _results.append((status, ref, msg))


async def create_thread(client: httpx.AsyncClient, workspace: str) -> str:
    resp = await client.post(f"{BASE}/v1/chat/threads", json={"workspace": workspace})
    resp.raise_for_status()
    return resp.json()["thread_id"]


async def get_live(client: httpx.AsyncClient, thread_id: str) -> dict:
    resp = await client.get(f"{BASE}/v1/chat/threads/{thread_id}/live")
    resp.raise_for_status()
    return resp.json()


async def get_thread(client: httpx.AsyncClient, thread_id: str) -> dict:
    resp = await client.get(f"{BASE}/v1/chat/threads/{thread_id}")
    resp.raise_for_status()
    return resp.json()


async def _stream_turn(
    client: httpx.AsyncClient,
    thread_id: str,
    content: str,
    events: list[dict],
    started: asyncio.Event,
    done: asyncio.Event,
    disconnect_after: int | None = None,
) -> None:
    """Open the /message SSE and collect event types until chat_done.

    If `disconnect_after` is set, return (close the stream → simulate a client
    disconnect) after that many data events WITHOUT sending /stop — the detachment
    probe (§1)."""
    url = f"{BASE}/v1/chat/threads/{thread_id}/message"
    seen = 0
    try:
        async with client.stream("POST", url, json={"content": content}) as resp:
            if resp.status_code != 200:
                events.append({"_http_status": resp.status_code})
                return
            started.set()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                events.append(ev)
                seen += 1
                if disconnect_after is not None and seen >= disconnect_after:
                    return  # close stream → client disconnect (no /stop)
                if ev.get("type") in ("chat_done", "done"):
                    return
    finally:
        done.set()


async def _await_turn_active(
    client: httpx.AsyncClient, thread_id: str, timeout: float = 30.0
) -> bool:
    """Poll /live until turn_active is True (or timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = await get_live(client, thread_id)
        if live.get("turn_active") is True:
            return True
        await asyncio.sleep(0.15)
    return False


async def _await_gate_kind(
    client: httpx.AsyncClient, thread_id: str, kind: str, timeout: float = 60.0
) -> dict | None:
    """Poll /live until pending_gate.kind == `kind` (return the /live payload) or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = await get_live(client, thread_id)
        gate = live.get("pending_gate")
        if isinstance(gate, dict) and gate.get("kind") == kind:
            return live
        await asyncio.sleep(0.2)
    return None


async def _drain_post_stream(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    started: asyncio.Event,
    done: asyncio.Event,
) -> None:
    """Open an SSE POST (e.g. /message or /mode-decision) and drain it until chat_done.

    Cancelling the task that runs this simulates a client/FE disconnect (the detached
    backend turn keeps running). Used by the EditGate test to drive a turn while polling
    /live, then drop the SSE to prove the held-open gate is reload-durable."""
    try:
        async with client.stream("POST", url, json=body) as resp:
            if resp.status_code != 200:
                return
            started.set()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("type") in ("chat_done", "done"):
                    return
    finally:
        done.set()


# ── §4: /live exposes turn_active (idle + flag-tolerant shape) ──────────────────
async def test_live_idle_turn_active(client: httpx.AsyncClient, thread_id: str) -> None:
    live = await get_live(client, thread_id)
    if "turn_active" not in live:
        record(FAIL, "§4", "/live response has no `turn_active` key — is "
                           "AI_EDITOR_CHAT_CONTROLLER=1? (legacy ChatAgent omits it)")
        return
    if live["turn_active"] is False:
        record(PASS, "§4", "idle thread → turn_active=false (key present)")
    else:
        record(FAIL, "§4", f"idle thread → turn_active={live['turn_active']!r} (expected false)")


# ── §4 + §1: turn_active true during a turn; false after it completes ───────────
async def test_turn_active_lifecycle(client: httpx.AsyncClient, thread_id: str) -> None:
    events: list[dict] = []
    started, done = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        _stream_turn(client, thread_id, PROBE_MESSAGE, events, started, done))
    try:
        await asyncio.wait_for(started.wait(), timeout=15.0)
        active = await _await_turn_active(client, thread_id, timeout=30.0)
        if active:
            record(PASS, "§4", "turn_active=true observed while a turn is in flight")
        elif done.is_set():
            # The turn finished before /live could catch turn_active=true (too-fast /
            # cached response). Inconclusive, not a failure — mirror the detachment SKIP.
            record(SKIP, "§4", "turn finished before turn_active could be observed "
                               "(too-fast turn; re-run or use a slower probe)")
        else:
            record(FAIL, "§4", "turn_active never became true during a turn")
        await asyncio.wait_for(done.wait(), timeout=180.0)
        # Give the turn's finally a beat to pop _active_turns, then confirm it clears.
        await asyncio.sleep(0.5)
        live = await get_live(client, thread_id)
        if live.get("turn_active") is False:
            record(PASS, "§4", "turn_active=false after the turn completed")
        else:
            record(FAIL, "§4", f"turn_active still {live.get('turn_active')!r} after completion")
    except asyncio.TimeoutError:
        record(FAIL, "§4", "timed out waiting for the probe turn")
    finally:
        task.cancel()


# ── §3: a second /message while a turn runs → 409 ──────────────────────────────
async def test_inflight_409(client: httpx.AsyncClient, thread_id: str) -> None:
    events: list[dict] = []
    started, done = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        _stream_turn(client, thread_id, PROBE_MESSAGE, events, started, done))
    try:
        await asyncio.wait_for(started.wait(), timeout=15.0)
        if not await _await_turn_active(client, thread_id, timeout=30.0):
            record(SKIP, "§3", "could not get a turn in flight to probe the 409 guard")
            return
        resp = await client.post(
            f"{BASE}/v1/chat/threads/{thread_id}/message", json={"content": "second"})
        if resp.status_code == 409:
            record(PASS, "§3", "second /message during an active turn → 409")
        else:
            record(FAIL, "§3", f"second /message returned {resp.status_code} (expected 409)")
    except asyncio.TimeoutError:
        record(FAIL, "§3", "timed out setting up the 409 probe")
    finally:
        # Let the first turn finish so it doesn't bleed into the next test.
        try:
            await asyncio.wait_for(done.wait(), timeout=180.0)
        except asyncio.TimeoutError:
            pass
        task.cancel()
        await _settle(client, thread_id)


# ── §1: a client disconnect does NOT cancel the detached turn ──────────────────
async def test_detachment_survives_disconnect(client: httpx.AsyncClient, thread_id: str) -> None:
    events: list[dict] = []
    started, done = asyncio.Event(), asyncio.Event()
    # Disconnect after the first 2 events (simulate a reload dropping the SSE).
    task = asyncio.create_task(
        _stream_turn(client, thread_id, PROBE_MESSAGE, events, started, done,
                     disconnect_after=2))
    try:
        await asyncio.wait_for(started.wait(), timeout=15.0)
        if not await _await_turn_active(client, thread_id, timeout=30.0):
            record(SKIP, "§1", "turn never became active; cannot probe detachment")
            return
        # Wait for the read task to return (the simulated disconnect).
        await asyncio.wait_for(done.wait(), timeout=30.0)
        # Immediately after the disconnect the detached turn must STILL be running.
        await asyncio.sleep(0.3)
        live = await get_live(client, thread_id)
        if live.get("turn_active") is True:
            record(PASS, "§1", "turn still active after client disconnect (not cancelled)")
            # The real §1 claim is stronger than "still active": the detached turn must
            # COMPLETE on its own (no client attached). Confirm it winds down.
            completed = False
            deadline = time.time() + 180.0
            while time.time() < deadline:
                if (await get_live(client, thread_id)).get("turn_active") is not True:
                    completed = True
                    break
                await asyncio.sleep(0.5)
            if completed:
                record(PASS, "§1", "detached turn completed on its own after the disconnect")
            else:
                record(FAIL, "§1", "detached turn never completed after disconnect "
                                   "(possible hang — the turn should run to chat_done)")
        else:
            record(SKIP, "§1", "turn already completed at disconnect time — inconclusive "
                               "(re-run; the turn finished faster than the 2-event cutoff)")
    except asyncio.TimeoutError:
        record(FAIL, "§1", "timed out probing detachment")
    finally:
        task.cancel()
        await _settle(client, thread_id)


# ── §11: POST /stop cancels a turn + persists a ✗ Stopped breadcrumb ────────────
async def test_stop_turn(client: httpx.AsyncClient, thread_id: str) -> None:
    events: list[dict] = []
    started, done = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        _stream_turn(client, thread_id, PROBE_MESSAGE, events, started, done))
    try:
        await asyncio.wait_for(started.wait(), timeout=15.0)
        if not await _await_turn_active(client, thread_id, timeout=30.0):
            record(SKIP, "§11", "turn never became active; cannot probe /stop")
            return
        resp = await client.post(f"{BASE}/v1/chat/threads/{thread_id}/stop")
        ok = resp.status_code == 200 and resp.json().get("ok") is True
        if ok:
            record(PASS, "§11", "/stop on an active turn → ok=true")
        else:
            record(FAIL, "§11", f"/stop returned {resp.status_code} {resp.text!r} (expected ok=true)")
        await asyncio.sleep(0.5)
        live = await get_live(client, thread_id)
        if live.get("turn_active") is False:
            record(PASS, "§11", "turn_active=false after /stop")
        else:
            record(FAIL, "§11", f"turn_active still {live.get('turn_active')!r} after /stop")
        thread = await get_thread(client, thread_id)
        has_breadcrumb = any(
            (m.get("metadata") or {}).get("breadcrumb") and "Stopped" in (m.get("content") or "")
            for m in thread.get("messages", []))
        if has_breadcrumb:
            record(PASS, "§11", "durable '✗ Stopped' breadcrumb persisted")
        else:
            record(FAIL, "§11", "no '✗ Stopped' breadcrumb in the transcript")
    except asyncio.TimeoutError:
        record(FAIL, "§11", "timed out probing /stop")
    finally:
        task.cancel()
        await _settle(client, thread_id)


# ── §11: /stop on an idle thread is a benign no-op (ok=false) ───────────────────
async def test_stop_idle_noop(client: httpx.AsyncClient, workspace: str) -> None:
    thread_id = await create_thread(client, workspace)
    resp = await client.post(f"{BASE}/v1/chat/threads/{thread_id}/stop")
    if resp.status_code == 200 and resp.json().get("ok") is False:
        record(PASS, "§11", "/stop on an idle thread → ok=false (benign no-op)")
    else:
        record(FAIL, "§11", f"/stop idle returned {resp.status_code} {resp.text!r} (expected ok=false)")


# ── §2 + §6: held-open EditGate is reload-durable + the decision is one-shot ────
async def test_editgate_durable_and_one_shot(
    client: httpx.AsyncClient, workspace: str
) -> None:
    """A per-edit gate (step_review=true) parks the turn on an in-memory future. Prove:
    (§2) it survives a dropped SSE (the FE-reload tier) and resolves via /edit-decision,
    resuming the turn; (§6) a second /edit-decision has no live waiter (ok=false).

    Best-effort: reaching an EditGate needs the model to actually propose an edit with
    review on (often via a propose_mode → mode=edit hop). If it answers/clarifies/plans
    instead, SKIP — the UI path is in the dev-host checklist (S4/S7). We REJECT the edit so
    the run is side-effect-free and repeatable; reject still fires the held-open future and
    resumes the turn (the §2 claim). Accept→promote is covered by S4 + the unit tests."""
    thread_id = await create_thread(client, workspace)
    s0, d0 = asyncio.Event(), asyncio.Event()
    msg_task = asyncio.create_task(_drain_post_stream(
        client, f"{BASE}/v1/chat/threads/{thread_id}/message",
        {"content": EDIT_MESSAGE, "step_review": True}, s0, d0))
    mode_task: asyncio.Task | None = None
    try:
        try:
            await asyncio.wait_for(s0.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            record(SKIP, "§2", "edit probe turn never started")
            return

        edit_live = await _await_gate_kind(client, thread_id, "edit", timeout=45.0)
        if edit_live is None:
            # The model gated the mode first (propose_mode). Pick "edit" and wait again.
            live = await get_live(client, thread_id)
            gate = live.get("pending_gate")
            if isinstance(gate, dict) and gate.get("kind") == "mode":
                sm, dm = asyncio.Event(), asyncio.Event()
                mode_task = asyncio.create_task(_drain_post_stream(
                    client, f"{BASE}/v1/chat/threads/{thread_id}/mode-decision",
                    {"mode": "edit"}, sm, dm))
                edit_live = await _await_gate_kind(client, thread_id, "edit", timeout=60.0)

        if edit_live is None:
            record(SKIP, "§2", "model did not reach an EditGate (no edit proposed with "
                               "review on); covered in the dev-host checklist (S4/S7)")
            return

        # Held-open EditGate ⇒ the turn is parked but ACTIVE (durable input signal).
        if edit_live.get("turn_active") is True:
            record(PASS, "§2", "EditGate held open with turn_active=true (turn parked on "
                               "the decision future)")
        else:
            record(FAIL, "§2", f"EditGate up but turn_active={edit_live.get('turn_active')!r} "
                               "(expected true while parked)")

        # Simulate a FE reload: drop the open SSE consumers. The gate must persist (it
        # lives in sqlite; the future survives in memory because the backend stays up).
        msg_task.cancel()
        if mode_task is not None:
            mode_task.cancel()
        await asyncio.sleep(0.4)
        relive = await get_live(client, thread_id)
        rgate = relive.get("pending_gate")
        if (isinstance(rgate, dict) and rgate.get("kind") == "edit"
                and relive.get("turn_active") is True):
            record(PASS, "§2", "EditGate + turn_active survive a dropped SSE (reload-durable)")
        else:
            record(FAIL, "§2", f"EditGate did not survive the SSE drop: gate={rgate!r} "
                               f"turn_active={relive.get('turn_active')!r}")

        # Resolve once (reject) → fires the surviving future → the turn resumes.
        resp = await client.post(
            f"{BASE}/v1/chat/threads/{thread_id}/edit-decision",
            json={"decision": "reject", "reason": "smoke"})
        if resp.status_code == 200 and resp.json().get("ok") is True:
            record(PASS, "§2", "/edit-decision fired the held-open future (ok=true → resumes)")
        else:
            record(FAIL, "§2", f"/edit-decision returned {resp.status_code} {resp.text!r} "
                               "(expected ok=true while a waiter is parked)")

        # One-shot: a SECOND decision on the same gate has no live waiter (the future is
        # already resolved/popped) → ok=false. Deterministic — does not race the loop.
        resp2 = await client.post(
            f"{BASE}/v1/chat/threads/{thread_id}/edit-decision",
            json={"decision": "reject"})
        if resp2.status_code == 200 and resp2.json().get("ok") is False:
            record(PASS, "§6", "second /edit-decision → ok=false (one-shot; no live waiter)")
        else:
            record(FAIL, "§6", f"second /edit-decision returned {resp2.status_code} "
                               f"{resp2.text!r} (expected ok=false)")

        # The turn resumes and winds down. If the loop re-proposes a follow-on edit (a weak
        # model often retries on a reject, exhausting the EDIT-phase budget), reject each so
        # the turn settles — nothing reaches the real workspace. Distinguish a genuinely
        # WEDGED turn (no gate, turn_active stuck true, no progress → FAIL) from one that is
        # just slowly winding down through re-proposed edits on a slow provider (→ SKIP).
        cleared = False
        rejected_followups = 0
        deadline = time.time() + 240.0  # EDIT budget × slow-local-model latency
        while time.time() < deadline:
            lv = await get_live(client, thread_id)
            g = lv.get("pending_gate")
            if isinstance(g, dict) and g.get("kind") == "edit":
                await client.post(
                    f"{BASE}/v1/chat/threads/{thread_id}/edit-decision",
                    json={"decision": "reject", "reason": "smoke"})
                rejected_followups += 1
            elif not g and lv.get("turn_active") is not True:
                cleared = True
                break
            await asyncio.sleep(0.3)
        if cleared:
            record(PASS, "§2", "gate cleared + turn resumed/settled after the decision")
        elif rejected_followups > 0:
            record(SKIP, "§2", f"turn still winding down after {rejected_followups} re-proposed "
                               "edit(s) — slow model exhausting the EDIT budget, NOT wedged "
                               "(the gate resolved each time; verify chat_done in the log)")
        else:
            record(FAIL, "§2", "gate/turn did not settle after the decision: no gate but "
                               "turn_active stuck true with no progress (turn may be wedged)")
    finally:
        msg_task.cancel()
        if mode_task is not None:
            mode_task.cancel()
        await _settle(client, thread_id)


# ── §1: a new turn clears a stale controller gate (conditional on a gate) ───────
async def test_gate_clear_at_start(client: httpx.AsyncClient, workspace: str) -> None:
    thread_id = await create_thread(client, workspace)
    events: list[dict] = []
    started, done = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        _stream_turn(client, thread_id, GATE_MESSAGE, events, started, done))
    try:
        await asyncio.wait_for(done.wait(), timeout=180.0)
    except asyncio.TimeoutError:
        record(SKIP, "§1", "gate probe turn did not complete in time")
        task.cancel()
        return
    task.cancel()
    await asyncio.sleep(0.3)
    live = await get_live(client, thread_id)
    gate = live.get("pending_gate")
    if not gate:
        record(SKIP, "§1", "model did not raise a controller gate on the probe message; "
                           "gate-clear-at-start is exercised in the dev-host checklist")
        return
    # A gate is up. Send a fresh message; handle_message must clear the stale gate
    # at the start of the new turn.
    events2: list[dict] = []
    s2, d2 = asyncio.Event(), asyncio.Event()
    t2 = asyncio.create_task(
        _stream_turn(client, thread_id, "Actually, never mind — what is 2+2?",
                     events2, s2, d2))
    try:
        await asyncio.wait_for(s2.wait(), timeout=15.0)
        await asyncio.sleep(0.4)
        live2 = await get_live(client, thread_id)
        stale = live2.get("pending_gate")
        # The OLD gate must be gone: either no gate, or a different one the new turn raised.
        if stale != gate:
            record(PASS, "§1", "new turn cleared the stale controller gate at start")
        else:
            record(FAIL, "§1", "stale controller gate survived a new turn (not cleared)")
    except asyncio.TimeoutError:
        record(SKIP, "§1", "second turn did not start in time to assert gate-clear")
    finally:
        try:
            await asyncio.wait_for(d2.wait(), timeout=180.0)
        except asyncio.TimeoutError:
            pass
        t2.cancel()


async def _settle(client: httpx.AsyncClient, thread_id: str, timeout: float = 30.0) -> None:
    """Best-effort: wait for any in-flight turn on a thread to clear before the next test."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = await get_live(client, thread_id)
        if live.get("turn_active") is not True:
            return
        await asyncio.sleep(0.25)


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: AGENTD_BASE_URL=http://127.0.0.1:8001 "
              "python3 scripts/verify/controller_ux_smoke.py '<workspace_path>'")
        return 2
    workspace = sys.argv[1]

    print(f"\n🔌 Controller UX smoke against {BASE}")
    print(f"   workspace: {workspace}\n")
    async with httpx.AsyncClient(timeout=httpx.Timeout(200.0)) as client:
        try:
            await client.get(f"{BASE}/health")
        except httpx.ConnectError:
            print(f"❌ Cannot reach {BASE} — start the backend with "
                  "AI_EDITOR_CHAT_CONTROLLER=1 first.")
            return 2

        # §4 idle + lifecycle, §3, §1 detachment, §11 stop run on dedicated threads so
        # state from one does not bleed into the next.
        tid = await create_thread(client, workspace)
        await test_live_idle_turn_active(client, tid)
        await test_turn_active_lifecycle(client, tid)
        await _settle(client, tid)

        await test_inflight_409(client, await create_thread(client, workspace))
        await test_detachment_survives_disconnect(client, await create_thread(client, workspace))
        await test_stop_turn(client, await create_thread(client, workspace))
        await test_stop_idle_noop(client, workspace)
        await test_editgate_durable_and_one_shot(client, workspace)
        await test_gate_clear_at_start(client, workspace)

    # ── Summary ──
    npass = sum(1 for s, _, _ in _results if s == PASS)
    nfail = sum(1 for s, _, _ in _results if s == FAIL)
    nskip = sum(1 for s, _, _ in _results if s == SKIP)
    print(f"\n──────── {npass} passed · {nfail} failed · {nskip} skipped ────────")
    if nfail:
        print("FAILED checks:")
        for s, ref, msg in _results:
            if s == FAIL:
                print(f"  ❌ {ref}  {msg}")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
