#!/usr/bin/env python3
"""Adds artifact-based request sizes to eval_rollout.py results and prints summary tables.

For every model request the controller writes controller-turn-NN.json (system
instructions, user payload, response schema). Their o200k_base token count is an
ESTIMATE of the request; it is the only size available for providers that report no
usage (Ollama). Provider counts from /usage stay the primary numbers where they exist.

    python analyze_rollout.py --workspace WS RESULT.json [...] > table.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import tiktoken

ENC = tiktoken.get_encoding("o200k_base")


def _tokens(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def _turn_dirs(workspace: Path, thread: str) -> list[Path]:
    root = workspace / ".crucible/state/artifacts/chat" / thread
    dirs = [d for d in root.iterdir() if d.is_dir()] if root.exists() else []
    return sorted(dirs, key=lambda d: min((f.stat().st_mtime for f in d.glob("controller-turn-*.json")),
                                          default=d.stat().st_mtime))


def _requests(turn_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(turn_dir.glob("controller-turn-*.json")):
        artifact = json.loads(path.read_text())
        system = str(artifact.get("system_instructions", ""))
        payload = json.dumps(artifact.get("user_payload", {}), ensure_ascii=False)
        parts = (artifact.get("context_measurement") or {}).get("system_parts") or {}
        rows.append({"est_tokens": _tokens(system) + _tokens(payload),
                     "system_tokens": _tokens(system),
                     "tool_catalog_chars": parts.get("tool_catalog"),
                     "mcp_guidance_chars": parts.get("mcp_guidance"),
                     "capability_directory_chars": parts.get("capability_directory")})
    return rows


def enrich(result: dict, workspace: Path) -> dict:
    for scenario in result["scenarios"]:
        dirs = _turn_dirs(workspace, scenario["thread"])
        turns = scenario["turns"]
        # One artifact directory per controller turn; a clarify continuation adds one.
        for index, turn in enumerate(turns):
            turn["artifact_requests"] = _requests(dirs[index]) if index < len(dirs) else []
    return result


def turn_summary(turn: dict) -> dict:
    calls = [c["tool"] for c in turn["tool_calls"]]
    loads = [c.get("args") for c in turn["tool_calls"] if c["tool"] == "load_capabilities"]
    mcp = [c for c in calls if c and c.startswith("mcp__")]
    est = [r["est_tokens"] for r in turn.get("artifact_requests", [])]
    return {
        "requests": turn["usage"]["requests"] or len(est),
        "input": turn["usage"]["input"], "cached": turn["usage"]["cached"],
        "output": turn["usage"]["output"],
        "first_input": (turn["request_inputs"] or [None])[0],
        "est_first": est[0] if est else None, "est_total": sum(est),
        "seconds": turn["seconds"], "loads": loads, "mcp_calls": mcp,
        "other_tools": [c for c in calls if c not in ("load_capabilities",) and c not in mcp],
        "unloaded_refusals": turn["unloaded_refusals"],
        "malformed": turn["malformed_corrections"],
        "compactions": len(turn["compactions"]), "gates": turn["gates"],
        "answer": turn["answer"], "error": turn.get("client_error"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="Write enriched results back")
    parser.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.results:
        result = enrich(json.loads(path.read_text()), args.workspace)
        if args.write:
            path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\n## {result['model']} flag={result['flag']} suite={result['suite']}")
        for scenario in result["scenarios"]:
            for number, turn in enumerate(scenario["turns"], 1):
                row = turn_summary(turn)
                print(f"{scenario['name']}#{number}: " + json.dumps(
                    {k: v for k, v in row.items() if k != "answer"}, default=str))
                print(f"   answer: {row['answer'][:300]!r}")


if __name__ == "__main__":
    main()
