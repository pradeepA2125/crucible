"""Drive the REAL running :8000 backend (TQP model, crucible-stress workspace) with an
ambiguous task to coax a real clarify, then poll /live for the clarify gate.

The bait: both src/tax.py and src/taxutil.py define with_tax(), so a request to modify
"the with_tax function in the tax helper" cannot be resolved by reading — the model should
ask which one, surfacing options [src/tax.py, src/taxutil.py].

Run: python scripts/drive_clarify_live.py
"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"
WS = "/Users/pradeepkumar/projects/AI editor/workspaces/crucible-stress"
PROMPT = ("There are two tax modules in src/ that both define a with_tax function: src/tax.py "
          "and src/taxutil.py. I want to add an upper-bound rate check to with_tax, but only in "
          "the one we actually use in production. I'm not sure which that is — do NOT guess or "
          "edit both; ask me which module to change before doing anything.")


def _post(path, body, stream=False, timeout=240):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout)


def _get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    # 1. Create a thread.
    th = json.loads(_post("/v1/chat/threads", {"workspace": WS, "title": "clarify live drive"},
                          timeout=10).read())
    tid = th["thread_id"]
    print(f"thread = {tid}")

    # 2. Stream the message turn to completion (the controller explores then decides).
    print(f"→ sending ambiguous prompt:\n  {PROMPT!r}\n→ streaming turn (model exploring)…")
    last_event = None
    t0 = time.time()
    resp = _post(f"/v1/chat/threads/{tid}/message", {"content": PROMPT, "step_review": False})
    for raw in resp:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:
            continue
        et = ev.get("type")
        if et != last_event:
            print(f"    [{time.time()-t0:5.1f}s] {et}")
            last_event = et
        if et in ("chat_done", "done"):
            break

    # 3. Poll /live for the clarify gate (the turn ended; the gate is durable).
    print("→ polling /live for the clarify gate…")
    gate = None
    for _ in range(10):
        live = _get(f"/v1/chat/threads/{tid}/live")
        gate = live.get("pending_gate")
        if gate:
            break
        time.sleep(0.5)

    print("\n" + "=" * 60)
    if gate and gate.get("kind") == "clarify":
        p = gate.get("payload", {})
        print("✅ REAL CLARIFY GATE on the live backend:")
        print(f"   question: {p.get('question')!r}")
        print(f"   options : {p.get('options')}")
        print(f"   thread  : {tid}")
        print("=" * 60)
        print("\nFull /live pending_gate:\n" + json.dumps(gate, indent=2))
        return 0
    print(f"❌ no clarify gate (model did not clarify). pending_gate = {gate}")
    print(f"   thread = {tid} (inspect transcript / artifacts)")
    print("=" * 60)
    return 2


if __name__ == "__main__":
    sys.exit(main())
