"""Run an explicitly selected provider on a frozen fictional judge matrix.

No thread/database reads and no production provider swaps. Never use this runner
as an implicit upload path for project-session evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from agentd.eval.memory_judge_legacy import _JUDGE_SYSTEM, make_llm_judge
from agentd.eval.memory_judges import metrics, validate_matrix
from agentd.providers.factory import build_transport
from agentd.providers.reasoning_effort import ReasoningEffort
from agentd.providers.validate import ping_transport


async def run(args: argparse.Namespace) -> None:
    from dotenv import dotenv_values

    data = json.loads(args.matrix.read_text())
    cases = data["cases"]
    validate_matrix(cases)
    if any(case.get("origin") != "Codex-authored fictional scenario; no project-session data"
           for case in cases):
        raise ValueError("This runner is authorized only for the self-authored fictional matrix")
    env = {key: value for key, value in dotenv_values(args.env_file).items() if value is not None}
    credentials: dict[str, str] = {}
    if args.backend == "openai_compatible":
        credentials = {
            "CRUCIBLE_OPENAI_COMPAT_BASE_URL": "https://integrate.api.nvidia.com/v1",
            "CRUCIBLE_OPENAI_COMPAT_API_KEY": env.get("NVIDIA_API_KEY", ""),
            "CRUCIBLE_OPENAI_COMPAT_JSON_MODE": "json_object",
            "CRUCIBLE_OPENAI_COMPAT_MAX_RETRIES": "0",
            "CRUCIBLE_OPENAI_COMPAT_TIMEOUT_SEC": "20",
            "CRUCIBLE_OPENAI_COMPAT_STREAM_TIMEOUT_SEC": "20",
            "CRUCIBLE_OPENAI_COMPAT_JSON_MAX_TOKENS": "2048",
        }
    elif args.backend == "chatgpt":
        credentials = {"CRUCIBLE_CHATGPT_REGISTRATION": args.registration or "",
                       "CRUCIBLE_CHATGPT_MAX_RETRIES": "0"}
    elif args.backend == "gemini":
        credentials = {"GEMINI_API_KEY": env.get("GEMINI_API_KEY", ""),
                       "CRUCIBLE_GEMINI_MAX_RETRIES": "0",
                       "CRUCIBLE_GEMINI_THINKING_ENABLED": "false"}
    elif args.backend == "claude_subscription":
        credentials = {"CRUCIBLE_CLAUDE_REGISTRATION": args.registration or ""}
    else:
        raise ValueError("Unsupported evaluation provider")
    report: dict[str, object] = {
        "matrix_sha256": hashlib.sha256(args.matrix.read_bytes()).hexdigest(),
        "backend": args.backend, "model": args.model, "deadline_seconds": 10,
        "requested_effort": None if args.backend == "gemini" else "low",
        "payload": "Fictional matrix only; no project-session evidence",
        "judge_prompt_sha256": hashlib.sha256(_JUDGE_SYSTEM.encode()).hexdigest(),
        "cases": {}, "probe": {},
    }

    def save() -> None:
        args.output.write_text(json.dumps(report, indent=2))

    started = time.monotonic()
    try:
        transport = build_transport(args.backend, credentials=credentials)
        effort = getattr(transport, "set_reasoning_effort", None)
        # The Gemini alias does not prove which thinking control its underlying
        # model accepts. Use its configured no-thinking probe, not an unverified
        # effort override (the first override probe failed).
        if effort is not None and args.backend != "gemini":
            effort(ReasoningEffort.LOW)
        await ping_transport(transport, args.model, timeout_sec=20)
        seconds = time.monotonic() - started
        report["probe"] = {"ok": True, "seconds": seconds}
    except Exception as exc:
        report["probe"] = {"ok": False, "seconds": time.monotonic() - started,
                           "error_type": type(exc).__name__}
        save()
        print("probe failed", args.backend, type(exc).__name__, flush=True)
        return
    save()
    print("probe ready", args.backend, round(seconds, 3), flush=True)
    if args.backend == "openai_compatible" and seconds > 10:
        report["deferred"] = "Probe exceeds production judge deadline; use available plan for spike"
        save()
        return
    judge = make_llm_judge(transport, args.model)
    outputs: dict[str, object] = {}
    chosen: dict[str, list[str]] = {}
    complete = []
    invalid_total = 0
    # Serial calls avoid quota/rate-limit contention and measure the real deadline.
    for case in cases:
        candidates = case["candidates"]
        catalog = [f"[m:{memory['id']}@1] {memory['topic']} — when: "
                   f"{memory['applies_when']}: {memory['content']} (agent_inferred)"
                   for memory in candidates]
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(judge(case["signals"], catalog), timeout=10)
            ids = raw.get("memory_ids") if isinstance(raw, dict) else None
            if (not isinstance(ids, list) or len(ids) > 4
                    or any(not isinstance(key, str) for key in ids)):
                raise ValueError("Invalid judge IDs or delivery cap")
            allowed = {memory["id"] for memory in candidates}
            invalid = [key for key in ids if key not in allowed]
            invalid_total += len(invalid)
            chosen[case["id"]] = [key for key in dict.fromkeys(ids) if key in allowed]
            outputs[case["id"]] = {"ok": True, "response": raw, "invalid_ids": invalid,
                                    "seconds": time.monotonic() - started}
            complete.append(case)
        except Exception as exc:
            outputs[case["id"]] = {"ok": False, "error_type": type(exc).__name__,
                                    "seconds": time.monotonic() - started}
        report.update(cases=outputs, complete_case_metrics=metrics(complete, chosen),
                      all_case_metrics=metrics(cases, chosen), invalid_ids=invalid_total)
        save()
        print(case["id"], outputs[case["id"]]["ok"], flush=True)  # type: ignore[index]
        if len(outputs) >= 3 and not complete:
            report["deferred"] = "Three consecutive failures; remaining provider cases pending"
            save()
            break


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--registration")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
